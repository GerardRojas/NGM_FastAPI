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
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from api.auth import JWT_SECRET, JWT_ALG, require_internal
from api.supabase_client import supabase
from api.routers import portal as portal_mod
from api.services import portal_notify
from api.services.email import FRONTEND_URL
from utils.auth import hash_password

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/connect", tags=["NGM Connect"])

VALID_ITEM_TYPES = {"photo", "plan_revision", "vault_file", "milestone", "phase", "deal", "estimate"}
INVITE_TTL_DAYS = 14

# Modules an anonymous link may expose. Client-only modules (messages, invoices)
# need an identity, so they can never ride a link — mirrors portal.CLIENT_ONLY_MODULES.
LINK_SHAREABLE_MODULES = {"overview", "photos", "plans", "timeline", "documents", "deals", "estimates"}


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


class CaptionUpdate(BaseModel):
    client_caption: Optional[str] = None


class ClientMessageCreate(BaseModel):
    content: str = ""
    attachments: List[Dict[str, Any]] = Field(default_factory=list)


class InvoiceCreate(BaseModel):
    description: str = Field(..., min_length=1)
    amount_cents: Optional[int] = None     # required for fixed; None for open-amount
    link_type: str = "fixed"               # "fixed" | "open"
    expires_days: int = 30
    caption: Optional[str] = None


class LinkCreate(BaseModel):
    project_id: str = Field(..., min_length=1)
    modules: Dict[str, bool] = Field(default_factory=dict)
    label: Optional[str] = None
    expires_days: int = 30                  # 1..365


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


@router.patch("/shares/{share_id}")
def update_share_caption(share_id: str, payload: CaptionUpdate, user: dict = Depends(require_internal)):
    """Edit a published item's client-facing caption (blank clears it)."""
    caption = (payload.client_caption or "").strip() or None
    try:
        res = (
            supabase.table("portal_shares")
            .update({"client_caption": caption})
            .eq("id", share_id)
            .eq("is_active", True)
            .execute()
        )
    except Exception as e:
        logger.error("[connect] update caption failed: %s", e)
        raise HTTPException(status_code=500, detail="Could not update caption")
    if not res.data:
        raise HTTPException(status_code=404, detail="Active share not found")
    return res.data[0]


# ============================================================
# Curation lists — every candidate item + its shared flag, so the team can
# publish/unpublish from inside NGM Connect (not just preview what's live).
# A single dispatcher serves every curatable section; each builder returns a
# uniform item shape and the SAME safe display subset as the portal builders
# (never internal fields). Estimates stay preview-only for now (no project-
# scoped source to enumerate candidates from).
# ============================================================

def _shares_by_item(project_id: str, item_type: str) -> Dict[str, dict]:
    try:
        rows = (
            supabase.table("portal_shares")
            .select("id, item_id, client_caption")
            .eq("project_id", project_id)
            .eq("item_type", item_type)
            .eq("is_active", True)
            .execute()
        ).data or []
    except Exception as e:
        logger.error("[connect] curate shares lookup (%s) failed: %s", item_type, e)
        rows = []
    return {str(r.get("item_id")): r for r in rows}


def _curate_item(item_type, item_id, smap, label, sublabel=None, url=None, thumb=None):
    s = smap.get(str(item_id))
    return {
        "item_id": str(item_id),
        "item_type": item_type,
        "label": label,
        "sublabel": sublabel,
        "url": url,
        "thumb": thumb,
        "shared": s is not None,
        "share_id": s.get("id") if s else None,
        "caption": (s or {}).get("client_caption") if s else None,
    }


def _cur_photos(project_id: str, user_id: str):
    smap = _shares_by_item(project_id, "photo")
    files = (
        supabase.table("vault_files")
        .select("id, name, mime_type, created_at")
        .eq("project_id", project_id).eq("is_folder", False).eq("is_deleted", False)
        .ilike("mime_type", "image/%").order("created_at", desc=True).execute()
    ).data or []
    out = []
    for f in files:
        url = portal_mod._file_url(str(f["id"]))
        out.append(_curate_item("photo", f["id"], smap, f.get("name") or "Photo", f.get("mime_type"), url=url, thumb=url))
    return out


