"""
Daneel Auto-Authorization Router
Endpoints for triggering, monitoring, and configuring Daneel's
automatic expense authorization engine.
"""

from fastapi import APIRouter, HTTPException, BackgroundTasks, Query
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime, timezone, timedelta

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
    daneel_bookkeeping_users: Optional[str] = None
    daneel_accounting_mgr_users: Optional[str] = None
    daneel_gpt_fallback_enabled: Optional[bool] = None
    daneel_gpt_fallback_confidence: Optional[int] = None
    daneel_mismatch_notify_andrew: Optional[bool] = None
    daneel_receipt_hash_check_enabled: Optional[bool] = None
    daneel_smart_layer_enabled: Optional[bool] = None
    daneel_followup_hours: Optional[int] = None
    daneel_escalation_hours: Optional[int] = None


# ================================
# TRIGGER ENDPOINTS
# ================================

@router.post("/auto-auth/run")
async def run_auto_auth(
    background_tasks: BackgroundTasks,
    project_id: Optional[str] = Query(None, description="Filter to a specific project")
):
    """
    Manually trigger a full auto-authorization run.
    Processes all new pending expenses since last run.
    Optionally filter by project_id.
    """
    from api.services.daneel_auto_auth import run_auto_auth as _run
    try:
        result = await _run(project_id=project_id)
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
        now = datetime.now(timezone.utc).isoformat()
        for key, value in update_data.items():
            # Non-strings get json.dumps so JSONB stores them as parseable strings
            json_val = value if isinstance(value, str) else json.dumps(value)
            # Explicit SELECT + UPDATE/INSERT (upsert can silently no-op)
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
# PROJECT SUMMARY
# ================================

@router.get("/auto-auth/project-summary")
async def get_project_summary():
    """
    Per-project expense summary for Daneel dashboard.
    Returns pending, authorized-by-Daneel, and missing-info counts per project.
    """
    try:
        from api.services.daneel_auto_auth import DANEEL_BOT_USER_ID

        # Get all pending expenses grouped by project
        pending_result = supabase.table("expenses_manual_COGS") \
            .select("expense_id, project, status") \
            .eq("status", "pending") \
            .execute()

        # Get all projects for name resolution
        projects_result = supabase.table("projects") \
            .select("project_id, project_name") \
            .execute()
        project_names = {p["project_id"]: p["project_name"] for p in (projects_result.data or [])}

        # Get Daneel authorizations (last 30 days)
        thirty_days_ago = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        auth_result = supabase.table("expense_status_log") \
            .select("expense_id, metadata") \
            .eq("changed_by", DANEEL_BOT_USER_ID) \
            .eq("new_status", "auth") \
            .gt("changed_at", thirty_days_ago) \
            .execute()

        # Get expense->project mapping for authorized expenses
        auth_expense_ids = [r["expense_id"] for r in (auth_result.data or []) if r.get("expense_id")]
        auth_by_project = {}
        if auth_expense_ids:
            # Batch lookup in chunks of 50
            for i in range(0, len(auth_expense_ids), 50):
                chunk = auth_expense_ids[i:i+50]
                exp_result = supabase.table("expenses_manual_COGS") \
                    .select("expense_id, project") \
                    .in_("expense_id", chunk) \
                    .execute()
                for e in (exp_result.data or []):
                    pid = e.get("project")
                    if pid:
                        auth_by_project[pid] = auth_by_project.get(pid, 0) + 1

        # Get unresolved pending info grouped by project
        pending_info_result = supabase.table("daneel_pending_info") \
            .select("expense_id, project_id") \
            .is_("resolved_at", "null") \
            .execute()
        missing_by_project = {}
        for pi in (pending_info_result.data or []):
            pid = pi.get("project_id")
            if pid:
                missing_by_project[pid] = missing_by_project.get(pid, 0) + 1

        # Aggregate pending by project
        pending_by_project = {}
        for e in (pending_result.data or []):
            pid = e.get("project")
            if pid:
                pending_by_project[pid] = pending_by_project.get(pid, 0) + 1

        # Build summary - only include projects that have activity
        all_project_ids = set(pending_by_project.keys()) | set(auth_by_project.keys()) | set(missing_by_project.keys())
        summary = []
        for pid in all_project_ids:
            summary.append({
                "project_id": pid,
                "project_name": project_names.get(pid, "Unknown Project"),
                "pending": pending_by_project.get(pid, 0),
                "authorized_by_daneel": auth_by_project.get(pid, 0),
                "missing_info": missing_by_project.get(pid, 0),
            })

        # Sort by pending count descending
        summary.sort(key=lambda x: x["pending"], reverse=True)
        return {"data": summary}

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error getting project summary: {str(e)}")


# ================================
# AUTH REPORTS
# ================================

