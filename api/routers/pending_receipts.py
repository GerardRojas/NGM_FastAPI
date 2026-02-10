# api/routers/pending_receipts.py
# ================================
# Pending Receipts API Router
# ================================
# Manages receipts uploaded to project channels for expense processing
#
# =============================================================================
# @process: Receipt_Processing
# @process_name: Receipt Processing Workflow
# @process_category: bookkeeping
# @process_trigger: event
# @process_description: Process uploaded receipts from project channels into expense entries
# @process_owner: Bookkeeper
#
# @step: 1
# @step_name: Receipt Upload
# @step_type: action
# @step_description: User uploads receipt image/PDF to project channel
# @step_connects_to: 2
#
# @step: 2
# @step_name: Store in Bucket
# @step_type: action
# @step_description: Save file to pending-expenses storage bucket
# @step_connects_to: 3
#
# @step: 3
# @step_name: OCR Analysis
# @step_type: action
# @step_description: Extract vendor, amount, date using OpenAI Vision
# @step_connects_to: 4
#
# @step: 4
# @step_name: Create Pending Record
# @step_type: action
# @step_description: Store extracted data in pending_receipts table
# @step_connects_to: 5
#
# @step: 5
# @step_name: Await Review
# @step_type: wait
# @step_description: Receipt waits for bookkeeper review and approval
# @step_connects_to: 6, 7
#
# @step: 6
# @step_name: Create Expense
# @step_type: action
# @step_description: Convert approved receipt into expenses_manual_COGS entry
#
# @step: 7
# @step_name: Reject Receipt
# @step_type: action
# @step_description: Mark receipt as rejected if invalid
# =============================================================================

from fastapi import APIRouter, HTTPException, File, UploadFile, Form, Query
from pydantic import BaseModel
from api.supabase_client import supabase
from typing import Optional, List, Dict, Any
import base64
import hashlib
import os
import re
import uuid
from datetime import datetime, timedelta
from openai import OpenAI
import json
import io
from pdf2image import convert_from_bytes
from api.helpers.andrew_messenger import post_andrew_message, ANDREW_BOT_USER_ID
from services.receipt_scanner import (
    scan_receipt as _scan_receipt_core,
    auto_categorize as _auto_categorize_core,
)

router = APIRouter(prefix="/pending-receipts", tags=["Pending Receipts"])

# Storage bucket name
RECEIPTS_BUCKET = "pending-expenses"


# ====== MODELS ======

class PendingReceiptCreate(BaseModel):
    project_id: str
    message_id: Optional[str] = None
    uploaded_by: str


class PendingReceiptUpdate(BaseModel):
    status: Optional[str] = None
    vendor_name: Optional[str] = None
    amount: Optional[float] = None
    receipt_date: Optional[str] = None
    suggested_category: Optional[str] = None
    suggested_account_id: Optional[str] = None


class LinkToExpenseRequest(BaseModel):
    expense_id: str


class CreateExpenseFromReceiptRequest(BaseModel):
    """Data to create an expense from a pending receipt"""
    project_id: str
    vendor_id: Optional[str] = None
    amount: float
    txn_date: Optional[str] = None
    description: Optional[str] = None
    account_id: Optional[str] = None
    payment_type: Optional[str] = None
    created_by: str


class CheckActionRequest(BaseModel):
    """User action in the check processing conversation"""
    action: str  # confirm_check, deny_check, submit_amount, split_yes, split_no,
                 # submit_description, submit_split_line, split_done, confirm_categories, cancel
    payload: Optional[Dict[str, Any]] = None
    user_id: Optional[str] = None


class DuplicateActionRequest(BaseModel):
    """User action in the duplicate confirmation conversation"""
    action: str  # confirm_process, skip
    user_id: Optional[str] = None


class ReceiptActionRequest(BaseModel):
    """User action in the receipt split conversation"""
    action: str  # single_project, split_projects, submit_split_line, split_done, cancel
    payload: Optional[Dict[str, Any]] = None
    user_id: Optional[str] = None


# ====== HELPERS ======

def ensure_bucket_exists():
    """Ensures the pending-expenses bucket exists, creates if not"""
    try:
        # Try to get bucket info
        supabase.storage.get_bucket(RECEIPTS_BUCKET)
    except Exception:
        # Bucket doesn't exist, create it
        try:
            supabase.storage.create_bucket(
                RECEIPTS_BUCKET,
                options={"public": True}
            )
            print(f"[PendingReceipts] Created bucket: {RECEIPTS_BUCKET}")
        except Exception as e:
            # Bucket might already exist (race condition) or other error
            print(f"[PendingReceipts] Bucket creation note: {e}")


def generate_storage_path(project_id: str, receipt_id: str, filename: str) -> str:
    """Generate storage path for receipt file"""
    # Clean filename
    safe_filename = "".join(c if c.isalnum() or c in ".-_" else "_" for c in filename)
    return f"{project_id}/{receipt_id}_{safe_filename}"


# ====== ENDPOINTS ======

@router.post("/upload", status_code=201)
async def upload_receipt(
    file: UploadFile = File(...),
    project_id: str = Form(...),
    uploaded_by: str = Form(...),
    message_id: Optional[str] = Form(None)
):
    """
    Upload a receipt file to the pending-expenses bucket.

    Creates a pending_receipt record for later processing.

    Accepts: images (JPG, PNG, WebP, GIF) and PDFs
    Max size: 20MB

    Returns the created pending receipt record.
    """
    try:
        # Validate file type
        allowed_types = ["image/jpeg", "image/jpg", "image/png", "image/webp", "image/gif", "application/pdf"]
        if file.content_type not in allowed_types:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid file type. Allowed: JPG, PNG, WebP, GIF, PDF. Got: {file.content_type}"
            )

        # Read file content
        file_content = await file.read()

        # Validate size (max 20MB)
        if len(file_content) > 20 * 1024 * 1024:
            raise HTTPException(status_code=400, detail="File too large. Maximum size is 20MB.")

        # Compute file hash for duplicate detection
        file_hash = hashlib.sha256(file_content).hexdigest()

        # Generate unique ID for this receipt
        receipt_id = str(uuid.uuid4())

        # Ensure bucket exists
        ensure_bucket_exists()

        # Generate storage path
        storage_path = generate_storage_path(project_id, receipt_id, file.filename)

        # Upload to Supabase Storage
        try:
            supabase.storage.from_(RECEIPTS_BUCKET).upload(
                path=storage_path,
                file=file_content,
                file_options={"content-type": file.content_type}
            )
        except Exception as upload_error:
            raise HTTPException(
                status_code=500,
                detail=f"Failed to upload file: {str(upload_error)}"
            )

        # Get public URL
        file_url = supabase.storage.from_(RECEIPTS_BUCKET).get_public_url(storage_path)

        # Generate thumbnail URL for images (Supabase can transform images)
        thumbnail_url = None
        if file.content_type.startswith("image/"):
            # Use Supabase image transformation for thumbnail
            thumbnail_url = f"{file_url}?width=200&height=200&resize=contain"

        # Create pending_receipt record
        receipt_data = {
            "id": receipt_id,
            "project_id": project_id,
            "message_id": message_id,
            "file_name": file.filename,
            "file_url": file_url,
            "file_type": file.content_type,
            "file_size": len(file_content),
            "file_hash": file_hash,
            "thumbnail_url": thumbnail_url,
            "status": "pending",
            "uploaded_by": uploaded_by,
            "created_at": datetime.utcnow().isoformat(),
            "updated_at": datetime.utcnow().isoformat()
        }

        result = supabase.table("pending_receipts").insert(receipt_data).execute()

        if not result.data:
            raise HTTPException(status_code=500, detail="Failed to create receipt record")

        return {
            "success": True,
            "data": result.data[0],
            "message": "Receipt uploaded successfully"
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error uploading receipt: {str(e)}")


@router.get("/project/{project_id}")
def get_project_receipts(
    project_id: str,
    status: Optional[str] = Query(None, description="Filter by status"),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0)
):
    """
    Get all pending receipts for a project.

    Optional filters:
    - status: Filter by status (pending, processing, ready, linked, rejected, error)
    """
    try:
        query = supabase.table("pending_receipts") \
            .select("*, users!uploaded_by(user_name, avatar_color)") \
            .eq("project_id", project_id) \
            .order("created_at", desc=True) \
            .range(offset, offset + limit - 1)

        if status:
            query = query.eq("status", status)

        result = query.execute()

        # Get counts
        counts_result = supabase.rpc(
            "get_pending_receipts_count",
            {"p_project_id": project_id}
        ).execute()

        counts = counts_result.data[0] if counts_result.data else {
            "total": 0, "pending": 0, "ready": 0, "processing": 0
        }

        return {
            "success": True,
            "data": result.data or [],
            "counts": counts,
            "pagination": {
                "offset": offset,
                "limit": limit,
                "has_more": len(result.data or []) == limit
            }
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching receipts: {str(e)}")


@router.get("/{receipt_id}")
def get_receipt(receipt_id: str):
    """Get a single pending receipt by ID"""
    try:
        result = supabase.table("pending_receipts") \
            .select("*, users!uploaded_by(user_name, avatar_color), projects(project_name)") \
            .eq("id", receipt_id) \
            .single() \
            .execute()

        if not result.data:
            raise HTTPException(status_code=404, detail="Receipt not found")

        return {"success": True, "data": result.data}

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching receipt: {str(e)}")


@router.post("/{receipt_id}/process")
async def process_receipt(receipt_id: str):
    """
    Process a pending receipt using OpenAI Vision API.

    Extracts: vendor, amount, date, suggested category
    Updates the receipt record with parsed data.
    """
    try:
        # Get receipt record
        receipt = supabase.table("pending_receipts") \
            .select("*") \
            .eq("id", receipt_id) \
            .single() \
            .execute()

        if not receipt.data:
            raise HTTPException(status_code=404, detail="Receipt not found")

        receipt_data = receipt.data

        # Check if already processed
        if receipt_data.get("status") == "linked":
            raise HTTPException(status_code=400, detail="Receipt already linked to an expense")

        # Update status to processing
        supabase.table("pending_receipts") \
            .update({"status": "processing", "updated_at": datetime.utcnow().isoformat()}) \
            .eq("id", receipt_id) \
            .execute()

        try:
            # Download file from storage
            file_url = receipt_data.get("file_url")
            file_type = receipt_data.get("file_type")

            # Fetch the file
            import httpx
            async with httpx.AsyncClient() as client:
                response = await client.get(file_url)
                if response.status_code != 200:
                    raise Exception("Failed to download receipt file")
                file_content = response.content

            # Get OpenAI API key
            openai_api_key = os.getenv("OPENAI_API_KEY")
            if not openai_api_key:
                raise HTTPException(status_code=500, detail="OpenAI API key not configured")

            # Initialize OpenAI client
            ai_client = OpenAI(api_key=openai_api_key)

            # Get vendors list for matching
            vendors_resp = supabase.table("Vendors").select("id, vendor_name").execute()
            vendors_list = [
                {"id": v.get("id"), "name": v.get("vendor_name")}
                for v in (vendors_resp.data or [])
                if v.get("vendor_name")
            ]

            # Get accounts list for category matching
            accounts_resp = supabase.table("accounts").select("account_id, Name").execute()
            accounts_list = [
                {"id": a.get("account_id"), "name": a.get("Name")}
                for a in (accounts_resp.data or [])
                if a.get("Name")
            ]

            # Process image or PDF
            base64_images = []
            media_type = file_type

            if file_type == "application/pdf":
                # Convert PDF to images
                import platform
                poppler_path = None
                if platform.system() == "Windows":
                    poppler_path = r'C:\poppler\poppler-24.08.0\Library\bin'

                images = convert_from_bytes(file_content, dpi=200, poppler_path=poppler_path)
                for img in images:
                    buffer = io.BytesIO()
                    img.save(buffer, format='PNG')
                    buffer.seek(0)
                    base64_images.append(base64.b64encode(buffer.getvalue()).decode('utf-8'))
                media_type = "image/png"
            else:
                base64_images = [base64.b64encode(file_content).decode('utf-8')]

            # Build prompt for OpenAI
            prompt = f"""Analyze this receipt/invoice and extract the key information.

AVAILABLE VENDORS (match to one if possible):
{json.dumps([v["name"] for v in vendors_list], indent=2)}

AVAILABLE EXPENSE CATEGORIES (match to one if possible):
{json.dumps([a["name"] for a in accounts_list], indent=2)}

Extract and return JSON with:
{{
    "vendor_name": "Matched vendor name or extracted name",
    "vendor_id": "UUID if matched to list, null otherwise",
    "amount": 123.45,
    "receipt_date": "YYYY-MM-DD",
    "description": "Brief description of purchase",
    "suggested_category": "Matched category name",
    "suggested_account_id": "UUID if matched to list, null otherwise",
    "confidence": 0.95
}}

RULES:
- Use the TOTAL amount, not subtotals or individual items
- For vendor, try to match exactly to the list first
- For category, match based on what was purchased
- Date format must be YYYY-MM-DD
- confidence is 0-1 indicating how sure you are of the extraction
"""

            # Build message content
            content = [{"type": "text", "text": prompt}]
            for img_b64 in base64_images[:3]:  # Limit to 3 pages
                content.append({
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{media_type};base64,{img_b64}",
                        "detail": "high"
                    }
                })

            # Call OpenAI Vision
            response = ai_client.chat.completions.create(
                model="gpt-4o",
                messages=[{"role": "user", "content": content}],
                max_tokens=1000,
                response_format={"type": "json_object"}
            )

            # Parse response
            parsed_data = json.loads(response.choices[0].message.content)

            # Find vendor_id if matched by name
            vendor_id = parsed_data.get("vendor_id")
            if not vendor_id and parsed_data.get("vendor_name"):
                for v in vendors_list:
                    if v["name"].lower() == parsed_data["vendor_name"].lower():
                        vendor_id = v["id"]
                        break

            # Find account_id if matched by name
            account_id = parsed_data.get("suggested_account_id")
            if not account_id and parsed_data.get("suggested_category"):
                for a in accounts_list:
                    if a["name"].lower() == parsed_data["suggested_category"].lower():
                        account_id = a["id"]
                        break

            # Update receipt with parsed data
            update_data = {
                "status": "ready",
                "parsed_data": parsed_data,
                "vendor_name": parsed_data.get("vendor_name"),
                "amount": parsed_data.get("amount"),
                "receipt_date": parsed_data.get("receipt_date"),
                "suggested_category": parsed_data.get("suggested_category"),
                "suggested_account_id": account_id,
                "processed_at": datetime.utcnow().isoformat(),
                "updated_at": datetime.utcnow().isoformat()
            }

            result = supabase.table("pending_receipts") \
                .update(update_data) \
                .eq("id", receipt_id) \
                .execute()

            return {
                "success": True,
                "data": result.data[0] if result.data else None,
                "parsed": parsed_data,
                "message": "Receipt processed successfully"
            }

        except Exception as process_error:
            # Update status to error
            supabase.table("pending_receipts") \
                .update({
                    "status": "error",
                    "processing_error": str(process_error),
                    "updated_at": datetime.utcnow().isoformat()
                }) \
                .eq("id", receipt_id) \
                .execute()
            raise HTTPException(status_code=500, detail=f"Processing error: {str(process_error)}")

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error processing receipt: {str(e)}")