def _cur_documents(project_id: str, user_id: str):
    smap = _shares_by_item(project_id, "vault_file")
    files = (
        supabase.table("vault_files")
        .select("id, name, mime_type, size_bytes, created_at")
        .eq("project_id", project_id).eq("is_folder", False).eq("is_deleted", False)
        .order("created_at", desc=True).execute()
    ).data or []
    out = []
    for f in files:
        mt = f.get("mime_type") or ""
        if mt.startswith("image/"):
            continue  # images are curated under Photos
        out.append(_curate_item("vault_file", f["id"], smap, f.get("name") or "File", mt or "file",
                                 url=portal_mod._file_url(str(f["id"]))))
    return out


def _cur_plans(project_id: str, user_id: str):
    smap = _shares_by_item(project_id, "plan_revision")
    plans = (
        supabase.table("project_plans").select("id, name, discipline").eq("project_id", project_id).execute()
    ).data or []
    if not plans:
        return []
    plmap = {p["id"]: p for p in plans}
    branches = (
        supabase.table("plan_branches").select("id, plan_id").in_("plan_id", list(plmap.keys())).execute()
    ).data or []
    if not branches:
        return []
    bmap = {b["id"]: b for b in branches}
    revs = (
        supabase.table("plan_revisions")
        .select("id, branch_id, file_id, label, status, created_at")
        .in_("branch_id", list(bmap.keys())).order("created_at", desc=True).execute()
    ).data or []
    out = []
    for r in revs:
        b = bmap.get(r.get("branch_id"), {})
        pl = plmap.get(b.get("plan_id"), {})
        sub = " · ".join([x for x in [pl.get("discipline"), r.get("label"), r.get("status")] if x]) or None
        url = portal_mod._file_url(str(r["file_id"])) if r.get("file_id") else None
        out.append(_curate_item("plan_revision", r["id"], smap, pl.get("name") or "Plan", sub, url=url))
    return out


def _cur_timeline(project_id: str, user_id: str):
    pmap = _shares_by_item(project_id, "phase")
    mmap = _shares_by_item(project_id, "milestone")
    out = []
    phases = (
        supabase.table("project_phases")
        .select("phase_id, phase_name, status, sort_order").eq("project_id", project_id)
        .order("sort_order").execute()
    ).data or []
    for p in phases:
        sub = " · ".join([x for x in ["Phase", p.get("status")] if x]) or "Phase"
        out.append(_curate_item("phase", p["phase_id"], pmap, p.get("phase_name") or "Phase", sub))
    ms = (
        supabase.table("project_milestones")
        .select("milestone_id, milestone_name, due_date, status").eq("project_id", project_id)
        .order("due_date").execute()
    ).data or []
    for m in ms:
        sub = " · ".join([x for x in ["Milestone", m.get("due_date")] if x]) or "Milestone"
        out.append(_curate_item("milestone", m["milestone_id"], mmap, m.get("milestone_name") or "Milestone", sub))
    return out


def _cur_deals(project_id: str, user_id: str):
    # Deals are user-scoped (not project-scoped): show the staff member's own
    # deals as candidates; publishing attaches the deal to this project's portal.
    smap = _shares_by_item(project_id, "deal")
    rows = (
        supabase.table("fix_flip_deals").select("id, name, data").eq("user_id", user_id).limit(200).execute()
    ).data or []
    out = []
    for r in rows:
        inputs = ((r.get("data") or {}).get("inputs") or {}) if isinstance(r.get("data"), dict) else {}
        ask = inputs.get("purchase_price")
        sub = f"Asking ${ask}" if ask else None
        out.append(_curate_item("deal", r["id"], smap, r.get("name") or "Deal", sub))
    return out


_CURATORS = {
    "photos": _cur_photos,
    "documents": _cur_documents,
    "plans": _cur_plans,
    "timeline": _cur_timeline,
    "deals": _cur_deals,
}


# ============================================================
# Client conversation (team side) — same channel the client uses, but reached
# through Connect (the workspace), never the internal Messages page. Reuses the
# shared helpers in portal.py so both planes write the same rows.
# ============================================================

@router.get("/projects/{project_id}/messages")
def connect_list_messages(project_id: str, user: dict = Depends(require_internal)):
    return {"messages": portal_mod.list_client_messages(project_id)}


