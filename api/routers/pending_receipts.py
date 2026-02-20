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

from fastapi import APIRouter, HTTPException, File, UploadFile, Form, Query, BackgroundTasks, Depends, Request
from pydantic import BaseModel
from api.supabase_client import supabase
from api.auth import get_current_user
from typing import Optional, List, Dict, Any
import base64
import gc
import hashlib
import logging
import os
import re
import uuid
from datetime import datetime, timedelta
import json
import io
import time
import asyncio

logger = logging.getLogger(__name__)
from api.helpers.andrew_messenger import post_andrew_message, ANDREW_BOT_USER_ID
from services.receipt_scanner import (
    scan_receipt as _scan_receipt_core,
    auto_categorize as _auto_categorize_core,
)
from api.services.vault_service import save_to_project_folder

router = APIRouter(prefix="/pending-receipts", tags=["Pending Receipts"])

# Storage bucket name
RECEIPTS_BUCKET = "pending-expenses"
MAX_RECEIPT_BYTES = 20 * 1024 * 1024  # 20 MB


def _check_content_length(request: Request, max_bytes: int = MAX_RECEIPT_BYTES):
    """Reject oversized uploads early using the Content-Length header."""
    cl = request.headers.get("content-length")
    if cl and int(cl) > max_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"Payload too large. Maximum upload size is {max_bytes // (1024*1024)} MB.",
        )


def _load_agent_config() -> dict:
    """Load agent_config with proper JSON parsing (avoid string-truthy bugs)."""
    try:
        result = supabase.table("agent_config").select("key, value").execute()
        cfg = {}
        for row in (result.data or []):
            raw = row["value"]
            try:
                cfg[row["key"]] = json.loads(raw) if isinstance(raw, str) else raw
            except (json.JSONDecodeError, ValueError):
                cfg[row["key"]] = raw
        return cfg
    except Exception as e:
        logger.warning("[AgentConfig] Failed to load agent_config: %s", e)
        return {}


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


class EditCategoriesRequest(BaseModel):
    """User confirmation of category changes for bill items"""
    assignments: List[Dict[str, Any]]  # [{ expense_id, account_id, account_name }]
    user_id: Optional[str] = None


class VaultBatchRequest(BaseModel):
    """Batch process receipts from vault's Receipts folder"""
    vault_file_ids: List[str]
    project_id: str


# ====== HELPERS ======

# ====== ENDPOINTS ======

@router.post("/upload", status_code=201)
async def upload_receipt(
    request: Request,
    file: UploadFile = File(...),
    project_id: str = Form(...),
    uploaded_by: str = Form(...),
    message_id: Optional[str] = Form(None),
    current_user: dict = Depends(get_current_user)
):
    """
    Upload a receipt file to the pending-expenses bucket.

    Creates a pending_receipt record for later processing.

    Accepts: images (JPG, PNG, WebP, GIF) and PDFs
    Max size: 20MB

    Returns the created pending receipt record.
    """
    # Reject oversized payloads before reading the body into memory
    _check_content_length(request, MAX_RECEIPT_BYTES)

    file_content = None
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
        if len(file_content) > MAX_RECEIPT_BYTES:
            raise HTTPException(status_code=413, detail="File too large. Maximum size is 20MB.")

        # Compute file hash for duplicate detection
        file_hash = hashlib.sha256(file_content).hexdigest()
        file_size = len(file_content)

        # Generate unique ID for this receipt
        receipt_id = str(uuid.uuid4())

        # Upload to Vault (primary storage)
        vault_result = save_to_project_folder(project_id, "Receipts", file_content, file.filename, file.content_type)

        # Free the large buffer immediately after upload
        del file_content
        file_content = None
        gc.collect()

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
            "file_size": file_size,
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
    finally:
        if file_content is not None:
            del file_content
            gc.collect()


@router.get("/project/{project_id}")
def get_project_receipts(
    project_id: str,
    status: Optional[str] = Query(None, description="Filter by status"),
    unprocessed_only: bool = Query(False, description="Only receipts without expenses"),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    current_user: dict = Depends(get_current_user)
):
    """
    Get pending receipts for a project.

    Modes:
    - unprocessed_only=true: Returns receipts that have no linked expenses
      (expense_id IS NULL, excludes rejected and processing).
      Used by the "From Pending" modal in expenses.
    - status=xxx: Legacy filter by status.
    - Neither: Returns all receipts for the project.
    """
    try:
        if unprocessed_only:
            # Auto-reset orphaned receipts: status='linked' but expense_id=NULL
            # (happens when all linked expenses are deleted via FK ON DELETE SET NULL)
            try:
                supabase.table("pending_receipts") \
                    .update({"status": "ready", "updated_at": datetime.utcnow().isoformat()}) \
                    .eq("project_id", project_id) \
                    .eq("status", "linked") \
                    .is_("expense_id", "null") \
                    .execute()
            except Exception as e:
                logger.error(f"[PendingReceipts] Auto-reset orphaned receipts error: {e}")

            # Fetch all unprocessed: expense_id IS NULL, exclude rejected/processing
            query = supabase.table("pending_receipts") \
                .select("*, users!uploaded_by(user_name, avatar_color)") \
                .eq("project_id", project_id) \
                .is_("expense_id", "null") \
                .not_.in_("status", ["rejected", "processing"]) \
                .order("created_at", desc=True) \
                .range(offset, offset + limit - 1)

            result = query.execute()
            data = result.data or []

            return {
                "success": True,
                "data": data,
                "total": len(data),
                "pagination": {
                    "offset": offset,
                    "limit": limit,
                    "has_more": len(data) == limit
                }
            }

        # Legacy mode: filter by status (used by other parts of the app)
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


# ====== AGENT MANAGER ENDPOINTS ======
# NOTE: These MUST be defined before /{receipt_id} catch-all route
# to prevent FastAPI from matching "/agent-stats" as a receipt_id.

@router.get("/agent-stats")
def get_agent_stats(
    days: int = Query(30, ge=1, le=365),
    current_user: dict = Depends(get_current_user),
):
    """Return aggregate stats for the receipt agent."""
    try:
        # Compute cutoff — respect non-destructive reset timestamp
        cutoff = datetime.utcnow() - timedelta(days=days)
        try:
            reset_row = supabase.table("agent_config").select("value").eq("key", "andrew_stats_reset_at").execute()
            if reset_row.data:
                raw = reset_row.data[0]["value"]
                reset_ts = datetime.fromisoformat(json.loads(raw) if raw.startswith('"') else raw)
                # Strip tzinfo for safe comparison with naive utcnow()
                if reset_ts.tzinfo is not None:
                    reset_ts = reset_ts.replace(tzinfo=None)
                if reset_ts > cutoff:
                    cutoff = reset_ts
        except Exception:
            pass  # config key missing — use normal cutoff

        cutoff_iso = cutoff.isoformat()
        all_receipts = supabase.table("pending_receipts") \
            .select("id, status, parsed_data, project_id, created_at") \
            .in_("status", ["ready", "linked", "error", "duplicate", "check_review"]) \
            .gte("created_at", cutoff_iso) \
            .order("created_at", desc=True) \
            .execute()

        data = all_receipts.data or []
        total = len(data)
        linked = sum(1 for r in data if r["status"] == "linked")
        errors = sum(1 for r in data if r["status"] == "error")
        duplicates = sum(1 for r in data if r["status"] == "duplicate")
        ready = sum(1 for r in data if r["status"] == "ready")

        # Count manual reviews and OCR failures
        manual_reviews = 0
        ocr_failures = 0
        error_logs = []

        for r in data:
            parsed = r.get("parsed_data") or {}

            # Check for manual confirmations
            line_items = parsed.get("line_items", [])
            if any(item.get("user_confirmed") for item in line_items):
                manual_reviews += 1

            # Check for OCR failures (missing vendor/amount/date)
            if not parsed.get("vendor_name") or not parsed.get("amount"):
                ocr_failures += 1

            # Collect error logs (last 10 errors)
            if r["status"] == "error" and len(error_logs) < 10:
                error_logs.append({
                    "id": r["id"],
                    "project_id": r.get("project_id"),
                    "created_at": r.get("created_at"),
                    "error_type": "Processing Error",
                    "details": parsed.get("error_message", "Unknown error")
                })

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
            "manual_reviews": manual_reviews,
            "ocr_failures": ocr_failures,
            "error_logs": error_logs,
            "period_days": days,
        }

    except Exception as e:
        logger.error(f"[agent-stats] Error getting agent stats: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error getting agent stats: {str(e)}")


@router.get("/agent-config")
def get_agent_config(current_user: dict = Depends(get_current_user)):
    """Return current agent configuration."""
    try:
        result = supabase.table("agent_config").select("*").execute()
        config = {}
        for row in (result.data or []):
            raw_value = row["value"]
            # Parse JSON values back to their original types
            try:
                config[row["key"]] = json.loads(raw_value) if isinstance(raw_value, str) else raw_value
            except (json.JSONDecodeError, ValueError):
                # If not valid JSON, keep as-is (backward compat for plain strings)
                config[row["key"]] = raw_value
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
def update_agent_config(payload: dict, current_user: dict = Depends(get_current_user)):
    """Update agent configuration (key-value pairs)."""
    try:
        now = datetime.utcnow().isoformat()
        for key, value in payload.items():
            json_val = json.dumps(value) if not isinstance(value, str) else value

            # Check if key exists (SELECT+UPDATE/INSERT pattern to avoid upsert issues)
            existing = supabase.table("agent_config") \
                .select("key") \
                .eq("key", key) \
                .execute()

            if existing.data:
                # Update existing
                supabase.table("agent_config") \
                    .update({"value": json_val, "updated_at": now}) \
                    .eq("key", key) \
                    .execute()
            else:
                # Insert new
                supabase.table("agent_config") \
                    .insert({"key": key, "value": json_val, "updated_at": now}) \
                    .execute()

        return {"ok": True}
    except Exception as e:
        logger.error(f"[agent-config] Error updating agent config: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error updating agent config: {str(e)}")


@router.delete("/agent-metrics")
def clear_agent_metrics(current_user: dict = Depends(get_current_user)):
    """Clear error and duplicate records from Andrew metrics."""
    try:
        # Delete all receipts with status 'error' or 'duplicate'
        # This will reset the error and duplicate counters in agent-stats
        result = supabase.table("pending_receipts") \
            .delete() \
            .in_("status", ["error", "duplicate"]) \
            .execute()

        deleted_count = len(result.data) if result.data else 0
        return {
            "ok": True,
            "message": f"Cleared {deleted_count} error/duplicate records",
            "deleted_count": deleted_count
        }
    except Exception as e:
        logger.error(f"[agent-metrics] Error clearing metrics: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error clearing metrics: {str(e)}")


# ====== NON-DESTRUCTIVE RESET (all agents) ======

_VALID_AGENT_IDS = {"andrew", "daneel", "arturito"}


@router.post("/agents/{agent_id}/reset-stats")
def reset_agent_stats(agent_id: str, current_user: dict = Depends(get_current_user)):
    """
    Non-destructive stats reset: stores a timestamp in agent_config.
    All stat queries use MAX(reset_at, cutoff_date) so data is preserved.
    """
    if agent_id not in _VALID_AGENT_IDS:
        raise HTTPException(status_code=400, detail=f"Invalid agent_id. Must be one of: {', '.join(_VALID_AGENT_IDS)}")

    try:
        config_key = f"{agent_id}_stats_reset_at"
        now = datetime.utcnow().isoformat()
        json_val = json.dumps(now)

        existing = supabase.table("agent_config").select("key").eq("key", config_key).execute()
        if existing.data:
            supabase.table("agent_config") \
                .update({"value": json_val, "updated_at": now}) \
                .eq("key", config_key) \
                .execute()
        else:
            supabase.table("agent_config") \
                .insert({"key": config_key, "value": json_val, "updated_at": now}) \
                .execute()

        return {"ok": True, "agent": agent_id, "reset_at": now}

    except Exception as e:
        logger.error(f"[reset-stats] Error resetting {agent_id} stats: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error resetting stats: {str(e)}")


