"""
Project Plans — git-style plan/submittal tracking (planos).

A plan is a named plan set for a project. Each plan has branches (default "main"
plus optional parallel branches), and each branch has an ordered list of
revisions/submittals — one uploaded Vault PDF per revision, with a status and
timeline dates. The PDF lives in the project's Vault "Plans" folder; the upload
itself goes through /vault/upload, then the file_id is registered here.

Tables (see sql/project_plans.sql): project_plans, plan_branches, plan_revisions.
"""

from datetime import datetime, timezone
from typing import Optional, List, Dict, Any

from fastapi import APIRouter, HTTPException, Query, Depends
from pydantic import BaseModel, Field

from api.supabase_client import supabase
from api.auth import get_current_user

router = APIRouter(prefix="/plans", tags=["Project Plans"])

VALID_STATUS = {"draft", "submitted", "under_review", "approved", "revise_resubmit", "superseded"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ====== MODELS ======

class PlanCreate(BaseModel):
    project_id: str = Field(..., min_length=1)
    name: str = Field(..., min_length=1, max_length=160)
    discipline: Optional[str] = None
    # First revision (the uploaded PDF) — a plan is created from its first submittal.
    file_id: str = Field(..., min_length=1)
    revision_label: Optional[str] = None
    status: Optional[str] = None


class BranchCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    parent_branch_id: Optional[str] = None


class RevisionCreate(BaseModel):
    file_id: str = Field(..., min_length=1)
    label: Optional[str] = None
    status: Optional[str] = None
    submitted_at: Optional[str] = None
    due_at: Optional[str] = None
    notes: Optional[str] = None


class RevisionUpdate(BaseModel):
    label: Optional[str] = None
    status: Optional[str] = None
    submitted_at: Optional[str] = None
    reviewed_at: Optional[str] = None
    due_at: Optional[str] = None
    notes: Optional[str] = None


def _check_status(status: Optional[str]) -> Optional[str]:
    if status is None:
        return None
    if status not in VALID_STATUS:
        raise HTTPException(status_code=400, detail=f"Invalid status. Allowed: {sorted(VALID_STATUS)}")
    return status


# ====== HELPERS ======

def _branches_with_revisions(plan_ids: List[str]) -> Dict[str, List[Dict[str, Any]]]:
    """Return { plan_id: [branch {..., revisions: [...] }] } for the given plans."""
    if not plan_ids:
        return {}
    branches = (
        supabase.table("plan_branches")
        .select("id, plan_id, name, parent_branch_id, is_default, created_at")
        .in_("plan_id", plan_ids)
        .order("created_at")
        .execute()
    ).data or []
    branch_ids = [b["id"] for b in branches]
    revisions = []
    if branch_ids:
        revisions = (
            supabase.table("plan_revisions")
            .select("id, branch_id, file_id, label, status, submitted_at, reviewed_at, due_at, notes, created_at")
            .in_("branch_id", branch_ids)
            .order("created_at")
            .execute()
        ).data or []
    revs_by_branch: Dict[str, List[Dict[str, Any]]] = {}
    for r in revisions:
        revs_by_branch.setdefault(r["branch_id"], []).append(r)
    out: Dict[str, List[Dict[str, Any]]] = {}
    for b in branches:
        b["revisions"] = revs_by_branch.get(b["id"], [])
        out.setdefault(b["plan_id"], []).append(b)
    return out


def _plan_current_status(branches: List[Dict[str, Any]]) -> Optional[str]:
    """Default branch's latest revision status (fallback: latest revision overall)."""
    default = next((b for b in branches if b.get("is_default")), None)
    pool = (default or {}).get("revisions") if default else None
    if not pool:
        pool = [r for b in branches for r in b.get("revisions", [])]
    if not pool:
        return None
    latest = max(pool, key=lambda r: r.get("created_at") or "")
    return latest.get("status")


# ====== PLANS ======

@router.get("")
def list_plans(project_id: str = Query(...), current_user: dict = Depends(get_current_user)):
    """Plans for a project, each with its branches + revisions and current status."""
    try:
        plans = (
            supabase.table("project_plans")
            .select("id, project_id, name, discipline, created_at")
            .eq("project_id", project_id)
            .order("created_at", desc=True)
            .execute()
        ).data or []
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Query failed: {e}")

    by_plan = _branches_with_revisions([p["id"] for p in plans])
    for p in plans:
        p["branches"] = by_plan.get(p["id"], [])
        p["current_status"] = _plan_current_status(p["branches"])
    return {"plans": plans}


@router.get("/by-files")
def plans_by_files(
    file_ids: str = Query(..., description="Comma-separated vault file IDs"),
    current_user: dict = Depends(get_current_user),
):
    """
    Which vault files are plan revisions — powers the Vault "is a plan" badge.
    Result: { assignments: [{file_id, plan_id, plan_name, branch_id, branch_name, status}] }.
    """
    ids = [x.strip() for x in file_ids.split(",") if x.strip()]
    if not ids:
        return {"assignments": []}
    try:
        revs = (
            supabase.table("plan_revisions")
            .select("file_id, branch_id, status, label")
            .in_("file_id", ids)
            .execute()
        ).data or []
        branch_ids = list({r["branch_id"] for r in revs})
        branches = (
            supabase.table("plan_branches").select("id, plan_id, name").in_("id", branch_ids).execute().data
            if branch_ids else []
        ) or []
        plan_ids = list({b["plan_id"] for b in branches})
        plans = (
            supabase.table("project_plans").select("id, name").in_("id", plan_ids).execute().data
            if plan_ids else []
        ) or []
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Query failed: {e}")

    branch_map = {b["id"]: b for b in branches}
    plan_map = {p["id"]: p for p in plans}
    assignments = []
    for r in revs:
        b = branch_map.get(r["branch_id"], {})
        p = plan_map.get(b.get("plan_id"), {})
        assignments.append({
            "file_id": r["file_id"],
            "plan_id": b.get("plan_id"),
            "plan_name": p.get("name"),
            "branch_id": r["branch_id"],
            "branch_name": b.get("name"),
            "status": r["status"],
            "label": r["label"],
        })
    return {"assignments": assignments}


@router.get("/{plan_id}")
def get_plan(plan_id: str, current_user: dict = Depends(get_current_user)):
    """Full plan detail: branches + their revisions (the submittal timeline)."""
    try:
        res = supabase.table("project_plans").select("*").eq("id", plan_id).execute()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Query failed: {e}")
    if not res.data:
        raise HTTPException(status_code=404, detail="Plan not found")
    plan = res.data[0]
    plan["branches"] = _branches_with_revisions([plan_id]).get(plan_id, [])
    plan["current_status"] = _plan_current_status(plan["branches"])
    return plan


@router.post("", status_code=201)
def create_plan(payload: PlanCreate, current_user: dict = Depends(get_current_user)):
    """Create a plan from its first uploaded PDF: plan + default 'main' branch + Rev 0."""
    user_id = current_user.get("user_id")
    status = _check_status(payload.status) or "submitted"
    try:
        plan = supabase.table("project_plans").insert({
            "project_id": payload.project_id,
            "name": payload.name.strip(),
            "discipline": (payload.discipline or "").strip() or None,
            "created_by": user_id,
        }).execute().data
        if not plan:
            raise HTTPException(status_code=500, detail="Create failed")
        plan_id = plan[0]["id"]

        branch = supabase.table("plan_branches").insert({
            "plan_id": plan_id,
            "name": "main",
            "is_default": True,
            "created_by": user_id,
        }).execute().data
        branch_id = branch[0]["id"]

        supabase.table("plan_revisions").insert({
            "branch_id": branch_id,
            "file_id": payload.file_id,
            "label": (payload.revision_label or "Rev 0").strip(),
            "status": status,
            "submitted_at": _now() if status == "submitted" else None,
            "created_by": user_id,
        }).execute()
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error creating plan: {e}")

    return get_plan(plan_id, current_user)


@router.delete("/{plan_id}")
def delete_plan(plan_id: str, current_user: dict = Depends(get_current_user)):
    """Delete a plan and (cascade) its branches + revisions. Vault files are untouched."""
    try:
        supabase.table("project_plans").delete().eq("id", plan_id).execute()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Delete failed: {e}")
    return {"ok": True, "id": plan_id}


# ====== BRANCHES ======

@router.post("/{plan_id}/branches", status_code=201)
def create_branch(plan_id: str, payload: BranchCreate, current_user: dict = Depends(get_current_user)):
    """Open a new branch off a plan (optionally forked from parent_branch_id)."""
    try:
        row = supabase.table("plan_branches").insert({
            "plan_id": plan_id,
            "name": payload.name.strip(),
            "parent_branch_id": payload.parent_branch_id,
            "is_default": False,
            "created_by": current_user.get("user_id"),
        }).execute().data
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Create failed: {e}")
    if not row:
        raise HTTPException(status_code=500, detail="Create failed")
    return row[0]


@router.delete("/branches/{branch_id}")
def delete_branch(branch_id: str, current_user: dict = Depends(get_current_user)):
    try:
        supabase.table("plan_branches").delete().eq("id", branch_id).execute()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Delete failed: {e}")
    return {"ok": True, "id": branch_id}


# ====== REVISIONS (submittals) ======

@router.post("/branches/{branch_id}/revisions", status_code=201)
def create_revision(branch_id: str, payload: RevisionCreate, current_user: dict = Depends(get_current_user)):
    """Add a submittal/revision (a newly uploaded PDF) to a branch."""
    status = _check_status(payload.status) or "submitted"
    submitted_at = payload.submitted_at or (_now() if status == "submitted" else None)
    try:
        row = supabase.table("plan_revisions").insert({
            "branch_id": branch_id,
            "file_id": payload.file_id,
            "label": (payload.label or "Rev").strip(),
            "status": status,
            "submitted_at": submitted_at,
            "due_at": payload.due_at,
            "notes": payload.notes,
            "created_by": current_user.get("user_id"),
        }).execute().data
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Create failed: {e}")
    if not row:
        raise HTTPException(status_code=500, detail="Create failed")
    return row[0]


@router.patch("/revisions/{revision_id}")
def update_revision(revision_id: str, payload: RevisionUpdate, current_user: dict = Depends(get_current_user)):
    """Update a revision's status / dates / notes / label."""
    updates: Dict[str, Any] = {}
    if payload.label is not None:
        updates["label"] = payload.label.strip()
    if payload.status is not None:
        updates["status"] = _check_status(payload.status)
    for field in ("submitted_at", "reviewed_at", "due_at", "notes"):
        val = getattr(payload, field)
        if val is not None:
            updates[field] = val
    if not updates:
        raise HTTPException(status_code=400, detail="Nothing to update")
    try:
        res = supabase.table("plan_revisions").update(updates).eq("id", revision_id).execute()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Update failed: {e}")
    if not res.data:
        raise HTTPException(status_code=404, detail="Revision not found")
    return res.data[0]


@router.delete("/revisions/{revision_id}")
def delete_revision(revision_id: str, current_user: dict = Depends(get_current_user)):
    try:
        supabase.table("plan_revisions").delete().eq("id", revision_id).execute()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Delete failed: {e}")
    return {"ok": True, "id": revision_id}
