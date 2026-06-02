"""
NGM Connect — internal curation & client-access control API (staff only).

This is where the team decides what each client sees:
  * publish / unpublish items to the client portal (portal_shares)
  * configure which projects + portal modules a client can access
  * preview the portal exactly as a given client would see it (WYSIWYG) — this
    reuses the very same builders the client-facing /portal router uses, so the
    preview can never diverge from reality
  * issue magic-link invitations to onboard client accounts

All endpoints require an internal account (require_internal). The portal read
path lives in routers/portal.py.

Tables: portal_shares, project_client_access, client_invites, users.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import jwt
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from api.auth import JWT_SECRET, JWT_ALG, require_internal
from api.supabase_client import supabase
from api.routers import portal as portal_mod
from utils.auth import hash_password

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/connect", tags=["NGM Connect"])

VALID_ITEM_TYPES = {"photo", "plan_revision", "vault_file", "milestone", "phase", "deal", "estimate"}
INVITE_TTL_DAYS = 14


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ============================================================
# Models
# ============================================================

class ShareCreate(BaseModel):
    project_id: str = Field(..., min_length=1)
    item_type: str = Field(..., min_length=1)
    item_id: str = Field(..., min_length=1)
    client_caption: Optional[str] = None


class ShareBulkCreate(BaseModel):
    project_id: str = Field(..., min_length=1)
    item_type: str = Field(..., min_length=1)
    item_ids: List[str] = Field(..., min_length=1)


class AccessUpsert(BaseModel):
    project_id: str = Field(..., min_length=1)
    modules: Dict[str, bool] = Field(default_factory=dict)


class InviteCreate(BaseModel):
    email: str = Field(..., min_length=3)


def _check_item_type(item_type: str) -> str:
    if item_type not in VALID_ITEM_TYPES:
        raise HTTPException(status_code=400, detail=f"Invalid item_type. Allowed: {sorted(VALID_ITEM_TYPES)}")
    return item_type


# ============================================================
# Publishing — portal_shares
# ============================================================

@router.post("/shares", status_code=201)
def publish_item(payload: ShareCreate, user: dict = Depends(require_internal)):
    """Publish one item to the client portal (re-activates if previously unpublished)."""
    _check_item_type(payload.item_type)
    # Reuse an existing row for this (project,type,item) if present — keeps the
    # unique index happy and preserves history across publish/unpublish cycles.
    existing = (
        supabase.table("portal_shares")
        .select("id")
        .eq("project_id", payload.project_id)
        .eq("item_type", payload.item_type)
        .eq("item_id", payload.item_id)
        .limit(1)
        .execute()
    ).data or []
    row = {
        "project_id": payload.project_id,
        "item_type": payload.item_type,
        "item_id": payload.item_id,
        "client_caption": payload.client_caption,
        "shared_by": user.get("user_id"),
        "is_active": True,
        "shared_at": _now().isoformat(),
    }
    try:
        if existing:
            res = supabase.table("portal_shares").update(row).eq("id", existing[0]["id"]).execute()
        else:
            res = supabase.table("portal_shares").insert(row).execute()
    except Exception as e:
        logger.error("[connect] publish failed: %s", e)
        raise HTTPException(status_code=500, detail="Publish failed")
    if not res.data:
        raise HTTPException(status_code=500, detail="Publish returned no data")
    return res.data[0]


@router.post("/shares/bulk")
def publish_bulk(payload: ShareBulkCreate, user: dict = Depends(require_internal)):
    """Publish many items of one type at once (e.g. 'share all photos')."""
    _check_item_type(payload.item_type)
    published = []
    for item_id in payload.item_ids:
        published.append(publish_item(
            ShareCreate(project_id=payload.project_id, item_type=payload.item_type, item_id=item_id),
            user,
        ))
    return {"published": len(published), "shares": published}


@router.delete("/shares/{share_id}")
def unpublish_item(share_id: str, user: dict = Depends(require_internal)):
    """Unpublish: deactivate the share (kept for audit trail)."""
    try:
        res = supabase.table("portal_shares").update({"is_active": False}).eq("id", share_id).execute()
    except Exception as e:
        logger.error("[connect] unpublish failed: %s", e)
        raise HTTPException(status_code=500, detail="Unpublish failed")
    if not res.data:
        raise HTTPException(status_code=404, detail="Share not found")
    return {"ok": True, "id": share_id}


@router.get("/projects/{project_id}/shares")
def list_shares(
    project_id: str,
    item_type: Optional[str] = Query(None),
    user: dict = Depends(require_internal),
):
    """What is currently published for a project (optionally filtered by type)."""
    q = (
        supabase.table("portal_shares")
        .select("id, item_type, item_id, client_caption, shared_by, shared_at")
        .eq("project_id", project_id)
        .eq("is_active", True)
        .order("shared_at", desc=True)
    )
    if item_type:
        q = q.eq("item_type", _check_item_type(item_type))
    try:
        res = q.execute()
    except Exception as e:
        logger.error("[connect] list_shares failed: %s", e)
        raise HTTPException(status_code=500, detail="Could not list shares")
    return {"shares": res.data or []}


# ============================================================
# Client access configuration — project_client_access
# ============================================================

@router.get("/clients/{client_id}/access")
def list_client_access(client_id: str, user: dict = Depends(require_internal)):
    """Which projects this client can see, and the enabled modules for each."""
    try:
        res = (
            supabase.table("project_client_access")
            .select("id, project_id, modules, granted_at")
            .eq("client_id", client_id)
            .execute()
        )
    except Exception as e:
        logger.error("[connect] list_client_access failed: %s", e)
        raise HTTPException(status_code=500, detail="Could not list access")
    return {"access": res.data or []}


@router.post("/clients/{client_id}/access")
def upsert_client_access(client_id: str, payload: AccessUpsert, user: dict = Depends(require_internal)):
    """Grant/update a client's access to a project and its enabled portal modules."""
    bad = set(payload.modules) - portal_mod.PORTAL_MODULES
    if bad:
        raise HTTPException(status_code=400, detail=f"Unknown modules: {sorted(bad)}")
    existing = (
        supabase.table("project_client_access")
        .select("id")
        .eq("client_id", client_id)
        .eq("project_id", payload.project_id)
        .limit(1)
        .execute()
    ).data or []
    try:
        if existing:
            res = (
                supabase.table("project_client_access")
                .update({"modules": payload.modules})
                .eq("id", existing[0]["id"])
                .execute()
            )
        else:
            res = supabase.table("project_client_access").insert({
                "client_id": client_id,
                "project_id": payload.project_id,
                "modules": payload.modules,
                "granted_by": user.get("user_id"),
            }).execute()
    except Exception as e:
        logger.error("[connect] upsert access failed: %s", e)
        raise HTTPException(status_code=500, detail="Could not save access")
    if not res.data:
        raise HTTPException(status_code=500, detail="Save returned no data")
    return res.data[0]


