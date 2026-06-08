"""
Router for the unified Responsibilities catalog.

A "responsibility" is a named duty owned by a role (one shared task, every member
as manager) or a specific user, that turns into pipeline tasks shown in each
owner's "My Work".

Two kinds, merged into one shape by GET /responsibilities:
  - SYSTEM: backed by an automation the backend already knows how to run. Stored
    in `automation_settings` (assignment = responsible_type / responsible_role_id /
    default_manager_id). Only the assignment + enabled flag are editable; not
    creatable or deletable here.
  - MANUAL: admin-created. Stored in `responsibilities`. Fully editable/deletable.

Unified item ids are prefixed so PUT/DELETE can route without ambiguity:
  - system: "sys:<automation_type>"
  - manual: "man:<responsibility_id>"
"""
from fastapi import APIRouter, HTTPException, Depends
from api.auth import require_internal
from typing import List, Dict, Any, Optional, Tuple
from pydantic import BaseModel
from datetime import datetime, timezone
import logging
import traceback

from api.supabase_client import supabase

logger = logging.getLogger(__name__)
router = APIRouter(dependencies=[Depends(require_internal)], prefix="/responsibilities", tags=["responsibilities"])


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class ResponsibilityCreate(BaseModel):
    """Create a manual responsibility."""
    title: str
    description: Optional[str] = None
    responsible_type: str = "role"            # 'role' | 'user'
    responsible_role_id: Optional[str] = None
    responsible_user_id: Optional[str] = None
    department_id: Optional[str] = None
    priority: int = 3
    recurrence: str = "none"                  # 'none' | 'daily' | 'weekly' | 'monthly'
    recurrence_config: Optional[Dict[str, Any]] = None
    project_scope: str = "none"               # 'none' | 'all_active' | 'specific'
    project_id: Optional[str] = None
    is_enabled: bool = True
    created_by: Optional[str] = None


class ResponsibilityUpdate(BaseModel):
    """Update a responsibility. For SYSTEM items only assignment + enabled apply."""
    title: Optional[str] = None
    description: Optional[str] = None
    is_enabled: Optional[bool] = None
    responsible_type: Optional[str] = None
    responsible_role_id: Optional[str] = None
    responsible_user_id: Optional[str] = None
    department_id: Optional[str] = None
    priority: Optional[int] = None
    recurrence: Optional[str] = None
    recurrence_config: Optional[Dict[str, Any]] = None
    project_scope: Optional[str] = None
    project_id: Optional[str] = None
    updated_by: Optional[str] = None


# ---------------------------------------------------------------------------
# Enrichment helpers
# ---------------------------------------------------------------------------

def _lookup_maps() -> Tuple[Dict[Any, dict], Dict[Any, int], Dict[Any, dict], Dict[Any, dict]]:
    """Return (roles_map, role_counts, users_map, depts_map) for enrichment."""
    roles_response = supabase.table("rols").select("rol_id, rol_name").execute()
    roles_map = {r["rol_id"]: r for r in (roles_response.data or [])}

    role_users_response = supabase.table("users").select("user_rol").execute()
    role_counts: Dict[Any, int] = {}
    for u in (role_users_response.data or []):
        rid = u.get("user_rol")
        if rid is not None:
            role_counts[rid] = role_counts.get(rid, 0) + 1

    users_response = supabase.table("users").select(
        "user_id, user_name, avatar_color"
    ).execute()
    users_map = {u["user_id"]: u for u in (users_response.data or [])}

    depts_response = supabase.table("task_departments").select(
        "department_id, department_name"
    ).execute()
    depts_map = {d["department_id"]: d for d in (depts_response.data or [])}

    return roles_map, role_counts, users_map, depts_map


def _assignment_block(
    responsible_type: Optional[str],
    role_id: Optional[str],
    user_id: Optional[str],
    roles_map: Dict[Any, dict],
    role_counts: Dict[Any, int],
    users_map: Dict[Any, dict],
) -> Dict[str, Any]:
    """Common 'who owns this' fields, shared by both kinds."""
    role_data = roles_map.get(role_id) if role_id is not None else None
    user_data = users_map.get(user_id) if user_id else None
    return {
        "responsible_type": responsible_type or "role",
        "responsible_role_id": role_id,
        "responsible_role_name": role_data["rol_name"] if role_data else None,
        "responsible_role_member_count": role_counts.get(role_id, 0) if role_id is not None else 0,
        "responsible_user_id": user_id,
        "responsible_user_name": user_data["user_name"] if user_data else None,
        "responsible_user_avatar_color": user_data.get("avatar_color") if user_data else None,
    }