# ====== AGENT HELPERS ======

def _check_file_hash_duplicate(file_hash: str, project_id: str, current_receipt_id: str):
    """Check if exact same file was already uploaded (by SHA-256 hash)."""
    try:
        result = supabase.table("pending_receipts") \
            .select("id, file_name, vendor_name, amount, receipt_date, status") \
            .eq("project_id", project_id) \
            .eq("file_hash", file_hash) \
            .neq("id", current_receipt_id) \
            .execute()
        return result.data[0] if result.data else None
    except Exception as e:
        print(f"[Agent] File hash duplicate check error: {e}")
        return None


def _check_data_duplicate(project_id: str, vendor_name, amount, receipt_date):
    """Check if similar receipt exists (same vendor + amount + date +/- 3 days)."""
    if not vendor_name or not amount or not receipt_date:
        return None
    try:
        dt = datetime.fromisoformat(str(receipt_date))
        min_date = (dt - timedelta(days=3)).date().isoformat()
        max_date = (dt + timedelta(days=3)).date().isoformat()

        result = supabase.table("pending_receipts") \
            .select("id, file_name, vendor_name, amount, receipt_date, status") \
            .eq("project_id", project_id) \
            .eq("vendor_name", vendor_name) \
            .eq("amount", amount) \
            .gte("receipt_date", min_date) \
            .lte("receipt_date", max_date) \
            .in_("status", ["ready", "linked"]) \
            .execute()
        return result.data[0] if result.data else None
    except Exception as e:
        print(f"[Agent] Data duplicate check error: {e}")
        return None


def _check_expense_duplicate(project_id: str, vendor_id, amount, txn_date):
    """Check if matching expense already exists in expenses_manual_COGS."""
    if not vendor_id or not amount or not txn_date:
        return None
    try:
        result = supabase.table("expenses_manual_COGS") \
            .select("expense_id, vendor_id, Amount, TxnDate, LineDescription") \
            .eq("project", project_id) \
            .eq("vendor_id", vendor_id) \
            .eq("Amount", amount) \
            .eq("TxnDate", txn_date) \
            .execute()
        return result.data[0] if result.data else None
    except Exception as e:
        print(f"[Agent] Expense duplicate check error: {e}")
        return None


def _check_file_hash_split_reuse(file_hash: str, project_id: str, receipt_id: str):
    """Check if hash matches a 'split' receipt from ANOTHER project (cross-project reuse)."""
    try:
        result = supabase.table("pending_receipts") \
            .select("*") \
            .eq("file_hash", file_hash) \
            .eq("status", "split") \
            .neq("project_id", project_id) \
            .neq("id", receipt_id) \
            .execute()
        if result.data:
            return sorted(result.data, key=lambda x: x.get("created_at", ""), reverse=True)[0]
        return None
    except Exception as e:
        print(f"[Agent] Split reuse check error: {e}")
        return None


def _create_receipt_expense(project_id, parsed_data, receipt_data, vendor_id, account_id,
                            amount=None, description=None, bill_id=None, txn_date=None,
                            txn_type_id=None, payment_method_id=None):
    """Create a single expense from receipt data. Returns expense record or None."""
    try:
        expense_data = {
            "project": project_id,
            "Amount": amount or parsed_data.get("amount"),
            "TxnDate": txn_date or parsed_data.get("receipt_date"),
            "LineDescription": description or parsed_data.get("description") or f"Receipt: {receipt_data.get('file_name')}",
            "account_id": account_id,
            "created_by": receipt_data.get("uploaded_by"),
            "receipt_url": receipt_data.get("file_url"),
            "auth_status": False,
        }
        if vendor_id:
            expense_data["vendor_id"] = vendor_id
        if bill_id:
            expense_data["bill_id"] = bill_id
        if txn_type_id:
            expense_data["txn_type_id"] = txn_type_id
        if payment_method_id:
            expense_data["payment_type"] = payment_method_id
        expense_data = {k: v for k, v in expense_data.items() if v is not None}

        result = supabase.table("expenses_manual_COGS").insert(expense_data).execute()
        if result.data:
            return result.data[0]
        return None
    except Exception as e:
        print(f"[Agent] Expense creation error: {e}")
        return None


def _parse_receipt_split_line(text: str):
    """Parse '500 plumbing materials' or '500.50 electrical supplies' format."""
    match = re.match(r'^\$?\s*([\d,]+\.?\d*)\s+(.+)$', text.strip())
    if not match:
        return None
    amount_str = match.group(1).replace(',', '')
    try:
        amount = float(amount_str)
        if amount <= 0:
            return None
        return {"amount": amount, "description": match.group(2).strip()}
    except ValueError:
        return None


def _parse_item_assignments(text: str, num_items: int):
    """Parse '3, 4 to Sunset Heights, 7 to Oak Park' into project assignments.

    Supports bilingual input (English/Spanish):
      - Separators: to, a, van a, go to, para
      - Number connectors: , (comma), y, and, spaces

    Returns list of {"item_indices": [int, ...], "project_query": str} or None on failure.
    """
    text = text.strip()
    if not text:
        return None

    # Split on common delimiters between assignments: period, semicolon, newline,
    # or comma followed by a digit (indicating a new assignment group)
    # We process the full text with findall instead of splitting
    separators = r'(?:to|a|van\s+a|go\s+to|para)\s+'
    number_group = r'((?:\d+)(?:\s*[,\s]\s*(?:y|and)?\s*\d+)*)'
    pattern = rf'{number_group}\s+{separators}(.+?)(?=\s*[,;.]\s*\d|\s*\n\s*\d|$)'

    matches = re.findall(pattern, text, re.IGNORECASE)
    if not matches:
        return None

    assignments = []
    seen_indices = set()
    for nums_str, proj_name in matches:
        indices = [int(n) for n in re.findall(r'\d+', nums_str)]
        # Validate range
        if not indices or any(i < 1 or i > num_items for i in indices):
            return None
        # Check for duplicate assignments
        for i in indices:
            if i in seen_indices:
                return None
            seen_indices.add(i)
        assignments.append({
            "item_indices": indices,
            "project_query": proj_name.strip().rstrip(",;."),
        })

    return assignments if assignments else None


def _resolve_project_by_name(query: str):
    """Fetch projects from DB and find first case-insensitive substring match.
    Returns {"project_id": ..., "project_name": ...} or None.
    """
    try:
        projects = supabase.table("projects").select("project_id, project_name").execute()
        query_lower = query.lower().strip()
        for p in (projects.data or []):
            if query_lower in p["project_name"].lower():
                return {"project_id": p["project_id"], "project_name": p["project_name"]}
    except Exception as e:
        print(f"[ReceiptFlow] Project lookup error: {e}")
    return None


def _detect_language(text: str) -> str:
    """Simple heuristic: returns 'es' for Spanish, 'en' for English (default)."""
    spanish_words = [
        "recibo", "factura", "materiales", "proyecto", "proveedor",
        "gracias", "para", "compra", "total", "fecha"
    ]
    text_lower = text.lower()
    hits = sum(1 for w in spanish_words if w in text_lower)
    if hits >= 2:
        return "es"
    if re.search(r'[\u00f1\u00e1\u00e9\u00ed\u00f3\u00fa\u00fc]', text_lower):
        return "es"
    return "en"


def _build_numbered_list(line_items: list, lang: str = "en") -> str:
    """Build a numbered list of line items with categories for the bot message."""
    lines = []
    for i, item in enumerate(line_items, 1):
        desc = item.get("description") or "Item"
        amt = item.get("amount", 0)
        cat = item.get("account_name") or "Uncategorized"
        conf = item.get("confidence", 0)
        lines.append(f"{i}. {desc} -- ${amt:,.2f} -> {cat} ({conf}%)")
    return "\n".join(lines)


def _build_agent_message(parsed_data, categorize_data, warnings, lang="en", expense_created=False):
    """Build Arturito's summary message for the receipts channel."""
    vendor = parsed_data.get("vendor_name") or "Unknown"
    amount = parsed_data.get("amount") or 0
    date = parsed_data.get("receipt_date") or "Unknown"
    category = categorize_data.get("account_name") or parsed_data.get("suggested_category") or "Uncategorized"
    confidence = int(categorize_data.get("confidence", 0))
    line_items = parsed_data.get("line_items", [])
    item_count = len(line_items)
    show_list = item_count > 1 and not expense_created

    if lang == "es":
        header = f"Escanee este recibo de **{vendor}** -- ${amount:,.2f}"
        if date != "Unknown":
            header += f" del {date}"
        header += "."
        if show_list:
            numbered = _build_numbered_list(line_items, lang)
            body = f"\n{numbered}"
        else:
            body = f"Categoria: **{category}** ({confidence}% confianza)"
        if warnings:
            body += "\n\nAtencion:\n" + "\n".join(f"- {w}" for w in warnings)
        if expense_created:
            body += "\n\nGasto guardado -- listo para autorizacion."
    else:
        header = f"I scanned this receipt from **{vendor}** -- ${amount:,.2f}"
        if date != "Unknown":
            header += f" on {date}"
        header += "."
        if show_list:
            numbered = _build_numbered_list(line_items, lang)
            body = f"\n{numbered}"
        else:
            body = f"Category: **{category}** ({confidence}% confidence)"
        if warnings:
            body += "\n\nHeads up:\n" + "\n".join(f"- {w}" for w in warnings)
        if expense_created:
            body += "\n\nExpense saved -- ready for authorization."

    return f"{header}\n{body}"


# ====== AGENT ENDPOINT ======

