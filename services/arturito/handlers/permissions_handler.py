# services/arturito/handlers/permissions_handler.py
# ================================
# MANAGE_EXPENSE_AUTHORIZER handler
# ================================
# Lets a CEO/COO grant or revoke "can authorize expenses" conversationally
# ("let Amora approve expenses"). Permissions are PER-ROLE, so this resolves the
# named person -> their role, discloses the blast radius (everyone with that
# role), and requires an explicit "yes" before writing.
#
# Security: the requester is gated on context["verified_role"], which the
# /web-chat endpoint sets from the JWT — NOT on the client-supplied user_role,
# which can be spoofed. The actual DB write reuses set_role_can_authorize() in
# api/routers/permissions.py so there is a single source of truth + audit trail.

import re
import time
import logging
from typing import Dict, Any, Optional, List

from api.services.agent_access import roles_for

logger = logging.getLogger(__name__)

ADMIN_ROLES_KEY = "arturito_admin_roles"
ADMIN_ROLES_DEFAULT = ["CEO", "COO", "KD COO"]
ALWAYS_AUTHORIZE_ROLES = ["CEO", "COO"]  # always authorizers, can't be toggled

# In-memory pending confirmations keyed by space_id (session). Volatile (resets
# on server restart) — fine for a short confirm window. Mirrors the per-space
# in-memory persona store. NOT for cross-instance use.
_PENDING: Dict[str, Dict[str, Any]] = {}
_PENDING_TTL_SECONDS = 180

_AFFIRM_RE = re.compile(
    r"^\s*(yes|yep|yeah|yup|confirm|confirmar|s[ií]|sip|dale|do it|go ahead|"
    r"ok|okay|proceed|hazlo|adelante|correct)\b", re.IGNORECASE)
_NEGATE_RE = re.compile(
    r"^\s*(no|nope|cancel|cancelar|never ?mind|stop|d[eé]jalo|olv[ií]dalo|forget it)\b",
    re.IGNORECASE)


def _supabase():
    from api.supabase_client import supabase
    return supabase


def _is_admin(role: Optional[str]) -> bool:
    return bool(role) and role in roles_for(ADMIN_ROLES_KEY, ADMIN_ROLES_DEFAULT)


def _admin_roles_label() -> str:
    return " / ".join(roles_for(ADMIN_ROLES_KEY, ADMIN_ROLES_DEFAULT))


def _resolve_person(name: str) -> List[Dict[str, Any]]:
    """Fuzzy-match a person by user_name and return [{user_id, user_name,
    rol_id, rol_name}]. Same join the team endpoint uses."""
    sb = _supabase()
    rows = (sb.table("users")
            .select("user_id, user_name, rols!users_user_rol_fkey(rol_id, rol_name)")
            .ilike("user_name", f"%{name}%")
            .order("user_name").limit(10).execute().data) or []
    out: List[Dict[str, Any]] = []
    for r in rows:
        rel = r.get("rols")
        if isinstance(rel, list):
            rel = rel[0] if rel else {}
        rel = rel or {}
        out.append({
            "user_id": r.get("user_id"),
            "user_name": r.get("user_name"),
            "rol_id": rel.get("rol_id"),
            "rol_name": rel.get("rol_name"),
        })
    return out


def _role_user_count(rol_id) -> int:
    """How many users share this role — the blast radius of a role-level grant."""
    try:
        sb = _supabase()
        res = sb.table("users").select("user_id", count="exact").eq("user_rol", rol_id).execute()
        return res.count or len(res.data or [])
    except Exception:
        return 0


def _current_can_authorize(rol_id) -> bool:
    sb = _supabase()
    rows = (sb.table("role_permissions").select("can_authorize")
            .eq("module_key", "expenses").eq("rol_id", rol_id)
            .limit(1).execute().data) or []
    return bool(rows[0].get("can_authorize")) if rows else False


# ================================
# Confirmation helpers (used by the /web-chat endpoint)
# ================================

def is_affirmation(text: str) -> bool:
    return bool(_AFFIRM_RE.match(text or ""))


def is_negation(text: str) -> bool:
    return bool(_NEGATE_RE.match(text or ""))


def has_pending(space_id: str) -> bool:
    p = _PENDING.get(space_id)
    if not p:
        return False
    if time.time() - p["ts"] > _PENDING_TTL_SECONDS:
        _PENDING.pop(space_id, None)
        return False
    return True


def cancel_pending(space_id: str) -> Dict[str, Any]:
    _PENDING.pop(space_id, None)
    return {
        "text": "Okay — I left expense authorization unchanged.",
        "action": "permission_change_cancelled",
    }