# ====== VAULT BATCH PROCESSING ======
# NOTE: These static routes MUST be defined before /{receipt_id} dynamic routes

ALLOWED_RECEIPT_MIMES = {
    "image/jpeg", "image/jpg", "image/png", "image/webp", "image/gif",
    "application/pdf",
}


@router.post("/process-from-vault")
async def process_from_vault(
    payload: VaultBatchRequest,
    background_tasks: BackgroundTasks,
    current_user: dict = Depends(get_current_user),
):
    """
    Bridge vault files to Andrew's receipt processing pipeline.
    Creates pending_receipt entries from vault files, then processes each
    in background with tiered OCR (fast-beta -> fast -> heavy).
    """
    from api.services.vault_service import get_download_url

    batch_id = str(uuid.uuid4())
    results = []
    queued_receipt_ids = []

    for vault_file_id in payload.vault_file_ids:
        try:
            # 1. Fetch vault file record
            vf_resp = supabase.table("vault_files") \
                .select("id, name, file_hash, bucket_path, mime_type, size_bytes, is_folder, is_deleted") \
                .eq("id", vault_file_id) \
                .execute()
            vault_file = vf_resp.data[0] if vf_resp.data else None

            if not vault_file:
                results.append({"vault_file_id": vault_file_id, "status": "error", "message": "File not found"})
                continue

            if vault_file.get("is_folder") or vault_file.get("is_deleted"):
                results.append({"vault_file_id": vault_file_id, "status": "skipped", "message": "Folder or deleted"})
                continue

            # 2. Validate mime type
            mime = vault_file.get("mime_type", "")
            if mime not in ALLOWED_RECEIPT_MIMES:
                results.append({"vault_file_id": vault_file_id, "status": "skipped",
                                "message": f"Unsupported type: {mime}"})
                continue

            # 3. Check if already processed
            file_hash = vault_file.get("file_hash")
            if file_hash:
                existing = supabase.table("pending_receipts") \
                    .select("id, status") \
                    .eq("file_hash", file_hash) \
                    .eq("project_id", payload.project_id) \
                    .in_("status", ["processing", "ready", "linked"]) \
                    .execute()
                if existing.data:
                    results.append({"vault_file_id": vault_file_id, "status": "skipped",
                                    "message": f"Already processed ({existing.data[0]['status']})"})
                    continue

            # 4. Get download URL
            download_url = get_download_url(vault_file_id)

            # 5. Create pending_receipt entry
            receipt_id = str(uuid.uuid4())
            receipt_data = {
                "id": receipt_id,
                "project_id": payload.project_id,
                "file_name": vault_file.get("name"),
                "file_url": download_url,
                "file_type": mime,
                "file_size": vault_file.get("size_bytes", 0),
                "file_hash": file_hash,
                "thumbnail_url": f"{download_url}?width=200&height=200&resize=contain" if mime.startswith("image/") else None,
                "status": "pending",
                "uploaded_by": current_user.get("user_id", current_user.get("uid")),
                "vault_file_id": vault_file_id,
                "batch_id": batch_id,
                "created_at": datetime.utcnow().isoformat(),
                "updated_at": datetime.utcnow().isoformat(),
            }
            supabase.table("pending_receipts").insert(receipt_data).execute()

            queued_receipt_ids.append(receipt_id)
            results.append({"vault_file_id": vault_file_id, "receipt_id": receipt_id, "status": "queued",
                            "file_name": vault_file.get("name")})

        except Exception as e:
            logger.error(f"[VaultBatch] Error queueing vault file {vault_file_id}: {e}")
            results.append({"vault_file_id": vault_file_id, "status": "error", "message": str(e)})

    # Queue background processing
    if queued_receipt_ids:
        background_tasks.add_task(
            _process_vault_batch,
            batch_id,
            queued_receipt_ids,
            payload.project_id,
        )

    return {
        "batch_id": batch_id,
        "total": len(payload.vault_file_ids),
        "queued": len(queued_receipt_ids),
        "results": results,
    }


async def _process_vault_batch(batch_id: str, receipt_ids: list, project_id: str):
    """Process a batch of vault receipts sequentially through Andrew's pipeline."""
    total = len(receipt_ids)
    logger.info(f"[VaultBatch] START batch={batch_id} | {total} receipt(s) | mode=auto")

    post_andrew_message(
        content=f"Processing **{total}** receipt(s) from Vault...",
        project_id=project_id,
        metadata={
            "agent_message": True,
            "vault_batch_id": batch_id,
            "vault_batch_total": total,
            "vault_batch_started": True,
        }
    )

    success_count = 0
    error_count = 0

    for i, receipt_id in enumerate(receipt_ids):
        try:
            logger.info(f"[VaultBatch] Processing {i+1}/{total} | receipt={receipt_id}")
            await _agent_process_receipt_core(receipt_id, scan_mode="auto")
            success_count += 1
        except Exception as e:
            error_count += 1
            logger.error(f"[VaultBatch] Error processing receipt {receipt_id}: {e}")
            try:
                supabase.table("pending_receipts").update({
                    "status": "error",
                    "processing_error": str(e),
                    "updated_at": datetime.utcnow().isoformat(),
                }).eq("id", receipt_id).execute()
            except Exception:
                pass

    # Post batch summary
    summary = f"Vault batch complete: **{success_count}** processed"
    if error_count > 0:
        summary += f", **{error_count}** failed"
    summary += f" ({total} total). Expenses are ready for review in the table."

    post_andrew_message(
        content=summary,
        project_id=project_id,
        metadata={
            "agent_message": True,
            "vault_batch_id": batch_id,
            "vault_batch_complete": True,
            "vault_batch_success": success_count,
            "vault_batch_errors": error_count,
        }
    )

    logger.info(f"[VaultBatch] DONE batch={batch_id} | success={success_count} errors={error_count}")


@router.get("/vault-batch-status")
async def vault_batch_status(
    batch_id: str = Query(...),
    current_user: dict = Depends(get_current_user),
):
    """Get processing status for a vault batch."""
    result = supabase.table("pending_receipts") \
        .select("id, file_name, status, processing_error, vault_file_id, file_hash") \
        .eq("batch_id", batch_id) \
        .execute()

    receipts = result.data or []
    statuses = {"pending": 0, "processing": 0, "ready": 0, "linked": 0, "error": 0}
    for r in receipts:
        s = r.get("status", "pending")
        if s in statuses:
            statuses[s] += 1
        elif s == "check_review":
            statuses["ready"] += 1

    terminal = {"ready", "linked", "error", "rejected", "check_review"}
    complete = all(r.get("status") in terminal for r in receipts) if receipts else False

    return {
        "batch_id": batch_id,
        "total": len(receipts),
        "statuses": statuses,
        "receipts": receipts,
        "complete": complete,
    }


# ====== DYNAMIC RECEIPT ROUTES ======