@router.post("/{receipt_id}/agent-process")
async def agent_process_receipt(
    receipt_id: str,
    force: bool = Query(False, description="Force processing even if duplicate detected")
):
    """
    Full automated processing pipeline for material receipts.

    Pipeline:
    1. Fetch receipt, download file
    2. Duplicate check (file hash) / split reuse detection
    3. OCR extraction (OpenAI Vision)
    4. Data duplicate check (vendor+amount+date)
    5. Auto-categorize (GPT with construction stage context)
    6. Update receipt with enriched data + receipt_flow
    7. Post Arturito summary + project question (awaiting user response)

    Expense creation happens in the receipt-action endpoint after user responds.
    """
    receipt_data = None
    project_id = None

    try:
        # ===== STEP 1: Fetch receipt =====
        print(f"[Agent] === START agent-process for receipt {receipt_id} ===")
        receipt = supabase.table("pending_receipts") \
            .select("*") \
            .eq("id", receipt_id) \
            .single() \
            .execute()

        if not receipt.data:
            raise HTTPException(status_code=404, detail="Receipt not found")

        receipt_data = receipt.data
        project_id = receipt_data.get("project_id")
        print(f"[Agent] Step 1: Receipt fetched | project={project_id} | file={receipt_data.get('file_name')}")

        if receipt_data.get("status") == "linked":
            raise HTTPException(status_code=400, detail="Receipt already linked to an expense")

        # Update status to processing
        supabase.table("pending_receipts") \
            .update({"status": "processing", "updated_at": datetime.utcnow().isoformat()}) \
            .eq("id", receipt_id) \
            .execute()

        # ===== STEP 2: Download file + duplicate check =====
        print(f"[Agent] Step 2: Downloading file...")
        file_url = receipt_data.get("file_url")
        file_type = receipt_data.get("file_type")
        file_hash = receipt_data.get("file_hash")

        import httpx
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(file_url)
            if resp.status_code != 200:
                raise Exception("Failed to download receipt file from storage")
            file_content = resp.content
        print(f"[Agent] Step 2: File downloaded | size={len(file_content)} bytes | type={file_type}")

        # Compute hash if not already stored (backwards compat with old uploads)
        if not file_hash:
            file_hash = hashlib.sha256(file_content).hexdigest()
            supabase.table("pending_receipts") \
                .update({"file_hash": file_hash}) \
                .eq("id", receipt_id) \
                .execute()

        # Check if this file was already processed as a split in another project
        split_reuse = _check_file_hash_split_reuse(file_hash, project_id, receipt_id)
        if split_reuse:
            print(f"[Agent] Step 2: Split reuse detected | original in project {split_reuse.get('project_id')}")
            original_parsed = split_reuse.get("parsed_data") or {}
            # Strip old flow states from copied data
            original_parsed.pop("receipt_flow", None)
            original_parsed.pop("check_flow", None)
            original_parsed.pop("duplicate_flow", None)

            receipt_flow = {
                "state": "awaiting_item_selection",
                "started_at": datetime.utcnow().isoformat(),
                "split_items": [],
                "total_for_project": 0.0,
                "reused_from": split_reuse.get("id"),
            }
            original_parsed["receipt_flow"] = receipt_flow

            supabase.table("pending_receipts").update({
                "status": "ready",
                "parsed_data": original_parsed,
                "vendor_name": original_parsed.get("vendor_name"),
                "amount": original_parsed.get("amount"),
                "receipt_date": original_parsed.get("receipt_date"),
                "suggested_category": (original_parsed.get("categorization") or {}).get("account_name"),
                "suggested_account_id": (original_parsed.get("categorization") or {}).get("account_id"),
                "processed_at": datetime.utcnow().isoformat(),
                "updated_at": datetime.utcnow().isoformat()
            }).eq("id", receipt_id).execute()

            # Get project name
            project_name = "this project"
            try:
                proj_resp = supabase.table("projects").select("project_name") \
                    .eq("project_id", project_id).single().execute()
                if proj_resp.data and proj_resp.data.get("project_name"):
                    project_name = proj_resp.data["project_name"]
            except Exception:
                pass

            vendor_name = original_parsed.get("vendor_name", "Unknown")
            amount = original_parsed.get("amount", 0)
            cat = original_parsed.get("categorization", {})

            post_andrew_message(
                content=(
                    f"This receipt from **{vendor_name}** for ${amount:,.2f} was already processed as a split in another project.\n"
                    f"Category: **{cat.get('account_name', 'Uncategorized')}** ({cat.get('confidence', 0)}% confidence)\n\n"
                    f"Is this entire bill for **{project_name}**?"
                ),
                project_id=project_id,
                metadata={
                    "agent_message": True,
                    "pending_receipt_id": receipt_id,
                    "receipt_status": "ready",
                    "receipt_flow_state": "awaiting_item_selection",
                    "receipt_flow_active": True,
                }
            )

            return {
                "success": True,
                "status": "ready",
                "message": "Split receipt reuse - awaiting item selection",
            }

        # Check exact file duplicate
        print(f"[Agent] Step 2: Checking file hash duplicate | hash={file_hash[:16]}... | force={force}")
        hash_dup = _check_file_hash_duplicate(file_hash, project_id, receipt_id)
        if hash_dup and not force:
            print(f"[Agent] Step 2: DUPLICATE FOUND | matches receipt {hash_dup.get('id')}")

            dup_vendor = hash_dup.get("vendor_name") or "Unknown"
            dup_amount = hash_dup.get("amount") or 0
            dup_date = hash_dup.get("receipt_date") or "Unknown"

            # Store duplicate flow state in parsed_data (same pattern as check_flow)
            duplicate_flow = {
                "state": "awaiting_confirmation",
                "detected_at": datetime.utcnow().isoformat(),
                "duplicate_of": hash_dup.get("id"),
                "dup_vendor": dup_vendor,
                "dup_amount": float(dup_amount),
                "dup_date": str(dup_date),
            }

            existing_parsed = receipt_data.get("parsed_data") or {}
            existing_parsed["duplicate_flow"] = duplicate_flow

            supabase.table("pending_receipts") \
                .update({
                    "status": "duplicate",
                    "parsed_data": existing_parsed,
                    "processing_error": f"Duplicate of receipt {hash_dup.get('id')}",
                    "updated_at": datetime.utcnow().isoformat()
                }) \
                .eq("id", receipt_id) \
                .execute()

            post_andrew_message(
                content=(
                    f"Heads up -- this looks like a duplicate. "
                    f"I found a matching receipt from {dup_vendor} for ${dup_amount:,.2f} "
                    f"({dup_date}), currently {hash_dup.get('status')}.\n\n"
                    "Want me to process it anyway?"
                ),
                project_id=project_id,
                metadata={
                    "agent_message": True,
                    "pending_receipt_id": receipt_id,
                    "receipt_status": "duplicate",
                    "duplicate_of": hash_dup.get("id"),
                    "allow_force_process": True,
                    "duplicate_flow_active": True,
                    "duplicate_flow_state": "awaiting_confirmation",
                }
            )

            return {
                "success": False,
                "status": "duplicate",
                "message": "Duplicate receipt detected",
                "duplicate_of": hash_dup
            }
        elif hash_dup and force:
            print(f"[Agent] Step 2: DUPLICATE FOUND but force=True, continuing...")

        # ===== STEP 2.5: Check Detection (Heuristic) =====
        file_name = receipt_data.get("file_name", "")
        is_image = (file_type or "").startswith("image/")
        is_likely_check = is_image and "check" in file_name.lower()

        if is_likely_check:
            print(f"[Agent] Step 2.5: CHECK DETECTED by filename heuristic: {file_name}")

            check_flow = {
                "state": "check_detected",
                "detected_at": datetime.utcnow().isoformat(),
                "amount": None,
                "is_split": None,
                "splits": [],
                "description": None,
                "categorizations": [],
            }

            supabase.table("pending_receipts").update({
                "status": "check_review",
                "parsed_data": {"check_flow": check_flow},
                "updated_at": datetime.utcnow().isoformat()
            }).eq("id", receipt_id).execute()

            post_andrew_message(
                content=(
                    f"This file \"{file_name}\" looks like a check. "
                    "Materials or labor?"
                ),
                project_id=project_id,
                metadata={
                    "agent_message": True,
                    "pending_receipt_id": receipt_id,
                    "receipt_status": "check_review",
                    "check_flow_state": "check_detected",
                    "check_flow_active": True,
                }
            )

            return {
                "success": True,
                "status": "check_review",
                "message": "Check detected - awaiting user confirmation",
            }

        # ===== STEP 3: OCR Extraction (Shared Service) =====
        print(f"[Agent] Step 2: No duplicate found, proceeding to OCR")
        print(f"[Agent] Step 3: Starting OCR extraction (shared service)...")
        try:
            scan_result = _scan_receipt_core(file_content, file_type, model="heavy")
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except RuntimeError as e:
            raise Exception(f"OCR extraction failed: {str(e)}")

        line_items = scan_result.get("expenses", [])
        validation = scan_result.get("validation", {})

        # Derive summary fields from line items (backwards compat)
        first_item = line_items[0] if line_items else {}
        vendor_name = first_item.get("vendor") or "Unknown"
        amount = validation.get("invoice_total") or sum(item.get("amount", 0) for item in line_items)
        receipt_date = first_item.get("date")
        bill_id = first_item.get("bill_id")
        description = first_item.get("description") or "Material purchase"

        # Resolve vendor_id from vendor name
        vendors_resp = supabase.table("Vendors").select("id, vendor_name").execute()
        vendors_list = [
            {"id": v.get("id"), "name": v.get("vendor_name")}
            for v in (vendors_resp.data or []) if v.get("vendor_name")
        ]
        vendor_id = None
        if vendor_name and vendor_name != "Unknown":
            for v in vendors_list:
                if v["name"].lower() == vendor_name.lower():
                    vendor_id = v["id"]
                    break

        parsed_data = {
            "vendor_name": vendor_name,
            "vendor_id": vendor_id,
            "amount": amount,
            "receipt_date": receipt_date,
            "bill_id": bill_id,
            "description": description,
            "line_items": line_items,
            "validation": validation,
        }
        print(f"[Agent] Step 3: OCR complete | vendor={vendor_name} | amount={amount} | date={receipt_date} | {len(line_items)} line item(s)")

        # ===== STEP 4: Data duplicate check (after OCR) =====
        print(f"[Agent] Step 4: Checking data duplicates | vendor_matched={'yes' if vendor_id else 'no'}")
        warnings = []

        data_dup = _check_data_duplicate(
            project_id,
            parsed_data.get("vendor_name"),
            parsed_data.get("amount"),
            parsed_data.get("receipt_date")
        )
        if data_dup:
            warnings.append("Similar receipt already exists in this project")

        expense_dup = _check_expense_duplicate(
            project_id, vendor_id,
            parsed_data.get("amount"),
            parsed_data.get("receipt_date")
        )
        if expense_dup:
            warnings.append("Matching expense already registered for this project")

        if not vendor_id:
            warnings.append("Vendor not found in database")

        # ===== STEP 5: Auto-categorize (Shared Service) =====
        print(f"[Agent] Step 5: Starting auto-categorization (shared service)...")
        construction_stage = "General Construction"
        try:
            proj_resp = supabase.table("projects") \
                .select("project_name, project_stage") \
                .eq("project_id", project_id) \
                .single() \
                .execute()
            if proj_resp.data and proj_resp.data.get("project_stage"):
                construction_stage = proj_resp.data["project_stage"]
        except Exception:
            pass

        # Categorize each line item via shared service
        cat_expenses = [
            {"rowIndex": i, "description": item.get("description", "")}
            for i, item in enumerate(line_items)
        ]

        categorizations = []
        try:
            if cat_expenses:
                categorizations = _auto_categorize_core(stage=construction_stage, expenses=cat_expenses)
        except Exception as cat_err:
            print(f"[Agent] Auto-categorization failed: {cat_err}")

        # Attach categorization to each line item
        cat_map = {c["rowIndex"]: c for c in categorizations}
        for i, item in enumerate(line_items):
            cat = cat_map.get(i, {})
            item["account_id"] = cat.get("account_id")
            item["account_name"] = cat.get("account_name")
            item["confidence"] = cat.get("confidence", 0)
            item["reasoning"] = cat.get("reasoning")
            item["warning"] = cat.get("warning")

        # Primary categorization = highest confidence item
        primary_cat = max(categorizations, key=lambda c: c.get("confidence", 0)) if categorizations else {}
        categorize_data = {
            "account_id": primary_cat.get("account_id"),
            "account_name": primary_cat.get("account_name"),
            "confidence": primary_cat.get("confidence", 0),
            "reasoning": primary_cat.get("reasoning"),
            "warning": primary_cat.get("warning"),
        }

        final_account_id = categorize_data.get("account_id")
        final_category = categorize_data.get("account_name")
        final_confidence = categorize_data.get("confidence", 0)
        print(f"[Agent] Step 5: Categorization complete | category={final_category} | confidence={final_confidence}% | {len(categorizations)} item(s) categorized")

        # Add categorizer warnings
        if categorize_data.get("warning"):
            warnings.append(categorize_data["warning"])

        if final_confidence < 70:
            warnings.append("Low categorization confidence - manual review recommended")

        # ===== STEP 6: Update receipt with enriched data =====
        if warnings:
            print(f"[Agent] Step 5: Warnings: {warnings}")

        # Initialize receipt flow for project decision
        receipt_flow = {
            "state": "awaiting_item_selection",
            "started_at": datetime.utcnow().isoformat(),
            "split_items": [],
            "total_for_project": 0.0,
        }

        enriched_parsed = {
            **parsed_data,
            "categorization": {
                "account_id": final_account_id,
                "account_name": final_category,
                "confidence": final_confidence,
                "reasoning": categorize_data.get("reasoning"),
                "warning": categorize_data.get("warning"),
            },
            "agent_warnings": warnings,
            "data_duplicate": data_dup is not None,
            "expense_duplicate": expense_dup is not None,
            "receipt_flow": receipt_flow,
        }

        update_data = {
            "status": "ready",
            "parsed_data": enriched_parsed,
            "vendor_name": parsed_data.get("vendor_name"),
            "amount": parsed_data.get("amount"),
            "receipt_date": parsed_data.get("receipt_date"),
            "suggested_category": final_category,
            "suggested_account_id": final_account_id,
            "processed_at": datetime.utcnow().isoformat(),
            "updated_at": datetime.utcnow().isoformat()
        }

        supabase.table("pending_receipts") \
            .update(update_data) \
            .eq("id", receipt_id) \
            .execute()

        print(f"[Agent] Step 6: Receipt updated in DB | status=ready")

        # ===== STEP 7: Post Arturito summary + project question =====
        text_sample = f"{parsed_data.get('vendor_name', '')} {description}"
        lang = _detect_language(text_sample)

        msg_content = _build_agent_message(parsed_data, categorize_data, warnings, lang, expense_created=False)

        # Get project name for the question
        project_name = "this project"
        try:
            proj_name_resp = supabase.table("projects") \
                .select("project_name") \
                .eq("project_id", project_id) \
                .single() \
                .execute()
            if proj_name_resp.data and proj_name_resp.data.get("project_name"):
                project_name = proj_name_resp.data["project_name"]
        except Exception:
            pass

        if lang == "es":
            msg_content += f"\n\nEste bill es solo para **{project_name}**?"
        else:
            msg_content += f"\n\nIs this entire bill for **{project_name}**?"

        post_andrew_message(
            content=msg_content,
            project_id=project_id,
            metadata={
                "agent_message": True,
                "pending_receipt_id": receipt_id,
                "receipt_status": "ready",
                "has_warnings": len(warnings) > 0,
                "confidence": final_confidence,
                "receipt_flow_state": "awaiting_item_selection",
                "receipt_flow_active": True,
            }
        )

        print(f"[Agent] Step 7: Arturito message posted | lang={lang} | awaiting item selection")
        print(f"[Agent] === DONE OCR receipt {receipt_id} | {parsed_data.get('vendor_name')} ${parsed_data.get('amount')} -> {final_category} ({final_confidence}%) | awaiting user response ===")

        return {
            "success": True,
            "status": "ready",
            "message": "Receipt processed - awaiting project decision",
            "data": {
                "vendor_name": parsed_data.get("vendor_name"),
                "amount": parsed_data.get("amount"),
                "receipt_date": parsed_data.get("receipt_date"),
                "category": final_category,
                "confidence": final_confidence,
            },
            "warnings": warnings,
        }

    except HTTPException:
        raise
    except Exception as e:
        print(f"[Agent] === ERROR receipt {receipt_id} | {str(e)} ===")
        # Update status to error
        try:
            supabase.table("pending_receipts") \
                .update({
                    "status": "error",
                    "processing_error": str(e),
                    "updated_at": datetime.utcnow().isoformat()
                }) \
                .eq("id", receipt_id) \
                .execute()
        except Exception:
            pass

        # Post error message if we know the project
        if project_id:
            post_andrew_message(
                content=(
                    f"I ran into a problem processing this receipt: {str(e)}\n\n"
                    "You can process it manually from Expenses > From Pending."
                ),
                project_id=project_id,
                metadata={
                    "agent_message": True,
                    "pending_receipt_id": receipt_id,
                    "receipt_status": "error"
                }
            )

        raise HTTPException(status_code=500, detail=f"Agent processing error: {str(e)}")