def _system_item(s: dict, maps) -> Dict[str, Any]:
    """Map an automation_settings row to the unified shape."""
    roles_map, role_counts, users_map, depts_map = maps
    # For SYSTEM, the 'specific user' is default_manager_id.
    user_id = s.get("default_manager_id") if (s.get("responsible_type") == "user") else None
    dept_id = s.get("default_department_id")
    dept_data = depts_map.get(dept_id) if dept_id else None
    return {
        "id": f"sys:{s.get('automation_type')}",
        "kind": "system",
        "key": s.get("automation_type"),
        "title": s.get("display_name") or s.get("automation_type"),
        "description": (s.get("config") or {}).get("description") if isinstance(s.get("config"), dict) else None,
        "is_enabled": bool(s.get("is_enabled")),
        "priority": s.get("default_priority"),
        "department_id": dept_id,
        "department_name": dept_data["department_name"] if dept_data else None,
        "recurrence": None,
        "project_scope": None,
        "editable": True,    # assignment + enabled only
        "deletable": False,
        **_assignment_block(
            s.get("responsible_type"), s.get("responsible_role_id"), user_id,
            roles_map, role_counts, users_map,
        ),
    }


def _manual_item(r: dict, maps) -> Dict[str, Any]:
    """Map a responsibilities row to the unified shape."""
    roles_map, role_counts, users_map, depts_map = maps
    dept_id = r.get("department_id")
    dept_data = depts_map.get(dept_id) if dept_id else None
    return {
        "id": f"man:{r.get('responsibility_id')}",
        "kind": "manual",
        "key": None,
        "title": r.get("title"),
        "description": r.get("description"),
        "is_enabled": bool(r.get("is_enabled")),
        "priority": r.get("priority"),
        "department_id": dept_id,
        "department_name": dept_data["department_name"] if dept_data else None,
        "recurrence": r.get("recurrence"),
        "recurrence_config": r.get("recurrence_config") or {},
        "project_scope": r.get("project_scope"),
        "project_id": r.get("project_id"),
        "last_generated_at": r.get("last_generated_at"),
        "editable": True,
        "deletable": True,
        **_assignment_block(
            r.get("responsible_type"), r.get("responsible_role_id"),
            r.get("responsible_user_id"), roles_map, role_counts, users_map,
        ),
    }


def _fetch_manual_rows() -> List[dict]:
    """Read manual responsibilities. Defensive: returns [] if the table doesn't
    exist yet (pre-migration), so the system catalog keeps working untouched."""
    try:
        return (supabase.table("responsibilities").select("*").execute().data) or []
    except Exception as e:
        logger.warning("[RESPONSIBILITIES] manual table read skipped (not migrated?): %s", e)
        return []


def _fetch_all_items() -> List[Dict[str, Any]]:
    maps = _lookup_maps()

    system_rows = (supabase.table("automation_settings").select("*").execute().data) or []
    manual_rows = _fetch_manual_rows()

    items = [_system_item(s, maps) for s in system_rows]
    items += [_manual_item(r, maps) for r in manual_rows]
    return items


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("")
def list_responsibilities() -> Dict[str, Any]:
    """Unified catalog: system automations + manual responsibilities."""
    logger.info("[RESPONSIBILITIES] GET /responsibilities")
    try:
        return {"responsibilities": _fetch_all_items()}
    except Exception as e:
        logger.error("[RESPONSIBILITIES] ERROR in GET: %s", repr(e))
        logger.debug(traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"Error: {e}") from e


@router.get("/by-role/{rol_id}")
def list_responsibilities_by_role(rol_id: str) -> Dict[str, Any]:
    """Responsibilities owned by a role (responsible_type='role' + this role)."""
    logger.info("[RESPONSIBILITIES] GET /responsibilities/by-role/%s", rol_id)
    try:
        items = _fetch_all_items()
        owned = [
            it for it in items
            if it.get("responsible_type") == "role"
            and str(it.get("responsible_role_id") or "") == str(rol_id)
        ]
        return {"responsibilities": owned}
    except Exception as e:
        logger.error("[RESPONSIBILITIES] ERROR in GET by-role: %s", repr(e))
        logger.debug(traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"Error: {e}") from e


