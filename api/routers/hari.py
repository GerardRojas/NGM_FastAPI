"""
Hari Coordinator Router
Endpoints for task management, configuration, and stats
for the Hari team coordination agent.
"""

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime, timezone
import json
import logging

from api.supabase_client import supabase

logger = logging.getLogger("hari.router")

router = APIRouter(prefix="/hari", tags=["hari"])


# ================================
# MODELS
# ================================

class HariConfigUpdate(BaseModel):
    hari_coordinator_enabled: Optional[bool] = None
    hari_default_follow_up_hours: Optional[int] = None
    hari_escalation_interval_hours: Optional[int] = None
    hari_max_escalations: Optional[int] = None
    hari_stale_task_hours: Optional[int] = None
    hari_instructor_roles: Optional[str] = None
    hari_viewer_roles: Optional[str] = None
    hari_auto_confirm_users: Optional[str] = None
    hari_notify_assignee_on_create: Optional[bool] = None
    hari_notify_channel: Optional[bool] = None


class TaskActionRequest(BaseModel):
    action: str  # confirm, cancel, complete, extend, reassign
    new_value: Optional[str] = None
    notes: Optional[str] = None


# ================================
# CONFIGURATION
# ================================

@router.get("/config")
async def get_hari_config():
    """Get all Hari configuration values."""
    try:
        from api.services.hari_coordinator import load_hari_config
        return load_hari_config()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error getting config: {str(e)}")


@router.post("/config")
async def update_hari_config(payload: HariConfigUpdate):
    """Update Hari configuration values."""
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
# TASK ENDPOINTS
# ================================

@router.get("/tasks")
async def list_tasks(
    project_id: Optional[str] = Query(None),
    assignee_name: Optional[str] = Query(None),
    status: Optional[str] = Query("all"),
    limit: int = Query(20, le=100),
):
    """List coordinator tasks with optional filters."""
    try:
        from api.services.hari_coordinator import get_tasks, resolve_user_by_name

        assigned_to = None
        if assignee_name:
            user = resolve_user_by_name(assignee_name)
            if user:
                assigned_to = user["user_id"]

        tasks = get_tasks(
            project_id=project_id,
            assigned_to=assigned_to,
            status=status,
            limit=limit,
        )

        # Enrich with user names
        user_ids = set()
        for t in tasks:
            if t.get("assigned_to"):
                user_ids.add(t["assigned_to"])
            if t.get("created_by"):
                user_ids.add(t["created_by"])

        user_names = {}
        if user_ids:
            result = supabase.table("users") \
                .select("user_id, user_name") \
                .in_("user_id", list(user_ids)) \
                .execute()
            user_names = {u["user_id"]: u["user_name"] for u in (result.data or [])}

        enriched = []
        for t in tasks:
            t["assignee_name"] = user_names.get(t.get("assigned_to"), "Unassigned")
            t["creator_name"] = user_names.get(t.get("created_by"), "Unknown")
            enriched.append(t)

        return {"tasks": enriched, "count": len(enriched)}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/tasks/{task_id}/action")
async def task_action(task_id: str, payload: TaskActionRequest):
    """Perform an action on a task (confirm, cancel, complete, etc.)."""
    try:
        from api.services.hari_coordinator import (
            confirm_task, cancel_task, complete_task, update_task_field, parse_deadline,
            resolve_user_by_name,
        )

        action = payload.action.lower()

        if action == "confirm":
            result = confirm_task(task_id)
        elif action == "cancel":
            result = cancel_task(task_id)
        elif action == "complete":
            # Use a placeholder user_id since this comes from API, not chat
            result = complete_task(task_id, completed_by="api", notes=payload.notes)
        elif action == "extend":
            if not payload.new_value:
                raise HTTPException(status_code=400, detail="new_value (deadline) required for extend")
            new_deadline = parse_deadline(payload.new_value)
            result = update_task_field(task_id, {"deadline": new_deadline})
        elif action == "reassign":
            if not payload.new_value:
                raise HTTPException(status_code=400, detail="new_value (assignee name) required for reassign")
            user = resolve_user_by_name(payload.new_value)
            if not user:
                raise HTTPException(status_code=404, detail=f"User '{payload.new_value}' not found")
            result = update_task_field(task_id, {"assigned_to": user["user_id"]})
        else:
            raise HTTPException(status_code=400, detail=f"Unknown action: {action}")

        if isinstance(result, dict) and "error" in result:
            raise HTTPException(status_code=400, detail=result["error"])

        return {"ok": True, "task": result}

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ================================
# STATS
# ================================

@router.get("/stats")
async def get_stats(
    days: int = Query(30, ge=1, le=365),
    project_id: Optional[str] = Query(None),
):
    """Get task statistics for the Agent Hub dashboard."""
    try:
        from api.services.hari_coordinator import get_task_stats
        return get_task_stats(days=days, project_id=project_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ================================
# FOLLOW-UP ENGINE (manual trigger)
# ================================

@router.post("/follow-up/run")
async def run_follow_up():
    """Manually trigger the follow-up engine check."""
    try:
        from api.services.hari_coordinator import run_follow_up_check
        result = run_follow_up_check()
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
