"""
Daneel Auto-Authorization Router
Endpoints for triggering, monitoring, and configuring Daneel's
automatic expense authorization engine.
"""

from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel
from typing import Optional
from datetime import datetime, timezone

from api.supabase_client import supabase

router = APIRouter(prefix="/daneel", tags=["daneel"])


# ================================
# MODELS
# ================================

class AutoAuthConfigUpdate(BaseModel):
    daneel_auto_auth_enabled: Optional[bool] = None
    daneel_auto_auth_require_bill: Optional[bool] = None
    daneel_auto_auth_require_receipt: Optional[bool] = None
    daneel_fuzzy_threshold: Optional[int] = None
    daneel_amount_tolerance: Optional[float] = None
    daneel_labor_keywords: Optional[str] = None
    daneel_bookkeeping_role: Optional[str] = None
    daneel_accounting_mgr_role: Optional[str] = None
    daneel_gpt_fallback_enabled: Optional[bool] = None
    daneel_gpt_fallback_confidence: Optional[int] = None


# ================================
# TRIGGER ENDPOINTS
# ================================

@router.post("/auto-auth/run")
async def run_auto_auth(background_tasks: BackgroundTasks):
    """
    Manually trigger a full auto-authorization run.
    Processes all new pending expenses since last run.
    """
    from api.services.daneel_auto_auth import run_auto_auth as _run
    try:
        result = await _run()
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Auto-auth run failed: {str(e)}")


@router.post("/auto-auth/run-backlog")
async def run_auto_auth_backlog():
    """
    One-time run: process ALL pending expenses regardless of creation date.
    Use this to clear the historical backlog.
    """
    from api.services.daneel_auto_auth import run_auto_auth as _run
    try:
        result = await _run(process_all=True)
        result["mode"] = "backlog"
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Backlog run failed: {str(e)}")


@router.post("/auto-auth/reprocess")
async def reprocess_pending():
    """
    Re-check expenses that were waiting for missing info.
    Call this after bookkeepers update expenses with missing data.
    """
    from api.services.daneel_auto_auth import reprocess_pending_info
    try:
        result = await reprocess_pending_info()
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Reprocess failed: {str(e)}")


# ================================
# STATUS & MONITORING
# ================================

@router.get("/auto-auth/status")
async def get_auto_auth_status():
    """
    Get auto-auth status: config, last run, pending info count,
    and recent authorization stats.
    """
    try:
        from api.services.daneel_auto_auth import load_auto_auth_config, DANEEL_BOT_USER_ID

        cfg = load_auto_auth_config()

        # Count unresolved pending info
        pending_result = supabase.table("daneel_pending_info") \
            .select("expense_id", count="exact") \
            .is_("resolved_at", "null") \
            .execute()
        pending_count = pending_result.count if hasattr(pending_result, 'count') else len(pending_result.data or [])

        # Count recent authorizations by Daneel (last 30 days)
        from datetime import timedelta
        thirty_days_ago = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        auth_result = supabase.table("expense_status_log") \
            .select("id", count="exact") \
            .eq("changed_by", DANEEL_BOT_USER_ID) \
            .eq("new_status", "auth") \
            .gt("changed_at", thirty_days_ago) \
            .execute()
        auth_count = auth_result.count if hasattr(auth_result, 'count') else len(auth_result.data or [])

        return {
            "enabled": cfg.get("daneel_auto_auth_enabled", False),
            "last_run": cfg.get("daneel_auto_auth_last_run"),
            "pending_info_count": pending_count,
            "authorized_last_30d": auth_count,
            "config": {
                "require_bill": cfg.get("daneel_auto_auth_require_bill", True),
                "require_receipt": cfg.get("daneel_auto_auth_require_receipt", True),
                "fuzzy_threshold": cfg.get("daneel_fuzzy_threshold", 85),
                "amount_tolerance": cfg.get("daneel_amount_tolerance", 0.05),
                "labor_keywords": cfg.get("daneel_labor_keywords", "labor"),
            }
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error getting status: {str(e)}")


@router.get("/auto-auth/pending-info")
async def list_pending_info():
    """List expenses currently waiting for missing info."""
    try:
        result = supabase.table("daneel_pending_info") \
            .select("*, expenses_manual_COGS(expense_id, vendor_id, Amount, TxnDate, bill_id, project)") \
            .is_("resolved_at", "null") \
            .order("requested_at", desc=True) \
            .execute()
        return {"data": result.data or [], "count": len(result.data or [])}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error listing pending info: {str(e)}")


# ================================
# CONFIG ENDPOINTS
# ================================

@router.get("/auto-auth/config")
async def get_auto_auth_config():
    """Get all auto-auth configuration values."""
    try:
        from api.services.daneel_auto_auth import load_auto_auth_config
        cfg = load_auto_auth_config()
        return cfg
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error getting config: {str(e)}")


@router.put("/auto-auth/config")
async def update_auto_auth_config(payload: AutoAuthConfigUpdate):
    """Update auto-auth configuration values."""
    import json
    try:
        update_data = {k: v for k, v in payload.dict().items() if v is not None}
        for key, value in update_data.items():
            supabase.table("agent_config").upsert({
                "key": key,
                "value": json.dumps(value) if not isinstance(value, (str, int, float, bool)) else value,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }).execute()
        return {"ok": True, "updated_keys": list(update_data.keys())}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error updating config: {str(e)}")