@router.post("/projects/{project_id}/messages", status_code=201)
def connect_send_message(
    project_id: str,
    payload: ClientMessageCreate,
    background_tasks: BackgroundTasks,
    user: dict = Depends(require_internal),
):
    msg = portal_mod.post_client_message(project_id, user.get("user_id"), payload.content, payload.attachments)
    # Email the client that the team replied (best-effort, after the response).
    background_tasks.add_task(
        portal_notify.notify_client_new_message,
        project_id,
        user.get("username") or "Your team",
        payload.content or "",
    )
    return msg


# ============================================================
# Invoices (team side) — create a Stripe payment link and share it with the
# project's client in the portal Billing module. Reuses the invoice_links table;
# portal_invoices ties it to the (project, client).
# ============================================================

def _create_invoice_link_row(client_name, client_email, description, amount_cents, link_type, expires_days, created_by):
    now = _now()
    exp = now + timedelta(days=max(1, min(365, expires_days or 30)))
    invoice_ref = f"INV-{hex(int(now.timestamp() * 1000))[-6:].upper()}"
    token = jwt.encode(
        {"type": "invoice_link", "invoice_ref": invoice_ref, "iat": int(now.timestamp()), "exp": int(exp.timestamp())},
        JWT_SECRET, algorithm=JWT_ALG,
    )
    row = {
        "invoice_ref": invoice_ref,
        "client_name": client_name or "Client",
        "client_email": client_email or "",
        "description": description,
        "amount_cents": amount_cents if link_type == "fixed" else None,
        "link_type": link_type,
        "status": "active",
        "token": token,
        "created_by": created_by,
        "expires_at": exp.isoformat(),
    }
    res = supabase.table("invoice_links").insert(row).execute()
    if not res.data:
        raise HTTPException(status_code=500, detail="Failed to create the payment link")
    return res.data[0]


@router.get("/projects/{project_id}/invoices")
def connect_list_invoices(project_id: str, user: dict = Depends(require_internal)):
    return {"invoices": portal_mod.list_project_invoices(project_id)}


@router.post("/projects/{project_id}/invoices", status_code=201)
def connect_create_invoice(
    project_id: str,
    payload: InvoiceCreate,
    background_tasks: BackgroundTasks,
    user: dict = Depends(require_internal),
):
    if payload.link_type not in ("fixed", "open"):
        raise HTTPException(status_code=400, detail="link_type must be 'fixed' or 'open'")
    if payload.link_type == "fixed" and (not payload.amount_cents or payload.amount_cents <= 0):
        raise HTTPException(status_code=400, detail="Amount must be greater than zero for a fixed invoice")

    access = (
        supabase.table("project_client_access").select("client_id").eq("project_id", project_id).limit(1).execute()
    ).data or []
    if not access:
        raise HTTPException(status_code=400, detail="No client is configured for this project")
    client_id = str(access[0]["client_id"])

    client_name = ""
    client_email = ""
    try:
        c = supabase.table("clients").select("client_name").eq("client_id", client_id).limit(1).execute().data or []
        client_name = (c[0].get("client_name") if c else "") or ""
    except Exception:
        pass
    try:
        u = supabase.table("users").select("user_name").eq("client_id", client_id).eq("account_type", "client").limit(1).execute().data or []
        client_email = (u[0].get("user_name") if u else "") or ""
    except Exception:
        pass

    link = _create_invoice_link_row(
        client_name, client_email, payload.description, payload.amount_cents,
        payload.link_type, payload.expires_days, user.get("user_id"),
    )
    ins = supabase.table("portal_invoices").insert({
        "project_id": project_id,
        "client_id": client_id,
        "invoice_link_id": link["id"],
        "caption": payload.caption,
        "created_by": user.get("user_id"),
    }).execute()
    if not ins.data:
        raise HTTPException(status_code=500, detail="Could not save the invoice")

    pay_url = f"{FRONTEND_URL}/client-billing.html?token={link.get('token')}"
    background_tasks.add_task(
        portal_notify.notify_client_new_invoice, project_id, payload.amount_cents, payload.description, pay_url,
    )
    return portal_mod._shape_invoice(ins.data[0], link)


