"""
Public workspace — anonymous, read-only access to a curated workspace via a
signed link. NO authentication: scope is resolved ONLY from the token's
workspace_links row, never from request params.

This is the link-only audience of NGM Connect (created in routers/connect.py).
It is a deliberately tiny, hardened surface:
  * decode the JWT (signature + expiry), require type 'workspace_link'
  * look up the row by token and re-check status + expiry (server-side revoke)
  * build sections with the SAME portal.py builders the client portal uses
    (default-deny: only portal_shares content shows)
  * client-only modules (messages, invoices) are NEVER served here — they need
    an identity

Like routers/portal.py, internal modules are never reused, so there is no filter
to forget and no way to leak.

Tables: workspace_links (see sql/workspace_links.sql), plus the read-only
portal_shares-backed builders in routers/portal.py.
"""

import logging
from datetime import datetime, timezone
from typing import Any, Dict

import jwt
from fastapi import APIRouter, HTTPException, Query

from api.auth import JWT_SECRET, JWT_ALG
from api.supabase_client import supabase
from api.routers import portal as portal_mod

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/public", tags=["Public Workspace"])

# Modules an anonymous link may expose (mirror of connect.LINK_SHAREABLE_MODULES).
# Messages/invoices are intentionally absent: they require a client identity.
_BUILDERS = {
    "overview": portal_mod.get_overview,
    "photos": portal_mod.get_photos,
    "plans": portal_mod.get_plans,
    "timeline": portal_mod.get_timeline,
    "documents": portal_mod.get_documents,
    "deals": portal_mod.get_deals,
    "estimates": portal_mod.get_estimates,
}


def _resolve_link(token: str) -> Dict[str, Any]:
    """Decode + validate the token and return its (active, unexpired) row."""
    if not token:
        raise HTTPException(status_code=400, detail="Missing link token")
    try:
        decoded = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALG])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=400, detail="This link has expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=400, detail="Invalid or tampered link")
    if decoded.get("type") != "workspace_link":
        raise HTTPException(status_code=400, detail="Invalid link")

    try:
        rows = supabase.table("workspace_links").select("*").eq("token", token).limit(1).execute().data or []
    except Exception as e:
        logger.error("[public] link lookup failed: %s", e)
        raise HTTPException(status_code=500, detail="Link lookup failed")
    if not rows:
        raise HTTPException(status_code=404, detail="Link not found")
    link = rows[0]

    if link.get("status") != "active":
        raise HTTPException(status_code=400, detail="This link is no longer active")

    expires_at = link.get("expires_at")
    if expires_at:
        try:
            exp = datetime.fromisoformat(str(expires_at).replace("Z", "+00:00"))
            if datetime.now(timezone.utc) > exp:
                supabase.table("workspace_links").update({"status": "revoked"}).eq("id", link["id"]).execute()
                raise HTTPException(status_code=400, detail="This link has expired")
        except HTTPException:
            raise
        except Exception:
            pass  # unparseable expiry — fall back to the JWT exp already checked
    return link


def _stamp_view(link: Dict[str, Any]) -> None:
    """Best-effort analytics — never blocks the read."""
    try:
        supabase.table("workspace_links").update({
            "viewed_at": datetime.now(timezone.utc).isoformat(),
            "view_count": int(link.get("view_count") or 0) + 1,
        }).eq("id", link["id"]).execute()
    except Exception:
        pass


@router.get("/workspace")
def public_workspace(token: str = Query(...)):
    """Return the curated, read-only workspace for an anonymous link. Scope (project
    + modules) comes ONLY from the token's row; sections are built default-deny."""
    link = _resolve_link(token)
    project_id = link.get("project_id")
    modules = link.get("modules") or {}

    # Project name for the header (best-effort; never leaks internal fields).
    project_name = None
    try:
        p = supabase.table("projects").select("project_name").eq("project_id", project_id).limit(1).execute().data or []
        project_name = (p[0].get("project_name") if p else None)
    except Exception:
        project_name = None

    sections: Dict[str, Any] = {}
    for key, builder in _BUILDERS.items():
        if modules.get(key):
            try:
                sections[key] = builder(project_id)
            except Exception as e:
                logger.warning("[public] section %s failed: %s", key, e)

    _stamp_view(link)

    # Only advertise modules we actually serve here (drop any client-only keys
    # that might have been stored).
    served = {k: bool(modules.get(k)) for k in _BUILDERS if modules.get(k)}
    served["overview"] = True

    return {
        "project_id": project_id,
        "project_name": project_name,
        "modules": served,
        "sections": sections,
    }
