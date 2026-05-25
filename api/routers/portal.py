"""
Client Portal — hardened, client-facing read API.

This is the ONLY router an external client account may reach. Every endpoint
resolves its scope from the JWT (get_current_client -> client_id), never from
request parameters, and returns ONLY content the team has explicitly published
to portal_shares (default-deny). Internal modules (/vault, /messages, ...) are
never reused here, so there is no filter to forget and no way to leak.

The same per-section helpers power the WYSIWYG "preview as client" workspace in
NGM Connect (see routers/connect.py), guaranteeing the team sees exactly what
the client sees.

Tables: portal_shares, project_client_access (see sql/client_portal_phase1.sql).
"""

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException

from api.auth import get_current_client
from api.supabase_client import supabase
from api.services.vault_service import get_download_url

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/portal", tags=["Client Portal"])

# Portal module keys carried in project_client_access.modules
PORTAL_MODULES = {"overview", "photos", "plans", "timeline", "documents", "messages"}


# ============================================================
# Access resolution (the scope gate)
# ============================================================

def get_access(client_id: str, project_id: str) -> Optional[Dict[str, Any]]:
    """Return the project_client_access row for (client, project), or None."""
    try:
        res = (
            supabase.table("project_client_access")
            .select("modules")
            .eq("client_id", client_id)
            .eq("project_id", project_id)
            .limit(1)
            .execute()
        )
    except Exception as e:
        logger.error("[portal] access lookup failed: %s", e)
        raise HTTPException(status_code=500, detail="Access lookup failed")
    rows = res.data or []
    return rows[0] if rows else None


def assert_module(client_id: str, project_id: str, module: str) -> Dict[str, bool]:
    """
    Ensure this client may see this project AND the given portal module is
    enabled for the pairing. Raises 403 otherwise. Returns the modules flag bag.
    """
    access = get_access(client_id, project_id)
    if access is None:
        raise HTTPException(status_code=403, detail="No access to this project")
    modules = access.get("modules") or {}
    if not modules.get(module, False):
        raise HTTPException(status_code=403, detail=f"The '{module}' section is not enabled")
    return modules


def _active_shares(project_id: str, item_type: str) -> List[Dict[str, Any]]:
    """Active portal_shares rows of a given type for a project (the publish list)."""
    try:
        res = (
            supabase.table("portal_shares")
            .select("item_id, client_caption, shared_at")
            .eq("project_id", project_id)
            .eq("item_type", item_type)
            .eq("is_active", True)
            .order("shared_at", desc=True)
            .execute()
        )
    except Exception as e:
        logger.error("[portal] shares lookup failed (%s): %s", item_type, e)
        raise HTTPException(status_code=500, detail="Shares lookup failed")
    return res.data or []


def _file_url(file_id: str) -> Optional[str]:
    try:
        return get_download_url(file_id)
    except Exception:
        return None


# ============================================================
# Per-section data builders (reused by /connect preview)
# ============================================================

def list_projects(client_id: str) -> List[Dict[str, Any]]:
    """Projects this client can access, with enabled modules and project name."""
    try:
        access = (
            supabase.table("project_client_access")
            .select("project_id, modules")
            .eq("client_id", client_id)
            .execute()
        ).data or []
    except Exception as e:
        logger.error("[portal] list_projects failed: %s", e)
        raise HTTPException(status_code=500, detail="Could not list projects")
    if not access:
        return []
    project_ids = [a["project_id"] for a in access]
    try:
        projects = (
            supabase.table("projects")
            .select("project_id, project_name, address, city")
            .in_("project_id", project_ids)
            .execute()
        ).data or []
    except Exception as e:
        logger.error("[portal] projects fetch failed: %s", e)
        raise HTTPException(status_code=500, detail="Could not load projects")
    pmap = {p["project_id"]: p for p in projects}
    out = []
    for a in access:
        p = pmap.get(a["project_id"], {})
        out.append({
            "project_id": a["project_id"],
            "project_name": p.get("project_name"),
            "address": p.get("address"),
            "city": p.get("city"),
            "modules": a.get("modules") or {},
        })
    return out


def get_photos(project_id: str) -> List[Dict[str, Any]]:
    """Published photos (item_type='photo' -> vault_files), with public URLs."""
    shares = _active_shares(project_id, "photo")
    if not shares:
        return []
    by_id = {s["item_id"]: s for s in shares}
    files = (
        supabase.table("vault_files")
        .select("id, name, mime_type, created_at")
        .in_("id", list(by_id.keys()))
        .eq("is_deleted", False)
        .execute()
    ).data or []
    out = []
    for f in files:
        s = by_id.get(f["id"], {})
        out.append({
            "id": f["id"],
            "name": s.get("client_caption") or f.get("name"),
            "mime_type": f.get("mime_type"),
            "url": _file_url(f["id"]),
            "shared_at": s.get("shared_at"),
        })
    return out


def get_documents(project_id: str) -> List[Dict[str, Any]]:
    """Published documents (item_type='vault_file' -> vault_files)."""
    shares = _active_shares(project_id, "vault_file")
    if not shares:
        return []
    by_id = {s["item_id"]: s for s in shares}
    files = (
        supabase.table("vault_files")
        .select("id, name, mime_type, size_bytes, created_at")
        .in_("id", list(by_id.keys()))
        .eq("is_deleted", False)
        .execute()
    ).data or []
    out = []
    for f in files:
        s = by_id.get(f["id"], {})
        out.append({
            "id": f["id"],
            "name": s.get("client_caption") or f.get("name"),
            "mime_type": f.get("mime_type"),
            "size_bytes": f.get("size_bytes"),
            "url": _file_url(f["id"]),
            "shared_at": s.get("shared_at"),
        })
    return out


