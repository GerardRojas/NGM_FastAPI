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
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from api.auth import get_current_user
from api.supabase_client import supabase
from api.services.vault_service import get_download_url

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/portal", tags=["Client Portal"])

# Portal module keys carried in project_client_access.modules
PORTAL_MODULES = {"overview", "photos", "plans", "timeline", "documents", "messages", "deals", "estimates", "invoices"}


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


def get_user_access(user_id: str, project_id: str) -> Optional[Dict[str, Any]]:
    """Return the project_user_access row for (external user, project), or None.
    Parallel to get_access but keyed by users.user_id (external collaborators)."""
    try:
        res = (
            supabase.table("project_user_access")
            .select("modules")
            .eq("user_id", user_id)
            .eq("project_id", project_id)
            .limit(1)
            .execute()
        )
    except Exception as e:
        logger.error("[portal] user access lookup failed: %s", e)
        raise HTTPException(status_code=500, detail="Access lookup failed")
    rows = res.data or []
    return rows[0] if rows else None


# Modules that are CLIENT-only and must never open to external collaborators:
# Messages is the client's private conversation; Invoices is the client's billing.
CLIENT_ONLY_MODULES = {"messages", "invoices"}


def get_portal_principal(current_user: dict = Depends(get_current_user)) -> Dict[str, Any]:
    """The portal's caller, normalized. Accepts client accounts (scope by
    client_id) and external collaborators (scope by user_id). Internal accounts
    are rejected — they use /connect, not /portal."""
    account_type = current_user.get("account_type") or "internal"
    if account_type == "client":
        cid = current_user.get("client_id")
        if not cid:
            raise HTTPException(status_code=403, detail="Client account is not linked to a client")
        return {"kind": "client", "user_id": current_user.get("user_id"), "access_id": str(cid), "client_id": str(cid)}
    if account_type == "external":
        uid = current_user.get("user_id")
        if not uid:
            raise HTTPException(status_code=403, detail="Invalid external session")
        return {"kind": "user", "user_id": str(uid), "access_id": str(uid), "client_id": None}
    raise HTTPException(status_code=403, detail="Client portal access only")


def assert_module(principal: Dict[str, Any], project_id: str, module: str) -> Dict[str, bool]:
    """
    Ensure this principal (client OR external user) may see this project AND the
    module is enabled. External users are additionally barred from client-only
    modules (messages/invoices). Raises 403 otherwise; returns the modules bag.
    """
    if principal.get("kind") == "user" and module in CLIENT_ONLY_MODULES:
        raise HTTPException(status_code=403, detail=f"The '{module}' section is not available")
    if principal.get("kind") == "client":
        access = get_access(principal["access_id"], project_id)
    else:
        access = get_user_access(principal["access_id"], project_id)
    if access is None:
        raise HTTPException(status_code=403, detail="No access to this project")
    modules = access.get("modules") or {}
    if not modules.get(module, False):
        raise HTTPException(status_code=403, detail=f"The '{module}' section is not enabled")
    return modules


# ============================================================
# Client conversation — the one channel a client participates in.
# Lives entirely in the Connect portal (workspace), NOT the internal Messages
# page, but reuses the messages/message_attachments tables. A project has one
# client (projects.client), so the channel is keyed by (channel_type, project).
# Both planes (client via /portal, team via /connect) read/write the same rows.
# ============================================================

CLIENT_CHANNEL_TYPE = "project_client"


class ClientMessageCreate(BaseModel):
    content: str = ""
    attachments: List[Dict[str, Any]] = Field(default_factory=list)


def _client_message_attachments(message_id: str) -> List[Dict[str, Any]]:
    try:
        return (
            supabase.table("message_attachments")
            .select("name, type, size, url, thumbnail_url")
            .eq("message_id", message_id)
            .execute()
        ).data or []
    except Exception:
        return []