# ====== CHECK FLOW HELPERS ======

def _parse_amount(text: str) -> Optional[float]:
    """Parse dollar amount from user text. Handles $1,250.00 or 1250 or 1250.50"""
    if not text:
        return None
    cleaned = re.sub(r'[$,\s]', '', text.strip())
    try:
        amount = float(cleaned)
        return amount if amount > 0 else None
    except (ValueError, TypeError):
        return None


def _get_labor_accounts() -> list:
    """Fetch accounts with 'Labor' in the name from the database."""
    try:
        accounts_resp = supabase.table("accounts").select("account_id, Name").execute()
        return [
            {"account_id": a["account_id"], "name": a["Name"]}
            for a in (accounts_resp.data or [])
            if a.get("Name") and "labor" in a["Name"].lower()
        ]
    except Exception as e:
        print(f"[CheckFlow] Error fetching labor accounts: {e}")
        return []


def _fuzzy_match_labor_account(description: str, accounts: list) -> Optional[dict]:
    """
    Word-overlap fuzzy matching for Labor accounts.
    Returns best match {account_id, name, score} or None if below threshold.
    """
    THRESHOLD = 0.4
    desc_words = set(description.lower().split())
    best_match = None
    best_score = 0

    for account in accounts:
        account_words = set(account["name"].lower().split())
        if not account_words:
            continue
        overlap = desc_words & account_words
        score = len(overlap) / len(account_words) if account_words else 0
        if score > best_score:
            best_score = score
            best_match = {
                "account_id": account["account_id"],
                "name": account["name"],
                "score": round(score * 100)
            }

    return best_match if best_match and best_score >= THRESHOLD else None


def _gpt_categorize_labor(description: str, labor_accounts: list,
                          construction_stage: str = "General") -> dict:
    """GPT fallback for matching a labor description to a Labor account."""
    openai_api_key = os.getenv("OPENAI_API_KEY")
    if not openai_api_key:
        return {"account_id": None, "account_name": "Uncategorized", "confidence": 0}

    try:
        ai_client = OpenAI(api_key=openai_api_key)

        prompt = f"""You are a construction accountant. Match this labor description to the best account.

CONSTRUCTION STAGE: {construction_stage}

AVAILABLE LABOR ACCOUNTS:
{json.dumps(labor_accounts, indent=2)}

DESCRIPTION: "{description}"

Return ONLY valid JSON:
{{
  "account_id": "exact-account-id-from-list",
  "account_name": "exact-account-name-from-list",
  "confidence": 85,
  "reasoning": "Brief explanation"
}}"""

        response = ai_client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": "Construction accounting expert. Return only valid JSON."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.3,
            max_tokens=300,
            response_format={"type": "json_object"}
        )
        return json.loads(response.choices[0].message.content)
    except Exception as e:
        print(f"[CheckFlow] GPT categorization error: {e}")
        return {"account_id": None, "account_name": "Uncategorized", "confidence": 0}


def _categorize_check_items(items: list, construction_stage: str = "General") -> list:
    """
    Categorize one or more labor descriptions.
    Each item: {"amount": float, "description": str, "project_name": str (optional)}
    Returns items with added "categorization" key.
    """
    labor_accounts = _get_labor_accounts()

    for item in items:
        desc = item.get("description", "")
        # Try fuzzy match first
        match = _fuzzy_match_labor_account(desc, labor_accounts)
        if match and match["score"] >= 60:
            item["categorization"] = {
                "account_id": match["account_id"],
                "account_name": match["name"],
                "confidence": match["score"],
                "method": "fuzzy_match"
            }
        else:
            # GPT fallback
            gpt_result = _gpt_categorize_labor(desc, labor_accounts, construction_stage)
            item["categorization"] = {
                "account_id": gpt_result.get("account_id"),
                "account_name": gpt_result.get("account_name", "Uncategorized"),
                "confidence": gpt_result.get("confidence", 0),
                "method": "gpt_fallback",
                "reasoning": gpt_result.get("reasoning")
            }

    return items


def _update_check_flow(receipt_id: str, check_flow: dict, parsed_data: dict):
    """Save updated check_flow state to the receipt's parsed_data."""
    parsed_data["check_flow"] = check_flow
    supabase.table("pending_receipts").update({
        "parsed_data": parsed_data,
        "updated_at": datetime.utcnow().isoformat()
    }).eq("id", receipt_id).execute()


def _create_check_expenses(receipt_id: str, receipt_data: dict, check_flow: dict) -> list:
    """Create expense entries from the completed check flow."""
    project_id = receipt_data["project_id"]
    created_expenses = []

    if check_flow.get("is_split") and check_flow.get("splits"):
        for split in check_flow["splits"]:
            cat = split.get("categorization", {})
            expense_data = {
                "project": split.get("project_id", project_id),
                "Amount": split["amount"],
                "TxnDate": datetime.utcnow().date().isoformat(),
                "LineDescription": f"Check: {split.get('description', '')}",
                "account_id": cat.get("account_id"),
                "payment_type": "Check",
                "created_by": receipt_data.get("uploaded_by"),
                "receipt_url": receipt_data.get("file_url"),
                "auth_status": False
            }
            expense_data = {k: v for k, v in expense_data.items() if v is not None}
            result = supabase.table("expenses_manual_COGS").insert(expense_data).execute()
            if result.data:
                created_expenses.append(result.data[0])
    else:
        cats = check_flow.get("categorizations", [{}])
        cat = cats[0] if cats else {}
        expense_data = {
            "project": project_id,
            "Amount": check_flow["amount"],
            "TxnDate": datetime.utcnow().date().isoformat(),
            "LineDescription": f"Check: {check_flow.get('description', '')}",
            "account_id": cat.get("account_id"),
            "payment_type": "Check",
            "created_by": receipt_data.get("uploaded_by"),
            "receipt_url": receipt_data.get("file_url"),
            "auth_status": False
        }
        expense_data = {k: v for k, v in expense_data.items() if v is not None}
        result = supabase.table("expenses_manual_COGS").insert(expense_data).execute()
        if result.data:
            created_expenses.append(result.data[0])

    # Link receipt
    if created_expenses:
        supabase.table("pending_receipts").update({
            "expense_id": created_expenses[0].get("expense_id"),
            "status": "linked",
            "linked_at": datetime.utcnow().isoformat(),
            "updated_at": datetime.utcnow().isoformat()
        }).eq("id", receipt_id).execute()

    return created_expenses


def _get_payroll_channel_id() -> Optional[str]:
    """Look up the Payroll group channel ID from the channels table."""
    try:
        result = supabase.table("channels") \
            .select("id").eq("type", "group").eq("name", "Payroll") \
            .limit(1).execute()
        if result.data and len(result.data) > 0:
            return str(result.data[0]["id"])
    except Exception as e:
        print(f"[CheckFlow] Error looking up Payroll channel: {e}")
    return None


def _check_flow_msg_kwargs(project_id: str, check_flow: dict) -> dict:
    """Return post_andrew_message routing kwargs based on check flow target channel."""
    channel_id = check_flow.get("channel_id")
    if channel_id:
        return {"channel_id": channel_id, "channel_type": "group"}
    return {"project_id": project_id}


# ====== CHECK FLOW STATE HANDLERS ======

def _handle_check_detected(receipt_id, project_id, check_flow, parsed_data, action):
    """Handle actions when state is check_detected (material / labor / not a check)."""

    if action == "confirm_material":
        # Material check: stay in receipts channel (same as old confirm_check)
        check_flow["state"] = "awaiting_amount"
        check_flow["check_type"] = "material"
        _update_check_flow(receipt_id, check_flow, parsed_data)

        post_andrew_message(
            content="Material check. What is the total amount?",
            project_id=project_id,
            metadata={
                "agent_message": True,
                "pending_receipt_id": receipt_id,
                "receipt_status": "check_review",
                "check_flow_state": "awaiting_amount",
                "check_flow_active": True,
                "awaiting_text_input": True,
            }
        )
        return {"success": True, "state": "awaiting_amount"}

    elif action == "confirm_labor":
        # Labor check: route to Payroll group channel
        payroll_channel_id = _get_payroll_channel_id()
        if not payroll_channel_id:
            post_andrew_message(
                content="Can't find the Payroll channel. Make sure it exists and try again.",
                project_id=project_id,
                metadata={
                    "agent_message": True,
                    "pending_receipt_id": receipt_id,
                    "receipt_status": "check_review",
                    "check_flow_state": "check_detected",
                    "check_flow_active": True,
                }
            )
            return {"success": False, "error": "payroll_channel_not_found"}

        check_flow["state"] = "awaiting_amount"
        check_flow["check_type"] = "labor"
        check_flow["channel_id"] = payroll_channel_id
        check_flow["origin_project_id"] = project_id
        _update_check_flow(receipt_id, check_flow, parsed_data)

        # Post redirect message in the receipts channel
        post_andrew_message(
            content=(
                "Got it, this is a labor check. I will continue in the **Payroll** channel.\n\n"
                "[Go to Payroll](/messages.html?channel=" + payroll_channel_id + "&type=group)"
            ),
            project_id=project_id,
            metadata={
                "agent_message": True,
                "pending_receipt_id": receipt_id,
                "receipt_status": "check_review",
                "check_flow_state": "redirected_to_payroll",
                "check_flow_active": False,
                "payroll_channel_id": payroll_channel_id,
            }
        )

        # Get receipt file info for context in Payroll
        receipt = supabase.table("pending_receipts") \
            .select("file_name, file_url") \
            .eq("id", receipt_id).single().execute()
        file_name = receipt.data.get("file_name", "check") if receipt.data else "check"
        file_url = receipt.data.get("file_url", "") if receipt.data else ""

        # Get project name
        proj_name = "Unknown"
        try:
            proj = supabase.table("projects") \
                .select("project_name") \
                .eq("project_id", project_id).single().execute()
            if proj.data:
                proj_name = proj.data.get("project_name", "Unknown")
        except Exception:
            pass

        file_link = f"[{file_name}]({file_url})" if file_url else file_name

        # Post check info + amount question in Payroll channel
        post_andrew_message(
            content=(
                f"Labor check received from project **{proj_name}**: {file_link}\n\n"
                "What is the total amount?"
            ),
            channel_id=payroll_channel_id,
            channel_type="group",
            metadata={
                "agent_message": True,
                "pending_receipt_id": receipt_id,
                "receipt_status": "check_review",
                "check_flow_state": "awaiting_amount",
                "check_flow_active": True,
                "awaiting_text_input": True,
                "origin_project_id": project_id,
            }
        )
        return {"success": True, "state": "awaiting_amount", "routed_to": "payroll"}

    elif action == "deny_check":
        check_flow["state"] = "cancelled"
        _update_check_flow(receipt_id, check_flow, parsed_data)

        # Reset status to pending so normal agent-process can run
        supabase.table("pending_receipts").update({
            "status": "pending",
            "updated_at": datetime.utcnow().isoformat()
        }).eq("id", receipt_id).execute()

        post_andrew_message(
            content="Not a check then. Processing it as a regular receipt.",
            project_id=project_id,
            metadata={
                "agent_message": True,
                "pending_receipt_id": receipt_id,
                "receipt_status": "pending",
                "check_flow_active": False,
            }
        )
        return {"success": True, "state": "cancelled", "reprocess": True}

    raise HTTPException(status_code=400, detail=f"Invalid action '{action}' for state 'check_detected'")