@router.delete("/projects/{project_id}/invoices/{invoice_id}")
def connect_void_invoice(project_id: str, invoice_id: str, user: dict = Depends(require_internal)):
    rows = (
        supabase.table("portal_invoices").select("id, invoice_link_id")
        .eq("id", invoice_id).eq("project_id", project_id).limit(1).execute()
    ).data or []
    if not rows:
        raise HTTPException(status_code=404, detail="Invoice not found")
    link_id = rows[0].get("invoice_link_id")
    if link_id:
        try:
            supabase.table("invoice_links").update({"status": "voided"}).eq("id", link_id).neq("status", "paid").execute()
        except Exception as e:
            logger.error("[connect] void link failed: %s", e)
    try:
        supabase.table("portal_invoices").delete().eq("id", invoice_id).execute()
    except Exception as e:
        logger.error("[connect] delete portal_invoice failed: %s", e)
        raise HTTPException(status_code=500, detail="Could not void the invoice")
    return {"ok": True, "id": invoice_id}


@router.get("/projects/{project_id}/curate/{section}")
def curate_section(project_id: str, section: str, user: dict = Depends(require_internal)):
    """List every candidate item for a section with its shared flag, so the team
    can publish/unpublish from inside the workspace."""
    fn = _CURATORS.get(section)
    if not fn:
        raise HTTPException(status_code=400, detail=f"Section is not curatable: {section}")
    try:
        return {"items": fn(project_id, user.get("user_id"))}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("[connect] curate %s failed: %s", section, e)
        raise HTTPException(status_code=500, detail="Could not load curation list")


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
# External-user access configuration — project_user_access
# ============================================================
# Parallel to project_client_access. Drives workspace access for users with
# is_external=true (external collaborators in Team Management).

@router.get("/users/{user_id}/access")
def list_user_access(user_id: str, user: dict = Depends(require_internal)):
    """Which projects this external user can see, and the enabled modules for each."""
    try:
        res = (
            supabase.table("project_user_access")
            .select("id, project_id, modules, granted_at")
            .eq("user_id", user_id)
            .execute()
        )
    except Exception as e:
        logger.error("[connect] list_user_access failed: %s", e)
        raise HTTPException(status_code=500, detail="Could not list access")
    return {"access": res.data or []}


@router.post("/users/{user_id}/access")
def upsert_user_access(user_id: str, payload: AccessUpsert, user: dict = Depends(require_internal)):
    """Grant/update an external user's access to a project and its enabled portal modules."""
    bad = set(payload.modules) - portal_mod.PORTAL_MODULES
    if bad:
        raise HTTPException(status_code=400, detail=f"Unknown modules: {sorted(bad)}")
    existing = (
        supabase.table("project_user_access")
        .select("id")
        .eq("user_id", user_id)
        .eq("project_id", payload.project_id)
        .limit(1)
        .execute()
    ).data or []
    try:
        if existing:
            res = (
                supabase.table("project_user_access")
                .update({"modules": payload.modules})
                .eq("id", existing[0]["id"])
                .execute()
            )
        else:
            res = supabase.table("project_user_access").insert({
                "user_id": user_id,
                "project_id": payload.project_id,
                "modules": payload.modules,
                "granted_by": user.get("user_id"),
            }).execute()
    except Exception as e:
        logger.error("[connect] upsert user access failed: %s", e)
        raise HTTPException(status_code=500, detail="Could not save access")
    if not res.data:
        raise HTTPException(status_code=500, detail="Save returned no data")
    return res.data[0]


@router.delete("/users/{user_id}/access/{project_id}")
def revoke_user_access(user_id: str, project_id: str, user: dict = Depends(require_internal)):
    """Remove an external user's access to a project entirely."""
    try:
        supabase.table("project_user_access").delete().eq("user_id", user_id).eq("project_id", project_id).execute()
    except Exception as e:
        logger.error("[connect] revoke user access failed: %s", e)
        raise HTTPException(status_code=500, detail="Could not revoke access")
    return {"ok": True}