def _shape_client_message(row: Dict[str, Any], sender: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    u = sender or row.get("users") or {}
    mid = row.get("id")
    return {
        "id": mid,
        "content": row.get("content") or "",
        "channel_type": row.get("channel_type"),
        "channel_id": row.get("channel_id"),
        "project_id": row.get("project_id"),
        "user_id": row.get("user_id"),
        "user_name": u.get("user_name"),
        "avatar_color": u.get("avatar_color"),
        "reply_to_id": row.get("reply_to_id"),
        "thread_count": 0,
        "is_edited": bool(row.get("is_edited")),
        "is_deleted": bool(row.get("is_deleted")),
        "created_at": row.get("created_at"),
        "reactions": {},
        "attachments": _client_message_attachments(mid),
        "metadata": row.get("metadata"),
    }


def list_client_messages(project_id: str, limit: int = 300) -> List[Dict[str, Any]]:
    """Every message in the project's client channel, oldest first."""
    try:
        rows = (
            supabase.table("messages")
            .select("*, users!user_id(user_name, avatar_color)")
            .eq("channel_type", CLIENT_CHANNEL_TYPE)
            .eq("project_id", project_id)
            .is_("reply_to_id", "null")
            .order("created_at", desc=False)
            .limit(limit)
            .execute()
        ).data or []
    except Exception as e:
        logger.error("[portal] list client messages failed: %s", e)
        raise HTTPException(status_code=500, detail="Could not load messages")
    return [_shape_client_message(r) for r in rows]


def post_client_message(
    project_id: str,
    user_id: str,
    content: str,
    attachments: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Append a message to the project's client channel (from client OR team)."""
    text = (content or "").strip()
    attachments = attachments or []
    if not text and not attachments:
        raise HTTPException(status_code=400, detail="Message must have content or an attachment.")
    try:
        res = (
            supabase.table("messages")
            .insert({
                "content": text,
                "channel_type": CLIENT_CHANNEL_TYPE,
                "project_id": project_id,
                "user_id": user_id,
            })
            .execute()
        )
    except Exception as e:
        logger.error("[portal] post client message failed: %s", e)
        raise HTTPException(status_code=500, detail="Could not send message")
    if not res.data:
        raise HTTPException(status_code=500, detail="Message was not created")
    msg = res.data[0]
    for att in attachments:
        try:
            supabase.table("message_attachments").insert({
                "message_id": msg["id"],
                "name": att.get("name", ""),
                "type": att.get("type", ""),
                "size": att.get("size", 0),
                "url": att.get("url", ""),
                "thumbnail_url": att.get("thumbnail_url"),
            }).execute()
        except Exception as e:
            logger.warning("[portal] attachment insert failed (non-blocking): %s", e)
    sender = None
    try:
        u = supabase.table("users").select("user_name, avatar_color").eq("user_id", user_id).limit(1).execute().data or []
        sender = u[0] if u else None
    except Exception:
        sender = None
    return _shape_client_message(msg, sender)


# ============================================================
# Invoices — a curated wrapper around invoice_links (Stripe). portal_invoices ties
# a Stripe payment link to a (project, client) + caption; the authoritative amount
# and payment status always come from the linked invoice_links row at read time.
# ============================================================

from api.services.email import FRONTEND_URL  # noqa: E402  (settings constant)


def _shape_invoice(pi: Dict[str, Any], link: Dict[str, Any]) -> Dict[str, Any]:
    token = link.get("token")
    return {
        "id": pi.get("id"),
        "invoice_ref": link.get("invoice_ref"),
        "description": link.get("description"),
        "amount_cents": link.get("amount_cents"),
        "link_type": link.get("link_type"),
        "status": link.get("status") or "active",
        "caption": pi.get("caption"),
        "pay_url": f"{FRONTEND_URL}/client-billing.html?token={token}" if token else None,
        "created_at": pi.get("created_at"),
        "viewed_at": pi.get("viewed_at"),
        "paid_at": link.get("paid_at"),
    }


def list_project_invoices(
    project_id: str,
    client_id: Optional[str] = None,
    mark_viewed: bool = False,
) -> List[Dict[str, Any]]:
    """Invoices shared on a project (optionally scoped to one client). Joins the
    invoice_links row for live amount + status. When mark_viewed, stamps any
    unseen invoice as viewed (client opened their billing tab)."""
    try:
        q = supabase.table("portal_invoices").select("*").eq("project_id", project_id)
        if client_id:
            q = q.eq("client_id", client_id)
        rows = q.order("created_at", desc=True).execute().data or []
    except Exception as e:
        logger.error("[portal] list invoices failed: %s", e)
        raise HTTPException(status_code=500, detail="Could not load invoices")
    if not rows:
        return []
    link_ids = [r["invoice_link_id"] for r in rows if r.get("invoice_link_id")]
    links: Dict[str, Dict[str, Any]] = {}
    if link_ids:
        try:
            lrows = (
                supabase.table("invoice_links")
                .select("id, invoice_ref, description, amount_cents, link_type, status, token, paid_at")
                .in_("id", link_ids).execute()
            ).data or []
            links = {str(l["id"]): l for l in lrows}
        except Exception as e:
            logger.error("[portal] invoice links join failed: %s", e)
    if mark_viewed:
        now_iso = datetime.now(timezone.utc).isoformat()
        for r in rows:
            if not r.get("viewed_at"):
                try:
                    supabase.table("portal_invoices").update({"viewed_at": now_iso}).eq("id", r["id"]).execute()
                    r["viewed_at"] = now_iso
                except Exception:
                    pass
    return [_shape_invoice(r, links.get(str(r.get("invoice_link_id")), {})) for r in rows]


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

def list_projects(principal: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Projects this principal (client OR external user) can access, with enabled
    modules and project name."""
    table = "project_client_access" if principal.get("kind") == "client" else "project_user_access"
    key = "client_id" if principal.get("kind") == "client" else "user_id"
    try:
        access = (
            supabase.table(table)
            .select("project_id, modules")
            .eq(key, principal["access_id"])
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


def get_deals(project_id: str) -> List[Dict[str, Any]]:
    """
    Published Fix & Flip deals (item_type='deal' -> fix_flip_deals). The client
    view is intentionally curated — we surface address-ish/name + headline
    numbers (asking & ARV) only, NOT rehab budget, profit or ROI. The team adds
    context with the share's client_caption.
    """
    shares = _active_shares(project_id, "deal")
    if not shares:
        return []
    by_id = {s["item_id"]: s for s in shares}
    rows = (
        supabase.table("fix_flip_deals")
        .select("id, name, notes, data")
        .in_("id", list(by_id.keys()))
        .execute()
    ).data or []
    out: List[Dict[str, Any]] = []
    for r in rows:
        s = by_id.get(r["id"], {})
        inputs = ((r.get("data") or {}).get("inputs") or {}) if isinstance(r.get("data"), dict) else {}
        out.append({
            "id": r["id"],
            "name": s.get("client_caption") or r.get("name"),
            "notes": r.get("notes") or None,
            "asking_price": _to_number(inputs.get("purchase_price")),
            "arv": _to_number(inputs.get("sale_price")),
            "client_caption": s.get("client_caption"),
            "shared_at": s.get("shared_at"),
        })
    return out


def get_estimates(project_id: str) -> List[Dict[str, Any]]:
    """
    Published estimates (item_type='estimate' -> estimates bucket). item_id is
    the estimate folder name. We surface name + shared_at; the PDF link is
    deferred until the export pipeline lives behind a stable URL.
    """
    shares = _active_shares(project_id, "estimate")
    if not shares:
        return []
    out: List[Dict[str, Any]] = []
    for s in shares:
        estimate_id = s["item_id"]
        name = s.get("client_caption") or _estimate_display_name(estimate_id)
        out.append({
            "id": estimate_id,
            "name": name,
            "client_caption": s.get("client_caption"),
            "shared_at": s.get("shared_at"),
        })
    return out


def _to_number(value: Any) -> Optional[float]:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _estimate_display_name(estimate_id: str) -> str:
    """Best-effort lookup of the estimate's project_name from its manifest."""
    try:
        from api.routers import estimator as estimator_mod  # local import: optional dep
        manifest = estimator_mod._download_json(estimator_mod._manifest_path(estimate_id))  # noqa: SLF001
        if isinstance(manifest, dict):
            name = manifest.get("project_name")
            if name:
                return str(name)
    except Exception:
        pass
    return estimate_id


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
def portal_projects(principal: dict = Depends(get_portal_principal)):
    return {"projects": list_projects(principal)}


@router.get("/projects/{project_id}/overview")
def portal_overview(project_id: str, principal: dict = Depends(get_portal_principal)):
    assert_module(principal, project_id, "overview")
    return get_overview(project_id)


@router.get("/projects/{project_id}/photos")
def portal_photos(project_id: str, principal: dict = Depends(get_portal_principal)):
    assert_module(principal, project_id, "photos")
    return {"photos": get_photos(project_id)}


@router.get("/projects/{project_id}/plans")
def portal_plans(project_id: str, principal: dict = Depends(get_portal_principal)):
    assert_module(principal, project_id, "plans")
    return {"plans": get_plans(project_id)}


@router.get("/projects/{project_id}/timeline")
def portal_timeline(project_id: str, principal: dict = Depends(get_portal_principal)):
    assert_module(principal, project_id, "timeline")
    return get_timeline(project_id)


@router.get("/projects/{project_id}/documents")
def portal_documents(project_id: str, principal: dict = Depends(get_portal_principal)):
    assert_module(principal, project_id, "documents")
    return {"documents": get_documents(project_id)}


@router.get("/projects/{project_id}/deals")
def portal_deals(project_id: str, principal: dict = Depends(get_portal_principal)):
    assert_module(principal, project_id, "deals")
    return {"deals": get_deals(project_id)}


@router.get("/projects/{project_id}/estimates")
def portal_estimates(project_id: str, principal: dict = Depends(get_portal_principal)):
    assert_module(principal, project_id, "estimates")
    return {"estimates": get_estimates(project_id)}


@router.get("/projects/{project_id}/messages")
def portal_messages(project_id: str, principal: dict = Depends(get_portal_principal)):
    assert_module(principal, project_id, "messages")  # client-only (blocks external)
    return {"messages": list_client_messages(project_id)}


@router.post("/projects/{project_id}/messages", status_code=201)
def portal_send_message(
    project_id: str,
    payload: ClientMessageCreate,
    principal: dict = Depends(get_portal_principal),
):
    assert_module(principal, project_id, "messages")  # client-only (blocks external)
    return post_client_message(project_id, principal["user_id"], payload.content, payload.attachments)


@router.get("/projects/{project_id}/invoices")
def portal_invoices(project_id: str, principal: dict = Depends(get_portal_principal)):
    assert_module(principal, project_id, "invoices")  # client-only (blocks external)
    return {"invoices": list_project_invoices(project_id, client_id=principal["client_id"], mark_viewed=True)}
