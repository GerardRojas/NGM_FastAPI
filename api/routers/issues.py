"""
Issues / Feedback Router
Internal board where hub users raise issues or suggestions (with optional
screenshot attachments) and admins flag each as resolved or not.

All endpoints require a valid session. Access to the page itself is governed by
role_permissions (module_key 'issues'), same as every other hub module.
"""
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel

from api.auth import get_current_user
from api.supabase_client import supabase

router = APIRouter(prefix="/issues", tags=["issues"])

ATTACHMENTS_BUCKET = "issue-attachments"
MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024  # 20 MB per file
ISSUE_TYPES = {"issue", "suggestion"}
ISSUE_STATUSES = {"open", "resolved"}


# ── Models ────────────────────────────────────────────────────

class IssueCreate(BaseModel):
    title: str
    description: Optional[str] = None
    type: Optional[str] = "issue"


class IssueUpdate(BaseModel):
    status: Optional[str] = None
    title: Optional[str] = None
    description: Optional[str] = None
    type: Optional[str] = None


def _clean(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    value = value.strip()
    return value or None


def _attachments_for(issue_ids: List[str]) -> Dict[str, List[Dict[str, Any]]]:
    """Return a {issue_id: [attachment, ...]} map for the given issues."""
    if not issue_ids:
        return {}
    rows = (
        supabase.table("issue_attachments")
        .select("*")
        .in_("issue_id", issue_ids)
        .order("created_at")
        .execute()
        .data
    ) or []
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(str(row.get("issue_id")), []).append(row)
    return grouped


# ── POST /issues ───────────────────────────────────────────────

@router.post("", status_code=201)
async def create_issue(payload: IssueCreate, current_user: dict = Depends(get_current_user)):
    """Raise a new issue or suggestion. Any authenticated hub user can do this."""
    title = _clean(payload.title)
    if not title:
        raise HTTPException(status_code=400, detail="Title is required")

    issue_type = (_clean(payload.type) or "issue").lower()
    if issue_type not in ISSUE_TYPES:
        issue_type = "issue"

    row = {
        "type": issue_type,
        "title": title,
        "description": _clean(payload.description),
        "status": "open",
        "created_by": current_user.get("user_id"),
        "created_by_name": current_user.get("username"),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    try:
        result = supabase.table("issue_reports").insert(row).execute()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to create issue: {e}")

    if not result.data:
        raise HTTPException(status_code=500, detail="Failed to create issue")

    created = result.data[0]
    created["attachments"] = []
    return created


# ── POST /issues/{id}/attachments ──────────────────────────────

@router.post("/{issue_id}/attachments", status_code=201)
async def upload_issue_attachment(
    issue_id: str,
    file: UploadFile = File(...),
    current_user: dict = Depends(get_current_user),
):
    """Attach a screenshot/file to an existing issue."""
    existing = supabase.table("issue_reports").select("id").eq("id", issue_id).execute()
    if not existing.data:
        raise HTTPException(status_code=404, detail="Issue not found")

    file_content = await file.read()
    if not file_content:
        raise HTTPException(status_code=400, detail="Empty file")
    if len(file_content) > MAX_ATTACHMENT_BYTES:
        raise HTTPException(status_code=413, detail="File too large (max 20MB)")

    safe_name = (file.filename or "attachment").replace("/", "_").replace("\\", "_")
    bucket_path = f"{issue_id}/{uuid.uuid4().hex}_{safe_name}"
    content_type = file.content_type or "application/octet-stream"

    try:
        supabase.storage.from_(ATTACHMENTS_BUCKET).upload(
            path=bucket_path,
            file=file_content,
            file_options={"content-type": content_type, "upsert": "true"},
        )
        public_url = supabase.storage.from_(ATTACHMENTS_BUCKET).get_public_url(bucket_path)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to upload attachment: {e}")

    row = {
        "issue_id": issue_id,
        "file_name": safe_name,
        "bucket_path": bucket_path,
        "file_url": public_url,
        "mime_type": content_type,
        "size_bytes": len(file_content),
        "uploaded_by": current_user.get("user_id"),
    }

    try:
        result = supabase.table("issue_attachments").insert(row).execute()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save attachment: {e}")

    return result.data[0] if result.data else row


# ── GET /issues ────────────────────────────────────────────────

@router.get("")
async def list_issues(
    q: Optional[str] = Query(default=None, description="Search by title or description"),
    status: Optional[str] = Query(default=None, description="Filter by status"),
    type: Optional[str] = Query(default=None, description="Filter by type"),
    current_user: dict = Depends(get_current_user),
) -> List[Dict[str, Any]]:
    """List issues (newest first) with their attachments."""
    try:
        qry = supabase.table("issue_reports").select("*")

        status_value = _clean(status)
        if status_value:
            qry = qry.eq("status", status_value)

        type_value = _clean(type)
        if type_value:
            qry = qry.eq("type", type_value)

        search = _clean(q)
        if search:
            like = f"%{search}%"
            qry = qry.or_(f"title.ilike.{like},description.ilike.{like}")

        issues = qry.order("created_at", desc=True).execute().data or []
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching issues: {e}")

    attachments = _attachments_for([str(i["id"]) for i in issues if i.get("id")])
    for issue in issues:
        issue["attachments"] = attachments.get(str(issue.get("id")), [])
    return issues


# ── PATCH /issues/{id} ─────────────────────────────────────────

@router.patch("/{issue_id}")
async def update_issue(issue_id: str, payload: IssueUpdate, current_user: dict = Depends(get_current_user)):
    """Update an issue — primarily its status (resolved / not)."""
    update: Dict[str, Any] = {}

    if payload.status is not None:
        status_value = (_clean(payload.status) or "").lower()
        if status_value not in ISSUE_STATUSES:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid status. Allowed: {', '.join(sorted(ISSUE_STATUSES))}",
            )
        update["status"] = status_value
        update["resolved_at"] = datetime.now(timezone.utc).isoformat() if status_value == "resolved" else None

    if payload.title is not None:
        title = _clean(payload.title)
        if not title:
            raise HTTPException(status_code=400, detail="Title cannot be empty")
        update["title"] = title

    if payload.description is not None:
        update["description"] = _clean(payload.description)

    if payload.type is not None:
        type_value = (_clean(payload.type) or "issue").lower()
        if type_value not in ISSUE_TYPES:
            raise HTTPException(status_code=400, detail="Invalid type")
        update["type"] = type_value

    if not update:
        raise HTTPException(status_code=400, detail="No fields to update")

    update["updated_at"] = datetime.now(timezone.utc).isoformat()

    try:
        result = supabase.table("issue_reports").update(update).eq("id", issue_id).execute()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to update issue: {e}")

    if not result.data:
        raise HTTPException(status_code=404, detail="Issue not found")

    updated = result.data[0]
    updated["attachments"] = _attachments_for([issue_id]).get(issue_id, [])
    return updated


# ── DELETE /issues/{id} ────────────────────────────────────────

@router.delete("/{issue_id}")
async def delete_issue(issue_id: str, current_user: dict = Depends(get_current_user)):
    """Delete an issue and its attachments (DB rows + storage objects)."""
    attachments = (
        supabase.table("issue_attachments").select("bucket_path").eq("issue_id", issue_id).execute().data
    ) or []

    paths = [a["bucket_path"] for a in attachments if a.get("bucket_path")]
    if paths:
        try:
            supabase.storage.from_(ATTACHMENTS_BUCKET).remove(paths)
        except Exception:
            # Storage cleanup is best-effort; the DB cascade still removes the rows.
            pass

    try:
        result = supabase.table("issue_reports").delete().eq("id", issue_id).execute()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete issue: {e}")

    if not result.data:
        raise HTTPException(status_code=404, detail="Issue not found")

    return {"success": True}
