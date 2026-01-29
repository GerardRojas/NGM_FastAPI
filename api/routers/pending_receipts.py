# api/routers/pending_receipts.py
# ================================
# Pending Receipts API Router
# ================================
# Manages receipts uploaded to project channels for expense processing

from fastapi import APIRouter, HTTPException, File, UploadFile, Form, Query
from pydantic import BaseModel
from api.supabase_client import supabase
from typing import Optional, List
import base64
import os
import uuid
from datetime import datetime
from openai import OpenAI
import json
import io
from pdf2image import convert_from_bytes

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