def _handle_awaiting_amount(receipt_id, project_id, check_flow, parsed_data, action, payload):
    """Handle amount submission."""
    if action != "submit_amount":
        raise HTTPException(status_code=400, detail=f"Invalid action '{action}' for state 'awaiting_amount'")

    text = (payload or {}).get("text", "")
    amount = _parse_amount(text)

    if amount is None:
        post_andrew_message(
            content=f"\"{text}\" doesn't look like a dollar amount. Try something like 1250 or $1,250.00.",
            **_check_flow_msg_kwargs(project_id, check_flow),
            metadata={
                "agent_message": True,
                "pending_receipt_id": receipt_id,
                "receipt_status": "check_review",
                "check_flow_state": "awaiting_amount",
                "check_flow_active": True,
                "awaiting_text_input": True,
            }
        )
        return {"success": True, "state": "awaiting_amount", "error": "invalid_amount"}

    check_flow["amount"] = amount
    check_flow["state"] = "awaiting_split_decision"
    _update_check_flow(receipt_id, check_flow, parsed_data)

    # Also update the receipt amount field
    supabase.table("pending_receipts").update({
        "amount": amount,
        "updated_at": datetime.utcnow().isoformat()
    }).eq("id", receipt_id).execute()

    post_andrew_message(
        content=f"**${amount:,.2f}**. Does this check need to be split across multiple projects?",
        **_check_flow_msg_kwargs(project_id, check_flow),
        metadata={
            "agent_message": True,
            "pending_receipt_id": receipt_id,
            "receipt_status": "check_review",
            "check_flow_state": "awaiting_split_decision",
            "check_flow_active": True,
        }
    )
    return {"success": True, "state": "awaiting_split_decision", "amount": amount}


def _handle_split_decision(receipt_id, project_id, check_flow, parsed_data, action):
    """Handle split yes/no decision."""
    if action == "split_no":
        check_flow["is_split"] = False
        check_flow["state"] = "awaiting_description"
        _update_check_flow(receipt_id, check_flow, parsed_data)

        post_andrew_message(
            content="Single project. What is the labor description? (e.g. \"Drywall labor\")",
            **_check_flow_msg_kwargs(project_id, check_flow),
            metadata={
                "agent_message": True,
                "pending_receipt_id": receipt_id,
                "receipt_status": "check_review",
                "check_flow_state": "awaiting_description",
                "check_flow_active": True,
                "awaiting_text_input": True,
            }
        )
        return {"success": True, "state": "awaiting_description"}

    elif action == "split_yes":
        check_flow["is_split"] = True
        check_flow["splits"] = []
        check_flow["state"] = "awaiting_split_details"
        _update_check_flow(receipt_id, check_flow, parsed_data)

        post_andrew_message(
            content=(
                "Alright, let us split it. Send each split as a message:\n"
                "**[amount] [description] for [project name]**\n\n"
                "Example: *500 drywall labor for Trasher Way*\n\n"
                "You can skip the project name if it is for this project.\n"
                "Type **done** when you are finished."
            ),
            **_check_flow_msg_kwargs(project_id, check_flow),
            metadata={
                "agent_message": True,
                "pending_receipt_id": receipt_id,
                "receipt_status": "check_review",
                "check_flow_state": "awaiting_split_details",
                "check_flow_active": True,
                "awaiting_text_input": True,
            }
        )
        return {"success": True, "state": "awaiting_split_details"}

    raise HTTPException(status_code=400, detail=f"Invalid action '{action}' for state 'awaiting_split_decision'")


def _handle_description(receipt_id, project_id, check_flow, parsed_data, action, payload):
    """Handle single labor description (no split)."""
    if action != "submit_description":
        raise HTTPException(status_code=400, detail=f"Invalid action '{action}' for state 'awaiting_description'")

    text = (payload or {}).get("text", "").strip()
    if not text:
        return {"success": True, "state": "awaiting_description", "error": "empty_description"}

    check_flow["description"] = text

    # Categorize
    items = [{"amount": check_flow["amount"], "description": text}]

    # Get construction stage (use origin project for labor checks routed to Payroll)
    stage_project_id = check_flow.get("origin_project_id", project_id)
    construction_stage = "General Construction"
    try:
        proj = supabase.table("projects").select("project_stage").eq("project_id", stage_project_id).single().execute()
        if proj.data and proj.data.get("project_stage"):
            construction_stage = proj.data["project_stage"]
    except Exception:
        pass

    items = _categorize_check_items(items, construction_stage)
    check_flow["categorizations"] = [items[0].get("categorization", {})]
    check_flow["state"] = "awaiting_category_confirm"
    _update_check_flow(receipt_id, check_flow, parsed_data)

    cat = items[0].get("categorization", {})
    cat_name = cat.get("account_name", "Uncategorized")
    cat_conf = cat.get("confidence", 0)
    method = cat.get("method", "")
    method_label = "(fuzzy match)" if method == "fuzzy_match" else "(AI suggestion)"

    post_andrew_message(
        content=(
            f"I categorized this as **{cat_name}** ({cat_conf}% confidence) {method_label}.\n\n"
            f"${check_flow['amount']:,.2f} -- {text}\n\n"
            "Does that look right?"
        ),
        **_check_flow_msg_kwargs(project_id, check_flow),
        metadata={
            "agent_message": True,
            "pending_receipt_id": receipt_id,
            "receipt_status": "check_review",
            "check_flow_state": "awaiting_category_confirm",
            "check_flow_active": True,
        }
    )
    return {"success": True, "state": "awaiting_category_confirm"}


def _handle_split_details(receipt_id, project_id, check_flow, parsed_data, action, payload):
    """Handle split line entries or done signal."""
    if action == "split_done":
        splits = check_flow.get("splits", [])
        if not splits:
            post_andrew_message(
                content="Nothing to split yet. Add at least one item, or just type a description.",
                **_check_flow_msg_kwargs(project_id, check_flow),
                metadata={
                    "agent_message": True,
                    "pending_receipt_id": receipt_id,
                    "receipt_status": "check_review",
                    "check_flow_state": "awaiting_split_details",
                    "check_flow_active": True,
                    "awaiting_text_input": True,
                }
            )
            return {"success": True, "state": "awaiting_split_details", "error": "no_splits"}

        # Categorize all splits (use origin project for labor checks routed to Payroll)
        stage_project_id = check_flow.get("origin_project_id", project_id)
        construction_stage = "General Construction"
        try:
            proj = supabase.table("projects").select("project_stage").eq("project_id", stage_project_id).single().execute()
            if proj.data and proj.data.get("project_stage"):
                construction_stage = proj.data["project_stage"]
        except Exception:
            pass

        splits = _categorize_check_items(splits, construction_stage)
        check_flow["splits"] = splits
        check_flow["state"] = "awaiting_category_confirm"
        _update_check_flow(receipt_id, check_flow, parsed_data)

        # Build summary
        total_split = sum(s.get("amount", 0) for s in splits)
        check_amount = check_flow.get("amount", 0)
        lines = []
        for i, s in enumerate(splits, 1):
            cat = s.get("categorization", {})
            cat_name = cat.get("account_name", "Uncategorized")
            cat_conf = cat.get("confidence", 0)
            proj_name = s.get("project_name", "This project")
            lines.append(f"{i}. ${s['amount']:,.2f} {s.get('description', '')} - **{cat_name}** ({cat_conf}%) - {proj_name}")

        summary = "\n".join(lines)
        diff_note = ""
        if abs(total_split - check_amount) > 0.01:
            diff_note = f"\n\nNote: Split total (${total_split:,.2f}) differs from check amount (${check_amount:,.2f}) by ${abs(total_split - check_amount):,.2f}"

        post_andrew_message(
            content=(
                f"Here is how I categorized the splits:\n\n{summary}{diff_note}\n\n"
                "Does everything look right?"
            ),
            **_check_flow_msg_kwargs(project_id, check_flow),
            metadata={
                "agent_message": True,
                "pending_receipt_id": receipt_id,
                "receipt_status": "check_review",
                "check_flow_state": "awaiting_category_confirm",
                "check_flow_active": True,
            }
        )
        return {"success": True, "state": "awaiting_category_confirm"}

    elif action == "submit_split_line":
        text = (payload or {}).get("text", "").strip()
        if not text:
            return {"success": True, "state": "awaiting_split_details", "error": "empty_line"}

        # Parse: [amount] [description] for [project name]
        # or: [amount] [description] (no project = current project)
        match = re.match(r'^([\d,.]+)\s+(.+?)(?:\s+for\s+(.+))?$', text, re.IGNORECASE)

        if match:
            amount = _parse_amount(match.group(1))
            description = match.group(2).strip()
            project_name = match.group(3).strip() if match.group(3) else None
        else:
            # Fallback: try to extract amount from start
            parts = text.split(None, 1)
            amount = _parse_amount(parts[0]) if parts else None
            description = parts[1] if len(parts) > 1 else text
            project_name = None

        if amount is None:
            post_andrew_message(
                content=f"Can't read an amount from \"{text}\". Try: **[amount] [description]** (e.g. \"500 drywall labor\")",
                **_check_flow_msg_kwargs(project_id, check_flow),
                metadata={
                    "agent_message": True,
                    "pending_receipt_id": receipt_id,
                    "receipt_status": "check_review",
                    "check_flow_state": "awaiting_split_details",
                    "check_flow_active": True,
                    "awaiting_text_input": True,
                }
            )
            return {"success": True, "state": "awaiting_split_details", "error": "parse_error"}

        # Resolve project if specified
        split_project_id = project_id
        if project_name:
            try:
                projects = supabase.table("projects").select("project_id, project_name").execute()
                for p in (projects.data or []):
                    if project_name.lower() in p["project_name"].lower():
                        split_project_id = p["project_id"]
                        project_name = p["project_name"]
                        break
            except Exception:
                pass

        split_entry = {
            "amount": amount,
            "description": description,
            "project_id": split_project_id,
            "project_name": project_name or "This project",
        }
        check_flow["splits"].append(split_entry)
        _update_check_flow(receipt_id, check_flow, parsed_data)

        total_so_far = sum(s.get("amount", 0) for s in check_flow["splits"])
        check_amount = check_flow.get("amount", 0)
        remaining = check_amount - total_so_far

        remaining_note = ""
        if remaining > 0.01:
            remaining_note = f" (${remaining:,.2f} remaining)"
        elif remaining < -0.01:
            remaining_note = f" (${abs(remaining):,.2f} over check amount)"

        post_andrew_message(
            content=(
                f"Added ${amount:,.2f} for {description} ({project_name or 'this project'}).\n"
                f"Running total: ${total_so_far:,.2f} of ${check_amount:,.2f}{remaining_note}\n\n"
                "Send another split or type **done** to wrap up."
            ),
            **_check_flow_msg_kwargs(project_id, check_flow),
            metadata={
                "agent_message": True,
                "pending_receipt_id": receipt_id,
                "receipt_status": "check_review",
                "check_flow_state": "awaiting_split_details",
                "check_flow_active": True,
                "awaiting_text_input": True,
            }
        )
        return {"success": True, "state": "awaiting_split_details", "split_count": len(check_flow["splits"])}

    raise HTTPException(status_code=400, detail=f"Invalid action '{action}' for state 'awaiting_split_details'")