@router.get("/{receipt_id}")
def get_receipt(receipt_id: str, current_user: dict = Depends(get_current_user)):
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
async def process_receipt(receipt_id: str, current_user: dict = Depends(get_current_user)):
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

            # ===== OCR via shared service (pdfplumber-first, 250 DPI, tax-aware) =====
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
                except Exception as _exc:
                    logger.debug("Suppressed: %s", _exc)

            # file_content no longer needed — free before vendor/categorize/DB phase
            del file_content

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
            # TODO: add project_stage column to projects table for stage-aware categorization
            construction_stage = "General Construction"

            categorizations = []
            cat_metrics = {}
            cat_expenses = [
                {"rowIndex": i, "description": item.get("description", "")}
                for i, item in enumerate(line_items)
            ]
            _cfg = _load_agent_config()
            _min_conf = int(_cfg.get("min_confidence", 60))
            try:
                if cat_expenses:
                    cat_result = await asyncio.to_thread(
                        _auto_categorize_core,
                        stage=construction_stage,
                        expenses=cat_expenses,
                        project_id=project_id,
                        receipt_id=receipt_id,
                        min_confidence=_min_conf,
                    )
                    categorizations = cat_result.get("categorizations", [])
                    cat_metrics = cat_result.get("metrics", {})
            except Exception as cat_err:
                logger.error(f"[Categorization] Error: {cat_err}")
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

            # ===== Resolve txn_type_id and payment_method_id from OCR =====
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
            # Try multiple variations to handle different DB naming conventions
            default_payment_id = (
                payment_map.get("debit") or
                payment_map.get("debit card") or
                payment_map.get("ach debit") or
                payment_map.get("bank account")
            )

            # If still no default found, use first available payment method as last resort
            if not default_payment_id and payment_resp.data:
                default_payment_id = payment_resp.data[0].get("id")
                logger.warning(f"[WARNING] No 'debit' payment method found, using fallback: {payment_resp.data[0].get('payment_method_name')}")

            ocr_txn_type = first_item.get("transaction_type", "Unknown")
            resolved_txn_type_id = txn_types_map.get(ocr_txn_type.lower()) if ocr_txn_type and ocr_txn_type != "Unknown" else None
            txn_type_id = resolved_txn_type_id or default_txn_type_id

            ocr_payment = first_item.get("payment_method", "Unknown")
            resolved_payment_id = payment_map.get(ocr_payment.lower()) if ocr_payment and ocr_payment != "Unknown" else None
            payment_method_id = resolved_payment_id or default_payment_id

            logger.info(f"[Receipt] Resolved payment: OCR='{ocr_payment}' -> ID={payment_method_id} (default={default_payment_id})")

            # ===== Build parsed_data and update DB =====
            parsed_data = {
                "vendor_name": vendor_name,
                "vendor_id": vendor_id,
                "amount": amount,
                "receipt_date": receipt_date,
                "description": description,
                "line_items": line_items,
                "validation": validation,
                "txn_type_id": txn_type_id,
                "payment_method_id": payment_method_id,
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
        logger.error(f"[Agent] File hash duplicate check error: {e}")
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
        logger.error(f"[Agent] Data duplicate check error: {e}")
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
        logger.error(f"[Agent] Expense duplicate check error: {e}")
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
        logger.error(f"[Agent] Split reuse check error: {e}")
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
        logger.error(f"[Agent] Error fetching bookkeeping mentions: {e}")
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
        logger.error(f"[Agent] Error fetching auth notify mentions: {e}")
        return ""


def _get_authorization_message():
    """
    Get authorization notification message based on Daneel status.

    If Daneel auto-auth is enabled: mention Daneel (without @) so humans know it's automated.
    If Daneel is disabled: @mention human users configured in andrew_auth_notify_users.

    Returns:
        str: Message to append to expense creation notification
    """
    try:
        # Check if Daneel auto-auth is enabled
        daneel_cfg = supabase.table("agent_config") \
            .select("value") \
            .eq("key", "daneel_auto_auth_enabled") \
            .execute()

        daneel_enabled = False
        if daneel_cfg.data:
            val = daneel_cfg.data[0].get("value")
            if isinstance(val, str):
                daneel_enabled = val.lower() == "true"
            else:
                daneel_enabled = bool(val)

        if daneel_enabled:
            # Daneel is active - mention it (without @) so humans know auto-auth is running
            return "\n\nDaneel will review this automatically."
        else:
            # Daneel is off - notify human reviewers with @mentions
            auth_mentions = _get_auth_notify_mentions()
            if auth_mentions:
                return f"\n\n{auth_mentions}"
            else:
                return ""
    except Exception as e:
        logger.error(f"[Agent] Error getting authorization message: {e}")
        # Fallback to human mentions if error
        auth_mentions = _get_auth_notify_mentions()
        return f"\n\n{auth_mentions}" if auth_mentions else ""


def _schedule_daneel_auto_auth(expense_id: str, project_id: str):
    """Schedule Daneel auto-auth check for a newly created expense (non-blocking)."""
    try:
        from api.services.daneel_auto_auth import trigger_auto_auth_check
        loop = asyncio.get_running_loop()
        loop.create_task(trigger_auto_auth_check(str(expense_id), str(project_id)))
    except RuntimeError:
        pass  # No running event loop (sync endpoint) - skip
    except Exception as e:
        logger.error(f"[DaneelTrigger] Failed to schedule auto-auth: {e}")


def _schedule_daneel_auto_auth_for_bill(expense_ids: list, bill_id: str, project_id: str,
                                         vendor_name: str = "", total_amount: float = 0.0):
    """Schedule bill-level Daneel auto-auth check (non-blocking)."""
    try:
        from api.services.daneel_auto_auth import trigger_auto_auth_for_bill
        loop = asyncio.get_running_loop()
        loop.create_task(trigger_auto_auth_for_bill(
            expense_ids=[str(eid) for eid in expense_ids],
            bill_id=str(bill_id),
            project_id=str(project_id),
            vendor_name=vendor_name,
            total_amount=total_amount,
        ))
        logger.info(f"[DaneelTrigger] Scheduled bill-level auth | bill={bill_id} | {len(expense_ids)} expenses")
    except RuntimeError:
        pass  # No running event loop
    except Exception as e:
        logger.error(f"[DaneelTrigger] Failed to schedule bill-level auto-auth: {e}")


def _trigger_bill_or_per_expense_auth(created_expenses: list, project_id: str, vendor_name: str = ""):
    """Group expenses by bill_id. Bill groups use bill-level auth, no-bill uses per-expense fallback."""
    bill_groups = {}
    no_bill = []
    for exp in created_expenses:
        bid = (exp.get("bill_id") or "").strip()
        if bid:
            bill_groups.setdefault(bid, []).append(exp)
        else:
            no_bill.append(exp)

    n_bill = sum(len(g) for g in bill_groups.values())
    n_per = len(no_bill)
    logger.info(f"[DaneelTrigger] Grouped {len(created_expenses)} expenses: {n_bill} bill-level ({len(bill_groups)} bills) + {n_per} per-expense")

    for bid, group in bill_groups.items():
        _schedule_daneel_auto_auth_for_bill(
            expense_ids=[e.get("expense_id") for e in group if e.get("expense_id")],
            bill_id=bid,
            project_id=project_id,
            vendor_name=vendor_name,
            total_amount=sum(float(e.get("Amount") or 0) for e in group),
        )
    for exp in no_bill:
        eid = exp.get("expense_id")
        if eid:
            _schedule_daneel_auto_auth(eid, project_id)


def _create_receipt_expense(project_id, parsed_data, receipt_data, vendor_id, account_id,
                            amount=None, description=None, bill_id=None, txn_date=None,
                            txn_type_id=None, payment_method_id=None, skip_auto_auth=False):
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
            expense_data["txn_type"] = final_txn_type
        if final_payment:
            expense_data["payment_type"] = final_payment
        expense_data = {k: v for k, v in expense_data.items() if v is not None}

        result = supabase.table("expenses_manual_COGS").insert(expense_data).execute()
        if result.data:
            exp = result.data[0]
            if not skip_auto_auth:
                _schedule_daneel_auto_auth(exp.get("expense_id"), exp.get("project") or project_id)
            return exp
        return None
    except Exception as e:
        logger.error(f"[Agent] Expense creation error: {e}")
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
        logger.error(f"[ReceiptFlow] Project lookup error: {e}")
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
                    logger.info(f"[UserContext] Category hint resolved item {idx} -> {matched_acct['name']}")

            # Check if ALL low-confidence items were resolved
            unresolved = [
                lci for lci in low_confidence_items
                if lci["index"] not in result["resolved_items"]
            ]
            if not unresolved:
                result["skip_categories"] = True
                logger.info(f"[UserContext] All {len(low_confidence_items)} low-confidence items resolved by hints")

    # --- 2. Project decision ---
    if project_decision == "all_this_project":
        result["skip_project_question"] = True
        result["pre_resolved"]["project_decision"] = "all_this_project"
        result["pre_resolved"]["project_id"] = project_id
        result["pre_resolved"]["project_name"] = project_name
        logger.info(f"[UserContext] Project decision: all for {project_name}")

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
                    logger.warning(f"[UserContext] Could not resolve project: '{sp_name}'")

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
            logger.info(f"[UserContext] Split resolved: {len(resolved_splits)} projects")

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

async def _agent_process_receipt_core(receipt_id: str, scan_mode: str = None):
    """
    Core processing pipeline for material receipts.
    Extracted so it can be called from both the endpoint and background batch jobs.

    scan_mode: None = read from agent_config (legacy), "auto" = tiered escalation,
               "fast-beta", "fast", "heavy" = force specific mode.
    """
    receipt_data = None
    project_id = None

    try:
        # ===== STEP 1: Fetch receipt =====
        logger.info(f"[Agent] === START agent-process for receipt {receipt_id} ===")
        receipt = supabase.table("pending_receipts") \
            .select("*") \
            .eq("id", receipt_id) \
            .single() \
            .execute()

        if not receipt.data:
            raise ValueError("Receipt not found")

        receipt_data = receipt.data
        project_id = receipt_data.get("project_id")
        logger.info(f"[Agent] Step 1: Receipt fetched | project={project_id} | file={receipt_data.get('file_name')}")

        if receipt_data.get("status") == "linked":
            raise ValueError("Receipt already linked to an expense")

        # Update status to processing
        supabase.table("pending_receipts") \
            .update({"status": "processing", "updated_at": datetime.utcnow().isoformat()}) \
            .eq("id", receipt_id) \
            .execute()

        # Send immediate confirmation message
        file_name = receipt_data.get("file_name", "receipt")
        file_url = receipt_data.get("file_url", "")
        file_link = f"[{file_name}]({file_url})" if file_url else file_name

        post_andrew_message(
            content=f"Got it! Processing {file_link}...",
            project_id=project_id,
            metadata={
                "agent_message": True,
                "pending_receipt_id": receipt_id,
                "receipt_status": "processing",
                "processing_started": True,
            }
        )
        logger.info(f"[Agent] Sent immediate confirmation message for receipt {receipt_id}")

        # ===== STEP 2: Download file + duplicate check =====
        logger.info("[Agent] Step 2: Downloading file...")
        file_url = receipt_data.get("file_url")
        file_type = receipt_data.get("file_type")
        file_hash = receipt_data.get("file_hash")

        import httpx
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(file_url)
            if resp.status_code != 200:
                raise Exception("Failed to download receipt file from storage")
            file_content = resp.content
        logger.info(f"[Agent] Step 2: File downloaded | size={len(file_content)} bytes | type={file_type}")

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
            logger.info(f"[Agent] Step 2: Split reuse detected | original in project {split_reuse.get('project_id')}")
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
            except Exception as _exc:
                logger.debug("Suppressed: %s", _exc)

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
            logger.info(f"[Agent] Step 2.5: LABOR CHECK auto-detected: {file_name}")

            payroll_channel_id = _get_payroll_channel_id()

            # Extract smart context from user message (if available)
            parsed_data = receipt_data.get("parsed_data") or {}
            user_message = parsed_data.get("user_message", "")
            check_context = {}

            if user_message:
                # Get project name for context
                proj_name = "Unknown"
                try:
                    proj = supabase.table("projects") \
                        .select("project_name").eq("project_id", project_id).single().execute()
                    if proj.data:
                        proj_name = proj.data.get("project_name", "Unknown")
                except Exception as _exc:
                    logger.debug("Suppressed: %s", _exc)

                logger.info(f"[CheckFlow] Extracting context from user message: {user_message[:100]}")
                from api.services.agent_brain import _extract_check_context
                check_context = await _extract_check_context(user_message, proj_name) or {}
                logger.info(f"[CheckFlow] Extracted context: {json.dumps(check_context, ensure_ascii=False)[:200]}")

            # Auto-create entries from context
            auto_entries = []
            is_split_detected = False
            initial_state = "awaiting_check_number"

            if check_context:
                workers_list = check_context.get("workers_list") or []
                split_projects = check_context.get("split_projects") or []
                project_decision = check_context.get("project_decision")
                labor_type_hint = check_context.get("labor_type_hint")
                check_number_hint = check_context.get("check_number_hint")

                # Helper: resolve project name to project_id
                def resolve_project(project_name: str) -> Optional[str]:
                    if not project_name:
                        return None
                    try:
                        # Try exact match first
                        result = supabase.table("projects") \
                            .select("project_id").eq("project_name", project_name).execute()
                        if result.data:
                            return result.data[0]["project_id"]

                        # Try fuzzy match (ILIKE)
                        result = supabase.table("projects") \
                            .select("project_id, project_name") \
                            .ilike("project_name", f"%{project_name}%") \
                            .execute()
                        if result.data:
                            return result.data[0]["project_id"]
                    except Exception as e:
                        logger.error(f"[CheckFlow] Project resolution error for '{project_name}': {e}")
                    return None

                # Auto-create entries from workers_list
                if workers_list:
                    logger.info(f"[CheckFlow] Auto-creating {len(workers_list)} entries from workers_list")
                    for idx, worker in enumerate(workers_list):
                        description = worker.get("description", "")
                        amount = worker.get("amount")
                        worker_project = worker.get("project", "")

                        # Resolve project
                        entry_project_id = None
                        if worker_project:
                            entry_project_id = resolve_project(worker_project)

                        # Fallback to origin project if no specific project
                        if not entry_project_id:
                            entry_project_id = project_id

                        if description and amount:
                            auto_entries.append({
                                "index": idx,
                                "description": description,
                                "amount": amount,
                                "project_id": entry_project_id,
                                "account_id": None,
                                "account_name": None,
                                "confidence": None,
                                "reasoning": None,
                                "labor_type_suggestion": labor_type_hint,  # Save for categorization
                            })

                            # Detect split if worker has different project
                            if entry_project_id != project_id:
                                is_split_detected = True

                # Auto-create entries from split_projects (when no workers_list)
                elif split_projects and len(split_projects) > 1:
                    logger.info(f"[CheckFlow] Auto-creating {len(split_projects)} entries from split_projects")
                    is_split_detected = True
                    for idx, proj_name in enumerate(split_projects):
                        entry_project_id = resolve_project(proj_name)
                        if not entry_project_id:
                            entry_project_id = project_id

                        auto_entries.append({
                            "index": idx,
                            "description": labor_type_hint or "Labor",
                            "amount": None,  # User will need to fill
                            "project_id": entry_project_id,
                            "account_id": None,
                            "account_name": None,
                            "confidence": None,
                            "reasoning": None,
                            "labor_type_suggestion": labor_type_hint,
                        })

                # Determine initial state based on context
                if check_number_hint:
                    if auto_entries:
                        # We have check number AND entries -> skip to entry review
                        initial_state = "awaiting_entry_confirmation"
                    else:
                        # We have check number but no entries -> ask split decision
                        initial_state = "awaiting_split_decision"
                else:
                    # No check number -> start from beginning
                    initial_state = "awaiting_check_number"

            check_flow = {
                "state": initial_state,
                "detected_at": datetime.utcnow().isoformat(),
                "check_type": "labor",
                "channel_id": payroll_channel_id,
                "origin_project_id": project_id,
                "check_number": check_context.get("check_number_hint"),
                "is_split": is_split_detected if is_split_detected else None,
                "entries": auto_entries,
                "date": check_context.get("date_hint"),
                "context": check_context,  # Store extracted context
            }

            # Get project name for context
            proj_name = "Unknown"
            try:
                proj = supabase.table("projects") \
                    .select("project_name").eq("project_id", project_id).single().execute()
                if proj.data:
                    proj_name = proj.data.get("project_name", "Unknown")
            except Exception as _exc:
                logger.debug("Suppressed: %s", _exc)

            file_url = receipt_data.get("file_url", "")
            file_link = f"[{file_name}]({file_url})" if file_url else file_name

            # Check if we can auto-resolve check number from context
            check_number_hint = check_context.get("check_number_hint")
            context_summary = []

            if check_number_hint:
                check_flow["check_number"] = check_number_hint
                check_flow["state"] = "awaiting_split_decision"
                context_summary.append(f"Check #{check_number_hint}")
                logger.info(f"[CheckFlow] Auto-resolved check number from context: {check_number_hint}")

            if check_context.get("labor_type_hint"):
                context_summary.append(f"Labor: {check_context['labor_type_hint']}")

            if check_context.get("project_decision") == "all_this_project":
                context_summary.append(f"All for {proj_name}")
            elif check_context.get("split_projects"):
                projects = check_context["split_projects"]
                if len(projects) > 1:
                    context_summary.append(f"Split: {len(projects)} projects")

            context_note = " (" + ", ".join(context_summary) + ")" if context_summary else ""

            # Save check_flow to database
            supabase.table("pending_receipts").update({
                "status": "check_review",
                "parsed_data": {"check_flow": check_flow},
                "updated_at": datetime.utcnow().isoformat()
            }).eq("id", receipt_id).execute()

            if payroll_channel_id:
                # Redirect message in receipts channel
                post_andrew_message(
                    content=(
                        f"Labor check detected{context_note}: {file_link}. Continuing in **Payroll**.\n\n"
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

                # Ask check number, split decision, or show entry confirmation based on context
                if initial_state == "awaiting_entry_confirmation":
                    # We have check number AND entries -> show summary for confirmation
                    entries_summary = "\n".join([
                        f"- {e['description']}: ${e['amount']:,.2f}" if e.get('amount') else f"- {e['description']}: (amount needed)"
                        for e in auto_entries
                    ])
                    post_andrew_message(
                        content=(
                            f"Labor check #{check_number_hint} from **{proj_name}**: {file_link}\n\n"
                            f"I detected the following entries from your message:\n{entries_summary}\n\n"
                            "Reply **'confirm'** to proceed with categorization or **'edit'** to modify."
                        ),
                        channel_id=payroll_channel_id,
                        channel_type="group",
                        metadata={
                            "agent_message": True,
                            "pending_receipt_id": receipt_id,
                            "receipt_status": "check_review",
                            "check_flow_state": "awaiting_entry_confirmation",
                            "check_flow_active": True,
                            "awaiting_text_input": True,
                            "origin_project_id": project_id,
                        }
                    )
                elif check_number_hint:
                    # Skip to split decision
                    post_andrew_message(
                        content=(
                            f"Labor check #{check_number_hint} from project **{proj_name}**: {file_link}\n\n"
                            f"Is this check for multiple projects or all for **{proj_name}**?"
                        ),
                        channel_id=payroll_channel_id,
                        channel_type="group",
                        metadata={
                            "agent_message": True,
                            "pending_receipt_id": receipt_id,
                            "receipt_status": "check_review",
                            "check_flow_state": "awaiting_split_decision",
                            "check_flow_active": True,
                            "origin_project_id": project_id,
                        }
                    )
                else:
                    # Ask check number
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
                if initial_state == "awaiting_entry_confirmation":
                    entries_summary = "\n".join([
                        f"- {e['description']}: ${e['amount']:,.2f}" if e.get('amount') else f"- {e['description']}: (amount needed)"
                        for e in auto_entries
                    ])
                    post_andrew_message(
                        content=(
                            f"Labor check #{check_number_hint} detected{context_note}: {file_link}\n\n"
                            f"I detected the following entries:\n{entries_summary}\n\n"
                            "Reply **'confirm'** to proceed or **'edit'** to modify."
                        ),
                        project_id=project_id,
                        metadata={
                            "agent_message": True,
                            "pending_receipt_id": receipt_id,
                            "receipt_status": "check_review",
                            "check_flow_state": "awaiting_entry_confirmation",
                            "check_flow_active": True,
                            "awaiting_text_input": True,
                        }
                    )
                elif check_number_hint:
                    post_andrew_message(
                        content=(
                            f"Labor check #{check_number_hint} detected{context_note}: {file_link}\n\n"
                            f"Is this check for multiple projects or all for **{proj_name}**?"
                        ),
                        project_id=project_id,
                        metadata={
                            "agent_message": True,
                            "pending_receipt_id": receipt_id,
                            "receipt_status": "check_review",
                            "check_flow_state": "awaiting_split_decision",
                            "check_flow_active": True,
                        }
                    )
                else:
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
                "message": f"Labor check auto-detected{context_note}, routed to Payroll",
            }

        # ===== STEP 3: OCR Extraction (Shared Service) =====
        logger.info("[Agent] Step 2: No duplicate found, proceeding to OCR")
        is_image_file = (file_type or "").startswith("image/")

        # Determine scan mode based on parameter or config
        if scan_mode == "auto":
            # Tiered: images -> heavy (vision required), PDFs -> fast-beta first
            _scan_mode = "heavy" if is_image_file else "fast-beta"
        elif scan_mode in ("fast-beta", "fast", "heavy"):
            _scan_mode = scan_mode
        else:
            # Legacy: read from agent_config (default: heavy)
            _scan_mode = "heavy"
            try:
                _mode_row = supabase.table("agent_config").select("value").eq("key", "andrew_scan_mode").execute()
                if _mode_row.data and _mode_row.data[0].get("value") in ("fast", "fast-beta", "heavy"):
                    _scan_mode = _mode_row.data[0]["value"]
            except Exception as _exc:
                logger.debug("Suppressed: %s", _exc)

        logger.info(f"[Agent] Step 3: Starting OCR extraction (mode={_scan_mode}, requested={scan_mode})...")

        # Tiered OCR with error-based escalation
        try:
            scan_result = _scan_receipt_core(file_content, file_type, model=_scan_mode)
        except ValueError as e:
            if _scan_mode == "fast-beta":
                logger.warning(f"[Agent] Step 3: fast-beta failed ({e}), escalating to fast...")
                try:
                    scan_result = _scan_receipt_core(file_content, file_type, model="fast")
                    _scan_mode = "fast"
                except ValueError as e2:
                    logger.warning(f"[Agent] Step 3: fast failed ({e2}), escalating to heavy...")
                    scan_result = _scan_receipt_core(file_content, file_type, model="heavy")
                    _scan_mode = "heavy"
            elif _scan_mode == "fast":
                logger.warning(f"[Agent] Step 3: fast failed ({e}), escalating to heavy...")
                scan_result = _scan_receipt_core(file_content, file_type, model="heavy")
                _scan_mode = "heavy"
            else:
                raise ValueError(f"OCR extraction failed: {str(e)}")
        except RuntimeError as e:
            raise Exception(f"OCR extraction failed: {str(e)}")

        # Confidence-based escalation: if fast-beta/fast returned no items or failed validation
        if scan_mode == "auto" and _scan_mode in ("fast-beta", "fast"):
            _items_count = len(scan_result.get("expenses", []))
            _val_check = scan_result.get("validation", {})
            if _items_count == 0 or not _val_check.get("validation_passed", True):
                _next = "fast" if _scan_mode == "fast-beta" else "heavy"
                logger.info(f"[Agent] Step 3: Confidence escalation {_scan_mode} -> {_next} (items={_items_count}, valid={_val_check.get('validation_passed')})")
                try:
                    scan_result = _scan_receipt_core(file_content, file_type, model=_next)
                    _scan_mode = _next
                    # Second escalation if still failing at fast
                    if _scan_mode == "fast":
                        _ic2 = len(scan_result.get("expenses", []))
                        _vc2 = scan_result.get("validation", {})
                        if _ic2 == 0 or not _vc2.get("validation_passed", True):
                            logger.info("[Agent] Step 3: Second escalation fast -> heavy")
                            scan_result = _scan_receipt_core(file_content, file_type, model="heavy")
                            _scan_mode = "heavy"
                except Exception:
                    if _scan_mode != "heavy":
                        scan_result = _scan_receipt_core(file_content, file_type, model="heavy")
                        _scan_mode = "heavy"

        line_items = scan_result.get("expenses", [])
        validation = scan_result.get("validation", {})

        # ===== STEP 3.5: Validation Correction Pass =====
        # If OCR totals don't match invoice total, run a focused correction pass
        validation_unresolved = False
        if not validation.get("validation_passed", True) and line_items:
            inv_total = validation.get("invoice_total", 0)
            calc_sum = validation.get("calculated_sum", 0)
            logger.warning(f"[Agent] Step 3.5: Validation FAILED | invoice={inv_total} vs calculated={calc_sum} | Triggering correction pass...")
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
                    logger.info(f"[Agent] Step 3.5: Correction PASSED | corrections: {corrections}")
                else:
                    # Correction still failed - flag for bookkeeping escalation
                    validation_unresolved = True
                    new_sum = corrected_validation.get("calculated_sum", calc_sum)
                    logger.warning(f"[Agent] Step 3.5: Correction FAILED | still {new_sum} vs {inv_total} | Will escalate to bookkeeping")
            except Exception as corr_err:
                validation_unresolved = True
                logger.error(f"[Agent] Step 3.5: Correction pass error: {corr_err} | Will escalate to bookkeeping")

        # file_content no longer needed — free before DB/message operations
        try:
            del file_content
        except NameError:
            pass

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
        # Try multiple variations to handle different DB naming conventions
        default_payment_id = (
            payment_map.get("debit") or
            payment_map.get("debit card") or
            payment_map.get("ach debit") or
            payment_map.get("bank account")
        )

        # If still no default found, use first available payment method as last resort
        if not default_payment_id and payment_resp.data:
            default_payment_id = payment_resp.data[0].get("id")
            logger.warning(f"[Agent] No 'debit' payment method found, using fallback: {payment_resp.data[0].get('payment_method_name')}")

        ocr_txn_type = first_item.get("transaction_type", "Unknown")
        resolved_txn_type_id = txn_types_map.get(ocr_txn_type.lower()) if ocr_txn_type and ocr_txn_type != "Unknown" else None
        txn_type_id = resolved_txn_type_id or default_txn_type_id

        ocr_payment = first_item.get("payment_method", "Unknown")
        resolved_payment_id = payment_map.get(ocr_payment.lower()) if ocr_payment and ocr_payment != "Unknown" else None
        payment_method_id = resolved_payment_id or default_payment_id

        logger.info(f"[Agent] Step 3: Resolved txn_type_id={txn_type_id} (ocr={ocr_txn_type}), payment_method_id={payment_method_id} (ocr={ocr_payment}, default={default_payment_id})")

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
        logger.info(f"[Agent] Step 3: OCR complete | vendor={vendor_name} | amount={amount} | date={receipt_date} | {len(line_items)} line item(s)")

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
            logger.info(f"[Agent] Step 3.7: Filename hint | hints={bill_hint} | mismatches={hint_validation.get('mismatches', [])}")

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
            logger.info(f"[Agent] Step 3.8: Smart resolved: {list(smart_analysis['resolutions'].keys())}")
        if smart_analysis.get("unresolved"):
            logger.info(f"[Agent] Step 3.8: Still missing: {smart_analysis['unresolved']}")
        if smart_analysis.get("attempts"):
            for attempt in smart_analysis["attempts"]:
                logger.info(f"[Agent] Step 3.8:   {attempt}")

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
        logger.info("[Agent] Step 5: Starting auto-categorization (shared service)...")
        # TODO: add project_stage column to projects table for stage-aware categorization
        construction_stage = "General Construction"

        # Categorize each line item via shared service
        cat_expenses = [
            {"rowIndex": i, "description": item.get("description", "")}
            for i, item in enumerate(line_items)
        ]

        # Load agent config BEFORE categorization so GPT fallback uses DB threshold
        agent_cfg = _load_agent_config()
        _min_conf = int(agent_cfg.get("min_confidence", 60))

        categorizations = []
        cat_metrics = {}
        try:
            if cat_expenses:
                cat_result = await asyncio.to_thread(
                    _auto_categorize_core,
                    stage=construction_stage,
                    expenses=cat_expenses,
                    project_id=project_id,
                    receipt_id=receipt_id,
                    min_confidence=_min_conf,
                )
                categorizations = cat_result.get("categorizations", [])
                cat_metrics = cat_result.get("metrics", {})
        except Exception as cat_err:
            logger.error(f"[Agent] Auto-categorization failed: {cat_err}")

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
        logger.info(f"[Agent] Step 5: Categorization complete | category={final_category} | confidence={final_confidence}% | {len(categorizations)} item(s) categorized")

        # Add categorizer warnings
        if categorize_data.get("warning"):
            warnings.append(categorize_data["warning"])

        if final_confidence < 70:
            warnings.append("Low categorization confidence - manual review recommended")

        # ===== STEP 5.5: Detect low-confidence categories =====
        # NOTE: This threshold check is for BULK PROCESSING only.
        # Manual confirmations (when user approves via @Andrew) ALWAYS bypass this check
        # by setting confidence=100 and user_confirmed=true.
        # agent_cfg already loaded before categorization (Step 5)

        # Check if threshold enforcement is enabled (default: false to avoid blocking)
        enforce_threshold = agent_cfg.get("enforce_confidence_threshold", "false")
        enforce_threshold = enforce_threshold in ("true", "True", True, "1", 1)

        min_confidence = int(agent_cfg.get("min_confidence", 70))

        low_confidence_items = []

        # Only enforce threshold if the switch is ON
        if enforce_threshold:
            for i, item in enumerate(line_items):
                item_conf = item.get("confidence", 0)
                # ALWAYS skip items that were manually confirmed by user
                if item.get("user_confirmed"):
                    continue
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
        else:
            # Threshold enforcement is OFF - only flag items WITHOUT account_id (unless manually confirmed)
            for i, item in enumerate(line_items):
                # Skip items that were manually confirmed by user
                if item.get("user_confirmed"):
                    continue
                if not item.get("account_id"):
                    low_confidence_items.append({
                        "index": i,
                        "description": item.get("description", ""),
                        "amount": item.get("amount"),
                        "suggested": None,
                        "suggested_account_id": None,
                        "confidence": 0,
                    })

        if low_confidence_items:
            logger.info(f"[Agent] Step 5.5: {len(low_confidence_items)} item(s) with low confidence - will ask user (enforce_threshold={enforce_threshold})")

        # ===== STEP 6: Update receipt with enriched data =====
        if warnings:
            logger.info(f"[Agent] Step 5: Warnings: {warnings}")

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

        logger.info("[Agent] Step 6: Receipt updated in DB | status=ready")

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
                f"\n\nI need your help with these items:\n{cat_list}\n\n"
                "Please review and confirm the suggested accounts, or select different ones from the dropdowns below."
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

        logger.info(f"[Agent] Step 7: Andrew smart message posted | state={flow_state} | unresolved={smart_analysis.get('unresolved', [])}")

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
            logger.info(f"[Agent] Step 7.5: Bookkeeping escalation posted | diff=${difference}")

        logger.info(f"[Agent] === DONE OCR receipt {receipt_id} | {parsed_data.get('vendor_name')} ${parsed_data.get('amount')} -> {final_category} ({final_confidence}%) | awaiting user response ===")

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
        logger.error(f"[Agent] === ERROR receipt {receipt_id} | {str(e)} ===")
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


@router.post("/{receipt_id}/agent-process")
async def agent_process_receipt(receipt_id: str, current_user: dict = Depends(get_current_user)):
    """Endpoint wrapper for agent processing (provides auth via Depends)."""
    return await _agent_process_receipt_core(receipt_id)


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
        logger.error(f"[CheckFlow] Vendor lookup error: {e}")
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
    except Exception as _exc:
        logger.debug("Suppressed: %s", _exc)
    return fallback_project_id, project_name


def _get_check_payment_uuid() -> Optional[str]:
    """Look up the UUID for 'Check' in paymet_methods table."""
    try:
        resp = supabase.table("paymet_methods") \
            .select("id").eq("payment_method_name", "Check").limit(1).execute()
        if resp.data:
            return str(resp.data[0]["id"])
    except Exception as e:
        logger.error(f"[CheckFlow] Error looking up Check payment method: {e}")
    return None


def _get_purchase_txn_type_id() -> Optional[str]:
    """Look up the UUID for 'Purchase' in txn_types table."""
    try:
        resp = supabase.table("txn_types") \
            .select("TnxType_id").eq("TnxType_name", "Purchase").limit(1).execute()
        if resp.data:
            return str(resp.data[0]["TnxType_id"])
    except Exception as e:
        logger.error(f"[CheckFlow] Error looking up Purchase txn type: {e}")
    return None


# ================================================================
# CHECK CONTEXT EXTRACTION - SMART CHECK FLOW
# ================================================================


# ================================================================
# LABOR CATEGORIZATION - CACHE, FEEDBACK LOOP, AND METRICS
# ================================================================

def _generate_labor_description_hash(description: str) -> str:
    """Generate MD5 hash of normalized description for labor cache lookups."""
    normalized = description.lower().strip()
    return hashlib.md5(normalized.encode('utf-8')).hexdigest()


def _get_cached_labor_categorization(description: str, stage: str) -> Optional[dict]:
    """
    Lookup labor categorization in cache.
    Returns cached result if found and < 30 days old, else None.
    """
    try:
        desc_hash = _generate_labor_description_hash(description)
        result = supabase.table("labor_categorization_cache") \
            .select("account_id, account_name, confidence, reasoning, cache_id") \
            .eq("description_hash", desc_hash) \
            .eq("construction_stage", stage) \
            .gte("created_at", (datetime.utcnow() - timedelta(days=30)).isoformat()) \
            .order("created_at", desc=True) \
            .limit(1) \
            .execute()

        if result.data and len(result.data) > 0:
            cache_entry = result.data[0]
            # Update hit count and last_used_at
            supabase.table("labor_categorization_cache").update({
                "hit_count": cache_entry.get("hit_count", 1) + 1,
                "last_used_at": datetime.utcnow().isoformat()
            }).eq("cache_id", cache_entry["cache_id"]).execute()

            return {
                "account_id": cache_entry["account_id"],
                "account_name": cache_entry["account_name"],
                "confidence": cache_entry["confidence"],
                "reasoning": cache_entry.get("reasoning"),
                "from_cache": True
            }
    except Exception as e:
        logger.error(f"[LaborCache] Lookup error: {e}")

    return None


def _save_to_labor_cache(description: str, stage: str, categorization: dict):
    """Save a labor categorization result to cache."""
    try:
        desc_hash = _generate_labor_description_hash(description)
        supabase.table("labor_categorization_cache").insert({
            "description_hash": desc_hash,
            "description_raw": description,
            "construction_stage": stage,
            "account_id": categorization["account_id"],
            "account_name": categorization["account_name"],
            "confidence": categorization["confidence"],
            "reasoning": categorization.get("reasoning"),
        }).execute()
    except Exception as e:
        logger.error(f"[LaborCache] Save error: {e}")


def _get_recent_labor_corrections(project_id: Optional[str], stage: str, limit: int = 5) -> list:
    """
    Fetch recent user corrections for labor in this project/stage to use as GPT context.
    Returns list of correction examples.
    """
    if not project_id:
        return []

    try:
        result = supabase.rpc("get_recent_labor_corrections", {
            "p_project_id": project_id,
            "p_stage": stage,
            "p_limit": limit
        }).execute()

        return result.data or []
    except Exception as e:
        logger.error(f"[LaborFeedback] Correction fetch error: {e}")
        return []


def _save_labor_categorization_metrics(
    project_id: Optional[str],
    check_id: Optional[str],
    stage: str,
    categorizations: list,
    metrics: dict
):
    """Save labor categorization metrics to database for analytics."""
    if not categorizations:
        return

    try:
        # Calculate confidence distribution
        confidences = [c.get("confidence", 0) for c in categorizations]
        avg_conf = sum(confidences) / len(confidences) if confidences else 0
        min_conf = min(confidences) if confidences else 0
        max_conf = max(confidences) if confidences else 0

        below_70 = len([c for c in confidences if c < 70])
        below_60 = len([c for c in confidences if c < 60])
        below_50 = len([c for c in confidences if c < 50])

        supabase.table("labor_categorization_metrics").insert({
            "project_id": project_id,
            "check_id": check_id,
            "construction_stage": stage,
            "total_workers": metrics.get("total_items", len(categorizations)),
            "avg_confidence": round(avg_conf, 2),
            "min_confidence": min_conf,
            "max_confidence": max_conf,
            "items_below_70": below_70,
            "items_below_60": below_60,
            "items_below_50": below_50,
            "cache_hits": metrics.get("cache_hits", 0),
            "cache_misses": metrics.get("cache_misses", 0),
            "gpt_tokens_used": metrics.get("tokens_used", 0),
            "processing_time_ms": metrics.get("processing_time_ms", 0),
        }).execute()
    except Exception as e:
        logger.error(f"[LaborMetrics] Save error: {e}")


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
        logger.error(f"[CheckFlow] Error fetching labor accounts: {e}")
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


def _gpt_categorize_labor(
    description: str,
    labor_accounts: list,
    construction_stage: str = "General",
    corrections_context: str = "",
    min_confidence: int = 70
) -> dict:
    """
    Enhanced GPT categorization for labor with examples and feedback loop.

    Args:
        description: Labor description to categorize
        labor_accounts: List of available labor accounts
        construction_stage: Current construction stage
        corrections_context: Recent user corrections for feedback loop

    Returns:
        {
            "account_id": str,
            "account_name": str,
            "confidence": int,
            "reasoning": str
        }
    """
    try:
        # Build enhanced prompt with examples and feedback
        prompt = f"""You are an expert construction accountant specializing in labor categorization.

CONSTRUCTION STAGE: {construction_stage}

AVAILABLE LABOR ACCOUNTS:
{json.dumps(labor_accounts, indent=2)}

DESCRIPTION: "{description}"

EXAMPLES OF GOOD CATEGORIZATIONS:

Example 1:
- Description: "Drywall installation crew"
- Stage: "Drywall"
- Best Match: "Drywall Labor"
- Confidence: 98
- Reasoning: "Direct stage match for drywall work crew"

Example 2:
- Description: "Framing carpenter for walls"
- Stage: "Framing"
- Best Match: "Framing Labor"
- Confidence: 95
- Reasoning: "Framing stage carpentry work"

Example 3:
- Description: "General labor for cleanup"
- Stage: "Rough Plumbing"
- Best Match: "General Labor"
- Confidence: 85
- Reasoning: "Cleanup work is general labor regardless of stage"

Example 4:
- Description: "Electrician rough-in"
- Stage: "Electrical"
- Best Match: "Electrical Labor"
- Confidence: 97
- Reasoning: "Electrical work during electrical stage"

Example 5:
- Description: "HVAC technician for install"
- Stage: "HVAC"
- Best Match: "HVAC Labor"
- Confidence: 96
- Reasoning: "HVAC installation labor during HVAC stage"{corrections_context}

INSTRUCTIONS:
- Match the description to the most appropriate labor account
- Use construction stage to guide your choice (e.g., "carpenter" in Framing stage → Framing Labor)
- Be stage-aware: same worker type can have different accounts based on stage
- Confidence scale:
  - 90-100: Perfect match (description + stage clearly indicate account)
  - 70-89: Good match (strong indicators, minor ambiguity)
  - 50-69: Uncertain (could fit multiple accounts)
  - 0-49: Low confidence (description too vague or no good match)
- Use exact account_id and account_name from the AVAILABLE LABOR ACCOUNTS list
- Be conservative with confidence - better to under-estimate

Return ONLY valid JSON:
{{
  "account_id": "exact-account-id-from-list",
  "account_name": "exact-account-name-from-list",
  "confidence": 85,
  "reasoning": "Brief explanation"
}}"""

        from api.services.gpt_client import gpt
        raw = gpt.with_fallback(
            "Construction accounting expert. Return only valid JSON.",
            prompt,
            min_confidence=min_confidence,
            max_tokens=400,
        )
        if not raw:
            return {"account_id": None, "account_name": "Uncategorized", "confidence": 0}
        return json.loads(raw)
    except Exception as e:
        logger.error(f"[LaborGPT] Categorization error: {e}")
        return {"account_id": None, "account_name": "Uncategorized", "confidence": 0}


def _categorize_check_items(
    items: list,
    construction_stage: str = "General",
    project_id: Optional[str] = None,
    check_id: Optional[str] = None,
    min_confidence: int = 70
) -> dict:
    """
    Enhanced labor categorization with caching, feedback loop, and metrics.

    Args:
        items: List of labor items with "description" and optionally "amount", "project_name"
        construction_stage: Current construction stage
        project_id: Optional project ID for feedback loop context
        check_id: Optional check ID for metrics tracking

    Returns:
        {
            "items": [...items with added "categorization" key...],
            "metrics": {
                "cache_hits": int,
                "cache_misses": int,
                "fuzzy_matches": int,
                "gpt_calls": int,
                "total_items": int,
                "processing_time_ms": int,
                "tokens_used": int
            }
        }
    """
    start_time = time.time()

    # Metrics tracking
    cache_hits = 0
    cache_misses = 0
    fuzzy_matches = 0
    gpt_calls = 0
    total_tokens = 0

    labor_accounts = _get_labor_accounts()
    items_needing_gpt = []
    categorizations = []

    # Step 1: Try cache first for each item
    for item in items:
        desc = item.get("description", "")
        cached = _get_cached_labor_categorization(desc, construction_stage)

        if cached:
            cache_hits += 1
            item["categorization"] = {
                "account_id": cached["account_id"],
                "account_name": cached["account_name"],
                "confidence": cached["confidence"],
                "reasoning": cached.get("reasoning", "") + " [from cache]",
                "method": "cache_hit"
            }
        else:
            cache_misses += 1
            # Try fuzzy match
            match = _fuzzy_match_labor_account(desc, labor_accounts)
            if match and match["score"] >= 60:
                fuzzy_matches += 1
                item["categorization"] = {
                    "account_id": match["account_id"],
                    "account_name": match["name"],
                    "confidence": match["score"],
                    "method": "fuzzy_match"
                }
                # Save fuzzy match to cache for future use
                _save_to_labor_cache(desc, construction_stage, item["categorization"])
            else:
                # Needs GPT
                items_needing_gpt.append(item)

    # Step 2: If GPT needed, fetch feedback loop corrections
    corrections_context = ""
    if items_needing_gpt and project_id:
        corrections = _get_recent_labor_corrections(project_id, construction_stage, limit=5)
        if corrections:
            corrections_list = []
            for c in corrections:
                corrections_list.append(
                    f"- '{c['description']}' was corrected from "
                    f"'{c['original_account']}' to '{c['corrected_account']}'"
                )
            corrections_context = "\n\nRECENT CORRECTIONS (learn from these):\n" + "\n".join(corrections_list)

    # Step 3: GPT categorization for remaining items
    for item in items_needing_gpt:
        desc = item.get("description", "")
        gpt_result = _gpt_categorize_labor(desc, labor_accounts, construction_stage, corrections_context, min_confidence=min_confidence)
        gpt_calls += 1

        item["categorization"] = {
            "account_id": gpt_result.get("account_id"),
            "account_name": gpt_result.get("account_name", "Uncategorized"),
            "confidence": gpt_result.get("confidence", 0),
            "method": "gpt_fallback",
            "reasoning": gpt_result.get("reasoning")
        }

        # Save GPT result to cache
        if gpt_result.get("account_id"):
            _save_to_labor_cache(desc, construction_stage, item["categorization"])

    # Step 4: Calculate metrics
    elapsed_ms = int((time.time() - start_time) * 1000)

    # Collect all categorizations
    categorizations = [item.get("categorization", {}) for item in items if item.get("categorization")]

    metrics = {
        "cache_hits": cache_hits,
        "cache_misses": cache_misses,
        "fuzzy_matches": fuzzy_matches,
        "gpt_calls": gpt_calls,
        "total_items": len(items),
        "processing_time_ms": elapsed_ms,
        "tokens_used": gpt_calls * 400  # Approximate tokens per GPT call
    }

    # Step 5: Save metrics to database
    if categorizations:
        _save_labor_categorization_metrics(
            project_id=project_id,
            check_id=check_id,
            stage=construction_stage,
            categorizations=categorizations,
            metrics=metrics
        )

    return {
        "items": items,
        "metrics": metrics
    }


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
            expense_data["txn_type"] = purchase_txn_type_id
        if check_payment_uuid:
            expense_data["payment_type"] = check_payment_uuid

        expense_data = {k: v for k, v in expense_data.items() if v is not None}
        try:
            result = supabase.table("expenses_manual_COGS").insert(expense_data).execute()
            if result.data:
                exp = result.data[0]
                created_expenses.append(exp)
                _schedule_daneel_auto_auth(exp.get("expense_id"), exp.get("project") or receipt_data.get("project_id"))
        except Exception as e:
            logger.error(f"[CheckFlow] Error creating expense: {e}")

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
        logger.error(f"[CheckFlow] Error looking up Payroll channel: {e}")
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


def _categorize_and_finish_check_flow(receipt_id, project_id, check_flow, parsed_data):
    """Categorize check entries and transition to review/confirm state."""
    # Get construction stage from origin project
    stage_project_id = check_flow.get("origin_project_id", project_id)
    # TODO: add project_stage column to projects table for stage-aware categorization
    construction_stage = "General Construction"

    # Load agent config BEFORE categorization so GPT fallback uses DB threshold
    agent_cfg = _load_agent_config()
    min_confidence = int(agent_cfg.get("min_confidence", 70))

    # Categorize entries
    entries = check_flow.get("entries", [])
    categorization_result = _categorize_check_items(
        entries,
        construction_stage,
        project_id=stage_project_id,
        check_id=receipt_id,
        min_confidence=min_confidence
    )
    categorized = categorization_result["items"]
    cat_metrics = categorization_result["metrics"]
    check_flow["entries"] = categorized

    # Check confidence vs min_confidence

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

    # Build summary with cache performance info
    perf_note = ""
    if cat_metrics.get("cache_hits", 0) > 0:
        perf_note = f" ⚡ {cat_metrics['cache_hits']}/{cat_metrics['total_items']} from cache"

    lines = []
    for i, entry in enumerate(categorized, 1):
        cat = entry.get("categorization", {})
        cat_name = cat.get("account_name", "Uncategorized")
        cat_conf = cat.get("confidence", 0)
        method = cat.get("method", "")
        if method == "cache_hit":
            method_label = "(cached)"
        elif method == "fuzzy_match":
            method_label = "(fuzzy)"
        else:
            method_label = "(AI)"
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
                f"Check #{check_flow.get('check_number', '?')} categorization:{perf_note}\n\n"
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
                f"Check #{check_flow.get('check_number', '?')} summary:{perf_note}\n\n"
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


def _handle_awaiting_entry_confirmation(receipt_id, project_id, check_flow, parsed_data, action, payload):
    """Handle confirmation or edit of auto-created entries."""
    if action == "submit_entry_confirmation":
        text = (payload or {}).get("text", "").strip().lower()

        if text in ["confirm", "yes", "ok", "confirmar", "si"]:
            # User confirmed - proceed to categorization
            entries = check_flow.get("entries", [])

            # Check if all entries have amounts
            missing_amounts = [e for e in entries if not e.get("amount")]
            if missing_amounts:
                # Ask for missing amounts
                check_flow["state"] = "awaiting_split_entries"
                _update_check_flow(receipt_id, check_flow, parsed_data)

                post_andrew_message(
                    content=(
                        "Some entries are missing amounts. Please provide them:\n\n"
                        "Send each entry as: **[amount] [description] for [project]**\n\n"
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

            # All entries have amounts - check if we have date
            if not check_flow.get("date"):
                # Ask for date before categorization
                check_flow["state"] = "awaiting_date_confirm"
                _update_check_flow(receipt_id, check_flow, parsed_data)

                date_hint = check_flow.get("context", {}).get("date_hint", "")
                date_msg = f"Date hint from message: **{date_hint}**\n\n" if date_hint else ""

                post_andrew_message(
                    content=(
                        f"{date_msg}When was this check dated? (MM/DD/YYYY format)\n\n"
                        "Or type **today** to use today's date."
                    ),
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
                return {"success": True, "state": "awaiting_date_confirm"}

            # All entries have amounts and date - proceed to categorization
            check_flow["state"] = "categorizing"
            _update_check_flow(receipt_id, check_flow, parsed_data)

            # Trigger categorization (same logic as finish_split)
            return _categorize_and_finish_check_flow(receipt_id, project_id, check_flow, parsed_data)

        elif text in ["edit", "modify", "change", "editar", "modificar"]:
            # User wants to edit - switch to manual split entry mode
            check_flow["state"] = "awaiting_split_entries"
            check_flow["entries"] = []  # Clear auto-entries
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

        else:
            # Unknown response
            post_andrew_message(
                content="Reply **'confirm'** to proceed or **'edit'** to modify the entries.",
                **_check_flow_msg_kwargs(project_id, check_flow),
                metadata={
                    "agent_message": True,
                    "pending_receipt_id": receipt_id,
                    "receipt_status": "check_review",
                    "check_flow_state": "awaiting_entry_confirmation",
                    "check_flow_active": True,
                    "awaiting_text_input": True,
                }
            )
            return {"success": True, "state": "awaiting_entry_confirmation", "error": "unknown_response"}

    raise HTTPException(status_code=400, detail=f"Invalid action '{action}' for state 'awaiting_entry_confirmation'")


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
    # TODO: add project_stage column to projects table for stage-aware categorization
    construction_stage = "General Construction"

    # Load agent config BEFORE categorization so GPT fallback uses DB threshold
    agent_cfg = _load_agent_config()
    min_confidence = int(agent_cfg.get("min_confidence", 70))

    entries = check_flow.get("entries", [])
    categorization_result = _categorize_check_items(
        entries,
        construction_stage,
        project_id=stage_project_id,
        check_id=receipt_id,
        min_confidence=min_confidence
    )
    categorized = categorization_result["items"]
    cat_metrics = categorization_result["metrics"]
    check_flow["entries"] = categorized

    # === Check confidence vs agent_config.min_confidence ===

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

    # Build summary lines with cache performance info
    perf_note = ""
    if cat_metrics.get("cache_hits", 0) > 0:
        perf_note = f" ⚡ {cat_metrics['cache_hits']}/{cat_metrics['total_items']} from cache"

    lines = []
    for i, entry in enumerate(categorized, 1):
        cat = entry.get("categorization", {})
        cat_name = cat.get("account_name", "Uncategorized")
        cat_conf = cat.get("confidence", 0)
        method = cat.get("method", "")
        if method == "cache_hit":
            method_label = "(cached)"
        elif method == "fuzzy_match":
            method_label = "(fuzzy)"
        else:
            method_label = "(AI)"
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
                f"Check #{check_flow.get('check_number', '?')} categorization:{perf_note}\n\n"
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
                f"Check #{check_flow.get('check_number', '?')} summary:{perf_note}\n\n"
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
async def check_action(receipt_id: str, payload: CheckActionRequest, current_user: dict = Depends(get_current_user)):
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

        logger.info(f"[CheckFlow] receipt={receipt_id} | state={current_state} | action={action}")

        if current_state == "awaiting_check_number":
            return _handle_awaiting_check_number(receipt_id, project_id, check_flow, parsed_data, action, payload.payload)
        elif current_state == "awaiting_entry_confirmation":
            return _handle_awaiting_entry_confirmation(receipt_id, project_id, check_flow, parsed_data, action, payload.payload)
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
        logger.error(f"[CheckFlow] Error: {e}")
        raise HTTPException(status_code=500, detail=f"Check action error: {str(e)}")


# ====== DUPLICATE-ACTION ENDPOINT ======

@router.post("/{receipt_id}/duplicate-action")
async def duplicate_action(receipt_id: str, payload: DuplicateActionRequest, current_user: dict = Depends(get_current_user)):
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

        logger.info(f"[DuplicateFlow] receipt={receipt_id} | state={current_state} | action={action}")

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
        logger.error(f"[DuplicateFlow] Error: {e}")
        raise HTTPException(status_code=500, detail=f"Duplicate action error: {str(e)}")


@router.post("/{receipt_id}/receipt-action")
async def receipt_action(receipt_id: str, payload: ReceiptActionRequest, current_user: dict = Depends(get_current_user)):
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

        logger.info(f"[ReceiptFlow] receipt={receipt_id} | state={current_state} | action={action}")

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
                logger.info(f"[ReceiptFlow] Category confirmation: user accepted all suggestions ({len(low_items)} items bumped to 100%)")
            elif assignments and isinstance(assignments, list):
                # Structured assignment from interactive account picker (no fuzzy matching needed)

                # TODO: add project_stage column to projects table for stage-aware categorization
                construction_stage = "General"

                updates_applied = 0
                corrections_logged = 0

                for assign in assignments:
                    idx = assign.get("index")
                    account_id = assign.get("account_id")
                    account_name = assign.get("account_name", "")
                    if idx is None or not account_id:
                        continue
                    if idx < len(line_items):
                        # Check if user corrected the suggestion (feedback loop)
                        original_suggestion = next(
                            (item for item in low_items if item.get("index") == idx),
                            None
                        )
                        if original_suggestion:
                            suggested_id = original_suggestion.get("suggested_account_id")
                            suggested_name = original_suggestion.get("suggested_account_name", "")
                            suggested_conf = original_suggestion.get("confidence", 0)
                            description = original_suggestion.get("description", line_items[idx].get("description", ""))

                            # If user selected different account, log as correction for feedback loop
                            if suggested_id and suggested_id != account_id:
                                try:
                                    supabase.table("categorization_corrections").insert({
                                        "project_id": project_id,
                                        "user_id": payload.user_id,
                                        "description": description,
                                        "construction_stage": construction_stage,
                                        "original_account_id": suggested_id,
                                        "original_account_name": suggested_name,
                                        "original_confidence": suggested_conf,
                                        "corrected_account_id": account_id,
                                        "corrected_account_name": account_name,
                                        "correction_reason": "User correction from low-confidence modal"
                                    }).execute()
                                    corrections_logged += 1
                                    logger.info(f"[FeedbackLoop] Logged correction: '{description}' from '{suggested_name}' -> '{account_name}' (stage: {construction_stage})")
                                except Exception as e:
                                    logger.error(f"[FeedbackLoop] Failed to log correction: {e}")

                        # Apply the user's selection
                        line_items[idx]["account_id"] = account_id
                        line_items[idx]["account_name"] = account_name
                        line_items[idx]["confidence"] = 100
                        line_items[idx]["user_confirmed"] = True
                        updates_applied += 1
                        logger.info(f"[ReceiptFlow] Structured assign: item {idx} -> {account_name} ({account_id})")

                if corrections_logged > 0:
                    logger.info(f"[FeedbackLoop] Logged {corrections_logged} user corrections for learning")

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
                        logger.info(f"[ReceiptFlow] Category update: item {idx} -> {matched_acct['name']}")
                    else:
                        logger.warning(f"[ReceiptFlow] Category update: no match for '{acct_name}'")

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

            # Bump top-level categorization confidence so downstream gates don't block
            cat_obj = parsed_data.get("categorization", {})
            if any(it.get("user_confirmed") for it in line_items):
                cat_obj["confidence"] = 100
                parsed_data["categorization"] = cat_obj

            # CRITICAL FIX: Ensure ALL items have account_id before proceeding
            # If user manually confirmed categories, we should use those for items without explicit assignment
            top_level_account = cat_obj.get("account_id")
            for item in line_items:
                if not item.get("account_id") and top_level_account:
                    # Apply top-level category to items that weren't explicitly assigned
                    item["account_id"] = top_level_account
                    item["account_name"] = cat_obj.get("account_name")
                    item["confidence"] = 100  # User reviewed this receipt, so treat as confirmed
                    logger.info(f"[ReceiptFlow] Applied top-level category to item: {item.get('description', '')[:50]}")

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
            except Exception as _exc:
                logger.debug("Suppressed: %s", _exc)

            if pre_resolved.get("project_decision"):
                # User context already answered the project question -- skip to confirmation
                receipt_flow["state"] = "awaiting_user_confirm"
                parsed_data["receipt_flow"] = receipt_flow

                update_result = supabase.table("pending_receipts").update({
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
                logger.info("[ReceiptFlow] Categories confirmed + pre_resolved -> awaiting_user_confirm")
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
            logger.info(f"[ReceiptFlow] Missing info reply interpreted: {interpretation}")

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
                except Exception as _exc:
                    logger.debug("Suppressed: %s", _exc)
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
                except Exception as _exc:
                    logger.debug("Suppressed: %s", _exc)

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
                # Human clicked Confirm -- no confidence gate needed

                # Check auto_create setting
                agent_cfg = _load_agent_config()
                auto_create = agent_cfg.get("auto_create_expense", True)
                if not auto_create:
                    # Still complete the flow but don't create expenses
                    receipt_flow["state"] = "completed"
                    parsed_data["receipt_flow"] = receipt_flow
                    supabase.table("pending_receipts").update({
                        "parsed_data": parsed_data,
                        "updated_at": datetime.utcnow().isoformat()
                    }).eq("id", receipt_id).execute()

                    post_andrew_message(
                        content="Auto-create is disabled in settings. Please create expenses manually from the Expenses tab.",
                        project_id=project_id,
                        metadata={
                            "agent_message": True,
                            "pending_receipt_id": receipt_id,
                            "receipt_status": "ready",
                            "receipt_flow_state": "completed",
                            "receipt_flow_active": False,
                        }
                    )
                    return {"success": True, "state": "completed"}

                decision = pre_resolved.get("project_decision", "all_this_project")
                cat = parsed_data.get("categorization", {})
                vendor_id = parsed_data.get("vendor_id")
                created_expenses = []

                if decision == "all_this_project":
                    # Human clicked Confirm -- no confidence gate needed
                    skipped_items = 0
                    for item in line_items:
                        item_account_id = item.get("account_id") or cat.get("account_id")
                        if not item_account_id:
                            skipped_items += 1
                            continue
                        expense = _create_receipt_expense(
                            project_id, parsed_data, receipt_data,
                            vendor_id, item_account_id,
                            amount=item.get("amount"),
                            description=item.get("description"),
                            bill_id=item.get("bill_id"),
                            txn_date=item.get("date"),
                            skip_auto_auth=True,
                        )
                        if expense:
                            created_expenses.append(expense)
                    if skipped_items:
                        logger.warning(f"[ReceiptFlow] {skipped_items}/{len(line_items)} items skipped (no account_id) for receipt {receipt_id}")

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
                                skip_auto_auth=True,
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
                    msg = f"{count} expense(s) saved -- ready for authorization." if count > 1 else "Expense saved -- ready for authorization."
                    auth_msg = _get_authorization_message()
                    if auth_msg:
                        msg += auth_msg
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
                    logger.info(f"[ReceiptFlow] User confirmed -> {count} expense(s) created")
                    # Trigger bill-level Daneel auth
                    _trigger_bill_or_per_expense_auth(created_expenses, project_id, parsed_data.get("vendor_name", ""))
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
                except Exception as _exc:
                    logger.debug("Suppressed: %s", _exc)

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
            except Exception as _exc:
                logger.debug("Suppressed: %s", _exc)

            if action == "all_this_project":
                # Create expenses for ALL line items in this project
                agent_cfg = _load_agent_config()
                auto_create = agent_cfg.get("auto_create_expense", True)
                cat = parsed_data.get("categorization", {})
                vendor_id = parsed_data.get("vendor_id")

                created_expenses = []
                line_items = parsed_data.get("line_items", [])

                if auto_create and line_items:
                    # Human clicked "All for this project" -- skip confidence gate
                    skipped_items = 0
                    for item in line_items:
                        item_account_id = item.get("account_id") or cat.get("account_id")
                        if not item_account_id:
                            skipped_items += 1
                            continue
                        expense = _create_receipt_expense(
                            project_id, parsed_data, receipt_data,
                            vendor_id, item_account_id,
                            amount=item.get("amount"),
                            description=item.get("description"),
                            bill_id=item.get("bill_id"),
                            txn_date=item.get("date"),
                            skip_auto_auth=True,
                        )
                        if expense:
                            created_expenses.append(expense)
                    if skipped_items:
                        logger.warning(f"[CheckFlow] {skipped_items}/{len(line_items)} items skipped (no account_id) for receipt {receipt_id}")
                elif auto_create and cat.get("account_id"):
                    expense = _create_receipt_expense(
                        project_id, parsed_data, receipt_data,
                        vendor_id, cat["account_id"],
                        skip_auto_auth=True,
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
                    auth_msg = _get_authorization_message()
                    if auth_msg:
                        msg += auth_msg
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
                    logger.info(f"[ReceiptFlow] All items -> {count} expense(s) created | first_id={expense_id}")
                    # Trigger bill-level Daneel auth
                    _trigger_bill_or_per_expense_auth(created_expenses, project_id, parsed_data.get("vendor_name", ""))
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
                    logger.warning("[ReceiptFlow] All items -> skipped expense creation (low confidence)")

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
                agent_cfg = _load_agent_config()
                auto_create = agent_cfg.get("auto_create_expense", True)
                cat = parsed_data.get("categorization", {})
                vendor_id = parsed_data.get("vendor_id")

                all_created = []
                summary_parts = []

                # Create expenses for THIS project (unassigned items)
                # Human assigned items -- skip confidence gate
                if this_project_indices:
                    created_here = []
                    for idx in this_project_indices:
                        item = line_items[idx - 1]
                        item_account = item.get("account_id") or cat.get("account_id")
                        if auto_create and item_account:
                            expense = _create_receipt_expense(
                                project_id, parsed_data, receipt_data,
                                vendor_id, item_account,
                                amount=item.get("amount"),
                                description=item.get("description"),
                                bill_id=item.get("bill_id"),
                                txn_date=item.get("date"),
                                skip_auto_auth=True,
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
                        if auto_create and item_account:
                            expense = _create_receipt_expense(
                                pa["project_id"], parsed_data, receipt_data,
                                vendor_id, item_account,
                                amount=item.get("amount"),
                                description=item.get("description"),
                                bill_id=item.get("bill_id"),
                                txn_date=item.get("date"),
                                skip_auto_auth=True,
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
                        split_auth_msg = _get_authorization_message()
                        if split_auth_msg:
                            split_msg += split_auth_msg

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

                logger.info(f"[ReceiptFlow] Split -> {len(all_created)} expenses across {num_projects} projects")
                # Trigger bill-level Daneel auth per project
                if created_here:
                    _trigger_bill_or_per_expense_auth(created_here, project_id, parsed_data.get("vendor_name", ""))
                for pa in project_assignments:
                    pa_expenses = [e for e in all_created if e.get("project") == pa["project_id"]]
                    if pa_expenses:
                        _trigger_bill_or_per_expense_auth(pa_expenses, pa["project_id"], parsed_data.get("vendor_name", ""))
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
            except Exception as _exc:
                logger.debug("Suppressed: %s", _exc)

            if action == "single_project":
                # Read agent config for confidence check
                agent_cfg = _load_agent_config()
                auto_create = agent_cfg.get("auto_create_expense", True)
                cat = parsed_data.get("categorization", {})
                vendor_id = parsed_data.get("vendor_id")

                # Human clicked "Only this project" -- skip confidence gate entirely
                line_items = parsed_data.get("line_items", [])
                should_create = auto_create

                created_expenses = []

                if should_create and line_items:
                    skipped_items = 0
                    for item in line_items:
                        item_account_id = item.get("account_id") or cat.get("account_id")
                        if not item_account_id:
                            skipped_items += 1
                            continue
                        expense = _create_receipt_expense(
                            project_id, parsed_data, receipt_data,
                            vendor_id, item_account_id,
                            amount=item.get("amount"),
                            description=item.get("description"),
                            bill_id=item.get("bill_id"),
                            txn_date=item.get("date"),
                            skip_auto_auth=True,
                        )
                        if expense:
                            created_expenses.append(expense)
                    if skipped_items:
                        logger.warning(f"[ReceiptFlow] {skipped_items}/{len(line_items)} items skipped (no account_id) for receipt {receipt_id}")

                elif should_create and cat.get("account_id"):
                    # Fallback: no line items (old data), single expense from summary
                    expense = _create_receipt_expense(
                        project_id, parsed_data, receipt_data,
                        vendor_id, cat["account_id"],
                        skip_auto_auth=True,
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
                    auth_msg = _get_authorization_message()
                    if auth_msg:
                        msg += auth_msg
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
                    logger.info(f"[ReceiptFlow] Single project -> {count} expense(s) created | first_id={expense_id}")
                    # Trigger bill-level Daneel auth
                    _trigger_bill_or_per_expense_auth(created_expenses, project_id, parsed_data.get("vendor_name", ""))
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
                    logger.warning(f"[ReceiptFlow] Single project -> skipped expense (confidence={cat.get('confidence', 'N/A')})")

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
                logger.info("[ReceiptFlow] Split selected -> awaiting_split_details")
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
                logger.info(f"[ReceiptFlow] Split item added | ${parsed_line['amount']:.2f} {parsed_line['description']} | total=${total:.2f}")
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
                except Exception as _exc:
                    logger.debug("Suppressed: %s", _exc)

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
                logger.info(f"[ReceiptFlow] Split done | {count} expenses created | total=${total:.2f}")
                return {"success": True, "state": "completed", "expense_ids": [e.get("expense_id") for e in created_expenses]}

            else:
                raise HTTPException(status_code=400, detail=f"Invalid action '{action}' for state '{current_state}'")

        else:
            raise HTTPException(status_code=400, detail=f"Receipt flow in terminal state '{current_state}'")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[ReceiptFlow] Error: {e}")
        raise HTTPException(status_code=500, detail=f"Receipt action error: {str(e)}")


@router.post("/{receipt_id}/create-expense")
def create_expense_from_receipt(receipt_id: str, payload: CreateExpenseFromReceiptRequest, background_tasks: BackgroundTasks, current_user: dict = Depends(get_current_user)):
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

        # Trigger Daneel auto-auth check
        if expense_id and payload.project_id:
            from api.services.daneel_auto_auth import trigger_auto_auth_check
            background_tasks.add_task(trigger_auto_auth_check, expense_id, payload.project_id)

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
def link_receipt_to_expense(receipt_id: str, payload: LinkToExpenseRequest, current_user: dict = Depends(get_current_user)):
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
def update_receipt(receipt_id: str, payload: PendingReceiptUpdate, current_user: dict = Depends(get_current_user)):
    """Update a pending receipt's status or parsed data"""
    try:
        update_data = payload.model_dump(exclude_unset=True)
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
def delete_receipt(receipt_id: str, current_user: dict = Depends(get_current_user)):
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
            logger.warning(f"[PendingReceipts] Could not delete file from storage: {storage_error}")

        # Delete record
        supabase.table("pending_receipts").delete().eq("id", receipt_id).execute()

        return {"success": True, "message": "Receipt deleted"}

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error deleting receipt: {str(e)}")


@router.get("/unprocessed/count")
def get_unprocessed_counts(current_user: dict = Depends(get_current_user)):
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


@router.post("/check-stale")
async def check_stale_receipts_endpoint(current_user: dict = Depends(get_current_user)):
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


# ====== EDIT BILL CATEGORIES ======

async def edit_bill_categories(
    bill_identifier: str,
    material_name: Optional[str] = None,
    project_id: Optional[str] = None,
    user_id: Optional[str] = None,
    channel_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Agent function to review and edit categories for items in an existing bill.
    Shows an interactive card with dropdowns for each expense item.

    Args:
        bill_identifier: Bill number, ID, or receipt reference
        material_name: Optional - filter to specific material
        project_id: Project context
        user_id: User making the request
        channel_id: Channel to post the message in
    """
    try:
        # 1. Try to find the bill (search by number, ID, or receipt_url)
        bill_id = None
        bill_number = None
        receipt_url = None

        # Clean identifier (remove 'Bill', '#', etc)
        clean_id = re.sub(r'(?i)bill\s*#?\s*', '', bill_identifier).strip()

        # Try as UUID first
        if len(clean_id) == 36 and '-' in clean_id:
            bill_result = supabase.table("bills") \
                .select("id, bill_number, receipt_url, project_id") \
                .eq("id", clean_id) \
                .execute()

            if bill_result.data:
                bill_data = bill_result.data[0]
                bill_id = bill_data["id"]
                bill_number = bill_data.get("bill_number")
                receipt_url = bill_data.get("receipt_url")
                if not project_id:
                    project_id = bill_data.get("project_id")

        # Try as bill number
        if not bill_id and clean_id.isdigit():
            bill_result = supabase.table("bills") \
                .select("id, bill_number, receipt_url, project_id") \
                .eq("bill_number", int(clean_id))

            if project_id:
                bill_result = bill_result.eq("project_id", project_id)

            bill_result = bill_result.execute()

            if bill_result.data:
                bill_data = bill_result.data[0]
                bill_id = bill_data["id"]
                bill_number = bill_data.get("bill_number")
                receipt_url = bill_data.get("receipt_url")
                if not project_id:
                    project_id = bill_data.get("project_id")

        # Try by receipt_url hash (in pending_receipts -> bills)
        if not bill_id:
            pending_result = supabase.table("pending_receipts") \
                .select("file_hash") \
                .ilike("file_url", f"%{clean_id}%") \
                .limit(1) \
                .execute()

            if pending_result.data:
                file_hash = pending_result.data[0]["file_hash"]
                bill_result = supabase.table("bills") \
                    .select("id, bill_number, receipt_url, project_id") \
                    .eq("receipt_url", file_hash)

                if project_id:
                    bill_result = bill_result.eq("project_id", project_id)

                bill_result = bill_result.execute()

                if bill_result.data:
                    bill_data = bill_result.data[0]
                    bill_id = bill_data["id"]
                    bill_number = bill_data.get("bill_number")
                    receipt_url = bill_data.get("receipt_url")
                    if not project_id:
                        project_id = bill_data.get("project_id")

        if not bill_id:
            return {
                "status": "error",
                "message": f"Bill '{bill_identifier}' not found. Please check the bill number or ID."
            }

        # 2. Load expenses for this bill
        expenses_query = supabase.table("expenses_manual_COGS") \
            .select("id, description, amount, account_id, accounts(id, Name, AccountCategory)") \
            .eq("bill_id", bill_id)

        if material_name:
            expenses_query = expenses_query.ilike("description", f"%{material_name}%")

        expenses_result = expenses_query.execute()

        if not expenses_result.data:
            return {
                "status": "error",
                "message": f"No expense items found for Bill #{bill_number or bill_id}."
            }

        # 3. Format items for interactive card
        editable_items = []
        for exp in expenses_result.data:
            account = exp.get("accounts") or {}
            editable_items.append({
                "expense_id": exp["id"],
                "description": exp.get("description") or "Unnamed item",
                "amount": float(exp.get("amount") or 0),
                "current_account_id": exp.get("account_id"),
                "current_account_name": account.get("Name"),
                "current_account_category": account.get("AccountCategory"),
            })

        # 4. Post Andrew message with interactive card
        bill_ref = f"Bill #{bill_number}" if bill_number else f"Bill {bill_id[:8]}"
        content = f"Here are the items for {bill_ref}. You can change the category for each item below:"

        metadata = {
            "agent_message": True,
            "edit_categories_card": True,
            "bill_id": bill_id,
            "bill_number": bill_number,
            "editable_items": editable_items,
        }

        post_andrew_message(
            content=content,
            project_id=project_id,
            channel_id=channel_id,
            metadata=metadata,
        )

        return {
            "status": "success",
            "message": f"Interactive card posted for {bill_ref} with {len(editable_items)} items"
        }

    except Exception as e:
        logger.error(f"[edit_bill_categories] Error: {e}", exc_info=True)
        return {
            "status": "error",
            "message": f"Error loading bill categories: {str(e)}"
        }


@router.post("/bill-categories/confirm")
async def confirm_bill_category_changes(payload: EditCategoriesRequest, current_user: dict = Depends(get_current_user)):
    """
    Process user confirmation of category changes for bill items.
    Updates account_id for each expense.
    """
    try:
        assignments = payload.assignments
        user_id = payload.user_id

        if not assignments:
            raise HTTPException(status_code=400, detail="No assignments provided")

        updated_count = 0
        errors = []

        for assignment in assignments:
            expense_id = assignment.get("expense_id")
            new_account_id = assignment.get("account_id")

            if not expense_id or not new_account_id:
                continue

            try:
                supabase.table("expenses_manual_COGS") \
                    .update({
                        "account_id": new_account_id,
                        "updated_at": datetime.utcnow().isoformat()
                    }) \
                    .eq("id", expense_id) \
                    .execute()

                updated_count += 1
            except Exception as e:
                logger.error(f"[bill-categories] Error updating expense {expense_id}: {e}")
                errors.append(str(e))

        return {
            "success": True,
            "updated_count": updated_count,
            "errors": errors if errors else None
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[bill-categories] Error confirming changes: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error confirming category changes: {str(e)}")