@router.post("")
def create_responsibility(payload: ResponsibilityCreate) -> Dict[str, Any]:
    """Create a MANUAL responsibility."""
    logger.info("[RESPONSIBILITIES] POST /responsibilities - %s", payload.title)
    try:
        if payload.responsible_type not in ("role", "user"):
            raise HTTPException(status_code=400, detail="responsible_type must be 'role' or 'user'")
        if payload.responsible_type == "role" and not payload.responsible_role_id:
            raise HTTPException(status_code=400, detail="responsible_role_id required when responsible_type='role'")
        if payload.responsible_type == "user" and not payload.responsible_user_id:
            raise HTTPException(status_code=400, detail="responsible_user_id required when responsible_type='user'")

        row = {
            "title": payload.title,
            "description": payload.description,
            "responsible_type": payload.responsible_type,
            "responsible_role_id": payload.responsible_role_id,
            "responsible_user_id": payload.responsible_user_id,
            "department_id": payload.department_id,
            "priority": payload.priority,
            "recurrence": payload.recurrence,
            "recurrence_config": payload.recurrence_config or {},
            "project_scope": payload.project_scope,
            "project_id": payload.project_id,
            "is_enabled": payload.is_enabled,
            "created_by": payload.created_by,
            "updated_by": payload.created_by,
        }
        response = supabase.table("responsibilities").insert(row).execute()
        if not response.data:
            raise HTTPException(status_code=500, detail="Failed to create responsibility")

        maps = _lookup_maps()
        return {"success": True, "responsibility": _manual_item(response.data[0], maps)}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("[RESPONSIBILITIES] ERROR in POST: %s", repr(e))
        logger.debug(traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"Error: {e}") from e


@router.put("/{item_id}")
def update_responsibility(item_id: str, payload: ResponsibilityUpdate) -> Dict[str, Any]:
    """Update a responsibility. Routes by prefixed id (sys:/man:)."""
    logger.info("[RESPONSIBILITIES] PUT /responsibilities/%s", item_id)
    try:
        kind, _, raw_id = item_id.partition(":")

        if kind == "sys":
            # SYSTEM: only assignment + enabled map onto automation_settings.
            # 'specific user' is stored as default_manager_id.
            update_data: Dict[str, Any] = {}
            if payload.is_enabled is not None:
                update_data["is_enabled"] = payload.is_enabled
            if payload.responsible_type is not None:
                if payload.responsible_type not in ("role", "user"):
                    raise HTTPException(status_code=400, detail="responsible_type must be 'role' or 'user'")
                update_data["responsible_type"] = payload.responsible_type
            if payload.responsible_role_id is not None:
                update_data["responsible_role_id"] = payload.responsible_role_id
            if payload.responsible_user_id is not None:
                update_data["default_manager_id"] = payload.responsible_user_id
            if not update_data:
                raise HTTPException(status_code=400, detail="No fields to update")

            existing = supabase.table("automation_settings").select("setting_id").eq(
                "automation_type", raw_id
            ).execute()
            if not existing.data:
                raise HTTPException(status_code=404, detail=f"System responsibility '{raw_id}' not found")

            response = supabase.table("automation_settings").update(update_data).eq(
                "automation_type", raw_id
            ).execute()
            if not response.data:
                raise HTTPException(status_code=500, detail="Failed to update responsibility")

            maps = _lookup_maps()
            return {"success": True, "responsibility": _system_item(response.data[0], maps)}

        if kind == "man":
            update_data = payload.model_dump(exclude_unset=True, exclude={"updated_by"})
            if not update_data and payload.updated_by is None:
                raise HTTPException(status_code=400, detail="No fields to update")
            if payload.updated_by is not None:
                update_data["updated_by"] = payload.updated_by
            update_data["updated_at"] = datetime.now(timezone.utc).isoformat()

            existing = supabase.table("responsibilities").select("responsibility_id").eq(
                "responsibility_id", raw_id
            ).execute()
            if not existing.data:
                raise HTTPException(status_code=404, detail=f"Responsibility '{raw_id}' not found")

            response = supabase.table("responsibilities").update(update_data).eq(
                "responsibility_id", raw_id
            ).execute()
            if not response.data:
                raise HTTPException(status_code=500, detail="Failed to update responsibility")

            maps = _lookup_maps()
            return {"success": True, "responsibility": _manual_item(response.data[0], maps)}

        raise HTTPException(status_code=400, detail="Invalid responsibility id (expected sys:/man: prefix)")
    except HTTPException:
        raise
    except Exception as e:
        logger.error("[RESPONSIBILITIES] ERROR in PUT: %s", repr(e))
        logger.debug(traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"Error: {e}") from e


@router.delete("/{item_id}")
def delete_responsibility(item_id: str) -> Dict[str, Any]:
    """Delete a MANUAL responsibility. System ones cannot be deleted."""
    logger.info("[RESPONSIBILITIES] DELETE /responsibilities/%s", item_id)
    try:
        kind, _, raw_id = item_id.partition(":")
        if kind != "man":
            raise HTTPException(status_code=400, detail="Only manual responsibilities can be deleted")

        supabase.table("responsibilities").delete().eq("responsibility_id", raw_id).execute()
        return {"success": True, "deleted": item_id}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("[RESPONSIBILITIES] ERROR in DELETE: %s", repr(e))
        logger.debug(traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"Error: {e}") from e
