"""
Beta Access Router
Public lead-capture endpoint for the landing page "request early access" form,
plus authenticated admin endpoints powering the internal Leads Management page.
Stores beta access requests in Supabase (table: beta_access_requests).
The landing POST is public; the /requests admin endpoints require a session.
"""
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from api.rate_limit import limiter
from pydantic import BaseModel

from api.auth import get_current_user, require_internal
from api.supabase_client import supabase

router = APIRouter(prefix="/beta", tags=["beta"])

logger = logging.getLogger("beta_access")

# Lifecycle states an internal user can move a lead through.
LEAD_STATUSES = {"pending", "contacted", "qualified", "converted", "rejected"}


# ── Models ────────────────────────────────────────────────────

class BetaAccessRequest(BaseModel):
    name: str
    email: str
    company: Optional[str] = None
    phone: Optional[str] = None
    role: Optional[str] = None
    industry: Optional[str] = None
    active_projects: Optional[str] = None
    plan_interest: Optional[str] = None
    billing_period: Optional[str] = None
    team_size: Optional[str] = None
    message: Optional[str] = None
    source: Optional[str] = "landing-beta"
    lang: Optional[str] = None


class LeadUpdate(BaseModel):
    """Partial update for an internal lead-management action."""
    status: Optional[str] = None
    notes: Optional[str] = None


def _clean(value: Optional[str]) -> Optional[str]:
    """Trim and normalise empty strings to None so the DB stays tidy."""
    if value is None:
        return None
    value = value.strip()
    return value or None


def _build_linked_lead(beta_id: Any, row: Dict[str, Any]) -> Dict[str, Any]:
    """
    Build a Leads-inbox row mirroring a demo/beta request so coordination can
    follow up. Linked back to the request via linked_request_id; the synthesized
    message carries the request context the Leads table has no columns for.
    """
    bits = []
    for label, key in (
        ("Company", "company"), ("Role", "role"), ("Industry", "industry"),
        ("Team size", "team_size"), ("Active proj", "active_projects"),
        ("Plan", "plan_interest"), ("Billing", "billing_period"),
    ):
        val = row.get(key)
        if val:
            bits.append(f"{label}: {val}")
    message = "Demo/beta access request."
    if bits:
        message += " " + " / ".join(bits)
    if row.get("message"):
        message += "\n\nMessage: " + str(row["message"])
    return {
        "name": row.get("name"),
        "email": row.get("email"),
        "message": message,
        "source": "demo-request",
        "lang": row.get("lang"),
        "status": "new",
        "linked_request_id": beta_id,
    }


# ── POST /beta/request-access ──────────────────────────────────

@router.post("/request-access")
@limiter.limit("5/minute;30/hour")
async def request_beta_access(request: Request, req: BetaAccessRequest):
    """
    Submit a beta access request from the landing page.
    Public endpoint — no auth required.
    """
    name = _clean(req.name)
    email = _clean(req.email)
    if not name or not email:
        raise HTTPException(status_code=400, detail="Name and email are required")

    row = {
        "name": name,
        "email": email,
        "company": _clean(req.company),
        "phone": _clean(req.phone),
        "role": _clean(req.role),
        "industry": _clean(req.industry),
        "active_projects": _clean(req.active_projects),
        "plan_interest": _clean(req.plan_interest),
        "billing_period": _clean(req.billing_period),
        "team_size": _clean(req.team_size),
        "message": _clean(req.message),
        "source": _clean(req.source) or "landing-beta",
        "lang": _clean(req.lang),
        "status": "pending",
        "requested_at": datetime.now(timezone.utc).isoformat(),
    }

    try:
        result = supabase.table("beta_access_requests").insert(row).execute()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save request: {e}")

    if not result.data:
        raise HTTPException(status_code=500, detail="Failed to save request")

    beta_id = result.data[0].get("id")

    # Mirror the request into the Leads inbox (coordination side), linked back to
    # the request. Best-effort: never fail the public submission if this errors.
    try:
        supabase.table("contact_messages").insert(_build_linked_lead(beta_id, row)).execute()
    except Exception as e:
        logger.warning("Could not create linked lead for beta request %s: %r", beta_id, e)

    return {"success": True, "id": beta_id}


# ── Admin (internal portal) — Leads Management ─────────────────
# These require a valid session token. The landing POST above stays public.

@router.get("/requests")
async def list_beta_requests(
    q: Optional[str] = Query(default=None, description="Search by name, email or company"),
    status: Optional[str] = Query(default=None, description="Filter by lifecycle status"),
    current_user: dict = Depends(require_internal),
) -> List[Dict[str, Any]]:
    """
    List beta access requests (leads) for the internal Leads Management page.
    Newest first, with optional status filter and free-text search.
    """
    try:
        qry = supabase.table("beta_access_requests").select("*")

        status_value = _clean(status)
        if status_value:
            qry = qry.eq("status", status_value)

        search = _clean(q)
        if search:
            like = f"%{search}%"
            qry = qry.or_(f"name.ilike.{like},email.ilike.{like},company.ilike.{like}")

        response = qry.order("requested_at", desc=True).execute()
        return response.data or []
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching leads: {e}")


@router.patch("/requests/{lead_id}")
async def update_beta_request(
    lead_id: str,
    payload: LeadUpdate,
    current_user: dict = Depends(require_internal),
):
    """
    Update a lead's status and/or internal notes (partial update).
    """
    update: Dict[str, Any] = {}

    if payload.status is not None:
        status_value = _clean(payload.status)
        if status_value and status_value not in LEAD_STATUSES:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid status '{status_value}'. Allowed: {', '.join(sorted(LEAD_STATUSES))}",
            )
        update["status"] = status_value

    if payload.notes is not None:
        update["notes"] = _clean(payload.notes)

    if not update:
        raise HTTPException(status_code=400, detail="No fields to update")

    update["updated_at"] = datetime.now(timezone.utc).isoformat()

    try:
        result = supabase.table("beta_access_requests").update(update).eq("id", lead_id).execute()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to update lead: {e}")

    if not result.data:
        raise HTTPException(status_code=404, detail="Lead not found")

    return {"success": True, "data": result.data[0]}


@router.delete("/requests/{lead_id}")
async def delete_beta_request(
    lead_id: str,
    current_user: dict = Depends(require_internal),
):
    """
    Delete a lead from the beta access requests list.
    """
    try:
        result = supabase.table("beta_access_requests").delete().eq("id", lead_id).execute()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete lead: {e}")

    if not result.data:
        raise HTTPException(status_code=404, detail="Lead not found")

    return {"success": True}