@router.get("/auto-auth/reports")
async def list_auth_reports(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    """List recent auth reports (newest first)."""
    try:
        import json
        result = supabase.table("daneel_auth_reports") \
            .select("report_id, report_type, project_id, project_name, created_at, summary") \
            .order("created_at", desc=True) \
            .range(offset, offset + limit - 1) \
            .execute()

        reports = []
        for r in (result.data or []):
            s = r.get("summary") or {}
            if isinstance(s, str):
                try:
                    s = json.loads(s)
                except Exception:
                    s = {}
            reports.append({
                "report_id": r["report_id"],
                "report_type": r["report_type"],
                "project_id": r.get("project_id"),
                "project_name": r.get("project_name"),
                "created_at": r["created_at"],
                "authorized": s.get("authorized", 0),
                "missing_info": s.get("missing_info", 0),
                "duplicates": s.get("duplicates", 0),
                "escalated": s.get("escalated", 0),
                "expenses_processed": s.get("expenses_processed", 0),
            })
        return {"data": reports}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error listing reports: {str(e)}")


@router.get("/auto-auth/reports/{report_id}")
async def get_auth_report(report_id: str):
    """Get a single auth report with full decision detail."""
    try:
        import json
        result = supabase.table("daneel_auth_reports") \
            .select("*") \
            .eq("report_id", report_id) \
            .single() \
            .execute()

        if not result.data:
            raise HTTPException(status_code=404, detail="Report not found")

        r = result.data
        s = r.get("summary") or {}
        d = r.get("decisions") or []
        if isinstance(s, str):
            try:
                s = json.loads(s)
            except Exception:
                s = {}
        if isinstance(d, str):
            try:
                d = json.loads(d)
            except Exception:
                d = []

        return {
            "report_id": r["report_id"],
            "report_type": r["report_type"],
            "project_id": r.get("project_id"),
            "project_name": r.get("project_name"),
            "created_at": r["created_at"],
            "summary": s,
            "decisions": d,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error getting report: {str(e)}")


@router.delete("/auto-auth/reports")
async def delete_auth_reports():
    """Delete all auth reports."""
    try:
        # Supabase requires a filter for delete; use created_at > epoch to match all
        supabase.table("daneel_auth_reports") \
            .delete() \
            .gt("created_at", "1970-01-01T00:00:00Z") \
            .execute()
        return {"ok": True, "message": "All reports deleted"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error deleting reports: {str(e)}")


# ================================
# SMART LAYER: FOLLOW-UPS
# ================================

@router.post("/auto-auth/follow-up-check")
async def run_follow_up_check():
    """
    Check for pending info items awaiting human response and send follow-ups.
    Should be called periodically (e.g., every 6 hours via cron/scheduler).
    """
    from api.services.daneel_smart_layer import check_pending_followups, execute_followups
    try:
        from api.services.daneel_auto_auth import load_auto_auth_config
        cfg = load_auto_auth_config()
        followup_h = int(cfg.get("daneel_followup_hours", 24))
        escalation_h = int(cfg.get("daneel_escalation_hours", 48))

        pending = check_pending_followups(followup_h, escalation_h)
        if not pending:
            return {"ok": True, "message": "No follow-ups needed", "stats": {}}
        stats = execute_followups(pending)
        return {
            "ok": True,
            "items_checked": len(pending),
            "stats": stats,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Follow-up check failed: {str(e)}")


@router.get("/auto-auth/pending-info-status")
async def get_pending_info_status():
    """
    Get summary of expenses currently awaiting missing info.
    Shows how long each has been waiting and what's needed.
    """
    try:
        result = supabase.table("daneel_pending_info") \
            .select("expense_id, project_id, missing_fields, requested_at") \
            .is_("resolved_at", "null") \
            .order("requested_at", desc=False) \
            .execute()

        now = datetime.now(timezone.utc)
        items = []
        for r in (result.data or []):
            hours = 0
            if r.get("requested_at"):
                try:
                    req = datetime.fromisoformat(r["requested_at"].replace("Z", "+00:00"))
                    if req.tzinfo is None:
                        req = req.replace(tzinfo=timezone.utc)
                    hours = round((now - req).total_seconds() / 3600, 1)
                except Exception:
                    pass
            items.append({
                "expense_id": r["expense_id"],
                "project_id": r.get("project_id"),
                "missing_fields": r.get("missing_fields", []),
                "hours_pending": hours,
                "status": "stale" if hours >= 72 else "overdue" if hours >= 24 else "recent",
            })

        return {
            "total": len(items),
            "stale": sum(1 for i in items if i["status"] == "stale"),
            "overdue": sum(1 for i in items if i["status"] == "overdue"),
            "items": items,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error getting pending info status: {str(e)}")