@router.post("/users/{user_id}/invite", status_code=201)
def create_user_invite(user_id: str, background_tasks: BackgroundTasks, user: dict = Depends(require_internal)):
    """Email an external user a magic-link to set their password and reach their
    workspace. The user must already exist in Team Management and be is_external;
    their user_name is used as the email address (convention, like clients)."""
    rows = (
        supabase.table("users").select("user_id, user_name, is_external")
        .eq("user_id", user_id).limit(1).execute()
    ).data or []
    if not rows:
        raise HTTPException(status_code=404, detail="User not found")
    u = rows[0]
    if not u.get("is_external"):
        raise HTTPException(status_code=400, detail="Only external users can be invited to a workspace")
    email = (u.get("user_name") or "").strip()
    if not email:
        raise HTTPException(status_code=400, detail="This user has no email/username to send to")

    now = _now()
    exp = now + timedelta(days=INVITE_TTL_DAYS)
    token = jwt.encode(
        {"type": "external_invite", "user_id": str(user_id), "iat": int(now.timestamp()), "exp": int(exp.timestamp())},
        JWT_SECRET, algorithm=JWT_ALG,
    )
    accept_path = f"/portal/accept?token={token}"
    background_tasks.add_task(portal_notify.send_invite_email, email, accept_path, "")
    return {"path": accept_path, "expires_at": exp.isoformat()}


# ============================================================
# Workspace enumeration — every configured (external party, project) pair
# ============================================================

@router.get("/workspaces")
def list_workspaces(user: dict = Depends(require_internal)):
    """Enumerate every configured workspace — clients AND external users — in a
    handful of batched queries. Powers the team-mode switcher, replacing the
    per-client N+1 the frontend used to walk. Names are resolved in one fetch per
    referenced table rather than a join, to avoid PostgREST relationship ambiguity.
    """
    client_rows: list = []
    user_rows: list = []
    try:
        client_rows = (
            supabase.table("project_client_access")
            .select("project_id, modules, client_id")
            .execute()
        ).data or []
    except Exception as e:
        logger.error("[connect] list_workspaces (client access) failed: %s", e)
    try:
        user_rows = (
            supabase.table("project_user_access")
            .select("project_id, modules, user_id")
            .execute()
        ).data or []
    except Exception as e:
        logger.error("[connect] list_workspaces (user access) failed: %s", e)

    project_ids = {str(r.get("project_id")) for r in client_rows + user_rows if r.get("project_id")}
    client_ids = {str(r.get("client_id")) for r in client_rows if r.get("client_id")}
    user_ids = {str(r.get("user_id")) for r in user_rows if r.get("user_id")}

    def _name_map(table: str, id_col: str, name_col: str, ids: set) -> dict:
        if not ids:
            return {}
        try:
            res = (
                supabase.table(table)
                .select(f"{id_col}, {name_col}")
                .in_(id_col, list(ids))
                .execute()
            ).data or []
            return {str(r.get(id_col)): r.get(name_col) for r in res}
        except Exception as e:
            logger.error("[connect] list_workspaces name map %s failed: %s", table, e)
            return {}

    project_names = _name_map("projects", "project_id", "project_name", project_ids)
    client_names = _name_map("clients", "client_id", "client_name", client_ids)
    user_names = _name_map("users", "user_id", "user_name", user_ids)

    out: list = []
    for r in client_rows:
        cid = str(r.get("client_id") or "")
        pid = str(r.get("project_id") or "")
        if not cid or not pid:
            continue
        out.append({
            "external_type": "client",
            "external_id": cid,
            "external_name": client_names.get(cid) or "",
            "project_id": pid,
            "project_name": project_names.get(pid) or "Project",
            "modules": r.get("modules") or {},
        })
    for r in user_rows:
        uid = str(r.get("user_id") or "")
        pid = str(r.get("project_id") or "")
        if not uid or not pid:
            continue
        out.append({
            "external_type": "user",
            "external_id": uid,
            "external_name": user_names.get(uid) or "",
            "project_id": pid,
            "project_name": project_names.get(pid) or "Project",
            "modules": r.get("modules") or {},
        })
    return {"workspaces": out}


# ============================================================
# WYSIWYG preview — render the portal as a given client sees it
# ============================================================

