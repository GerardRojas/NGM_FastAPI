"""
Andrew Mismatch Reconciliation Router
Endpoints for triggering and configuring Andrew's bill reconciliation protocol.
"""

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from typing import Optional
from datetime import datetime, timezone

from api.supabase_client import supabase

router = APIRouter(prefix="/andrew", tags=["andrew"])


# ================================
# MODELS
# ================================

class MismatchConfigUpdate(BaseModel):
    andrew_mismatch_enabled: Optional[bool] = None
    andrew_mismatch_auto_correct: Optional[bool] = None
    andrew_mismatch_confidence_min: Optional[int] = None
    andrew_mismatch_amount_tolerance: Optional[float] = None
    andrew_smart_layer_enabled: Optional[bool] = None
    andrew_followup_hours: Optional[int] = None
    andrew_escalation_hours: Optional[int] = None


# ================================
# TRIGGER
# ================================

@router.post("/reconcile-bill")
async def reconcile_bill(
    bill_id: str = Query(..., description="The bill_id to reconcile"),
    project_id: str = Query(..., description="The project UUID"),
    source: str = Query("manual", description="Trigger source: daneel or manual"),
):
    """
    Trigger Andrew's mismatch reconciliation protocol for a specific bill.
    Extracts line items from the receipt via Vision OCR and compares against
    DB expenses, identifying mismatches and optionally auto-correcting.
    """
    from api.services.andrew_mismatch_protocol import run_mismatch_reconciliation
    try:
        result = await run_mismatch_reconciliation(
            bill_id=bill_id,
            project_id=project_id,
            source=source,
        )
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Reconciliation failed: {str(e)}")


# ================================
# CONFIG
# ================================

@router.get("/mismatch-config")
async def get_mismatch_config():
    """Get Andrew mismatch reconciliation config."""
    try:
        from api.services.andrew_mismatch_protocol import _load_mismatch_config
        return _load_mismatch_config()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error getting config: {str(e)}")


@router.put("/mismatch-config")
async def update_mismatch_config(payload: MismatchConfigUpdate):
    """Update Andrew mismatch reconciliation config."""
    import json
    try:
        update_data = {k: v for k, v in payload.dict().items() if v is not None}
        now = datetime.now(timezone.utc).isoformat()
        for key, value in update_data.items():
            json_val = value if isinstance(value, str) else json.dumps(value)
            existing = supabase.table("agent_config") \
                .select("key") \
                .eq("key", key) \
                .execute()
            if existing.data:
                supabase.table("agent_config") \
                    .update({"value": json_val, "updated_at": now}) \
                    .eq("key", key) \
                    .execute()
            else:
                supabase.table("agent_config") \
                    .insert({"key": key, "value": json_val, "updated_at": now}) \
                    .execute()
        return {"ok": True, "updated_keys": list(update_data.keys())}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error updating config: {str(e)}")


# ================================
# SMART LAYER: FOLLOW-UPS
# ================================

@router.post("/follow-up-check")
async def run_follow_up_check():
    """
    Check for receipts awaiting human response and send follow-ups.
    Should be called periodically (e.g., every 6 hours via cron/scheduler).

    Thresholds:
    - 24h: first follow-up reminder
    - 48h: escalation to bookkeeping
    - 72h: marked as stale
    """
    from api.services.andrew_smart_layer import check_pending_followups, execute_followups
    try:
        pending = check_pending_followups()
        if not pending:
            return {"ok": True, "message": "No follow-ups needed", "stats": {}}
        stats = execute_followups(pending)
        return {
            "ok": True,
            "receipts_checked": len(pending),
            "stats": stats,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Follow-up check failed: {str(e)}")


@router.get("/pending-receipts-status")
async def get_pending_receipts_status():
    """
    Get summary of receipts currently awaiting human response.
    Shows how long each has been waiting and what's needed.
    """
    from api.services.andrew_smart_layer import check_pending_followups
    try:
        pending = check_pending_followups()
        # Also get receipts that are pending but not yet overdue
        all_awaiting = supabase.table("pending_receipts") \
            .select("id, project_id, vendor_name, amount, updated_at, parsed_data") \
            .eq("status", "ready") \
            .execute()

        awaiting = []
        for r in (all_awaiting.data or []):
            flow = (r.get("parsed_data") or {}).get("receipt_flow") or {}
            state = flow.get("state", "")
            if state in ("awaiting_item_selection", "awaiting_category_confirmation",
                         "awaiting_missing_info", "awaiting_check_number"):
                smart = (r.get("parsed_data") or {}).get("smart_analysis") or {}
                awaiting.append({
                    "receipt_id": r["id"],
                    "project_id": r.get("project_id"),
                    "vendor": r.get("vendor_name"),
                    "amount": r.get("amount"),
                    "state": state,
                    "missing_fields": smart.get("unresolved", []),
                    "followup_count": flow.get("followup_count", 0),
                    "updated_at": r.get("updated_at"),
                })

        return {
            "total_awaiting": len(awaiting),
            "overdue": len(pending),
            "receipts": awaiting,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error getting status: {str(e)}")