def confirm_pending(space_id: str, context: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Commit a pending change. Returns None if there is nothing valid to confirm
    (so the caller falls through to normal NLU)."""
    p = _PENDING.get(space_id)
    if not p or time.time() - p["ts"] > _PENDING_TTL_SECONDS:
        _PENDING.pop(space_id, None)
        return None

    # Re-verify the requester at commit time too — never trust the client role.
    if not _is_admin(context.get("verified_role")):
        _PENDING.pop(space_id, None)
        return {
            "text": f"Only {_admin_roles_label()} can change who authorizes expenses.",
            "action": "permission_denied",
        }

    _PENDING.pop(space_id, None)
    from api.routers.permissions import set_role_can_authorize
    try:
        result = set_role_can_authorize(
            p["rol_id"], p["target"],
            rol_name=p["rol_name"],
            actor_user_id=context.get("actor_user_id"),
            actor_name=context.get("user_name"),
            actor_role=context.get("verified_role"),
            source="art",
        )
    except Exception as e:
        logger.error("[Art] expense authorizer commit failed: %s", e)
        return {
            "text": "Something went wrong applying that change. Please try again.",
            "action": "error",
        }

    if p["target"]:
        msg = (f"Done — the **{p['rol_name']}** role can now authorize expenses. "
               f"Anyone with that role needs to log out and back in for it to take effect.")
    else:
        msg = (f"Done — the **{p['rol_name']}** role can no longer authorize expenses. "
               f"It takes effect after they log out and back in.")
    return {
        "text": msg,
        "action": "permission_change_applied",
        "data": {
            "rol_id": p["rol_id"],
            "rol_name": p["rol_name"],
            "can_authorize": result.get("new"),
        },
    }


# ================================
# Main handler (propose step)
# ================================

async def handle_manage_expense_authorizer(
    request: Dict[str, Any],
    context: Dict[str, Any],
    db_client=None,
) -> Dict[str, Any]:
    entities = request.get("entities", {}) or {}
    person_q = (entities.get("person") or "").strip()
    operation = (entities.get("operation") or "grant").lower()
    if operation not in ("grant", "revoke"):
        operation = "grant"
    ctx = context or {}
    space_id = ctx.get("space_id", "default")

    # 1) Gate the requester on the VERIFIED role (from JWT), never user_role.
    if not _is_admin(ctx.get("verified_role")):
        return {
            "text": (f"Only {_admin_roles_label()} can change who authorizes expenses. "
                     f"Ask one of them, or set it in Roles Management → Expense Authorization."),
            "action": "permission_denied",
            "data": {"intent": "MANAGE_EXPENSE_AUTHORIZER"},
        }

    # 2) Need a name to act on.
    if not person_q:
        return {
            "text": "Sure — who should I update? Tell me the person's name, e.g. \"let Amora approve expenses\".",
            "action": "need_clarification",
        }

    # 3) Resolve the person -> their role.
    candidates = _resolve_person(person_q)
    if not candidates:
        return {
            "text": (f"I couldn't find anyone matching \"{person_q}\". "
                     f"Try their full name as it appears in the team list."),
            "action": "need_clarification",
        }
    if len(candidates) > 1:
        lines = [f"- {c['user_name']} ({c['rol_name'] or 'no role'})" for c in candidates[:6]]
        return {
            "text": ("I found a few people that could match — who do you mean?\n"
                     + "\n".join(lines)),
            "action": "need_clarification",
        }

    c = candidates[0]
    rol_id, rol_name = c.get("rol_id"), c.get("rol_name")
    if not rol_id:
        return {
            "text": (f"{c['user_name']} doesn't have a role assigned, so there's no role "
                     f"permission to change. Assign them a role first in Team / Roles Management."),
            "action": "need_clarification",
        }

    # 4) Short-circuit edge cases.
    if rol_name in ALWAYS_AUTHORIZE_ROLES:
        if operation == "revoke":
            return {
                "text": f"{rol_name} always has expense authorization — that can't be turned off.",
                "action": "noop",
            }
        return {
            "text": f"{c['user_name']} is a {rol_name}, who can already authorize expenses. Nothing to change.",
            "action": "noop",
        }

    target = (operation == "grant")
    if _current_can_authorize(rol_id) == target:
        if target:
            txt = f"The **{rol_name}** role already authorizes expenses. Nothing to change."
        else:
            txt = f"The **{rol_name}** role already cannot authorize expenses. Nothing to change."
        return {"text": txt, "action": "noop"}

    # 5) Blast radius + proposal; stash pending for the confirmation turn.
    count = _role_user_count(rol_id)
    pending = {
        "rol_id": str(rol_id),
        "rol_name": rol_name,
        "operation": operation,
        "target": target,
        "person_name": c["user_name"],
        "count": count,
        "ts": time.time(),
    }
    _PENDING[space_id] = pending

    if count > 1:
        radius = f" This affects **all {count} people** with the **{rol_name}** role."
    elif count == 1:
        radius = f" {c['user_name']} is the only person with the **{rol_name}** role."
    else:
        radius = ""

    if target:
        msg = (f"**{c['user_name']}**'s role is **{rol_name}**. Granting expense "
               f"authorization lets everyone with that role authorize expenses.{radius}"
               f"\n\nReply **yes** to confirm, or **no** to cancel.")
    else:
        msg = (f"**{c['user_name']}**'s role is **{rol_name}**. Revoking expense "
               f"authorization stops everyone with that role from authorizing expenses.{radius}"
               f"\n\nReply **yes** to confirm, or **no** to cancel.")

    return {
        "text": msg,
        "action": "confirm_permission_change",
        "data": {k: v for k, v in pending.items() if k != "ts"},
    }