@router.delete("/clients/{client_id}/access/{project_id}")
def revoke_client_access(client_id: str, project_id: str, user: dict = Depends(require_internal)):
    """Remove a client's access to a project entirely."""
    try:
        supabase.table("project_client_access").delete().eq("client_id", client_id).eq("project_id", project_id).execute()
    except Exception as e:
        logger.error("[connect] revoke access failed: %s", e)
        raise HTTPException(status_code=500, detail="Could not revoke access")
    return {"ok": True}


# ============================================================
# WYSIWYG preview — render the portal as a given client sees it
# ============================================================

@router.get("/workspace")
def preview_workspace(
    client_id: str = Query(...),
    project_id: str = Query(...),
    user: dict = Depends(require_internal),
):
    """
    Return everything the portal would show this client for this project, using
    the exact same builders as /portal. If access isn't configured yet, modules
    come back empty so the UI can prompt the team to configure it.
    """
    access = portal_mod.get_access(client_id, project_id)
    modules = (access or {}).get("modules") or {}

    sections: Dict[str, Any] = {}
    if modules.get("overview"):
        sections["overview"] = portal_mod.get_overview(project_id)
    if modules.get("photos"):
        sections["photos"] = portal_mod.get_photos(project_id)
    if modules.get("plans"):
        sections["plans"] = portal_mod.get_plans(project_id)
    if modules.get("timeline"):
        sections["timeline"] = portal_mod.get_timeline(project_id)
    if modules.get("documents"):
        sections["documents"] = portal_mod.get_documents(project_id)
    if modules.get("deals"):
        sections["deals"] = portal_mod.get_deals(project_id)
    if modules.get("estimates"):
        sections["estimates"] = portal_mod.get_estimates(project_id)

    return {
        "client_id": client_id,
        "project_id": project_id,
        "configured": access is not None,
        "modules": modules,
        "sections": sections,
    }


# ============================================================
# Client invitations — magic-link onboarding
# ============================================================

@router.post("/clients/{client_id}/invite", status_code=201)
def create_invite(client_id: str, payload: InviteCreate, user: dict = Depends(require_internal)):
    """
    Create a magic-link invitation for a client account. Mirrors invoice_links:
    a signed JWT stored with status; the URL is returned for the team to send
    (no email is sent from here).
    """
    now = _now()
    exp = now + timedelta(days=INVITE_TTL_DAYS)
    token = jwt.encode(
        {
            "type": "client_invite",
            "client_id": client_id,
            "email": payload.email.strip().lower(),
            "iat": int(now.timestamp()),
            "exp": int(exp.timestamp()),
        },
        JWT_SECRET,
        algorithm=JWT_ALG,
    )
    try:
        res = supabase.table("client_invites").insert({
            "client_id": client_id,
            "email": payload.email.strip().lower(),
            "token": token,
            "status": "pending",
            "created_by": user.get("user_id"),
            "expires_at": exp.isoformat(),
        }).execute()
    except Exception as e:
        logger.error("[connect] create_invite failed: %s", e)
        raise HTTPException(status_code=500, detail="Could not create invite")
    if not res.data:
        raise HTTPException(status_code=500, detail="Invite returned no data")
    return {
        "id": res.data[0]["id"],
        "token": token,
        "path": f"/portal/accept?token={token}",
        "expires_at": exp.isoformat(),
    }


@router.get("/clients/{client_id}/invites")
def list_invites(client_id: str, user: dict = Depends(require_internal)):
    try:
        res = (
            supabase.table("client_invites")
            .select("id, email, status, created_at, expires_at, accepted_at")
            .eq("client_id", client_id)
            .order("created_at", desc=True)
            .execute()
        )
    except Exception as e:
        logger.error("[connect] list_invites failed: %s", e)
        raise HTTPException(status_code=500, detail="Could not list invites")
    return {"invites": res.data or []}


@router.delete("/invites/{invite_id}")
def revoke_invite(invite_id: str, user: dict = Depends(require_internal)):
    try:
        res = supabase.table("client_invites").update({"status": "revoked"}).eq("id", invite_id).eq("status", "pending").execute()
    except Exception as e:
        logger.error("[connect] revoke_invite failed: %s", e)
        raise HTTPException(status_code=500, detail="Could not revoke invite")
    if not res.data:
        raise HTTPException(status_code=404, detail="Pending invite not found")
    return {"ok": True, "id": invite_id}