@router.get("/workspace")
def preview_workspace(
    client_id: str = Query(...),
    project_id: str = Query(...),
    external_type: str = Query("client"),  # "client" | "user" (external collaborator)
    user: dict = Depends(require_internal),
):
    """
    Return everything the portal would show this external party for this project,
    using the exact same builders as /portal. `client_id` carries the client_id OR
    the external user_id depending on external_type. If access isn't configured
    yet, modules come back empty so the UI can prompt the team to configure it.
    """
    if external_type == "user":
        access = portal_mod.get_user_access(client_id, project_id)
    else:
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
def create_invite(
    client_id: str,
    payload: InviteCreate,
    background_tasks: BackgroundTasks,
    user: dict = Depends(require_internal),
):
    """
    Create a magic-link invitation for a client account and email it. Mirrors
    invoice_links: a signed JWT stored with status; the URL is also returned so
    the team can copy/share it manually if needed.
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
    accept_path = f"/portal/accept?token={token}"
    # Resolve the client's display name for the email (best-effort).
    client_name = ""
    try:
        c = supabase.table("clients").select("client_name").eq("client_id", client_id).limit(1).execute().data or []
        client_name = (c[0].get("client_name") if c else "") or ""
    except Exception:
        client_name = ""
    background_tasks.add_task(portal_notify.send_invite_email, payload.email.strip().lower(), accept_path, client_name)
    return {
        "id": res.data[0]["id"],
        "token": token,
        "path": accept_path,
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


# ============================================================
# Anonymous workspace links (link-only audience) — team management.
# The public read plane lives in routers/public_workspace.py.
# ============================================================

def _link_url(token: str) -> str:
    return f"{FRONTEND_URL}/workspace?token={token}"


def _sanitize_link_modules(modules: Dict[str, bool]) -> Dict[str, bool]:
    """Keep only enabled, link-shareable modules. Overview is always on so the
    link has a landing page even if nothing else is toggled."""
    out = {k: True for k, v in (modules or {}).items() if v and k in LINK_SHAREABLE_MODULES}
    out["overview"] = True
    return out


@router.post("/links", status_code=201)
def create_link(payload: LinkCreate, user: dict = Depends(require_internal)):
    """Mint a signed, anonymous read-only link to a curated workspace for one
    project. The JWT carries the expiry; the row carries the curated modules and
    lets us revoke server-side."""
    if payload.expires_days < 1 or payload.expires_days > 365:
        raise HTTPException(status_code=400, detail="Expiry must be between 1 and 365 days")
    modules = _sanitize_link_modules(payload.modules)
    now = _now()
    exp = now + timedelta(days=payload.expires_days)
    token = jwt.encode(
        {
            "type": "workspace_link",
            "project_id": payload.project_id,
            "iat": int(now.timestamp()),
            "exp": int(exp.timestamp()),
        },
        JWT_SECRET,
        algorithm=JWT_ALG,
    )
    row = {
        "token": token,
        "project_id": payload.project_id,
        "modules": modules,
        "label": (payload.label or "").strip() or None,
        "status": "active",
        "created_by": user.get("user_id"),
        "expires_at": exp.isoformat(),
    }
    try:
        res = supabase.table("workspace_links").insert(row).execute()
    except Exception as e:
        logger.error("[connect] create_link failed: %s", e)
        raise HTTPException(status_code=500, detail="Could not create link")
    if not res.data:
        raise HTTPException(status_code=500, detail="Link returned no data")
    created = res.data[0]
    return {
        "id": created["id"],
        "token": token,
        "url": _link_url(token),
        "project_id": payload.project_id,
        "modules": modules,
        "label": row["label"],
        "status": "active",
        "expires_at": exp.isoformat(),
    }


@router.get("/links")
def list_links(project_id: Optional[str] = Query(None), user: dict = Depends(require_internal)):
    """List anonymous links, optionally for one project. Includes the share URL so
    the team can re-copy it (the token is needed to rebuild the URL)."""
    try:
        q = supabase.table("workspace_links").select(
            "id, token, project_id, modules, label, status, expires_at, created_at, viewed_at, view_count"
        ).order("created_at", desc=True)
        if project_id:
            q = q.eq("project_id", project_id)
        rows = q.execute().data or []
    except Exception as e:
        logger.error("[connect] list_links failed: %s", e)
        raise HTTPException(status_code=500, detail="Could not list links")
    out = []
    for r in rows:
        token = r.pop("token", "")
        r["url"] = _link_url(token) if token else None
        out.append(r)
    return {"links": out}


@router.delete("/links/{link_id}")
def revoke_link(link_id: str, user: dict = Depends(require_internal)):
    try:
        res = supabase.table("workspace_links").update({"status": "revoked"}).eq("id", link_id).eq("status", "active").execute()
    except Exception as e:
        logger.error("[connect] revoke_link failed: %s", e)
        raise HTTPException(status_code=500, detail="Could not revoke link")
    if not res.data:
        raise HTTPException(status_code=404, detail="Active link not found")
    return {"ok": True, "id": link_id}
