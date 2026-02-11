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