def _handle_category_confirm(receipt_id, project_id, check_flow, parsed_data, action, receipt_data):
    """Handle category confirmation or cancellation."""
    if action == "confirm_categories":
        expenses = _create_check_expenses(receipt_id, receipt_data, check_flow)
        check_flow["state"] = "completed"
        _update_check_flow(receipt_id, check_flow, parsed_data)

        count = len(expenses)
        post_andrew_message(
            content=(
                f"{count} expense{'s' if count != 1 else ''} created from this check. "
                "Review them in Expenses > From Pending."
            ),
            **_check_flow_msg_kwargs(project_id, check_flow),
            metadata={
                "agent_message": True,
                "pending_receipt_id": receipt_id,
                "receipt_status": "linked",
                "check_flow_state": "completed",
                "check_flow_active": False,
            }
        )
        return {"success": True, "state": "completed", "expenses_created": count}

    elif action == "cancel":
        check_flow["state"] = "cancelled"
        _update_check_flow(receipt_id, check_flow, parsed_data)

        supabase.table("pending_receipts").update({
            "status": "pending",
            "updated_at": datetime.utcnow().isoformat()
        }).eq("id", receipt_id).execute()

        post_andrew_message(
            content="Check processing cancelled. Receipt is back to pending.",
            **_check_flow_msg_kwargs(project_id, check_flow),
            metadata={
                "agent_message": True,
                "pending_receipt_id": receipt_id,
                "receipt_status": "pending",
                "check_flow_state": "cancelled",
                "check_flow_active": False,
            }
        )
        return {"success": True, "state": "cancelled"}

    raise HTTPException(status_code=400, detail=f"Invalid action '{action}' for state 'awaiting_category_confirm'")


# ====== CHECK-ACTION ENDPOINT ======

@router.post("/{receipt_id}/check-action")
async def check_action(receipt_id: str, payload: CheckActionRequest):
    """
    Handle a user action in the check processing conversation.
    Routes to the appropriate handler based on current state + action.
    """
    try:
        receipt = supabase.table("pending_receipts") \
            .select("*") \
            .eq("id", receipt_id) \
            .single() \
            .execute()

        if not receipt.data:
            raise HTTPException(status_code=404, detail="Receipt not found")

        receipt_data = receipt.data
        project_id = receipt_data["project_id"]
        parsed_data = receipt_data.get("parsed_data") or {}
        check_flow = parsed_data.get("check_flow")

        if not check_flow:
            raise HTTPException(status_code=400, detail="No active check flow for this receipt")

        current_state = check_flow.get("state")
        action = payload.action

        print(f"[CheckFlow] receipt={receipt_id} | state={current_state} | action={action}")

        if current_state == "check_detected":
            result = _handle_check_detected(receipt_id, project_id, check_flow, parsed_data, action)
            # If user denied and wants reprocess, trigger normal agent-process
            if result.get("reprocess"):
                try:
                    await agent_process_receipt(receipt_id, force=False)
                except Exception as e:
                    print(f"[CheckFlow] Reprocess after deny failed: {e}")
            return result
        elif current_state == "awaiting_amount":
            return _handle_awaiting_amount(receipt_id, project_id, check_flow, parsed_data, action, payload.payload)
        elif current_state == "awaiting_split_decision":
            return _handle_split_decision(receipt_id, project_id, check_flow, parsed_data, action)
        elif current_state == "awaiting_description":
            return _handle_description(receipt_id, project_id, check_flow, parsed_data, action, payload.payload)
        elif current_state == "awaiting_split_details":
            return _handle_split_details(receipt_id, project_id, check_flow, parsed_data, action, payload.payload)
        elif current_state == "awaiting_category_confirm":
            return _handle_category_confirm(receipt_id, project_id, check_flow, parsed_data, action, receipt_data)
        elif current_state in ("completed", "cancelled"):
            raise HTTPException(status_code=400, detail=f"Check flow already {current_state}")
        else:
            raise HTTPException(status_code=400, detail=f"Invalid check flow state: {current_state}")

    except HTTPException:
        raise
    except Exception as e:
        print(f"[CheckFlow] Error: {e}")
        raise HTTPException(status_code=500, detail=f"Check action error: {str(e)}")


# ====== DUPLICATE-ACTION ENDPOINT ======

@router.post("/{receipt_id}/duplicate-action")
async def duplicate_action(receipt_id: str, payload: DuplicateActionRequest):
    """
    Handle a user response in the duplicate confirmation conversation.
    Actions: confirm_process (yes), skip (no).
    """
    try:
        receipt = supabase.table("pending_receipts") \
            .select("*") \
            .eq("id", receipt_id) \
            .single() \
            .execute()

        if not receipt.data:
            raise HTTPException(status_code=404, detail="Receipt not found")

        receipt_data = receipt.data
        project_id = receipt_data["project_id"]
        parsed_data = receipt_data.get("parsed_data") or {}
        duplicate_flow = parsed_data.get("duplicate_flow")

        if not duplicate_flow:
            raise HTTPException(status_code=400, detail="No active duplicate flow for this receipt")

        current_state = duplicate_flow.get("state")
        action = payload.action

        print(f"[DuplicateFlow] receipt={receipt_id} | state={current_state} | action={action}")

        if current_state != "awaiting_confirmation":
            raise HTTPException(status_code=400, detail=f"Duplicate flow already resolved (state: {current_state})")

        if action == "confirm_process":
            # User confirmed: process anyway
            duplicate_flow["state"] = "confirmed"
            parsed_data["duplicate_flow"] = duplicate_flow
            supabase.table("pending_receipts").update({
                "status": "pending",
                "parsed_data": parsed_data,
                "processing_error": None,
                "updated_at": datetime.utcnow().isoformat()
            }).eq("id", receipt_id).execute()

            post_andrew_message(
                content="Processing it anyway.",
                project_id=project_id,
                metadata={
                    "agent_message": True,
                    "pending_receipt_id": receipt_id,
                    "receipt_status": "processing",
                    "duplicate_flow_active": False,
                    "duplicate_flow_state": "confirmed",
                }
            )

            # Re-trigger processing with force=True
            await agent_process_receipt(receipt_id, force=True)
            return {"success": True, "state": "confirmed"}

        elif action == "skip":
            # User declined: keep as duplicate
            duplicate_flow["state"] = "skipped"
            parsed_data["duplicate_flow"] = duplicate_flow
            supabase.table("pending_receipts").update({
                "parsed_data": parsed_data,
                "updated_at": datetime.utcnow().isoformat()
            }).eq("id", receipt_id).execute()

            post_andrew_message(
                content="Skipping this one.",
                project_id=project_id,
                metadata={
                    "agent_message": True,
                    "pending_receipt_id": receipt_id,
                    "receipt_status": "duplicate",
                    "duplicate_flow_active": False,
                    "duplicate_flow_state": "skipped",
                }
            )

            return {"success": True, "state": "skipped"}

        else:
            raise HTTPException(status_code=400, detail=f"Invalid action '{action}' for duplicate flow")

    except HTTPException:
        raise
    except Exception as e:
        print(f"[DuplicateFlow] Error: {e}")
        raise HTTPException(status_code=500, detail=f"Duplicate action error: {str(e)}")