def get_plans(project_id: str) -> List[Dict[str, Any]]:
    """Published plan revisions (item_type='plan_revision' -> plan_revisions)."""
    shares = _active_shares(project_id, "plan_revision")
    if not shares:
        return []
    by_id = {s["item_id"]: s for s in shares}
    revs = (
        supabase.table("plan_revisions")
        .select("id, branch_id, file_id, label, status, submitted_at, created_at")
        .in_("id", list(by_id.keys()))
        .execute()
    ).data or []
    # Resolve plan name via branch -> plan.
    branch_ids = list({r["branch_id"] for r in revs if r.get("branch_id")})
    branches = (
        supabase.table("plan_branches").select("id, plan_id, name").in_("id", branch_ids).execute().data
        if branch_ids else []
    ) or []
    bmap = {b["id"]: b for b in branches}
    plan_ids = list({b["plan_id"] for b in branches})
    plans = (
        supabase.table("project_plans").select("id, name, discipline").in_("id", plan_ids).execute().data
        if plan_ids else []
    ) or []
    plmap = {p["id"]: p for p in plans}
    out = []
    for r in revs:
        s = by_id.get(r["id"], {})
        b = bmap.get(r.get("branch_id"), {})
        pl = plmap.get(b.get("plan_id"), {})
        out.append({
            "id": r["id"],
            "plan_name": s.get("client_caption") or pl.get("name"),
            "discipline": pl.get("discipline"),
            "label": r.get("label"),
            "status": r.get("status"),
            "submitted_at": r.get("submitted_at"),
            "url": _file_url(r["file_id"]) if r.get("file_id") else None,
            "shared_at": s.get("shared_at"),
        })
    return out


def get_timeline(project_id: str) -> Dict[str, Any]:
    """Published phases + milestones (item_type 'phase' / 'milestone')."""
    phase_shares = _active_shares(project_id, "phase")
    ms_shares = _active_shares(project_id, "milestone")

    phases: List[Dict[str, Any]] = []
    if phase_shares:
        ids = [s["item_id"] for s in phase_shares]
        rows = (
            supabase.table("project_phases")
            .select("phase_id, phase_name, phase_type, status, progress_pct, start_date, end_date, sort_order")
            .in_("phase_id", ids)
            .order("sort_order")
            .execute()
        ).data or []
        cap = {s["item_id"]: s.get("client_caption") for s in phase_shares}
        for r in rows:
            r["phase_name"] = cap.get(r["phase_id"]) or r.get("phase_name")
        phases = rows

    milestones: List[Dict[str, Any]] = []
    if ms_shares:
        ids = [s["item_id"] for s in ms_shares]
        rows = (
            supabase.table("project_milestones")
            .select("milestone_id, milestone_name, due_date, status")
            .in_("milestone_id", ids)
            .order("due_date")
            .execute()
        ).data or []
        cap = {s["item_id"]: s.get("client_caption") for s in ms_shares}
        for r in rows:
            r["milestone_name"] = cap.get(r["milestone_id"]) or r.get("milestone_name")
        milestones = rows

    return {"phases": phases, "milestones": milestones}


def get_overview(project_id: str) -> Dict[str, Any]:
    """Derived progress snapshot from PUBLISHED content only."""
    project = (
        supabase.table("projects")
        .select("project_id, project_name, address, city")
        .eq("project_id", project_id)
        .limit(1)
        .execute()
    ).data or []
    project = project[0] if project else {}

    timeline = get_timeline(project_id)
    phases = timeline["phases"]
    progress_vals = [float(p.get("progress_pct") or 0) for p in phases]
    overall = round(sum(progress_vals) / len(progress_vals), 1) if progress_vals else 0

    # Next upcoming published milestone (not completed).
    upcoming = next(
        (m for m in timeline["milestones"] if m.get("status") != "completed"),
        None,
    )
    # Current phase = first published phase still in progress.
    current_phase = next(
        (p.get("phase_name") for p in phases if p.get("status") == "in_progress"),
        None,
    )
    latest_photos = get_photos(project_id)[:6]

    return {
        "project": project,
        "overall_progress_pct": overall,
        "current_phase": current_phase,
        "next_milestone": upcoming,
        "latest_photos": latest_photos,
        "published_phase_count": len(phases),
    }


# ============================================================
# Endpoints (client-authenticated; scope from token only)
# ============================================================

@router.get("/projects")
def portal_projects(client: dict = Depends(get_current_client)):
    return {"projects": list_projects(client["client_id"])}


@router.get("/projects/{project_id}/overview")
def portal_overview(project_id: str, client: dict = Depends(get_current_client)):
    assert_module(client["client_id"], project_id, "overview")
    return get_overview(project_id)


@router.get("/projects/{project_id}/photos")
def portal_photos(project_id: str, client: dict = Depends(get_current_client)):
    assert_module(client["client_id"], project_id, "photos")
    return {"photos": get_photos(project_id)}


@router.get("/projects/{project_id}/plans")
def portal_plans(project_id: str, client: dict = Depends(get_current_client)):
    assert_module(client["client_id"], project_id, "plans")
    return {"plans": get_plans(project_id)}


@router.get("/projects/{project_id}/timeline")
def portal_timeline(project_id: str, client: dict = Depends(get_current_client)):
    assert_module(client["client_id"], project_id, "timeline")
    return get_timeline(project_id)


@router.get("/projects/{project_id}/documents")
def portal_documents(project_id: str, client: dict = Depends(get_current_client)):
    assert_module(client["client_id"], project_id, "documents")
    return {"documents": get_documents(project_id)}
