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
from typing import Optional, List
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
from api.helpers.bot_messenger import post_bot_message, ARTURITO_BOT_USER_ID

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
            .select("*, users!uploaded_by(user_name, avatar_url)") \
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
            .select("*, users!uploaded_by(user_name, avatar_url), projects(project_name)") \
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
            vendors_resp = supabase.table("Vendors").select("vendor_id, vendor_name").execute()
            vendors_list = [
                {"id": v.get("vendor_id"), "name": v.get("vendor_name")}
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


def _build_agent_message(parsed_data, categorize_data, warnings, lang="en"):
    """Build Arturito's summary message for the receipts channel."""
    vendor = parsed_data.get("vendor_name") or "Unknown"
    amount = parsed_data.get("amount") or 0
    date = parsed_data.get("receipt_date") or "Unknown"
    category = categorize_data.get("account_name") or parsed_data.get("suggested_category") or "Uncategorized"
    confidence = int(categorize_data.get("confidence", 0))

    if lang == "es":
        header = "**Gasto de material procesado**"
        if warnings:
            header += " - Revision recomendada"
        body = (
            f"Proveedor: {vendor}\n"
            f"Monto: ${amount:,.2f}\n"
            f"Fecha: {date}\n"
            f"Categoria: {category} ({confidence}% confianza)"
        )
        if warnings:
            body += "\n\nAdvertencias:\n" + "\n".join(f"- {w}" for w in warnings)
        body += "\n\nListo para revision en Expenses > From Pending"
    else:
        header = "**Material expense processed**"
        if warnings:
            header += " - Review recommended"
        body = (
            f"Vendor: {vendor}\n"
            f"Amount: ${amount:,.2f}\n"
            f"Date: {date}\n"
            f"Category: {category} ({confidence}% confidence)"
        )
        if warnings:
            body += "\n\nWarnings:\n" + "\n".join(f"- {w}" for w in warnings)
        body += "\n\nReady for review in Expenses > From Pending"

    return f"{header}\n{body}"


# ====== AGENT ENDPOINT ======

@router.post("/{receipt_id}/agent-process")
async def agent_process_receipt(receipt_id: str):
    """
    Full automated processing pipeline for material receipts.

    Pipeline:
    1. Fetch receipt, download file
    2. Duplicate check (file hash)
    3. OCR extraction (OpenAI Vision)
    4. Data duplicate check (vendor+amount+date)
    5. Auto-categorize (GPT with construction stage context)
    6. Update receipt with enriched data
    7. Post Arturito summary message to Receipts channel
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

        # Check exact file duplicate
        print(f"[Agent] Step 2: Checking file hash duplicate | hash={file_hash[:16]}...")
        hash_dup = _check_file_hash_duplicate(file_hash, project_id, receipt_id)
        if hash_dup:
            print(f"[Agent] Step 2: DUPLICATE FOUND | matches receipt {hash_dup.get('id')}")
            supabase.table("pending_receipts") \
                .update({
                    "status": "duplicate",
                    "processing_error": f"Duplicate of receipt {hash_dup.get('id')}",
                    "updated_at": datetime.utcnow().isoformat()
                }) \
                .eq("id", receipt_id) \
                .execute()

            dup_vendor = hash_dup.get("vendor_name") or "Unknown"
            dup_amount = hash_dup.get("amount") or 0
            dup_date = hash_dup.get("receipt_date") or "Unknown"

            post_bot_message(
                content=(
                    "**Duplicate receipt detected**\n"
                    f"This receipt matches an existing entry:\n"
                    f"- Receipt from {dup_vendor}, ${dup_amount:,.2f} ({dup_date})\n"
                    f"- Status: {hash_dup.get('status')}\n\n"
                    "Flagged for review."
                ),
                project_id=project_id,
                metadata={
                    "agent_message": True,
                    "pending_receipt_id": receipt_id,
                    "receipt_status": "duplicate",
                    "duplicate_of": hash_dup.get("id")
                }
            )

            return {
                "success": False,
                "status": "duplicate",
                "message": "Duplicate receipt detected",
                "duplicate_of": hash_dup
            }

        # ===== STEP 3: OCR Extraction =====
        print(f"[Agent] Step 2: No duplicate found, proceeding to OCR")
        print(f"[Agent] Step 3: Starting OCR extraction (GPT-4o Vision)...")
        openai_api_key = os.getenv("OPENAI_API_KEY")
        if not openai_api_key:
            raise Exception("OpenAI API key not configured")

        ai_client = OpenAI(api_key=openai_api_key)

        # Get vendor and account lists for matching
        vendors_resp = supabase.table("Vendors").select("vendor_id, vendor_name").execute()
        vendors_list = [
            {"id": v.get("vendor_id"), "name": v.get("vendor_name")}
            for v in (vendors_resp.data or []) if v.get("vendor_name")
        ]

        accounts_resp = supabase.table("accounts").select("account_id, Name").execute()
        accounts_list = [
            {"id": a.get("account_id"), "name": a.get("Name")}
            for a in (accounts_resp.data or []) if a.get("Name")
        ]

        # Convert file to base64 images
        base64_images = []
        media_type = file_type

        if file_type == "application/pdf":
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

        ocr_prompt = f"""Analyze this receipt/invoice and extract the key information.

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

        content = [{"type": "text", "text": ocr_prompt}]
        for img_b64 in base64_images[:3]:
            content.append({
                "type": "image_url",
                "image_url": {
                    "url": f"data:{media_type};base64,{img_b64}",
                    "detail": "high"
                }
            })

        ocr_response = ai_client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": content}],
            max_tokens=1000,
            response_format={"type": "json_object"}
        )

        parsed_data = json.loads(ocr_response.choices[0].message.content)
        print(f"[Agent] Step 3: OCR complete | vendor={parsed_data.get('vendor_name')} | amount={parsed_data.get('amount')} | date={parsed_data.get('receipt_date')} | confidence={parsed_data.get('confidence')}")

        # Resolve vendor_id by name if not matched
        vendor_id = parsed_data.get("vendor_id")
        if not vendor_id and parsed_data.get("vendor_name"):
            for v in vendors_list:
                if v["name"].lower() == parsed_data["vendor_name"].lower():
                    vendor_id = v["id"]
                    break

        # Resolve account_id by name if not matched
        ocr_account_id = parsed_data.get("suggested_account_id")
        if not ocr_account_id and parsed_data.get("suggested_category"):
            for a in accounts_list:
                if a["name"].lower() == parsed_data["suggested_category"].lower():
                    ocr_account_id = a["id"]
                    break

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

        # ===== STEP 5: Auto-categorize =====
        print(f"[Agent] Step 5: Starting auto-categorization...")
        # Get project stage if available
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

        description = parsed_data.get("description", "Material purchase")

        # Filter out Labor accounts for material categorization
        material_accounts = [
            {"account_id": a["id"], "name": a["name"]}
            for a in accounts_list if "labor" not in a["name"].lower()
        ]

        categorize_prompt = f"""You are an expert construction accountant specializing in categorizing expenses.

CONSTRUCTION STAGE: {construction_stage}

AVAILABLE ACCOUNTS:
{json.dumps(material_accounts, indent=2)}

EXPENSE TO CATEGORIZE:
Description: "{description}"
Vendor: "{parsed_data.get('vendor_name', 'Unknown')}"

INSTRUCTIONS:
1. Determine the MOST APPROPRIATE account from the available accounts list.
2. Consider the construction stage when categorizing.
3. Calculate confidence (0-100) based on description clarity and stage match.
4. ONLY use account_id values from the provided accounts list.

SPECIAL RULES:
- POWER TOOLS (drills, saws, grinders, nail guns, etc.) are CAPITAL ASSETS, set confidence to 0 with warning.
- Consumables FOR power tools (drill bits, saw blades, nails) ARE valid COGS.
- BEVERAGES (water, energy drinks, coffee) go under "Base Materials".

Return ONLY valid JSON:
{{
  "account_id": "exact-account-id-from-list",
  "account_name": "exact-account-name-from-list",
  "confidence": 85,
  "reasoning": "Brief explanation",
  "warning": null
}}"""

        categorize_data = {}
        try:
            cat_response = ai_client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {"role": "system", "content": "You are a construction accounting expert. Return only valid JSON."},
                    {"role": "user", "content": categorize_prompt}
                ],
                temperature=0.3,
                max_tokens=500,
                response_format={"type": "json_object"}
            )
            categorize_data = json.loads(cat_response.choices[0].message.content)
        except Exception as cat_err:
            print(f"[Agent] Categorization failed, using OCR suggestion: {cat_err}")
            categorize_data = {
                "account_id": ocr_account_id,
                "account_name": parsed_data.get("suggested_category"),
                "confidence": int((parsed_data.get("confidence", 0.5)) * 100),
                "reasoning": "OCR suggestion (auto-categorize unavailable)",
                "warning": None
            }

        # Pick the best account_id
        final_account_id = categorize_data.get("account_id") or ocr_account_id
        final_category = categorize_data.get("account_name") or parsed_data.get("suggested_category")
        final_confidence = categorize_data.get("confidence", 0)
        print(f"[Agent] Step 5: Categorization complete | category={final_category} | confidence={final_confidence}% | reasoning={categorize_data.get('reasoning', 'N/A')}")

        # Add categorizer warnings
        if categorize_data.get("warning"):
            warnings.append(categorize_data["warning"])

        if final_confidence < 70:
            warnings.append("Low categorization confidence - manual review recommended")

        # ===== STEP 6: Update receipt with enriched data =====
        if warnings:
            print(f"[Agent] Step 5: Warnings: {warnings}")
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

        # ===== STEP 7: Post Arturito summary message =====
        text_sample = f"{parsed_data.get('vendor_name', '')} {description}"
        lang = _detect_language(text_sample)

        msg_content = _build_agent_message(parsed_data, categorize_data, warnings, lang)

        post_bot_message(
            content=msg_content,
            project_id=project_id,
            metadata={
                "agent_message": True,
                "pending_receipt_id": receipt_id,
                "receipt_status": "ready",
                "has_warnings": len(warnings) > 0,
                "confidence": final_confidence
            }
        )

        print(f"[Agent] Step 7: Arturito message posted | lang={lang}")
        print(f"[Agent] === DONE receipt {receipt_id} | {parsed_data.get('vendor_name')} ${parsed_data.get('amount')} -> {final_category} ({final_confidence}%) ===")

        return {
            "success": True,
            "status": "ready",
            "message": "Receipt processed successfully",
            "data": {
                "vendor_name": parsed_data.get("vendor_name"),
                "amount": parsed_data.get("amount"),
                "receipt_date": parsed_data.get("receipt_date"),
                "category": final_category,
                "confidence": final_confidence,
            },
            "warnings": warnings
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
            post_bot_message(
                content=(
                    "**Receipt processing failed**\n\n"
                    f"Error: {str(e)}\n\n"
                    "Please process this receipt manually via Expenses > From Pending."
                ),
                project_id=project_id,
                metadata={
                    "agent_message": True,
                    "pending_receipt_id": receipt_id,
                    "receipt_status": "error"
                }
            )

        raise HTTPException(status_code=500, detail=f"Agent processing error: {str(e)}")


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
            .select("id, expense_id") \
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