@router.post("/{receipt_id}/receipt-action")
async def receipt_action(receipt_id: str, payload: ReceiptActionRequest):
    """
    Handle user action in the receipt split conversation.
    Routes to handler based on receipt_flow.state + action.
    """
    try:
        receipt = supabase.table("pending_receipts") \
            .select("*") \
            .eq("id", receipt_id) \
            .single() \
            .execute()

        if not receipt.data:
            raise HTTPException(status_code=404, detail="Receipt not found")

        receipt_data = receipt.data
        project_id = receipt_data["project_id"]
        parsed_data = receipt_data.get("parsed_data") or {}
        receipt_flow = parsed_data.get("receipt_flow")

        if not receipt_flow:
            raise HTTPException(status_code=400, detail="No active receipt flow for this receipt")

        current_state = receipt_flow.get("state")
        action = payload.action

        print(f"[ReceiptFlow] receipt={receipt_id} | state={current_state} | action={action}")

        if action == "cancel":
            receipt_flow["state"] = "cancelled"
            parsed_data["receipt_flow"] = receipt_flow
            supabase.table("pending_receipts").update({
                "parsed_data": parsed_data,
                "updated_at": datetime.utcnow().isoformat()
            }).eq("id", receipt_id).execute()

            post_andrew_message(
                content="Split cancelled. Receipt is ready for manual processing.",
                project_id=project_id,
                metadata={
                    "agent_message": True,
                    "pending_receipt_id": receipt_id,
                    "receipt_status": "ready",
                    "receipt_flow_state": "cancelled",
                    "receipt_flow_active": False,
                }
            )
            return {"success": True, "state": "cancelled"}

        if current_state == "awaiting_item_selection":
            # Get project name
            project_name = "this project"
            try:
                proj_resp = supabase.table("projects").select("project_name") \
                    .eq("project_id", project_id).single().execute()
                if proj_resp.data and proj_resp.data.get("project_name"):
                    project_name = proj_resp.data["project_name"]
            except Exception:
                pass

            if action == "all_this_project":
                # Create expenses for ALL line items in this project
                agent_cfg = {}
                try:
                    cfg_result = supabase.table("agent_config").select("key, value").execute()
                    for row in (cfg_result.data or []):
                        agent_cfg[row["key"]] = row["value"]
                except Exception:
                    pass

                auto_create = agent_cfg.get("auto_create_expense", True)
                min_confidence = int(agent_cfg.get("min_confidence", 70))
                cat = parsed_data.get("categorization", {})
                vendor_id = parsed_data.get("vendor_id")

                created_expenses = []
                line_items = parsed_data.get("line_items", [])

                if auto_create and line_items:
                    for item in line_items:
                        item_account_id = item.get("account_id") or cat.get("account_id")
                        item_confidence = item.get("confidence", 0)
                        if not item_account_id or item_confidence < min_confidence:
                            continue
                        expense = _create_receipt_expense(
                            project_id, parsed_data, receipt_data,
                            vendor_id, item_account_id,
                            amount=item.get("amount"),
                            description=item.get("description"),
                            bill_id=item.get("bill_id"),
                            txn_date=item.get("date"),
                        )
                        if expense:
                            created_expenses.append(expense)
                elif auto_create and cat.get("account_id"):
                    expense = _create_receipt_expense(
                        project_id, parsed_data, receipt_data,
                        vendor_id, cat["account_id"]
                    )
                    if expense:
                        created_expenses.append(expense)

                expense_id = created_expenses[0].get("expense_id") if created_expenses else None
                receipt_flow["state"] = "completed"
                parsed_data["receipt_flow"] = receipt_flow

                if created_expenses:
                    supabase.table("pending_receipts").update({
                        "status": "linked",
                        "expense_id": expense_id,
                        "parsed_data": parsed_data,
                        "linked_at": datetime.utcnow().isoformat(),
                        "updated_at": datetime.utcnow().isoformat()
                    }).eq("id", receipt_id).execute()

                    count = len(created_expenses)
                    msg = f"{count} expense(s) saved -- ready for authorization." if count > 1 else "Expense saved -- ready for authorization."
                    post_andrew_message(
                        content=msg,
                        project_id=project_id,
                        metadata={
                            "agent_message": True,
                            "pending_receipt_id": receipt_id,
                            "receipt_status": "linked",
                            "receipt_flow_state": "completed",
                            "receipt_flow_active": False,
                        }
                    )
                    print(f"[ReceiptFlow] All items -> {count} expense(s) created | first_id={expense_id}")
                else:
                    supabase.table("pending_receipts").update({
                        "parsed_data": parsed_data,
                        "updated_at": datetime.utcnow().isoformat()
                    }).eq("id", receipt_id).execute()

                    post_andrew_message(
                        content="Processed. Ready for review.",
                        project_id=project_id,
                        metadata={
                            "agent_message": True,
                            "pending_receipt_id": receipt_id,
                            "receipt_status": "ready",
                            "receipt_flow_state": "completed",
                            "receipt_flow_active": False,
                        }
                    )
                    print(f"[ReceiptFlow] All items -> skipped expense creation (low confidence)")

                return {"success": True, "state": "completed", "expense_id": expense_id}

            elif action == "start_assign":
                # User wants to assign items to other projects -- show prompt
                line_items = parsed_data.get("line_items", [])
                text_sample = f"{parsed_data.get('vendor_name', '')} {parsed_data.get('description', '')}"
                lang = _detect_language(text_sample)
                numbered = _build_numbered_list(line_items, lang)

                if lang == "es":
                    prompt_msg = (
                        f"Cuales items van a otros proyectos? Los no mencionados se quedan en **{project_name}**.\n\n"
                        f"{numbered}\n\n"
                        "Escribe tus asignaciones, ej: **3, 4 a Sunset Heights, 7 a Oak Park**"
                    )
                else:
                    prompt_msg = (
                        f"Which items go to other projects? Unmentioned items stay with **{project_name}**.\n\n"
                        f"{numbered}\n\n"
                        "Type your assignments, e.g.: **3, 4 to Sunset Heights, 7 to Oak Park**"
                    )

                post_andrew_message(
                    content=prompt_msg,
                    project_id=project_id,
                    metadata={
                        "agent_message": True,
                        "pending_receipt_id": receipt_id,
                        "receipt_status": "ready",
                        "receipt_flow_state": "awaiting_item_selection",
                        "receipt_flow_active": True,
                        "awaiting_text_input": True,
                    }
                )
                return {"success": True, "state": "awaiting_item_selection"}

            elif action == "assign_items":
                text = (payload.payload or {}).get("text", "")
                line_items = parsed_data.get("line_items", [])
                num_items = len(line_items)

                if not line_items:
                    raise HTTPException(status_code=400, detail="No line items available for assignment")

                # Parse item-to-project assignments
                assignments = _parse_item_assignments(text, num_items)
                if not assignments:
                    post_andrew_message(
                        content="Didn't catch that. Use this format:\n**3, 4 to Sunset Heights, 7 to Oak Park**",
                        project_id=project_id,
                        metadata={
                            "agent_message": True,
                            "pending_receipt_id": receipt_id,
                            "receipt_status": "ready",
                            "receipt_flow_state": "awaiting_item_selection",
                            "receipt_flow_active": True,
                            "awaiting_text_input": True,
                        }
                    )
                    return {"success": True, "state": "awaiting_item_selection", "error": "parse_error"}

                # Resolve project names
                assigned_indices = set()
                project_assignments = []

                for assignment in assignments:
                    project = _resolve_project_by_name(assignment["project_query"])
                    if not project:
                        post_andrew_message(
                            content=f'No project matching "{assignment["project_query"]}". Try again.',
                            project_id=project_id,
                            metadata={
                                "agent_message": True,
                                "pending_receipt_id": receipt_id,
                                "receipt_status": "ready",
                                "receipt_flow_state": "awaiting_item_selection",
                                "receipt_flow_active": True,
                                "awaiting_text_input": True,
                            }
                        )
                        return {"success": True, "state": "awaiting_item_selection", "error": "project_not_found"}

                    project_assignments.append({
                        "project_id": project["project_id"],
                        "project_name": project["project_name"],
                        "item_indices": assignment["item_indices"],
                    })
                    assigned_indices.update(assignment["item_indices"])

                # Remaining items (not assigned) stay with this project
                this_project_indices = [i for i in range(1, num_items + 1) if i not in assigned_indices]

                # Read agent config for expense creation
                agent_cfg = {}
                try:
                    cfg_result = supabase.table("agent_config").select("key, value").execute()
                    for row in (cfg_result.data or []):
                        agent_cfg[row["key"]] = row["value"]
                except Exception:
                    pass

                auto_create = agent_cfg.get("auto_create_expense", True)
                min_confidence = int(agent_cfg.get("min_confidence", 70))
                cat = parsed_data.get("categorization", {})
                vendor_id = parsed_data.get("vendor_id")

                all_created = []
                summary_parts = []

                # Create expenses for THIS project (unassigned items)
                if this_project_indices:
                    created_here = []
                    for idx in this_project_indices:
                        item = line_items[idx - 1]
                        item_account = item.get("account_id") or cat.get("account_id")
                        if auto_create and item_account and item.get("confidence", 0) >= min_confidence:
                            expense = _create_receipt_expense(
                                project_id, parsed_data, receipt_data,
                                vendor_id, item_account,
                                amount=item.get("amount"),
                                description=item.get("description"),
                                bill_id=item.get("bill_id"),
                                txn_date=item.get("date"),
                            )
                            if expense:
                                created_here.append(expense)
                                all_created.append(expense)
                    this_total = sum(line_items[i - 1].get("amount", 0) for i in this_project_indices)
                    summary_parts.append(f"**{project_name}**: {len(created_here)} item(s), ${this_total:,.2f}")

                # Create expenses for OTHER projects
                for pa in project_assignments:
                    created_other = []
                    for idx in pa["item_indices"]:
                        item = line_items[idx - 1]
                        item_account = item.get("account_id") or cat.get("account_id")
                        if auto_create and item_account and item.get("confidence", 0) >= min_confidence:
                            expense = _create_receipt_expense(
                                pa["project_id"], parsed_data, receipt_data,
                                vendor_id, item_account,
                                amount=item.get("amount"),
                                description=item.get("description"),
                                bill_id=item.get("bill_id"),
                                txn_date=item.get("date"),
                            )
                            if expense:
                                created_other.append(expense)
                                all_created.append(expense)

                    other_total = sum(line_items[i - 1].get("amount", 0) for i in pa["item_indices"])
                    summary_parts.append(f"**{pa['project_name']}**: {len(created_other)} item(s), ${other_total:,.2f}")

                    # Post split notification to other project's receipt channel
                    item_descriptions = [line_items[i - 1].get("description", "Item") for i in pa["item_indices"]]
                    split_msg = (
                        f"Split from **{project_name}** receipt ({parsed_data.get('vendor_name', 'Unknown')}):\n"
                        + "\n".join(f"- {desc}" for desc in item_descriptions)
                        + f"\nTotal: ${other_total:,.2f}"
                    )
                    if created_other:
                        split_msg += f"\n\n{len(created_other)} expense(s) auto-created."

                    post_andrew_message(
                        content=split_msg,
                        project_id=pa["project_id"],
                        metadata={
                            "agent_message": True,
                            "pending_receipt_id": receipt_id,
                            "receipt_status": "split",
                            "split_repost": True,
                            "source_project": project_name,
                        }
                    )

                # Update receipt flow
                receipt_flow["state"] = "completed"
                receipt_flow["assignments"] = [
                    {"project_id": pa["project_id"], "project_name": pa["project_name"], "items": pa["item_indices"]}
                    for pa in project_assignments
                ]
                receipt_flow["this_project_items"] = this_project_indices
                parsed_data["receipt_flow"] = receipt_flow

                expense_id = all_created[0].get("expense_id") if all_created else None
                supabase.table("pending_receipts").update({
                    "status": "split",
                    "expense_id": expense_id,
                    "parsed_data": parsed_data,
                    "linked_at": datetime.utcnow().isoformat(),
                    "updated_at": datetime.utcnow().isoformat()
                }).eq("id", receipt_id).execute()

                # Post summary to this project
                num_projects = len(project_assignments) + (1 if this_project_indices else 0)
                post_andrew_message(
                    content=(
                        f"Done! Items split across {num_projects} project(s):\n"
                        + "\n".join(f"- {s}" for s in summary_parts)
                    ),
                    project_id=project_id,
                    metadata={
                        "agent_message": True,
                        "pending_receipt_id": receipt_id,
                        "receipt_status": "split",
                        "receipt_flow_state": "completed",
                        "receipt_flow_active": False,
                    }
                )

                print(f"[ReceiptFlow] Split -> {len(all_created)} expenses across {num_projects} projects")
                return {"success": True, "state": "completed", "expense_ids": [e.get("expense_id") for e in all_created]}

            else:
                raise HTTPException(status_code=400, detail=f"Invalid action '{action}' for state '{current_state}'")

        # ── Backward compat: old states ──
        if current_state == "awaiting_project_decision":
            # Get project name
            project_name = "this project"
            try:
                proj_resp = supabase.table("projects").select("project_name") \
                    .eq("project_id", project_id).single().execute()
                if proj_resp.data and proj_resp.data.get("project_name"):
                    project_name = proj_resp.data["project_name"]
            except Exception:
                pass

            if action == "single_project":
                # Read agent config for confidence check
                agent_cfg = {}
                try:
                    cfg_result = supabase.table("agent_config").select("key, value").execute()
                    for row in (cfg_result.data or []):
                        agent_cfg[row["key"]] = row["value"]
                except Exception:
                    pass

                auto_create = agent_cfg.get("auto_create_expense", True)
                min_confidence = int(agent_cfg.get("min_confidence", 70))
                cat = parsed_data.get("categorization", {})
                final_confidence = cat.get("confidence", 0)
                vendor_id = parsed_data.get("vendor_id")

                should_create = auto_create and final_confidence >= min_confidence

                created_expenses = []
                line_items = parsed_data.get("line_items", [])

                if should_create and line_items:
                    # Create one expense per line item
                    for item in line_items:
                        item_account_id = item.get("account_id") or cat.get("account_id")
                        item_confidence = item.get("confidence", 0)
                        if not item_account_id or item_confidence < min_confidence:
                            continue
                        expense = _create_receipt_expense(
                            project_id, parsed_data, receipt_data,
                            vendor_id, item_account_id,
                            amount=item.get("amount"),
                            description=item.get("description"),
                            bill_id=item.get("bill_id"),
                            txn_date=item.get("date"),
                        )
                        if expense:
                            created_expenses.append(expense)

                elif should_create and cat.get("account_id"):
                    # Fallback: no line items (old data), single expense from summary
                    expense = _create_receipt_expense(
                        project_id, parsed_data, receipt_data,
                        vendor_id, cat["account_id"]
                    )
                    if expense:
                        created_expenses.append(expense)

                expense_id = created_expenses[0].get("expense_id") if created_expenses else None
                receipt_flow["state"] = "completed"
                parsed_data["receipt_flow"] = receipt_flow

                if created_expenses:
                    supabase.table("pending_receipts").update({
                        "status": "linked",
                        "expense_id": expense_id,
                        "parsed_data": parsed_data,
                        "linked_at": datetime.utcnow().isoformat(),
                        "updated_at": datetime.utcnow().isoformat()
                    }).eq("id", receipt_id).execute()

                    count = len(created_expenses)
                    msg = f"{count} expense(s) saved -- ready for authorization." if count > 1 else "Expense saved -- ready for authorization."
                    post_andrew_message(
                        content=msg,
                        project_id=project_id,
                        metadata={
                            "agent_message": True,
                            "pending_receipt_id": receipt_id,
                            "receipt_status": "linked",
                            "receipt_flow_state": "completed",
                            "receipt_flow_active": False,
                        }
                    )
                    print(f"[ReceiptFlow] Single project -> {count} expense(s) created | first_id={expense_id}")
                else:
                    supabase.table("pending_receipts").update({
                        "parsed_data": parsed_data,
                        "updated_at": datetime.utcnow().isoformat()
                    }).eq("id", receipt_id).execute()

                    post_andrew_message(
                        content="Processed. Ready for review.",
                        project_id=project_id,
                        metadata={
                            "agent_message": True,
                            "pending_receipt_id": receipt_id,
                            "receipt_status": "ready",
                            "receipt_flow_state": "completed",
                            "receipt_flow_active": False,
                        }
                    )
                    print(f"[ReceiptFlow] Single project -> skipped expense (confidence={final_confidence})")

                return {"success": True, "state": "completed", "expense_id": expense_id}

            elif action == "split_projects":
                receipt_flow["state"] = "awaiting_split_details"
                parsed_data["receipt_flow"] = receipt_flow
                supabase.table("pending_receipts").update({
                    "parsed_data": parsed_data,
                    "updated_at": datetime.utcnow().isoformat()
                }).eq("id", receipt_id).execute()

                post_andrew_message(
                    content=(
                        f"Got it, this bill is split. Tell me which items belong to **{project_name}**.\n\n"
                        "Send each item as: **[amount] [description]**\n"
                        "Example: *500 plumbing materials*\n\n"
                        "Type **done** when finished."
                    ),
                    project_id=project_id,
                    metadata={
                        "agent_message": True,
                        "pending_receipt_id": receipt_id,
                        "receipt_status": "ready",
                        "receipt_flow_state": "awaiting_split_details",
                        "receipt_flow_active": True,
                        "awaiting_text_input": True,
                    }
                )
                print(f"[ReceiptFlow] Split selected -> awaiting_split_details")
                return {"success": True, "state": "awaiting_split_details"}

            else:
                raise HTTPException(status_code=400, detail=f"Invalid action '{action}' for state '{current_state}'")

        elif current_state == "awaiting_split_details":
            receipt_amount = parsed_data.get("amount", 0)

            if action == "submit_split_line":
                text = (payload.payload or {}).get("text", "")
                parsed_line = _parse_receipt_split_line(text)

                if not parsed_line:
                    post_andrew_message(
                        content="Didn't catch that. Send each item as: **[amount] [description]**\nExample: *500 plumbing materials*",
                        project_id=project_id,
                        metadata={
                            "agent_message": True,
                            "pending_receipt_id": receipt_id,
                            "receipt_status": "ready",
                            "receipt_flow_state": "awaiting_split_details",
                            "receipt_flow_active": True,
                            "awaiting_text_input": True,
                        }
                    )
                    return {"success": True, "state": "awaiting_split_details", "error": "invalid_format"}

                receipt_flow["split_items"].append(parsed_line)
                receipt_flow["total_for_project"] = round(
                    sum(item["amount"] for item in receipt_flow["split_items"]), 2
                )
                parsed_data["receipt_flow"] = receipt_flow
                supabase.table("pending_receipts").update({
                    "parsed_data": parsed_data,
                    "updated_at": datetime.utcnow().isoformat()
                }).eq("id", receipt_id).execute()

                total = receipt_flow["total_for_project"]
                remaining = round(receipt_amount - total, 2)
                remaining_note = f" (${remaining:,.2f} remaining)" if remaining > 0 else ""

                post_andrew_message(
                    content=(
                        f"Added: ${parsed_line['amount']:,.2f} -- {parsed_line['description']}\n"
                        f"Running total: ${total:,.2f} of ${receipt_amount:,.2f}{remaining_note}\n\n"
                        "Send another item or type **done**."
                    ),
                    project_id=project_id,
                    metadata={
                        "agent_message": True,
                        "pending_receipt_id": receipt_id,
                        "receipt_status": "ready",
                        "receipt_flow_state": "awaiting_split_details",
                        "receipt_flow_active": True,
                        "awaiting_text_input": True,
                    }
                )
                print(f"[ReceiptFlow] Split item added | ${parsed_line['amount']:.2f} {parsed_line['description']} | total=${total:.2f}")
                return {"success": True, "state": "awaiting_split_details", "items": receipt_flow["split_items"]}

            elif action == "split_done":
                items = receipt_flow.get("split_items", [])
                if not items:
                    post_andrew_message(
                        content="Add at least one item before finishing.",
                        project_id=project_id,
                        metadata={
                            "agent_message": True,
                            "pending_receipt_id": receipt_id,
                            "receipt_status": "ready",
                            "receipt_flow_state": "awaiting_split_details",
                            "receipt_flow_active": True,
                            "awaiting_text_input": True,
                        }
                    )
                    return {"success": True, "state": "awaiting_split_details", "error": "no_items"}

                # Create expenses for each split item
                cat = parsed_data.get("categorization", {})
                account_id = cat.get("account_id")
                vendor_id = parsed_data.get("vendor_id")
                created_expenses = []

                for item in items:
                    expense = _create_receipt_expense(
                        project_id, parsed_data, receipt_data,
                        vendor_id, account_id,
                        amount=item["amount"],
                        description=item["description"]
                    )
                    if expense:
                        created_expenses.append(expense)

                receipt_flow["state"] = "completed"
                parsed_data["receipt_flow"] = receipt_flow
                expense_id = created_expenses[0].get("expense_id") if created_expenses else None

                supabase.table("pending_receipts").update({
                    "status": "split",
                    "expense_id": expense_id,
                    "parsed_data": parsed_data,
                    "linked_at": datetime.utcnow().isoformat(),
                    "updated_at": datetime.utcnow().isoformat()
                }).eq("id", receipt_id).execute()

                # Get project name for message
                project_name = "this project"
                try:
                    proj_resp = supabase.table("projects").select("project_name") \
                        .eq("project_id", project_id).single().execute()
                    if proj_resp.data and proj_resp.data.get("project_name"):
                        project_name = proj_resp.data["project_name"]
                except Exception:
                    pass

                total = receipt_flow["total_for_project"]
                count = len(created_expenses)
                post_andrew_message(
                    content=(
                        f"Done! {count} expense(s) created for **{project_name}** totaling ${total:,.2f}.\n\n"
                        "This receipt is marked as split -- upload it to other projects to register their portions."
                    ),
                    project_id=project_id,
                    metadata={
                        "agent_message": True,
                        "pending_receipt_id": receipt_id,
                        "receipt_status": "split",
                        "receipt_flow_state": "completed",
                        "receipt_flow_active": False,
                    }
                )
                print(f"[ReceiptFlow] Split done | {count} expenses created | total=${total:.2f}")
                return {"success": True, "state": "completed", "expense_ids": [e.get("expense_id") for e in created_expenses]}

            else:
                raise HTTPException(status_code=400, detail=f"Invalid action '{action}' for state '{current_state}'")

        else:
            raise HTTPException(status_code=400, detail=f"Receipt flow in terminal state '{current_state}'")

    except HTTPException:
        raise
    except Exception as e:
        print(f"[ReceiptFlow] Error: {e}")
        raise HTTPException(status_code=500, detail=f"Receipt action error: {str(e)}")


