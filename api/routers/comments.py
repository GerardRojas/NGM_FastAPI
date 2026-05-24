"""
Cell Comments — cross-module commenting system.
Supports comments on any cell/row in any page with @mentions.
Only the creator can delete their own comments.
"""

import logging
from fastapi import APIRouter, HTTPException, Query, Depends
from pydantic import BaseModel, Field
from typing import Optional, List
from api.supabase_client import supabase
from api.auth import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/comments", tags=["Comments"])

# Maps a comment's `module` to the React route that shows that record, so a
# notification can deep-link straight to where the user was tagged. Falls back
# to /<module>. Covers board/spreadsheet modules plus estimator & vault.
_MODULE_ROUTES = {
    "expenses": "/expenses",
    "budgets": "/budgets",
    "budget_monitor": "/budget-monitor",
    "pipeline": "/pipeline-manager",
    "accounts": "/accounts",
    "vendors": "/vendors",
    "estimator": "/estimator",
    "estimator_database": "/estimator-database",
    "vault": "/vault",
}


def _comment_deep_link(module: str, record_id: str, column_key: Optional[str]) -> str:
    base = _MODULE_ROUTES.get((module or "").lower(), f"/{module}")
    link = f"{base}?record={record_id}"
    if column_key:
        link += f"&col={column_key}"
    return link


# ====== PYDANTIC MODELS ======

class CommentCreate(BaseModel):
    module: str = Field(..., min_length=1, max_length=50)
    record_id: str = Field(..., min_length=1, max_length=255)
    column_key: Optional[str] = Field(default=None, max_length=100)
    body: str = Field(..., min_length=1)
    mentions: Optional[List[str]] = Field(default=[])


class CommentUpdate(BaseModel):
    body: Optional[str] = Field(default=None, min_length=1)
    mentions: Optional[List[str]] = None


class CommentResolve(BaseModel):
    is_resolved: bool


# ====== ENDPOINTS ======

@router.get("/")
def list_comments(
    module: str = Query(...),
    record_id: str = Query(...),
    column_key: Optional[str] = Query(default=None),
    current_user: dict = Depends(get_current_user),
):
    """
    List comments for a specific cell or row.
    If column_key is None, returns ALL comments for the record (all columns + row-level).
    """
    try:
        q = (
            supabase.table("cell_comments")
            .select("*, users!cell_comments_created_by_fkey(user_id, user_name, user_photo, avatar_color)")
            .eq("module", module)
            .eq("record_id", record_id)
        )

        if column_key is not None:
            q = q.eq("column_key", column_key)

        q = q.order("created_at", desc=False)
        res = q.execute()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Query failed: {e}")

    return {"comments": res.data or []}


@router.get("/counts")
def comment_counts(
    module: str = Query(...),
    record_ids: str = Query(..., description="Comma-separated record IDs"),
    current_user: dict = Depends(get_current_user),
):
    """
    Get comment counts per record_id + column_key for batch rendering.
    Returns dict keyed by 'record_id::column_key' with count values.
    """
    ids = [rid.strip() for rid in record_ids.split(",") if rid.strip()]
    if not ids:
        return {"counts": {}}

    try:
        res = (
            supabase.table("cell_comments")
            .select("record_id, column_key")
            .eq("module", module)
            .in_("record_id", ids)
            .execute()
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Query failed: {e}")

    counts = {}
    for row in res.data or []:
        key = f"{row['record_id']}::{row.get('column_key') or ''}"
        counts[key] = counts.get(key, 0) + 1

    return {"counts": counts}


@router.post("/")
def create_comment(
    payload: CommentCreate,
    current_user: dict = Depends(get_current_user),
):
    """Create a new comment. Returns the created row with user info."""
    user_id = current_user.get("user_id")

    row = {
        "module": payload.module,
        "record_id": payload.record_id,
        "column_key": payload.column_key,
        "body": payload.body,
        "mentions": payload.mentions or [],
        "created_by": user_id,
    }

    try:
        res = supabase.table("cell_comments").insert(row).execute()
        if not res.data:
            raise HTTPException(status_code=500, detail="Insert failed")

        comment_id = res.data[0]["id"]

        # In-app notifications feed: one per @mentioned user (dashboard widget).
        if payload.mentions:
            try:
                from api.services.notifications_feed import create_notifications
                create_notifications(
                    payload.mentions,
                    type="mention_comment",
                    module=payload.module,
                    actor_id=user_id,
                    reference_type="comment",
                    reference_id=comment_id,
                    deep_link=_comment_deep_link(payload.module, payload.record_id, payload.column_key),
                    preview=payload.body,
                    context={
                        "module": payload.module,
                        "record_id": payload.record_id,
                        "column_key": payload.column_key,
                    },
                )
            except Exception as notif_err:
                logger.debug("[Comments] notifications feed insert failed: %s", notif_err)

        # Re-fetch with user join
        fetched = (
            supabase.table("cell_comments")
            .select("*, users!cell_comments_created_by_fkey(user_id, user_name, user_photo, avatar_color)")
            .eq("id", comment_id)
            .execute()
        )
        return fetched.data[0] if fetched.data else res.data[0]

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error creating comment: {e}")


@router.patch("/{comment_id}")
def update_comment(
    comment_id: str,
    payload: CommentUpdate,
    current_user: dict = Depends(get_current_user),
):
    """Update a comment. Only the creator can edit."""
    user_id = current_user.get("user_id")

    # Verify ownership
    try:
        existing = (
            supabase.table("cell_comments")
            .select("id, created_by")
            .eq("id", comment_id)
            .execute()
        )
        if not existing.data:
            raise HTTPException(status_code=404, detail="Comment not found")
        if existing.data[0]["created_by"] != user_id:
            raise HTTPException(status_code=403, detail="Only the creator can edit this comment")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error: {e}")

    data = payload.model_dump(exclude_unset=True)
    if not data:
        return existing.data[0]

    try:
        res = supabase.table("cell_comments").update(data).eq("id", comment_id).execute()
        return res.data[0] if res.data else existing.data[0]
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error updating comment: {e}")


@router.patch("/{comment_id}/resolve")
def resolve_comment(
    comment_id: str,
    payload: CommentResolve,
    current_user: dict = Depends(get_current_user),
):
    """Toggle resolved state on a comment."""
    user_id = current_user.get("user_id")

    update_obj = {"is_resolved": payload.is_resolved}
    if payload.is_resolved:
        update_obj["resolved_by"] = user_id
        from datetime import datetime, timezone
        update_obj["resolved_at"] = datetime.now(timezone.utc).isoformat()
    else:
        update_obj["resolved_by"] = None
        update_obj["resolved_at"] = None

    try:
        res = supabase.table("cell_comments").update(update_obj).eq("id", comment_id).execute()
        if not res.data:
            raise HTTPException(status_code=404, detail="Comment not found")
        return res.data[0]
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error: {e}")


@router.delete("/{comment_id}")
def delete_comment(
    comment_id: str,
    current_user: dict = Depends(get_current_user),
):
    """Delete a comment. Only the creator can delete."""
    user_id = current_user.get("user_id")

    # Verify ownership
    try:
        existing = (
            supabase.table("cell_comments")
            .select("id, created_by")
            .eq("id", comment_id)
            .execute()
        )
        if not existing.data:
            raise HTTPException(status_code=404, detail="Comment not found")
        if existing.data[0]["created_by"] != user_id:
            raise HTTPException(status_code=403, detail="Only the creator can delete this comment")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error: {e}")

    try:
        supabase.table("cell_comments").delete().eq("id", comment_id).execute()
        return {"ok": True, "deleted_id": comment_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error deleting comment: {e}")
