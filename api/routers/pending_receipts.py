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
import logging
import os
import re
import uuid
from datetime import datetime, timedelta
from openai import OpenAI
import json
import io

logger = logging.getLogger(__name__)
from pdf2image import convert_from_bytes
from api.helpers.andrew_messenger import post_andrew_message, ANDREW_BOT_USER_ID
from services.receipt_scanner import (
    scan_receipt as _scan_receipt_core,
    auto_categorize as _auto_categorize_core,
)
from api.services.vault_service import save_to_project_folder

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

        # Upload to Vault (primary storage)
        vault_result = save_to_project_folder(project_id, "Receipts", file_content, file.filename, file.content_type)
        if not vault_result or not vault_result.get("public_url"):
            raise HTTPException(status_code=500, detail="Failed to upload file to vault")

        file_url = vault_result["public_url"]

        # Generate thumbnail URL for images (Supabase can transform images)
        thumbnail_url = None
        if file.content_type.startswith("image/"):
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
    Process a pending receipt using the shared OCR service (scan_receipt).
    Used by the human bookkeeper from the expenses pending-receipts panel.

    Pipeline: fetch -> download -> OCR (scan_receipt) -> vendor match ->
    auto-categorize -> update receipt to ready.
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

        if receipt_data.get("status") == "linked":
            raise HTTPException(status_code=400, detail="Receipt already linked to an expense")

        # Update status to processing
        supabase.table("pending_receipts") \
            .update({"status": "processing", "updated_at": datetime.utcnow().isoformat()}) \
            .eq("id", receipt_id) \
            .execute()

        try:
            # Download file
            file_url = receipt_data.get("file_url")
            file_type = receipt_data.get("file_type")

            import httpx
            async with httpx.AsyncClient() as client:
                response = await client.get(file_url)
                if response.status_code != 200:
                    raise Exception("Failed to download receipt file")
                file_content = response.content

            # ===== OCR via shared service (pdfplumber-first, 300 DPI, tax-aware) =====
            scan_result = _scan_receipt_core(file_content, file_type, model="heavy")

            line_items = scan_result.get("expenses", [])
            validation = scan_result.get("validation", {})

            # Correction pass if validation failed
            if not validation.get("validation_passed", True) and line_items:
                try:
                    corrected = _scan_receipt_core(
                        file_content, file_type, model="heavy",
                        correction_context={
                            "invoice_total": validation.get("invoice_total", 0),
                            "calculated_sum": validation.get("calculated_sum", 0),
                            "items": line_items,
                        }
                    )
                    if corrected.get("validation", {}).get("validation_passed", False):
                        scan_result = corrected
                        line_items = corrected.get("expenses", [])
                        validation = corrected.get("validation", {})
                except Exception:
                    pass

            # Derive summary from line items
            first_item = line_items[0] if line_items else {}
            vendor_name = first_item.get("vendor") or "Unknown"
            amount = validation.get("invoice_total") or sum(
                item.get("amount", 0) for item in line_items
            )
            receipt_date = first_item.get("date")
            description = first_item.get("description") or "Material purchase"

            # ===== Vendor matching =====
            vendors_resp = supabase.table("Vendors").select("id, vendor_name").execute()
            vendor_id = None
            if vendor_name and vendor_name != "Unknown":
                for v in (vendors_resp.data or []):
                    if (v.get("vendor_name") or "").lower() == vendor_name.lower():
                        vendor_id = v["id"]
                        break

            # ===== Auto-categorize via shared service =====
            project_id = receipt_data.get("project_id")
            construction_stage = "General Construction"
            if project_id:
                try:
                    proj = supabase.table("projects") \
                        .select("project_stage") \
                        .eq("project_id", project_id) \
                        .single().execute()
                    if proj.data and proj.data.get("project_stage"):
                        construction_stage = proj.data["project_stage"]
                except Exception:
                    pass

            categorizations = []
            cat_expenses = [
                {"rowIndex": i, "description": item.get("description", "")}
                for i, item in enumerate(line_items)
            ]
            try:
                if cat_expenses:
                    categorizations = _auto_categorize_core(
                        stage=construction_stage, expenses=cat_expenses
                    )
            except Exception:
                pass

            # Attach categorization to line items
            cat_map = {c["rowIndex"]: c for c in categorizations}
            for i, item in enumerate(line_items):
                cat = cat_map.get(i, {})
                item["account_id"] = cat.get("account_id")
                item["account_name"] = cat.get("account_name")
                item["confidence"] = cat.get("confidence", 0)

            primary_cat = max(categorizations, key=lambda c: c.get("confidence", 0)) if categorizations else {}
            final_account_id = primary_cat.get("account_id")
            final_category = primary_cat.get("account_name")
            final_confidence = primary_cat.get("confidence", 0)

            # ===== Build parsed_data and update DB =====
            parsed_data = {
                "vendor_name": vendor_name,
                "vendor_id": vendor_id,
                "amount": amount,
                "receipt_date": receipt_date,
                "description": description,
                "line_items": line_items,
                "validation": validation,
                "categorization": {
                    "account_id": final_account_id,
                    "account_name": final_category,
                    "confidence": final_confidence,
                },
            }

            update_data = {
                "status": "ready",
                "parsed_data": parsed_data,
                "vendor_name": vendor_name,
                "amount": amount,
                "receipt_date": receipt_date,
                "suggested_category": final_category,
                "suggested_account_id": final_account_id,
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


def _get_bookkeeping_mentions():
    """Get @mention strings for all Bookkeeper/Accounting Manager users."""
    try:
        result = supabase.table("users") \
            .select("user_name, rols!users_user_rol_fkey(rol_name)") \
            .execute()
        bookkeeping_roles = {"Bookkeeper", "Accounting Manager"}
        mentions = []
        for u in (result.data or []):
            role = u.get("rols") or {}
            if role.get("rol_name") in bookkeeping_roles:
                name = (u.get("user_name") or "").replace(" ", "")
                if name:
                    mentions.append(f"@{name}")
        return " ".join(mentions) if mentions else ""
    except Exception as e:
        print(f"[Agent] Error fetching bookkeeping mentions: {e}")
        return ""


def _get_auth_notify_mentions():
    """Get @mention strings for users configured in andrew_auth_notify_users."""
    try:
        cfg = supabase.table("agent_config") \
            .select("value") \
            .eq("key", "andrew_auth_notify_users") \
            .execute()
        if not cfg.data:
            return ""
        user_ids = cfg.data[0]["value"]
        if isinstance(user_ids, str):
            user_ids = json.loads(user_ids)
        if not user_ids:
            return ""
        result = supabase.table("users") \
            .select("user_id, user_name") \
            .in_("user_id", user_ids) \
            .execute()
        mentions = []
        for u in (result.data or []):
            name = (u.get("user_name") or "").replace(" ", "")
            if name:
                mentions.append(f"@{name}")
        return " ".join(mentions) if mentions else ""
    except Exception as e:
        print(f"[Agent] Error fetching auth notify mentions: {e}")
        return ""


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
        # Auto-pull from parsed_data when not explicitly passed
        final_txn_type = txn_type_id or parsed_data.get("txn_type_id")
        final_payment = payment_method_id or parsed_data.get("payment_method_id")
        if final_txn_type:
            expense_data["txn_type_id"] = final_txn_type
        if final_payment:
            expense_data["payment_type"] = final_payment
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


# ====== USER CONTEXT APPLICATION ======

def _apply_user_context(
    user_context: dict,
    line_items: list,
    low_confidence_items: list,
    project_id: str,
    project_name: str,
    parsed_data: dict,
) -> dict:
    """
    Apply user-provided context hints to auto-resolve receipt flow steps.

    Returns dict with resolution results:
    - skip_categories: bool - all low-confidence items resolved by hints
    - skip_project_question: bool - user already specified project decision
    - pre_resolved: dict - pre-computed decisions for awaiting_user_confirm state
    - start_state: str - the receipt_flow state to start at
    """
    result = {
        "skip_categories": False,
        "skip_project_question": False,
        "pre_resolved": {},
        "start_state": None,
        "resolved_items": [],
    }

    if not user_context:
        return result

    project_decision = user_context.get("project_decision")
    split_projects = user_context.get("split_projects") or []
    category_hints = user_context.get("category_hints") or []

    # --- 1. Category resolution via hints ---
    if category_hints and low_confidence_items:
        # Fetch accounts for fuzzy matching
        try:
            accounts_resp = supabase.table("accounts") \
                .select("account_id, Name") \
                .execute()
            accounts_list = [
                {"id": a["account_id"], "name": a["Name"]}
                for a in (accounts_resp.data or []) if a.get("Name")
            ]
        except Exception:
            accounts_list = []

        if accounts_list:
            # Build lookup: lowercase account name -> account obj
            accounts_lower = {a["name"].lower(): a for a in accounts_list}

            for lci in low_confidence_items:
                idx = lci["index"]
                if idx >= len(line_items):
                    continue

                # Check if any category hint matches an account
                matched_acct = None
                for hint in category_hints:
                    hint_lower = hint.lower()
                    # Exact match
                    if hint_lower in accounts_lower:
                        matched_acct = accounts_lower[hint_lower]
                        break
                    # Partial match (hint substring of account name or vice versa)
                    for aname, aobj in accounts_lower.items():
                        if hint_lower in aname or aname in hint_lower:
                            matched_acct = aobj
                            break
                    if matched_acct:
                        break

                if matched_acct:
                    line_items[idx]["account_id"] = matched_acct["id"]
                    line_items[idx]["account_name"] = matched_acct["name"]
                    line_items[idx]["confidence"] = 95
                    line_items[idx]["user_confirmed"] = True
                    result["resolved_items"].append(idx)
                    print(f"[UserContext] Category hint resolved item {idx} -> {matched_acct['name']}")

            # Check if ALL low-confidence items were resolved
            unresolved = [
                lci for lci in low_confidence_items
                if lci["index"] not in result["resolved_items"]
            ]
            if not unresolved:
                result["skip_categories"] = True
                print(f"[UserContext] All {len(low_confidence_items)} low-confidence items resolved by hints")

    # --- 2. Project decision ---
    if project_decision == "all_this_project":
        result["skip_project_question"] = True
        result["pre_resolved"]["project_decision"] = "all_this_project"
        result["pre_resolved"]["project_id"] = project_id
        result["pre_resolved"]["project_name"] = project_name
        print(f"[UserContext] Project decision: all for {project_name}")

    elif project_decision == "split" and split_projects:
        # Resolve project names
        resolved_splits = []
        all_resolved = True
        total_amount = float(parsed_data.get("amount") or 0)

        for sp in split_projects:
            sp_name = sp.get("name", "")
            sp_portion = sp.get("portion")
            sp_amount = sp.get("amount")

            if sp_name == "this_project":
                resolved_splits.append({
                    "project_id": project_id,
                    "project_name": project_name,
                    "portion": sp_portion,
                    "amount": sp_amount,
                })
            else:
                project = _resolve_project_by_name(sp_name)
                if project:
                    resolved_splits.append({
                        "project_id": project["project_id"],
                        "project_name": project["project_name"],
                        "portion": sp_portion,
                        "amount": sp_amount,
                    })
                else:
                    all_resolved = False
                    print(f"[UserContext] Could not resolve project: '{sp_name}'")

        if all_resolved and resolved_splits:
            # Calculate amounts from portions if not explicitly given
            for split in resolved_splits:
                if split["amount"] is None and split["portion"] and total_amount > 0:
                    portion = split["portion"]
                    if portion == "half":
                        split["amount"] = round(total_amount / 2, 2)
                    elif portion == "third":
                        split["amount"] = round(total_amount / 3, 2)
                    elif portion == "quarter":
                        split["amount"] = round(total_amount / 4, 2)

            result["skip_project_question"] = True
            result["pre_resolved"]["project_decision"] = "split"
            result["pre_resolved"]["split_details"] = resolved_splits
            print(f"[UserContext] Split resolved: {len(resolved_splits)} projects")

    # --- 3. Determine start state ---
    if result["skip_categories"] and result["skip_project_question"]:
        result["start_state"] = "awaiting_user_confirm"
    elif not result["skip_categories"] and low_confidence_items:
        result["start_state"] = "awaiting_category_confirmation"
    elif result["skip_project_question"]:
        result["start_state"] = "awaiting_user_confirm"
    else:
        result["start_state"] = None  # Normal flow

    return result


def _build_confirm_summary(pre_resolved: dict, line_items: list, parsed_data: dict) -> str:
    """Build Andrew's confirmation summary message from pre-resolved data."""
    vendor = parsed_data.get("vendor_name") or "Unknown"
    total = parsed_data.get("amount") or 0
    decision = pre_resolved.get("project_decision")

    parts = [f"Receipt scanned: **{vendor}** -- ${total:,.2f}"]

    if decision == "all_this_project":
        project_name = pre_resolved.get("project_name", "this project")
        parts.append(f"\nAll items for **{project_name}**:")
        for i, item in enumerate(line_items):
            desc = (item.get("description") or "Item")[:60]
            amt = item.get("amount", 0)
            cat = item.get("account_name") or "Uncategorized"
            parts.append(f"  {i+1}. {desc} -- ${amt:,.2f} -> {cat}")

    elif decision == "split":
        splits = pre_resolved.get("split_details", [])
        parts.append("\nSplit between projects:")
        for sp in splits:
            sp_name = sp.get("project_name", "?")
            sp_amt = sp.get("amount")
            if sp_amt:
                parts.append(f"  - **{sp_name}**: ${sp_amt:,.2f}")
            else:
                portion = sp.get("portion", "?")
                parts.append(f"  - **{sp_name}**: {portion}")
        if line_items:
            parts.append("\nItems:")
            for i, item in enumerate(line_items):
                desc = (item.get("description") or "Item")[:60]
                amt = item.get("amount", 0)
                cat = item.get("account_name") or "Uncategorized"
                parts.append(f"  {i+1}. {desc} -- ${amt:,.2f} -> {cat}")

    parts.append("\n**Does this look right?**")
    return "\n".join(parts)


# ====== AGENT ENDPOINT ======

@router.post("/{receipt_id}/agent-process")
async def agent_process_receipt(receipt_id: str):
    """
    Full automated processing pipeline for material receipts.

    Pipeline:
    1. Fetch receipt, download file
    2. Split reuse detection / check detection
    3. OCR extraction (OpenAI Vision)
    4. Auto-categorize (GPT with construction stage context)
    5. Update receipt with enriched data + receipt_flow
    6. Post Andrew summary + project question (awaiting user response)

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

        # (Duplicate detection removed -- was matching error/incomplete receipts)

        # ===== STEP 2.5: Check Detection (Heuristic) =====
        file_name = receipt_data.get("file_name", "")
        is_image = (file_type or "").startswith("image/")
        is_likely_check = is_image and "check" in file_name.lower()

        if is_likely_check:
            print(f"[Agent] Step 2.5: LABOR CHECK auto-detected: {file_name}")

            payroll_channel_id = _get_payroll_channel_id()

            check_flow = {
                "state": "awaiting_check_number",
                "detected_at": datetime.utcnow().isoformat(),
                "check_type": "labor",
                "channel_id": payroll_channel_id,
                "origin_project_id": project_id,
                "check_number": None,
                "is_split": None,
                "entries": [],
                "date": None,
            }

            supabase.table("pending_receipts").update({
                "status": "check_review",
                "parsed_data": {"check_flow": check_flow},
                "updated_at": datetime.utcnow().isoformat()
            }).eq("id", receipt_id).execute()

            # Get project name for context
            proj_name = "Unknown"
            try:
                proj = supabase.table("projects") \
                    .select("project_name").eq("project_id", project_id).single().execute()
                if proj.data:
                    proj_name = proj.data.get("project_name", "Unknown")
            except Exception:
                pass

            file_url = receipt_data.get("file_url", "")
            file_link = f"[{file_name}]({file_url})" if file_url else file_name

            if payroll_channel_id:
                # Redirect message in receipts channel
                post_andrew_message(
                    content=(
                        f"Labor check detected: {file_link}. Continuing in **Payroll**.\n\n"
                        f"[Go to Payroll](/messages.html?channel={payroll_channel_id}&type=group)"
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
                # Ask check number in Payroll
                post_andrew_message(
                    content=(
                        f"Labor check from project **{proj_name}**: {file_link}\n\n"
                        "What is the **check number**?"
                    ),
                    channel_id=payroll_channel_id,
                    channel_type="group",
                    metadata={
                        "agent_message": True,
                        "pending_receipt_id": receipt_id,
                        "receipt_status": "check_review",
                        "check_flow_state": "awaiting_check_number",
                        "check_flow_active": True,
                        "awaiting_text_input": True,
                        "origin_project_id": project_id,
                    }
                )
            else:
                # No Payroll channel -- continue in receipts
                post_andrew_message(
                    content=(
                        f"Labor check detected: {file_link}\n\n"
                        "What is the **check number**?"
                    ),
                    project_id=project_id,
                    metadata={
                        "agent_message": True,
                        "pending_receipt_id": receipt_id,
                        "receipt_status": "check_review",
                        "check_flow_state": "awaiting_check_number",
                        "check_flow_active": True,
                        "awaiting_text_input": True,
                    }
                )

            return {
                "success": True,
                "status": "check_review",
                "message": "Labor check auto-detected, routed to Payroll",
            }

        # ===== STEP 3: OCR Extraction (Shared Service) =====
        print(f"[Agent] Step 2: No duplicate found, proceeding to OCR")
        # Read scan mode from agent_config (default: heavy)
        _scan_mode = "heavy"
        try:
            _mode_row = supabase.table("agent_config").select("value").eq("key", "andrew_scan_mode").execute()
            if _mode_row.data and _mode_row.data[0].get("value") in ("fast", "heavy"):
                _scan_mode = _mode_row.data[0]["value"]
        except Exception:
            pass
        print(f"[Agent] Step 3: Starting OCR extraction (mode={_scan_mode})...")
        try:
            scan_result = _scan_receipt_core(file_content, file_type, model=_scan_mode)
        except ValueError as e:
            if _scan_mode == "fast":
                # Fast mode failed (no extractable text) -- auto-fallback to heavy
                print(f"[Agent] Step 3: Fast mode failed ({e}), falling back to heavy...")
                scan_result = _scan_receipt_core(file_content, file_type, model="heavy")
            else:
                raise HTTPException(status_code=400, detail=str(e))
        except RuntimeError as e:
            raise Exception(f"OCR extraction failed: {str(e)}")

        line_items = scan_result.get("expenses", [])
        validation = scan_result.get("validation", {})

        # ===== STEP 3.5: Validation Correction Pass =====
        # If OCR totals don't match invoice total, run a focused correction pass
        validation_unresolved = False
        if not validation.get("validation_passed", True) and line_items:
            inv_total = validation.get("invoice_total", 0)
            calc_sum = validation.get("calculated_sum", 0)
            print(f"[Agent] Step 3.5: Validation FAILED | invoice={inv_total} vs calculated={calc_sum} | Triggering correction pass...")
            try:
                correction_context = {
                    "invoice_total": inv_total,
                    "calculated_sum": calc_sum,
                    "items": line_items,
                }
                corrected_result = _scan_receipt_core(
                    file_content, file_type, model="heavy",
                    correction_context=correction_context
                )
                corrected_validation = corrected_result.get("validation", {})
                if corrected_validation.get("validation_passed", False):
                    # Correction succeeded - use corrected data
                    scan_result = corrected_result
                    line_items = corrected_result.get("expenses", [])
                    validation = corrected_validation
                    corrections = corrected_validation.get("corrections_made", "")
                    print(f"[Agent] Step 3.5: Correction PASSED | corrections: {corrections}")
                else:
                    # Correction still failed - flag for bookkeeping escalation
                    validation_unresolved = True
                    new_sum = corrected_validation.get("calculated_sum", calc_sum)
                    print(f"[Agent] Step 3.5: Correction FAILED | still {new_sum} vs {inv_total} | Will escalate to bookkeeping")
            except Exception as corr_err:
                validation_unresolved = True
                print(f"[Agent] Step 3.5: Correction pass error: {corr_err} | Will escalate to bookkeeping")

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

        # Resolve txn_type_id and payment_method_id from OCR strings
        # Default: type = "Purchase", payment = "Debit" when OCR returns Unknown/empty
        txn_types_resp = supabase.table("txn_types").select("TnxType_id, TnxType_name").execute()
        txn_types_map = {
            t["TnxType_name"].lower(): t["TnxType_id"]
            for t in (txn_types_resp.data or []) if t.get("TnxType_name")
        }
        payment_resp = supabase.table("paymet_methods").select("id, payment_method_name").execute()
        payment_map = {
            p["payment_method_name"].lower(): p["id"]
            for p in (payment_resp.data or []) if p.get("payment_method_name")
        }

        # Always default to Purchase for receipt-based expenses
        default_txn_type_id = txn_types_map.get("purchase")
        # Default to Debit when OCR can't determine payment method
        default_payment_id = payment_map.get("debit")

        ocr_txn_type = first_item.get("transaction_type", "Unknown")
        resolved_txn_type_id = txn_types_map.get(ocr_txn_type.lower()) if ocr_txn_type and ocr_txn_type != "Unknown" else None
        txn_type_id = resolved_txn_type_id or default_txn_type_id

        ocr_payment = first_item.get("payment_method", "Unknown")
        resolved_payment_id = payment_map.get(ocr_payment.lower()) if ocr_payment and ocr_payment != "Unknown" else None
        payment_method_id = resolved_payment_id or default_payment_id

        print(f"[Agent] Step 3: Resolved txn_type_id={txn_type_id} (ocr={ocr_txn_type}), payment_method_id={payment_method_id} (ocr={ocr_payment})")

        parsed_data = {
            "vendor_name": vendor_name,
            "vendor_id": vendor_id,
            "amount": amount,
            "receipt_date": receipt_date,
            "bill_id": bill_id,
            "description": description,
            "line_items": line_items,
            "validation": validation,
            "txn_type_id": txn_type_id,
            "payment_method_id": payment_method_id,
        }
        print(f"[Agent] Step 3: OCR complete | vendor={vendor_name} | amount={amount} | date={receipt_date} | {len(line_items)} line item(s)")

        # ===== STEP 3.7: Filename hint cross-validation =====
        from api.helpers.bill_hint_parser import parse_bill_hint, cross_validate_bill_hint
        hint_file_name = receipt_data.get("file_name", "")
        bill_hint = parse_bill_hint(hint_file_name)
        hint_validation = {}
        if bill_hint:
            hint_validation = cross_validate_bill_hint(
                bill_hint,
                vendor_name=vendor_name if vendor_name != "Unknown" else None,
                amount=amount,
                date_str=receipt_date,
            )
            parsed_data["bill_hint"] = bill_hint
            parsed_data["bill_hint_validation"] = hint_validation
            print(f"[Agent] Step 3.7: Filename hint | hints={bill_hint} | mismatches={hint_validation.get('mismatches', [])}")

        # ===== STEP 3.8: Smart missing info analysis + auto-resolve =====
        from api.services.andrew_smart_layer import (
            analyze_missing_info, apply_resolutions, craft_receipt_message,
            craft_escalation_message,
        )
        smart_analysis = analyze_missing_info(parsed_data, receipt_data, project_id)
        if smart_analysis.get("resolutions"):
            parsed_data = apply_resolutions(parsed_data, smart_analysis["resolutions"])
            # Re-derive summary fields after resolution
            vendor_name = parsed_data.get("vendor_name", vendor_name)
            vendor_id = parsed_data.get("vendor_id", vendor_id)
            amount = parsed_data.get("amount", amount)
            receipt_date = parsed_data.get("receipt_date", receipt_date)
            bill_id = parsed_data.get("bill_id", bill_id)
            print(f"[Agent] Step 3.8: Smart resolved: {list(smart_analysis['resolutions'].keys())}")
        if smart_analysis.get("unresolved"):
            print(f"[Agent] Step 3.8: Still missing: {smart_analysis['unresolved']}")
        if smart_analysis.get("attempts"):
            for attempt in smart_analysis["attempts"]:
                print(f"[Agent] Step 3.8:   {attempt}")

        # ===== STEP 4: Warnings =====
        warnings = []
        if validation_unresolved:
            warnings.append("Invoice total mismatch - could not auto-correct, escalating to bookkeeping")
        elif validation.get("corrections_made"):
            warnings.append("Amounts corrected by verification pass")

        # Filename cross-check mismatches
        for mismatch in hint_validation.get("mismatches", []):
            warnings.append(f"Filename cross-check: {mismatch}")

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

        # ===== STEP 5.5: Detect low-confidence categories =====
        agent_cfg = {}
        try:
            cfg_result = supabase.table("agent_config").select("key, value").execute()
            for row in (cfg_result.data or []):
                agent_cfg[row["key"]] = row["value"]
        except Exception:
            pass
        min_confidence = int(agent_cfg.get("min_confidence", 70))

        low_confidence_items = []
        for i, item in enumerate(line_items):
            item_conf = item.get("confidence", 0)
            if item_conf < min_confidence and item.get("account_id"):
                low_confidence_items.append({
                    "index": i,
                    "description": item.get("description", ""),
                    "amount": item.get("amount"),
                    "suggested": item.get("account_name"),
                    "suggested_account_id": item.get("account_id"),
                    "confidence": item_conf,
                })
            elif not item.get("account_id"):
                low_confidence_items.append({
                    "index": i,
                    "description": item.get("description", ""),
                    "amount": item.get("amount"),
                    "suggested": None,
                    "suggested_account_id": None,
                    "confidence": 0,
                })

        if low_confidence_items:
            print(f"[Agent] Step 5.5: {len(low_confidence_items)} item(s) with low confidence - will ask user")

        # ===== STEP 6: Update receipt with enriched data =====
        if warnings:
            print(f"[Agent] Step 5: Warnings: {warnings}")

        # Initialize receipt flow - ask about categories first if needed
        if low_confidence_items:
            receipt_flow = {
                "state": "awaiting_category_confirmation",
                "started_at": datetime.utcnow().isoformat(),
                "low_confidence_items": low_confidence_items,
                "split_items": [],
                "total_for_project": 0.0,
            }
        else:
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

        # ===== STEP 7: Post Andrew smart message =====
        # Determine flow state
        if low_confidence_items:
            flow_state = "awaiting_category_confirmation"
        elif smart_analysis.get("unresolved"):
            flow_state = "awaiting_missing_info"
        else:
            flow_state = "awaiting_item_selection"

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

        # Build intelligent message with personality + context
        msg_content = craft_receipt_message(
            parsed_data=parsed_data,
            categorize_data=categorize_data,
            warnings=warnings,
            analysis=smart_analysis,
            project_name=project_name,
            flow_state=flow_state,
        )

        # Append category confirmation prompt if needed
        if low_confidence_items:
            cat_lines = []
            for lci in low_confidence_items:
                idx = lci["index"] + 1
                desc = lci["description"][:60]
                if lci["suggested"]:
                    cat_lines.append(f"{idx}. '{desc}' -- suggested: {lci['suggested']} ({lci['confidence']}%)")
                else:
                    cat_lines.append(f"{idx}. '{desc}' -- no match found")
            cat_list = "\n".join(cat_lines)
            msg_content += (
                f"\n\nI'm not sure about the category for these items:\n{cat_list}\n\n"
                "Type the correct account for each, e.g.: `1 Materials, 2 Delivery`\n"
                "Or reply **all correct** to accept the suggestions."
            )

        # Store smart analysis in parsed_data for later use
        enriched_parsed["smart_analysis"] = {
            "missing_fields": smart_analysis.get("missing_fields", []),
            "resolutions": smart_analysis.get("resolutions", {}),
            "unresolved": smart_analysis.get("unresolved", []),
            "attempts": smart_analysis.get("attempts", []),
        }
        # Update enriched receipt flow with missing info state
        if flow_state == "awaiting_missing_info":
            enriched_parsed["receipt_flow"]["state"] = "awaiting_missing_info"
            enriched_parsed["receipt_flow"]["missing_fields"] = smart_analysis.get("unresolved", [])

        # Re-save with smart analysis data
        supabase.table("pending_receipts") \
            .update({"parsed_data": enriched_parsed}) \
            .eq("id", receipt_id) \
            .execute()

        awaiting_text = bool(low_confidence_items) or bool(smart_analysis.get("unresolved"))
        post_andrew_message(
            content=msg_content,
            project_id=project_id,
            metadata={
                "agent_message": True,
                "pending_receipt_id": receipt_id,
                "receipt_status": "ready",
                "has_warnings": len(warnings) > 0,
                "confidence": final_confidence,
                "receipt_flow_state": flow_state,
                "receipt_flow_active": True,
                "awaiting_text_input": awaiting_text,
                "low_confidence_items": low_confidence_items if low_confidence_items else None,
            }
        )

        print(f"[Agent] Step 7: Andrew smart message posted | state={flow_state} | unresolved={smart_analysis.get('unresolved', [])}")

        # ===== STEP 7.5: Bookkeeping escalation for unresolved validation =====
        if validation_unresolved:
            mentions = _get_bookkeeping_mentions()
            inv_total = validation.get("invoice_total", 0)
            calc_sum = validation.get("calculated_sum", 0)
            difference = round(abs(inv_total - calc_sum), 2)
            escalation_msg = craft_escalation_message(
                receipt_data={"parsed_data": parsed_data},
                mentions=mentions,
                issue=(
                    f"Invoice says ${inv_total:,.2f} but items sum to "
                    f"${calc_sum:,.2f} (${difference:,.2f} off)"
                ),
                attempts=smart_analysis.get("attempts", []),
            )
            post_andrew_message(
                content=escalation_msg,
                project_id=project_id,
                metadata={
                    "agent_message": True,
                    "pending_receipt_id": receipt_id,
                    "escalation": "validation_mismatch",
                    "invoice_total": inv_total,
                    "calculated_sum": calc_sum,
                    "difference": difference,
                }
            )
            print(f"[Agent] Step 7.5: Bookkeeping escalation posted | diff=${difference}")

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


def _resolve_vendor(payee_name: str) -> tuple:
    """Fuzzy match payee against Vendors table. Returns (vendor_id, resolved_name)."""
    if not payee_name:
        return None, None
    try:
        vendors_resp = supabase.table("Vendors").select("id, vendor_name").execute()
        query = payee_name.lower().strip()
        # Exact match first
        for v in (vendors_resp.data or []):
            v_name = (v.get("vendor_name") or "").strip()
            if v_name.lower() == query:
                return v["id"], v_name
        # Partial: shortest name containing query
        best, best_len = None, float("inf")
        for v in (vendors_resp.data or []):
            v_name = (v.get("vendor_name") or "").strip()
            if query in v_name.lower() and len(v_name) < best_len:
                best, best_len = v, len(v_name)
        if best:
            return best["id"], best["vendor_name"]
    except Exception as e:
        print(f"[CheckFlow] Vendor lookup error: {e}")
    return None, payee_name


def _resolve_project(project_name: str, fallback_project_id: str) -> tuple:
    """Resolve project name to (project_id, project_name). Exact first, then shortest partial."""
    if not project_name:
        return fallback_project_id, None
    try:
        projects = supabase.table("projects").select("project_id, project_name").execute()
        query = project_name.lower().strip()
        best, best_len = None, float("inf")
        for p in (projects.data or []):
            p_name = (p.get("project_name") or "").strip()
            p_lower = p_name.lower()
            if p_lower == query:
                return p["project_id"], p_name
            if query in p_lower and len(p_name) < best_len:
                best, best_len = p, len(p_name)
        if best:
            return best["project_id"], best["project_name"]
    except Exception:
        pass
    return fallback_project_id, project_name


def _get_check_payment_uuid() -> Optional[str]:
    """Look up the UUID for 'Check' in paymet_methods table."""
    try:
        resp = supabase.table("paymet_methods") \
            .select("id").eq("payment_method_name", "Check").limit(1).execute()
        if resp.data:
            return str(resp.data[0]["id"])
    except Exception as e:
        print(f"[CheckFlow] Error looking up Check payment method: {e}")
    return None


def _get_purchase_txn_type_id() -> Optional[str]:
    """Look up the UUID for 'Purchase' in txn_types table."""
    try:
        resp = supabase.table("txn_types") \
            .select("TnxType_id").eq("TnxType_name", "Purchase").limit(1).execute()
        if resp.data:
            return str(resp.data[0]["TnxType_id"])
    except Exception as e:
        print(f"[CheckFlow] Error looking up Purchase txn type: {e}")
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
            model="gpt-5.1",
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
    """Create expense entries from the completed check flow using entries[] array."""
    created_expenses = []

    check_number = check_flow.get("check_number")
    entries = check_flow.get("entries", [])
    txn_date = check_flow.get("date", datetime.utcnow().date().isoformat())

    # Resolve payment_type and txn_type_id as UUIDs
    check_payment_uuid = _get_check_payment_uuid()
    purchase_txn_type_id = _get_purchase_txn_type_id()

    for entry in entries:
        cat = entry.get("categorization", {})
        expense_data = {
            "project": entry.get("project_id", receipt_data.get("project_id")),
            "Amount": entry["amount"],
            "TxnDate": txn_date,
            "LineDescription": f"Check: {entry.get('description', '')}",
            "account_id": cat.get("account_id"),
            "created_by": receipt_data.get("uploaded_by"),
            "receipt_url": receipt_data.get("file_url"),
            "auth_status": False,
        }
        if entry.get("vendor_id"):
            expense_data["vendor_id"] = entry["vendor_id"]
        if check_number:
            expense_data["bill_id"] = check_number
        if purchase_txn_type_id:
            expense_data["txn_type_id"] = purchase_txn_type_id
        if check_payment_uuid:
            expense_data["payment_type"] = check_payment_uuid

        expense_data = {k: v for k, v in expense_data.items() if v is not None}
        try:
            result = supabase.table("expenses_manual_COGS").insert(expense_data).execute()
            if result.data:
                created_expenses.append(result.data[0])
        except Exception as e:
            print(f"[CheckFlow] Error creating expense: {e}")

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

def _handle_awaiting_check_number(receipt_id, project_id, check_flow, parsed_data, action, payload):
    """Handle check number text submission."""
    if action != "submit_check_number":
        raise HTTPException(status_code=400, detail=f"Invalid action '{action}' for state 'awaiting_check_number'")

    text = (payload or {}).get("text", "").strip()
    if not text:
        return {"success": True, "state": "awaiting_check_number", "error": "empty"}

    check_number = re.sub(r'[^a-zA-Z0-9]', '', text)
    if not check_number:
        post_andrew_message(
            content=f"\"{text}\" does not look like a check number. Please type the number printed on the check.",
            **_check_flow_msg_kwargs(project_id, check_flow),
            metadata={
                "agent_message": True,
                "pending_receipt_id": receipt_id,
                "receipt_status": "check_review",
                "check_flow_state": "awaiting_check_number",
                "check_flow_active": True,
                "awaiting_text_input": True,
            }
        )
        return {"success": True, "state": "awaiting_check_number", "error": "invalid"}

    check_flow["check_number"] = check_number
    check_flow["state"] = "awaiting_split_decision"
    _update_check_flow(receipt_id, check_flow, parsed_data)

    post_andrew_message(
        content=f"Check **#{check_number}**. Is this split across multiple projects?",
        **_check_flow_msg_kwargs(project_id, check_flow),
        metadata={
            "agent_message": True,
            "pending_receipt_id": receipt_id,
            "receipt_status": "check_review",
            "check_flow_state": "awaiting_split_decision",
            "check_flow_active": True,
        }
    )
    return {"success": True, "state": "awaiting_split_decision"}


def _handle_split_decision(receipt_id, project_id, check_flow, parsed_data, action):
    """Handle split yes/no decision."""
    if action == "split_no":
        check_flow["is_split"] = False
        check_flow["state"] = "awaiting_vendor_info"
        _update_check_flow(receipt_id, check_flow, parsed_data)

        post_andrew_message(
            content=(
                "Single project. Who is the payee, what is the amount, and a brief description?\n\n"
                "Example: **1500 Smith Plumbing drywall labor**"
            ),
            **_check_flow_msg_kwargs(project_id, check_flow),
            metadata={
                "agent_message": True,
                "pending_receipt_id": receipt_id,
                "receipt_status": "check_review",
                "check_flow_state": "awaiting_vendor_info",
                "check_flow_active": True,
                "awaiting_text_input": True,
            }
        )
        return {"success": True, "state": "awaiting_vendor_info"}

    elif action == "split_yes":
        check_flow["is_split"] = True
        check_flow["entries"] = []
        check_flow["state"] = "awaiting_split_entries"
        _update_check_flow(receipt_id, check_flow, parsed_data)

        post_andrew_message(
            content=(
                "Send each entry as:\n"
                "**[amount] [description] by [vendor] for [project]**\n\n"
                "Example: *500 drywall labor by Smith Plumbing for Oak Park*\n\n"
                "Type **done** when finished."
            ),
            **_check_flow_msg_kwargs(project_id, check_flow),
            metadata={
                "agent_message": True,
                "pending_receipt_id": receipt_id,
                "receipt_status": "check_review",
                "check_flow_state": "awaiting_split_entries",
                "check_flow_active": True,
                "awaiting_text_input": True,
            }
        )
        return {"success": True, "state": "awaiting_split_entries"}

    raise HTTPException(status_code=400, detail=f"Invalid action '{action}' for state 'awaiting_split_decision'")


def _handle_vendor_info(receipt_id, project_id, check_flow, parsed_data, action, payload):
    """Handle non-split vendor + amount + description submission."""
    if action != "submit_vendor_info":
        raise HTTPException(status_code=400, detail=f"Invalid action '{action}' for state 'awaiting_vendor_info'")

    text = (payload or {}).get("text", "").strip()
    if not text:
        return {"success": True, "state": "awaiting_vendor_info", "error": "empty"}

    # Parse amount from beginning
    match = re.match(r'^([\$\d,.]+)\s+(.+)$', text)
    if not match:
        post_andrew_message(
            content="Start with the amount, e.g.: **1500 Smith Plumbing drywall labor**",
            **_check_flow_msg_kwargs(project_id, check_flow),
            metadata={
                "agent_message": True,
                "pending_receipt_id": receipt_id,
                "receipt_status": "check_review",
                "check_flow_state": "awaiting_vendor_info",
                "check_flow_active": True,
                "awaiting_text_input": True,
            }
        )
        return {"success": True, "state": "awaiting_vendor_info", "error": "parse_error"}

    amount = _parse_amount(match.group(1))
    remainder = match.group(2).strip()

    if amount is None:
        post_andrew_message(
            content=f"\"{match.group(1)}\" does not look like an amount. Try: **1500 Smith Plumbing drywall labor**",
            **_check_flow_msg_kwargs(project_id, check_flow),
            metadata={
                "agent_message": True,
                "pending_receipt_id": receipt_id,
                "receipt_status": "check_review",
                "check_flow_state": "awaiting_vendor_info",
                "check_flow_active": True,
                "awaiting_text_input": True,
            }
        )
        return {"success": True, "state": "awaiting_vendor_info", "error": "invalid_amount"}

    # Smart vendor extraction: try progressively shorter left-anchored word prefixes
    words = remainder.split()
    vendor_id = None
    vendor_name = None
    description = remainder

    for i in range(len(words), 0, -1):
        candidate = " ".join(words[:i])
        vid, vname = _resolve_vendor(candidate)
        if vid:
            vendor_id = vid
            vendor_name = vname
            description = " ".join(words[i:]) if i < len(words) else ""
            break

    # If no vendor matched, treat first word(s) as vendor hint, rest as description
    if not vendor_id and len(words) > 1:
        vendor_name = words[0]
        description = " ".join(words[1:])

    if not description:
        description = "Labor"

    entry = {
        "amount": amount,
        "vendor_id": vendor_id,
        "vendor_name": vendor_name or "Unknown",
        "description": description,
        "project_id": check_flow.get("origin_project_id", project_id),
        "project_name": None,
    }
    check_flow["entries"] = [entry]

    # Update receipt amount
    supabase.table("pending_receipts").update({
        "amount": amount,
        "updated_at": datetime.utcnow().isoformat()
    }).eq("id", receipt_id).execute()

    check_flow["state"] = "awaiting_date_confirm"
    _update_check_flow(receipt_id, check_flow, parsed_data)

    vendor_note = f"**{vendor_name}**" if vendor_name else "Unknown vendor"
    if vendor_id:
        vendor_note += " (matched)"

    post_andrew_message(
        content=(
            f"${amount:,.2f} -- {description} -- {vendor_note}\n\n"
            f"Is **today** ({datetime.utcnow().strftime('%m/%d/%Y')}) the right date, "
            "or do you want to specify a different one?"
        ),
        **_check_flow_msg_kwargs(project_id, check_flow),
        metadata={
            "agent_message": True,
            "pending_receipt_id": receipt_id,
            "receipt_status": "check_review",
            "check_flow_state": "awaiting_date_confirm",
            "check_flow_active": True,
        }
    )
    return {"success": True, "state": "awaiting_date_confirm"}


def _handle_split_entries(receipt_id, project_id, check_flow, parsed_data, action, payload):
    """Handle split entry lines or done signal."""
    if action == "split_done":
        entries = check_flow.get("entries", [])
        if not entries:
            post_andrew_message(
                content="No entries yet. Add at least one, or type **done** after adding entries.",
                **_check_flow_msg_kwargs(project_id, check_flow),
                metadata={
                    "agent_message": True,
                    "pending_receipt_id": receipt_id,
                    "receipt_status": "check_review",
                    "check_flow_state": "awaiting_split_entries",
                    "check_flow_active": True,
                    "awaiting_text_input": True,
                }
            )
            return {"success": True, "state": "awaiting_split_entries", "error": "no_entries"}

        # Update receipt amount with total
        total = sum(e.get("amount", 0) for e in entries)
        supabase.table("pending_receipts").update({
            "amount": total,
            "updated_at": datetime.utcnow().isoformat()
        }).eq("id", receipt_id).execute()

        check_flow["state"] = "awaiting_date_confirm"
        _update_check_flow(receipt_id, check_flow, parsed_data)

        post_andrew_message(
            content=(
                f"{len(entries)} entries, total **${total:,.2f}**.\n\n"
                f"Is **today** ({datetime.utcnow().strftime('%m/%d/%Y')}) the right date, "
                "or do you want to specify a different one?"
            ),
            **_check_flow_msg_kwargs(project_id, check_flow),
            metadata={
                "agent_message": True,
                "pending_receipt_id": receipt_id,
                "receipt_status": "check_review",
                "check_flow_state": "awaiting_date_confirm",
                "check_flow_active": True,
            }
        )
        return {"success": True, "state": "awaiting_date_confirm"}

    elif action == "submit_split_entry":
        text = (payload or {}).get("text", "").strip()
        if not text:
            return {"success": True, "state": "awaiting_split_entries", "error": "empty"}

        # Parse: [amount] [description] by [vendor] for [project]
        amount_match = re.match(r'^([\$\d,.]+)\s+(.+)$', text)
        if not amount_match:
            post_andrew_message(
                content="Start with the amount. Example: **500 drywall labor by Smith Plumbing for Oak Park**",
                **_check_flow_msg_kwargs(project_id, check_flow),
                metadata={
                    "agent_message": True,
                    "pending_receipt_id": receipt_id,
                    "receipt_status": "check_review",
                    "check_flow_state": "awaiting_split_entries",
                    "check_flow_active": True,
                    "awaiting_text_input": True,
                }
            )
            return {"success": True, "state": "awaiting_split_entries", "error": "parse_error"}

        amount = _parse_amount(amount_match.group(1))
        rest = amount_match.group(2).strip()

        if amount is None:
            post_andrew_message(
                content=f"\"{amount_match.group(1)}\" is not a valid amount.",
                **_check_flow_msg_kwargs(project_id, check_flow),
                metadata={
                    "agent_message": True,
                    "pending_receipt_id": receipt_id,
                    "receipt_status": "check_review",
                    "check_flow_state": "awaiting_split_entries",
                    "check_flow_active": True,
                    "awaiting_text_input": True,
                }
            )
            return {"success": True, "state": "awaiting_split_entries", "error": "invalid_amount"}

        # Extract "for [project]" (rightmost)
        vendor_name_raw = None
        project_name_raw = None

        for_match = re.search(r'\s+for\s+(.+)$', rest, re.IGNORECASE)
        if for_match:
            project_name_raw = for_match.group(1).strip()
            rest = rest[:for_match.start()].strip()

        # Extract "by [vendor]"
        by_match = re.search(r'\s+by\s+(.+)$', rest, re.IGNORECASE)
        if by_match:
            vendor_name_raw = by_match.group(1).strip()
            rest = rest[:by_match.start()].strip()

        description = rest if rest else "Labor"

        # Resolve vendor
        vendor_id, vendor_name = _resolve_vendor(vendor_name_raw) if vendor_name_raw else (None, None)

        # Resolve project
        origin_project_id = check_flow.get("origin_project_id", project_id)
        split_project_id, split_project_name = _resolve_project(project_name_raw, origin_project_id)

        entry = {
            "amount": amount,
            "vendor_id": vendor_id,
            "vendor_name": vendor_name or vendor_name_raw or "Unknown",
            "description": description,
            "project_id": split_project_id,
            "project_name": split_project_name or "This project",
        }
        check_flow["entries"].append(entry)
        _update_check_flow(receipt_id, check_flow, parsed_data)

        total_so_far = sum(e.get("amount", 0) for e in check_flow["entries"])
        vendor_note = f" by {vendor_name or vendor_name_raw}" if (vendor_name or vendor_name_raw) else ""
        proj_note = f" for {split_project_name or 'this project'}"

        post_andrew_message(
            content=(
                f"Added: ${amount:,.2f} {description}{vendor_note}{proj_note}\n"
                f"Running total: **${total_so_far:,.2f}** ({len(check_flow['entries'])} entries)\n\n"
                "Send another entry or type **done**."
            ),
            **_check_flow_msg_kwargs(project_id, check_flow),
            metadata={
                "agent_message": True,
                "pending_receipt_id": receipt_id,
                "receipt_status": "check_review",
                "check_flow_state": "awaiting_split_entries",
                "check_flow_active": True,
                "awaiting_text_input": True,
            }
        )
        return {"success": True, "state": "awaiting_split_entries", "entry_count": len(check_flow["entries"])}

    raise HTTPException(status_code=400, detail=f"Invalid action '{action}' for state 'awaiting_split_entries'")


def _handle_date_confirm(receipt_id, project_id, check_flow, parsed_data, action, payload):
    """Handle date confirmation: today button or custom date text."""
    if action == "use_today":
        check_flow["date"] = datetime.utcnow().date().isoformat()
    elif action == "submit_date":
        text = (payload or {}).get("text", "").strip()
        parsed_date = None
        for fmt in ("%m/%d/%Y", "%m-%d-%Y", "%Y-%m-%d", "%m/%d/%y", "%m-%d-%y"):
            try:
                parsed_date = datetime.strptime(text, fmt).date()
                break
            except ValueError:
                continue
        if not parsed_date:
            post_andrew_message(
                content=f"Could not parse \"{text}\" as a date. Use MM/DD/YYYY format (e.g. 02/10/2026).",
                **_check_flow_msg_kwargs(project_id, check_flow),
                metadata={
                    "agent_message": True,
                    "pending_receipt_id": receipt_id,
                    "receipt_status": "check_review",
                    "check_flow_state": "awaiting_date_confirm",
                    "check_flow_active": True,
                    "awaiting_text_input": True,
                }
            )
            return {"success": True, "state": "awaiting_date_confirm", "error": "invalid_date"}
        check_flow["date"] = parsed_date.isoformat()
    else:
        raise HTTPException(status_code=400, detail=f"Invalid action '{action}' for state 'awaiting_date_confirm'")

    # === Run labor-only categorization ===
    stage_project_id = check_flow.get("origin_project_id", project_id)
    construction_stage = "General Construction"
    try:
        proj = supabase.table("projects").select("project_stage").eq("project_id", stage_project_id).single().execute()
        if proj.data and proj.data.get("project_stage"):
            construction_stage = proj.data["project_stage"]
    except Exception:
        pass

    entries = check_flow.get("entries", [])
    categorized = _categorize_check_items(entries, construction_stage)
    check_flow["entries"] = categorized

    # === Check confidence vs agent_config.min_confidence ===
    agent_cfg = {}
    try:
        cfg_result = supabase.table("agent_config").select("key, value").execute()
        for row in (cfg_result.data or []):
            agent_cfg[row["key"]] = row["value"]
    except Exception:
        pass
    min_confidence = int(agent_cfg.get("min_confidence", 70))

    low_confidence = []
    for i, entry in enumerate(categorized):
        cat = entry.get("categorization", {})
        conf = cat.get("confidence", 0)
        if conf < min_confidence or not cat.get("account_id"):
            low_confidence.append({
                "index": i,
                "description": entry.get("description", ""),
                "suggested": cat.get("account_name"),
                "confidence": conf,
            })

    # Build summary lines
    lines = []
    for i, entry in enumerate(categorized, 1):
        cat = entry.get("categorization", {})
        cat_name = cat.get("account_name", "Uncategorized")
        cat_conf = cat.get("confidence", 0)
        method = cat.get("method", "")
        method_label = "(fuzzy)" if method == "fuzzy_match" else "(AI)"
        line = f"{i}. ${entry['amount']:,.2f} -- {entry.get('description', '')} -- **{cat_name}** ({cat_conf}%) {method_label}"
        v_name = entry.get("vendor_name")
        if v_name and v_name != "Unknown":
            line += f" -- {v_name}"
        p_name = entry.get("project_name")
        if p_name and p_name != "This project":
            line += f" -- {p_name}"
        lines.append(line)

    summary = "\n".join(lines)

    if low_confidence:
        mentions = _get_bookkeeping_mentions()
        check_flow["state"] = "awaiting_category_review"
        check_flow["low_confidence"] = low_confidence
        _update_check_flow(receipt_id, check_flow, parsed_data)

        low_items = ", ".join(f"#{lc['index']+1}" for lc in low_confidence)
        post_andrew_message(
            content=(
                f"Check #{check_flow.get('check_number', '?')} categorization:\n\n"
                f"{summary}\n\n"
                f"Items {low_items} have low confidence (below {min_confidence}%).\n"
                f"{mentions} Can you verify or correct?\n\n"
                "Type the item number and correct account, e.g.:\n"
                "**1 Drywall Subcontract Labor**\n\n"
                "Or type **confirm** to accept as-is."
            ),
            **_check_flow_msg_kwargs(project_id, check_flow),
            metadata={
                "agent_message": True,
                "pending_receipt_id": receipt_id,
                "receipt_status": "check_review",
                "check_flow_state": "awaiting_category_review",
                "check_flow_active": True,
                "awaiting_text_input": True,
            }
        )
        return {"success": True, "state": "awaiting_category_review"}
    else:
        check_flow["state"] = "awaiting_category_confirm"
        _update_check_flow(receipt_id, check_flow, parsed_data)

        post_andrew_message(
            content=(
                f"Check #{check_flow.get('check_number', '?')} summary:\n\n"
                f"{summary}\n\n"
                f"Date: {check_flow['date']}\n\n"
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


def _handle_category_review(receipt_id, project_id, check_flow, parsed_data, action, payload):
    """Handle corrections to low-confidence categorizations from bookkeeping team."""
    if action == "confirm_categories":
        # Accept as-is, move to final confirm
        check_flow["state"] = "awaiting_category_confirm"
        check_flow.pop("low_confidence", None)
        _update_check_flow(receipt_id, check_flow, parsed_data)

        entries = check_flow.get("entries", [])
        lines = []
        for i, entry in enumerate(entries, 1):
            cat = entry.get("categorization", {})
            lines.append(f"{i}. ${entry['amount']:,.2f} -- {entry.get('description', '')} -- **{cat.get('account_name', 'Uncategorized')}**")
        summary = "\n".join(lines)

        post_andrew_message(
            content=f"Summary:\n\n{summary}\n\nDate: {check_flow.get('date')}\n\nConfirm?",
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

    elif action == "submit_category_correction":
        text = (payload or {}).get("text", "").strip()
        if not text:
            return {"success": True, "state": "awaiting_category_review", "error": "empty"}

        # Parse: "1 Drywall Subcontract Labor"
        match = re.match(r'^(\d+)\s+(.+)$', text)
        if not match:
            post_andrew_message(
                content="Type the item number and account name, e.g.: **1 Drywall Subcontract Labor**",
                **_check_flow_msg_kwargs(project_id, check_flow),
                metadata={
                    "agent_message": True,
                    "pending_receipt_id": receipt_id,
                    "receipt_status": "check_review",
                    "check_flow_state": "awaiting_category_review",
                    "check_flow_active": True,
                    "awaiting_text_input": True,
                }
            )
            return {"success": True, "state": "awaiting_category_review", "error": "parse_error"}

        idx = int(match.group(1)) - 1
        account_name_input = match.group(2).strip()
        entries = check_flow.get("entries", [])

        if idx < 0 or idx >= len(entries):
            post_andrew_message(
                content=f"Item #{idx+1} does not exist. Valid range: 1 to {len(entries)}.",
                **_check_flow_msg_kwargs(project_id, check_flow),
                metadata={
                    "agent_message": True,
                    "pending_receipt_id": receipt_id,
                    "receipt_status": "check_review",
                    "check_flow_state": "awaiting_category_review",
                    "check_flow_active": True,
                    "awaiting_text_input": True,
                }
            )
            return {"success": True, "state": "awaiting_category_review", "error": "invalid_index"}

        # Fuzzy match against labor accounts
        labor_accounts = _get_labor_accounts()
        matched = _fuzzy_match_labor_account(account_name_input, labor_accounts)

        if matched:
            entries[idx]["categorization"] = {
                "account_id": matched["account_id"],
                "account_name": matched["name"],
                "confidence": 100,
                "method": "manual_correction",
            }
        else:
            entries[idx]["categorization"] = {
                "account_id": None,
                "account_name": account_name_input,
                "confidence": 100,
                "method": "manual_correction",
            }

        check_flow["entries"] = entries
        # Remove corrected item from low_confidence list
        remaining_low = [lc for lc in check_flow.get("low_confidence", []) if lc["index"] != idx]
        check_flow["low_confidence"] = remaining_low
        _update_check_flow(receipt_id, check_flow, parsed_data)

        result_name = matched["name"] if matched else account_name_input
        match_note = "(matched)" if matched else "(no match in accounts)"

        post_andrew_message(
            content=(
                f"Item #{idx+1} updated to **{result_name}** {match_note}.\n\n"
                + (f"{len(remaining_low)} item(s) still need review.\n" if remaining_low else "")
                + "Send another correction, or type **confirm** to proceed."
            ),
            **_check_flow_msg_kwargs(project_id, check_flow),
            metadata={
                "agent_message": True,
                "pending_receipt_id": receipt_id,
                "receipt_status": "check_review",
                "check_flow_state": "awaiting_category_review",
                "check_flow_active": True,
                "awaiting_text_input": True,
            }
        )
        return {"success": True, "state": "awaiting_category_review"}

    raise HTTPException(status_code=400, detail=f"Invalid action '{action}' for state 'awaiting_category_review'")


def _handle_category_confirm(receipt_id, project_id, check_flow, parsed_data, action, receipt_data):
    """Handle final confirmation or cancellation."""
    if action == "confirm_categories":
        expenses = _create_check_expenses(receipt_id, receipt_data, check_flow)
        check_flow["state"] = "completed"
        _update_check_flow(receipt_id, check_flow, parsed_data)

        count = len(expenses)
        post_andrew_message(
            content=(
                f"{count} expense{'s' if count != 1 else ''} created from check #{check_flow.get('check_number', '?')}. "
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

        if current_state == "awaiting_check_number":
            return _handle_awaiting_check_number(receipt_id, project_id, check_flow, parsed_data, action, payload.payload)
        elif current_state == "awaiting_split_decision":
            return _handle_split_decision(receipt_id, project_id, check_flow, parsed_data, action)
        elif current_state == "awaiting_vendor_info":
            return _handle_vendor_info(receipt_id, project_id, check_flow, parsed_data, action, payload.payload)
        elif current_state == "awaiting_split_entries":
            return _handle_split_entries(receipt_id, project_id, check_flow, parsed_data, action, payload.payload)
        elif current_state == "awaiting_date_confirm":
            return _handle_date_confirm(receipt_id, project_id, check_flow, parsed_data, action, payload.payload)
        elif current_state == "awaiting_category_review":
            return _handle_category_review(receipt_id, project_id, check_flow, parsed_data, action, payload.payload)
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

            # Re-trigger processing
            await agent_process_receipt(receipt_id)
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

        # ── Category confirmation flow ──
        if current_state == "awaiting_category_confirmation":
            payload_data = payload.payload or {}
            text = payload_data.get("text", "").strip() if isinstance(payload_data, dict) else ""
            assignments = payload_data.get("assignments") if isinstance(payload_data, dict) else None

            low_items = receipt_flow.get("low_confidence_items", [])
            line_items = parsed_data.get("line_items", [])

            if not text and not assignments:
                raise HTTPException(status_code=400, detail="Text or assignments required for category confirmation")

            if text.lower() in ("all correct", "ok", "accept", "si", "yes"):
                # Accept all suggested categories as-is — bump confidence so expense creation won't skip them
                for lci in low_items:
                    idx = lci["index"]
                    if idx < len(line_items) and line_items[idx].get("account_id"):
                        line_items[idx]["confidence"] = 100
                        line_items[idx]["user_confirmed"] = True
                print(f"[ReceiptFlow] Category confirmation: user accepted all suggestions ({len(low_items)} items bumped to 100%)")
            elif assignments and isinstance(assignments, list):
                # Structured assignment from interactive account picker (no fuzzy matching needed)
                updates_applied = 0
                for assign in assignments:
                    idx = assign.get("index")
                    account_id = assign.get("account_id")
                    account_name = assign.get("account_name", "")
                    if idx is None or not account_id:
                        continue
                    if idx < len(line_items):
                        line_items[idx]["account_id"] = account_id
                        line_items[idx]["account_name"] = account_name
                        line_items[idx]["confidence"] = 100
                        line_items[idx]["user_confirmed"] = True
                        updates_applied += 1
                        print(f"[ReceiptFlow] Structured assign: item {idx} -> {account_name} ({account_id})")

                if updates_applied == 0:
                    post_andrew_message(
                        content="No valid account assignments found. Please try again.",
                        project_id=project_id,
                        metadata={
                            "agent_message": True,
                            "pending_receipt_id": receipt_id,
                            "receipt_flow_state": "awaiting_category_confirmation",
                            "receipt_flow_active": True,
                            "awaiting_text_input": True,
                            "low_confidence_items": low_items,
                        }
                    )
                    return {"success": True, "state": "awaiting_category_confirmation", "error": "no_valid_assignments"}
            else:
                # Parse "N account_name" pairs: "1 Materials, 2 Delivery"
                # Fetch accounts catalog for fuzzy matching
                accounts_resp = supabase.table("accounts") \
                    .select("account_id, Name") \
                    .execute()
                accounts_list = [
                    {"id": a["account_id"], "name": a["Name"]}
                    for a in (accounts_resp.data or []) if a.get("Name")
                ]
                accounts_lower = {a["name"].lower(): a for a in accounts_list}

                # Parse assignments
                parts = re.split(r'[,;]\s*', text)
                updates_applied = 0
                for part in parts:
                    part = part.strip()
                    match = re.match(r'^(\d+)\s+(.+)$', part)
                    if not match:
                        continue
                    item_num = int(match.group(1))
                    acct_name = match.group(2).strip()
                    # Find the line_item index from low_confidence_items
                    lci = next((x for x in low_items if x["index"] + 1 == item_num), None)
                    if not lci:
                        continue
                    idx = lci["index"]
                    if idx >= len(line_items):
                        continue
                    # Fuzzy match account name
                    matched_acct = accounts_lower.get(acct_name.lower())
                    if not matched_acct:
                        # Try partial match
                        for aname, aobj in accounts_lower.items():
                            if acct_name.lower() in aname or aname in acct_name.lower():
                                matched_acct = aobj
                                break
                    if matched_acct:
                        line_items[idx]["account_id"] = matched_acct["id"]
                        line_items[idx]["account_name"] = matched_acct["name"]
                        line_items[idx]["confidence"] = 100
                        line_items[idx]["user_confirmed"] = True
                        updates_applied += 1
                        print(f"[ReceiptFlow] Category update: item {idx} -> {matched_acct['name']}")
                    else:
                        print(f"[ReceiptFlow] Category update: no match for '{acct_name}'")

                if updates_applied == 0:
                    post_andrew_message(
                        content="I couldn't match any of those accounts. Try again with the exact account name, e.g.: `1 Materials, 2 Delivery`",
                        project_id=project_id,
                        metadata={
                            "agent_message": True,
                            "pending_receipt_id": receipt_id,
                            "receipt_flow_state": "awaiting_category_confirmation",
                            "receipt_flow_active": True,
                            "awaiting_text_input": True,
                        }
                    )
                    return {"success": True, "state": "awaiting_category_confirmation", "error": "no_matches"}

            # Check if user context already resolved the project question
            pre_resolved = receipt_flow.get("pre_resolved", {})
            receipt_flow.pop("low_confidence_items", None)
            parsed_data["line_items"] = line_items
            parsed_data["receipt_flow"] = receipt_flow

            project_name = "this project"
            try:
                proj_resp = supabase.table("projects").select("project_name") \
                    .eq("project_id", project_id).single().execute()
                if proj_resp.data:
                    project_name = proj_resp.data.get("project_name", project_name)
            except Exception:
                pass

            if pre_resolved.get("project_decision"):
                # User context already answered the project question -- skip to confirmation
                receipt_flow["state"] = "awaiting_user_confirm"
                parsed_data["receipt_flow"] = receipt_flow
                supabase.table("pending_receipts").update({
                    "parsed_data": parsed_data,
                    "updated_at": datetime.utcnow().isoformat()
                }).eq("id", receipt_id).execute()

                confirm_msg = _build_confirm_summary(pre_resolved, line_items, parsed_data)
                confirm_msg = "Categories confirmed.\n\n" + confirm_msg
                post_andrew_message(
                    content=confirm_msg,
                    project_id=project_id,
                    metadata={
                        "agent_message": True,
                        "pending_receipt_id": receipt_id,
                        "receipt_status": "ready",
                        "receipt_flow_state": "awaiting_user_confirm",
                        "receipt_flow_active": True,
                        "pre_resolved": pre_resolved,
                    }
                )
                print(f"[ReceiptFlow] Categories confirmed + pre_resolved -> awaiting_user_confirm")
                return {"success": True, "state": "awaiting_user_confirm"}
            else:
                # Normal transition to item selection
                receipt_flow["state"] = "awaiting_item_selection"
                parsed_data["receipt_flow"] = receipt_flow
                supabase.table("pending_receipts").update({
                    "parsed_data": parsed_data,
                    "updated_at": datetime.utcnow().isoformat()
                }).eq("id", receipt_id).execute()

                post_andrew_message(
                    content=f"Categories confirmed. Is this entire bill for **{project_name}**?",
                    project_id=project_id,
                    metadata={
                        "agent_message": True,
                        "pending_receipt_id": receipt_id,
                        "receipt_status": "ready",
                        "receipt_flow_state": "awaiting_item_selection",
                        "receipt_flow_active": True,
                    }
                )
                return {"success": True, "state": "awaiting_item_selection"}

        # ── Missing info flow (smart layer) ──
        if current_state == "awaiting_missing_info":
            text = (payload.payload or {}).get("text", "").strip() if payload.payload else ""
            if not text:
                raise HTTPException(status_code=400, detail="Text input required for missing info")

            from api.services.andrew_smart_layer import (
                interpret_reply, craft_reply_response,
            )

            # Build context for reply interpretation
            smart = parsed_data.get("smart_analysis") or {}
            reply_context = {
                "flow_state": "awaiting_missing_info",
                "original_question": "Missing info about receipt",
                "vendor_name": parsed_data.get("vendor_name"),
                "amount": parsed_data.get("amount"),
                "missing_fields": smart.get("unresolved", []),
            }
            interpretation = interpret_reply(text, reply_context)
            print(f"[ReceiptFlow] Missing info reply interpreted: {interpretation}")

            # Apply extracted fields
            updated_fields = []
            if interpretation.get("vendor_name"):
                parsed_data["vendor_name"] = interpretation["vendor_name"]
                if parsed_data.get("line_items") and len(parsed_data["line_items"]) > 0:
                    parsed_data["line_items"][0]["vendor"] = interpretation["vendor_name"]
                # Try to resolve vendor_id
                try:
                    v_resp = supabase.table("Vendors").select("id, vendor_name").execute()
                    for v in (v_resp.data or []):
                        if (v.get("vendor_name") or "").lower() == interpretation["vendor_name"].lower():
                            parsed_data["vendor_id"] = v["id"]
                            break
                except Exception:
                    pass
                updated_fields.append("vendor")

            if interpretation.get("receipt_date"):
                parsed_data["receipt_date"] = interpretation["receipt_date"]
                if parsed_data.get("line_items") and len(parsed_data["line_items"]) > 0:
                    parsed_data["line_items"][0]["date"] = interpretation["receipt_date"]
                updated_fields.append("date")

            if interpretation.get("check_number"):
                parsed_data["check_number"] = interpretation["check_number"]
                updated_fields.append("check_number")

            # Remove resolved fields from unresolved list
            remaining = [f for f in smart.get("unresolved", [])
                         if f not in updated_fields and f != "vendor_not_in_db"]

            if interpretation.get("unclear"):
                # Ask again with clarification
                response_msg = craft_reply_response(interpretation, reply_context)
                if response_msg:
                    post_andrew_message(
                        content=response_msg,
                        project_id=project_id,
                        metadata={
                            "agent_message": True,
                            "pending_receipt_id": receipt_id,
                            "receipt_flow_state": "awaiting_missing_info",
                            "receipt_flow_active": True,
                            "awaiting_text_input": True,
                        }
                    )
                return {"success": True, "state": "awaiting_missing_info", "clarification_needed": True}

            # Update parsed data and transition if all resolved
            smart["unresolved"] = remaining
            parsed_data["smart_analysis"] = smart

            if remaining:
                # Still missing fields -- stay in this state
                receipt_flow["missing_fields"] = remaining
                parsed_data["receipt_flow"] = receipt_flow
                supabase.table("pending_receipts").update({
                    "parsed_data": parsed_data,
                    "updated_at": datetime.utcnow().isoformat()
                }).eq("id", receipt_id).execute()

                response_msg = craft_reply_response(interpretation, reply_context)
                if response_msg:
                    post_andrew_message(
                        content=response_msg,
                        project_id=project_id,
                        metadata={
                            "agent_message": True,
                            "pending_receipt_id": receipt_id,
                            "receipt_flow_state": "awaiting_missing_info",
                            "receipt_flow_active": True,
                            "awaiting_text_input": True,
                        }
                    )
                return {"success": True, "state": "awaiting_missing_info",
                        "updated_fields": updated_fields, "still_missing": remaining}
            else:
                # All resolved -- transition to item selection
                receipt_flow["state"] = "awaiting_item_selection"
                receipt_flow.pop("missing_fields", None)
                parsed_data["receipt_flow"] = receipt_flow
                supabase.table("pending_receipts").update({
                    "parsed_data": parsed_data,
                    "vendor_name": parsed_data.get("vendor_name"),
                    "amount": parsed_data.get("amount"),
                    "receipt_date": parsed_data.get("receipt_date"),
                    "updated_at": datetime.utcnow().isoformat()
                }).eq("id", receipt_id).execute()

                # Get project name
                project_name = "this project"
                try:
                    proj_resp = supabase.table("projects").select("project_name") \
                        .eq("project_id", project_id).single().execute()
                    if proj_resp.data:
                        project_name = proj_resp.data.get("project_name", project_name)
                except Exception:
                    pass

                ack_msg = f"Got it -- updated {', '.join(updated_fields)}. Is this entire bill for **{project_name}**?"
                post_andrew_message(
                    content=ack_msg,
                    project_id=project_id,
                    metadata={
                        "agent_message": True,
                        "pending_receipt_id": receipt_id,
                        "receipt_flow_state": "awaiting_item_selection",
                        "receipt_flow_active": True,
                    }
                )
                return {"success": True, "state": "awaiting_item_selection",
                        "updated_fields": updated_fields}

        # -- User confirmation flow (from smart context resolution) --
        if current_state == "awaiting_user_confirm":
            pre_resolved = receipt_flow.get("pre_resolved", {})
            line_items = parsed_data.get("line_items", [])

            if action == "confirm":
                # Create expenses based on pre-resolved decisions
                decision = pre_resolved.get("project_decision", "all_this_project")

                agent_cfg = {}
                try:
                    cfg_result = supabase.table("agent_config").select("key, value").execute()
                    for row in (cfg_result.data or []):
                        agent_cfg[row["key"]] = row["value"]
                except Exception:
                    pass

                min_confidence = int(agent_cfg.get("min_confidence", 70))
                cat = parsed_data.get("categorization", {})
                vendor_id = parsed_data.get("vendor_id")
                created_expenses = []

                if decision == "all_this_project":
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

                elif decision == "split":
                    split_details = pre_resolved.get("split_details", [])
                    total_amount = float(parsed_data.get("amount") or 0)
                    primary_account_id = (line_items[0].get("account_id") if line_items else None) or cat.get("account_id")

                    for sp in split_details:
                        sp_project_id = sp.get("project_id", project_id)
                        sp_amount = sp.get("amount")
                        if sp_amount and primary_account_id:
                            expense = _create_receipt_expense(
                                sp_project_id, parsed_data, receipt_data,
                                vendor_id, primary_account_id,
                                amount=sp_amount,
                                description=parsed_data.get("description") or f"Split from {parsed_data.get('vendor_name', 'receipt')}",
                            )
                            if expense:
                                created_expenses.append(expense)

                # Update receipt flow
                receipt_flow["state"] = "completed"
                parsed_data["receipt_flow"] = receipt_flow
                expense_id = created_expenses[0].get("expense_id") if created_expenses else None

                if created_expenses:
                    supabase.table("pending_receipts").update({
                        "status": "linked",
                        "expense_id": expense_id,
                        "parsed_data": parsed_data,
                        "linked_at": datetime.utcnow().isoformat(),
                        "updated_at": datetime.utcnow().isoformat()
                    }).eq("id", receipt_id).execute()

                    count = len(created_expenses)
                    auth_mentions = _get_auth_notify_mentions()
                    msg = f"{count} expense(s) saved -- ready for authorization." if count > 1 else "Expense saved -- ready for authorization."
                    if auth_mentions:
                        msg += f"\n\n{auth_mentions}"
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
                    print(f"[ReceiptFlow] User confirmed -> {count} expense(s) created")
                else:
                    supabase.table("pending_receipts").update({
                        "parsed_data": parsed_data,
                        "updated_at": datetime.utcnow().isoformat()
                    }).eq("id", receipt_id).execute()

                    post_andrew_message(
                        content="I couldn't auto-create expenses (low confidence or missing category). Please add them manually from the Expenses tab.",
                        project_id=project_id,
                        metadata={
                            "agent_message": True,
                            "pending_receipt_id": receipt_id,
                            "receipt_status": "ready",
                            "receipt_flow_state": "completed",
                            "receipt_flow_active": False,
                        }
                    )

                return {"success": True, "state": "completed", "expense_id": expense_id}

            elif action == "edit":
                # User wants to change -- drop back to awaiting_item_selection
                receipt_flow["state"] = "awaiting_item_selection"
                receipt_flow.pop("pre_resolved", None)
                parsed_data["receipt_flow"] = receipt_flow
                supabase.table("pending_receipts").update({
                    "parsed_data": parsed_data,
                    "updated_at": datetime.utcnow().isoformat()
                }).eq("id", receipt_id).execute()

                project_name = "this project"
                try:
                    proj_resp = supabase.table("projects").select("project_name") \
                        .eq("project_id", project_id).single().execute()
                    if proj_resp.data and proj_resp.data.get("project_name"):
                        project_name = proj_resp.data["project_name"]
                except Exception:
                    pass

                post_andrew_message(
                    content=f"No problem -- is this entire bill for **{project_name}**?",
                    project_id=project_id,
                    metadata={
                        "agent_message": True,
                        "pending_receipt_id": receipt_id,
                        "receipt_status": "ready",
                        "receipt_flow_state": "awaiting_item_selection",
                        "receipt_flow_active": True,
                    }
                )
                return {"success": True, "state": "awaiting_item_selection"}

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
                    auth_mentions = _get_auth_notify_mentions()
                    msg = f"{count} expense(s) saved -- ready for authorization." if count > 1 else "Expense saved -- ready for authorization."
                    if auth_mentions:
                        msg += f"\n\n{auth_mentions}"
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
                        content="I couldn't auto-create expenses (low confidence or missing category). Please add them manually from the Expenses tab.",
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
                        split_auth_mentions = _get_auth_notify_mentions()
                        split_msg += f"\n\n{len(created_other)} expense(s) auto-created."
                        if split_auth_mentions:
                            split_msg += f"\n{split_auth_mentions}"

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
                    auth_mentions = _get_auth_notify_mentions()
                    msg = f"{count} expense(s) saved -- ready for authorization." if count > 1 else "Expense saved -- ready for authorization."
                    if auth_mentions:
                        msg += f"\n\n{auth_mentions}"
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
                        content="I couldn't auto-create expenses (low confidence or missing category). Please add them manually from the Expenses tab.",
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

        # Try to delete from storage (supports both vault and legacy bucket URLs)
        try:
            if "vault" in file_url:
                bucket = "vault"
            else:
                bucket = RECEIPTS_BUCKET
            bucket_marker = f"/object/public/{bucket}/"
            if bucket_marker in file_url:
                storage_path = file_url.split(bucket_marker, 1)[1].split("?")[0]
                supabase.storage.from_(bucket).remove([storage_path])
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
        success_rate = round(((total - errors) / total) * 100) if total > 0 else 0

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
        logger.error(f"[agent-stats] Error getting agent stats: {e}", exc_info=True)
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
        logger.error(f"[agent-config] Error loading agent config: {e}", exc_info=True)
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
        logger.error(f"[agent-config] Error updating agent config: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error updating agent config: {str(e)}")


@router.post("/check-stale")
async def check_stale_receipts_endpoint():
    """
    Trigger stale receipt check. Sends reminders for receipts waiting
    too long for user action. Call via cron or manually.
    """
    from api.services.receipt_monitor import check_stale_receipts
    try:
        result = await check_stale_receipts()
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Stale receipt check error: {str(e)}")