@router.post("/{receipt_id}/create-expense")
def create_expense_from_receipt(receipt_id: str, payload: CreateExpenseFromReceiptRequest):
    """
    Create an expense from a pending receipt and link them.

    Uses the parsed data from the receipt to pre-fill the expense.
    """
    try:
        # Get receipt
        receipt = supabase.table("pending_receipts") \
            .select("*") \
            .eq("id", receipt_id) \
            .single() \
            .execute()

        if not receipt.data:
            raise HTTPException(status_code=404, detail="Receipt not found")

        receipt_data = receipt.data

        if receipt_data.get("expense_id"):
            raise HTTPException(status_code=400, detail="Receipt already linked to an expense")

        # Create expense
        expense_data = {
            "project": payload.project_id,
            "vendor_id": payload.vendor_id,
            "Amount": payload.amount,
            "TxnDate": payload.txn_date,
            "LineDescription": payload.description or f"Receipt: {receipt_data.get('file_name')}",
            "account_id": payload.account_id,
            "payment_type": payload.payment_type,
            "created_by": payload.created_by,
            "receipt_url": receipt_data.get("file_url"),  # Link receipt file
            "auth_status": False  # Needs authorization
        }

        # Remove None values
        expense_data = {k: v for k, v in expense_data.items() if v is not None}

        expense_result = supabase.table("expenses_manual_COGS").insert(expense_data).execute()

        if not expense_result.data:
            raise HTTPException(status_code=500, detail="Failed to create expense")

        expense_id = expense_result.data[0].get("expense_id")

        # Link receipt to expense
        supabase.table("pending_receipts") \
            .update({
                "expense_id": expense_id,
                "status": "linked",
                "linked_at": datetime.utcnow().isoformat(),
                "updated_at": datetime.utcnow().isoformat()
            }) \
            .eq("id", receipt_id) \
            .execute()

        # Post confirmation to receipts channel
        vendor_name = receipt_data.get("vendor_name") or "Unknown vendor"
        post_andrew_message(
            content=f"Expense created -- ${payload.amount:,.2f} from {vendor_name}. Ready for authorization.",
            project_id=receipt_data["project_id"],
            metadata={
                "agent_message": True,
                "pending_receipt_id": receipt_id,
                "receipt_status": "linked",
            }
        )

        return {
            "success": True,
            "expense": expense_result.data[0],
            "receipt_id": receipt_id,
            "message": "Expense created and linked to receipt"
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error creating expense: {str(e)}")


@router.post("/{receipt_id}/link")
def link_receipt_to_expense(receipt_id: str, payload: LinkToExpenseRequest):
    """
    Link an existing pending receipt to an existing expense.
    """
    try:
        # Verify receipt exists
        receipt = supabase.table("pending_receipts") \
            .select("id, expense_id, project_id, vendor_name, amount") \
            .eq("id", receipt_id) \
            .single() \
            .execute()

        if not receipt.data:
            raise HTTPException(status_code=404, detail="Receipt not found")

        if receipt.data.get("expense_id"):
            raise HTTPException(status_code=400, detail="Receipt already linked to an expense")

        # Verify expense exists
        expense = supabase.table("expenses_manual_COGS") \
            .select("expense_id") \
            .eq("expense_id", payload.expense_id) \
            .single() \
            .execute()

        if not expense.data:
            raise HTTPException(status_code=404, detail="Expense not found")

        # Link them
        result = supabase.table("pending_receipts") \
            .update({
                "expense_id": payload.expense_id,
                "status": "linked",
                "linked_at": datetime.utcnow().isoformat(),
                "updated_at": datetime.utcnow().isoformat()
            }) \
            .eq("id", receipt_id) \
            .execute()

        # Also update the expense with the receipt URL
        receipt_full = supabase.table("pending_receipts") \
            .select("file_url") \
            .eq("id", receipt_id) \
            .single() \
            .execute()

        if receipt_full.data:
            supabase.table("expenses_manual_COGS") \
                .update({"receipt_url": receipt_full.data.get("file_url")}) \
                .eq("expense_id", payload.expense_id) \
                .execute()

        # Post linking confirmation to receipts channel
        link_project_id = receipt.data.get("project_id")
        if link_project_id:
            link_vendor = receipt.data.get("vendor_name") or "receipt"
            link_amount = receipt.data.get("amount")
            amount_text = f" (${link_amount:,.2f})" if link_amount else ""
            post_andrew_message(
                content=f"Receipt from {link_vendor}{amount_text} linked to an existing expense.",
                project_id=link_project_id,
                metadata={
                    "agent_message": True,
                    "pending_receipt_id": receipt_id,
                    "receipt_status": "linked",
                }
            )

        return {
            "success": True,
            "data": result.data[0] if result.data else None,
            "message": "Receipt linked to expense"
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error linking receipt: {str(e)}")


@router.patch("/{receipt_id}")
def update_receipt(receipt_id: str, payload: PendingReceiptUpdate):
    """Update a pending receipt's status or parsed data"""
    try:
        update_data = payload.model_dump(exclude_none=True)
        update_data["updated_at"] = datetime.utcnow().isoformat()

        result = supabase.table("pending_receipts") \
            .update(update_data) \
            .eq("id", receipt_id) \
            .execute()

        if not result.data:
            raise HTTPException(status_code=404, detail="Receipt not found")

        return {"success": True, "data": result.data[0]}

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error updating receipt: {str(e)}")


@router.delete("/{receipt_id}")
def delete_receipt(receipt_id: str):
    """
    Delete a pending receipt.

    Also removes the file from storage.
    """
    try:
        # Get receipt to find storage path
        receipt = supabase.table("pending_receipts") \
            .select("file_url, project_id, expense_id") \
            .eq("id", receipt_id) \
            .single() \
            .execute()

        if not receipt.data:
            raise HTTPException(status_code=404, detail="Receipt not found")

        if receipt.data.get("expense_id"):
            raise HTTPException(
                status_code=400,
                detail="Cannot delete receipt linked to an expense. Unlink first."
            )

        # Extract storage path from URL
        file_url = receipt.data.get("file_url", "")
        project_id = receipt.data.get("project_id")

        # Try to delete from storage
        try:
            # Parse path from URL
            if RECEIPTS_BUCKET in file_url:
                path_start = file_url.find(RECEIPTS_BUCKET) + len(RECEIPTS_BUCKET) + 1
                storage_path = file_url[path_start:].split("?")[0]
                supabase.storage.from_(RECEIPTS_BUCKET).remove([storage_path])
        except Exception as storage_error:
            print(f"[PendingReceipts] Warning: Could not delete file from storage: {storage_error}")

        # Delete record
        supabase.table("pending_receipts").delete().eq("id", receipt_id).execute()

        return {"success": True, "message": "Receipt deleted"}

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error deleting receipt: {str(e)}")


@router.get("/unprocessed/count")
def get_unprocessed_counts():
    """
    Get counts of unprocessed receipts across all projects.

    Useful for dashboard widgets showing pending work.
    """
    try:
        result = supabase.table("pending_receipts") \
            .select("project_id, status", count="exact") \
            .in_("status", ["pending", "ready"]) \
            .is_("expense_id", "null") \
            .execute()

        # Group by project
        by_project = {}
        for row in (result.data or []):
            pid = row.get("project_id")
            if pid not in by_project:
                by_project[pid] = {"pending": 0, "ready": 0}
            by_project[pid][row.get("status", "pending")] += 1

        return {
            "success": True,
            "total_count": result.count or 0,
            "by_project": by_project
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error getting counts: {str(e)}")


# ====== AGENT MANAGER ENDPOINTS ======

@router.get("/agent-stats")
def get_agent_stats():
    """Return aggregate stats for the receipt agent."""
    try:
        all_receipts = supabase.table("pending_receipts") \
            .select("id, status, parsed_data") \
            .in_("status", ["ready", "linked", "error", "duplicate", "check_review"]) \
            .execute()

        data = all_receipts.data or []
        total = len(data)
        linked = sum(1 for r in data if r["status"] == "linked")
        errors = sum(1 for r in data if r["status"] == "error")
        duplicates = sum(1 for r in data if r["status"] == "duplicate")
        ready = sum(1 for r in data if r["status"] == "ready")

        confidences = []
        for r in data:
            cat = (r.get("parsed_data") or {}).get("categorization") or {}
            if cat.get("confidence"):
                confidences.append(cat["confidence"])
        avg_confidence = round(sum(confidences) / len(confidences)) if confidences else 0
        success_rate = round((linked / total) * 100) if total > 0 else 0

        return {
            "total_processed": total,
            "expenses_created": linked,
            "pending_review": ready,
            "errors": errors,
            "duplicates_caught": duplicates,
            "success_rate": success_rate,
            "avg_confidence": avg_confidence,
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error getting agent stats: {str(e)}")


@router.get("/agent-config")
def get_agent_config():
    """Return current agent configuration."""
    try:
        result = supabase.table("agent_config").select("*").execute()
        config = {}
        for row in (result.data or []):
            config[row["key"]] = row["value"]
        return config
    except Exception as e:
        # Table might not exist yet -- return defaults
        return {
            "auto_create_expense": True,
            "min_confidence": 70,
            "auto_skip_duplicates": False,
        }


@router.patch("/agent-config")
def update_agent_config(payload: dict):
    """Update agent configuration (key-value pairs)."""
    try:
        for key, value in payload.items():
            supabase.table("agent_config") \
                .upsert({
                    "key": key,
                    "value": json.dumps(value) if not isinstance(value, str) else value,
                    "updated_at": datetime.utcnow().isoformat()
                }) \
                .execute()
        return {"ok": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error updating agent config: {str(e)}")
