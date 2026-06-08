"""
Contact Router
Public contact-form endpoint for the landing page "Contact us" modal, plus
authenticated admin endpoints powering the internal Contact inbox in the hub.
Stores contact messages in Supabase (table: contact_messages).

Kept SEPARATE from beta_access / leads on purpose: a contact message is a
general inquiry, not a beta-access lead, so it lives in its own table with its
own lifecycle (new -> read -> replied -> archived) and its own hub view.

The POST is public (the landing form is unauthenticated); the admin endpoints
require a valid session.
"""
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from api.rate_limit import limiter
from pydantic import BaseModel

from api.auth import get_current_user, require_internal
from api.supabase_client import supabase

router = APIRouter(prefix="/contact", tags=["contact"])

# Lifecycle states an internal user can move a contact message through.
CONTACT_STATUSES = {"new", "read", "replied", "archived"}


# ── Models ────────────────────────────────────────────────────

class ContactMessageRequest(BaseModel):
    name: str
    email: str
    message: Optional[str] = None
    source: Optional[str] = "landing-contact"
    lang: Optional[str] = None


class ContactUpdate(BaseModel):
    """Partial update for an internal contact-inbox action."""
    status: Optional[str] = None
    notes: Optional[str] = None


def _clean(value: Optional[str]) -> Optional[str]:
    """Trim and normalise empty strings to None so the DB stays tidy."""
    if value is None:
        return None
    value = value.strip()
    return value or None


# ── POST /contact ─────────────────────────────────────────────

@router.post("")
@limiter.limit("5/minute;30/hour")
async def submit_contact_message(request: Request, req: ContactMessageRequest):
    """
    Submit a contact message from the landing page.
    Public endpoint — no auth required.
    """
    name = _clean(req.name)
    email = _clean(req.email)
    if not name or not email:
        raise HTTPException(status_code=400, detail="Name and email are required")

    row = {
        "name": name,
        "email": email,
        "message": _clean(req.message),
        "source": _clean(req.source) or "landing-contact",
        "lang": _clean(req.lang),
        "status": "new",
        "submitted_at": datetime.now(timezone.utc).isoformat(),
    }

    try:
        result = supabase.table("contact_messages").insert(row).execute()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save message: {e}")

    if not result.data:
        raise HTTPException(status_code=500, detail="Failed to save message")

    return {"success": True, "id": result.data[0].get("id")}


# ── Admin (internal hub) — Contact inbox ──────────────────────
# These require a valid session token. The POST above stays public.

@router.get("")
async def list_contact_messages(
    q: Optional[str] = Query(default=None, description="Search by name, email or message"),
    status: Optional[str] = Query(default=None, description="Filter by lifecycle status"),
    current_user: dict = Depends(require_internal),
) -> List[Dict[str, Any]]:
    """
    List contact messages for the internal Contact inbox.
    Newest first, with optional status filter and free-text search.
    """
    try:
        qry = supabase.table("contact_messages").select("*")

        status_value = _clean(status)
        if status_value:
            qry = qry.eq("status", status_value)

        search = _clean(q)
        if search:
            like = f"%{search}%"
            qry = qry.or_(f"name.ilike.{like},email.ilike.{like},message.ilike.{like}")

        response = qry.order("submitted_at", desc=True).execute()
        return response.data or []
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching contact messages: {e}")


@router.patch("/{message_id}")
async def update_contact_message(
    message_id: str,
    payload: ContactUpdate,
    current_user: dict = Depends(require_internal),
):
    """
    Update a contact message's status and/or internal notes (partial update).
    """
    update: Dict[str, Any] = {}

    if payload.status is not None:
        status_value = _clean(payload.status)
        if status_value and status_value not in CONTACT_STATUSES:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid status '{status_value}'. Allowed: {', '.join(sorted(CONTACT_STATUSES))}",
            )
        update["status"] = status_value

    if payload.notes is not None:
        update["notes"] = _clean(payload.notes)

    if not update:
        raise HTTPException(status_code=400, detail="No fields to update")

    update["updated_at"] = datetime.now(timezone.utc).isoformat()

    try:
        result = supabase.table("contact_messages").update(update).eq("id", message_id).execute()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to update message: {e}")

    if not result.data:
        raise HTTPException(status_code=404, detail="Message not found")

    return {"success": True, "data": result.data[0]}


@router.delete("/{message_id}")
async def delete_contact_message(
    message_id: str,
    current_user: dict = Depends(require_internal),
):
    """
    Delete a contact message from the inbox.
    """
    try:
        result = supabase.table("contact_messages").delete().eq("id", message_id).execute()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete message: {e}")

    if not result.data:
        raise HTTPException(status_code=404, detail="Message not found")

    return {"success": True}
