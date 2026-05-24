# api/services/agent_access.py
# ================================
# Agent Access Control (RBAC)
# ================================
# Controls who can COMMAND an agent (run its functions, via chat @mention OR the
# operator modal) and who can VIEW it (see it in the Agent Hub / dashboard card).
#
# Config lives in agent_config (key/value), per agent:
#   <agent>_operator_roles   JSON list of role names allowed to command
#   <agent>_operator_users   JSON list of user_ids allowed to command
#   <agent>_viewer_roles     JSON list of role names allowed to view
#   <agent>_viewer_users     JSON list of user_ids allowed to view
#
# Defaults (when a level is fully unconfigured):
#   operator -> management roles (locked down by default)
#   viewer   -> falls back to the operator allow-set
#
# Hari predates this and uses its own keys (hari_instructor_roles / _viewer_roles);
# we map to those so its established config keeps working.

import json
import logging
from typing import Any, Dict, List, Optional

from api.supabase_client import supabase

logger = logging.getLogger("agent.access")

# Roles with FULL access to every agent (command + view), regardless of any
# per-agent config. Top management / system admins are never locked out.
# Overridable via agent_config key "agent_full_access_roles" (JSON list).
_FULL_ACCESS_ROLES_DEFAULT = ["CEO", "COO", "Admin", "Owner"]

# Default roles allowed to command an agent when nothing is configured.
_MANAGEMENT_DEFAULT = ["CEO", "COO", "Admin", "Owner", "Accounting Manager"]


def _full_access_roles() -> List[str]:
    configured = _load_list("agent_full_access_roles")
    return configured or list(_FULL_ACCESS_ROLES_DEFAULT)


def has_full_agent_access(user_id: str) -> bool:
    """True if the user's role grants blanket access to all agents."""
    user = _get_user(user_id)
    return bool(user and _role_matches(user.get("role"), _full_access_roles()))

# Per-agent default operator roles (Hari keeps its historical set).
_DEFAULT_OPERATOR_ROLES = {
    "hari": ["CEO", "COO", "Coordinator", "PM"],
}


def _config_keys(agent: str) -> Dict[str, Optional[str]]:
    """Map an agent to its (operator/viewer) x (roles/users) config keys."""
    if agent == "hari":
        return {
            "operator_roles": "hari_instructor_roles",
            "operator_users": "hari_auto_confirm_users",
            "viewer_roles": "hari_viewer_roles",
            "viewer_users": None,
        }
    return {
        "operator_roles": f"{agent}_operator_roles",
        "operator_users": f"{agent}_operator_users",
        "viewer_roles": f"{agent}_viewer_roles",
        "viewer_users": f"{agent}_viewer_users",
    }


def _load_list(key: Optional[str]) -> List[str]:
    if not key:
        return []
    try:
        row = supabase.table("agent_config").select("value").eq("key", key).execute()
        if not row.data:
            return []
        val = row.data[0].get("value")
        parsed = json.loads(val) if isinstance(val, str) else val
        return [str(x) for x in parsed] if isinstance(parsed, list) else []
    except Exception as e:
        logger.debug("[AgentAccess] load %s failed: %s", key, e)
        return []


def _get_user(user_id: str) -> Optional[Dict[str, Any]]:
    try:
        r = supabase.table("users").select("user_id, user_name, role").eq("user_id", user_id).execute()
        return r.data[0] if r.data else None
    except Exception as e:
        logger.debug("[AgentAccess] user lookup failed: %s", e)
        return None


def _role_matches(role: Optional[str], allowed: List[str]) -> bool:
    rl = (role or "").strip().lower()
    return bool(rl) and any(rl == str(r).strip().lower() for r in allowed)


def check_agent_operator_permission(agent: str, user_id: str) -> Dict[str, Any]:
    """Can this user COMMAND the agent? -> {allowed, role, reason}."""
    agent = (agent or "").lower()

    user = _get_user(user_id)
    if not user:
        return {"allowed": False, "role": "unknown", "reason": "User not found"}
    role = user.get("role", "")

    # Top management / admins always have full access to every agent.
    if _role_matches(role, _full_access_roles()):
        return {"allowed": True, "role": role, "reason": "full access"}

    keys = _config_keys(agent)
    op_roles = _load_list(keys["operator_roles"])
    op_users = _load_list(keys["operator_users"])

    # Unconfigured -> lock to management (per-agent default if any).
    if not op_roles and not op_users:
        op_roles = list(_DEFAULT_OPERATOR_ROLES.get(agent, _MANAGEMENT_DEFAULT))

    if str(user_id) in op_users or _role_matches(role, op_roles):
        return {"allowed": True, "role": role, "reason": ""}

    return {
        "allowed": False,
        "role": role,
        "reason": f"Role '{role or 'unknown'}' is not authorized. Allowed: {', '.join(op_roles) or 'none'}.",
    }


def check_agent_viewer_permission(agent: str, user_id: str) -> Dict[str, Any]:
    """Can this user VIEW the agent (Hub / dashboard)? -> {allowed, role, reason}.

    Operators can always view. When no viewer level is configured, viewing
    falls back to the operator allow-set.
    """
    agent = (agent or "").lower()

    user = _get_user(user_id)
    if not user:
        return {"allowed": False, "role": "unknown", "reason": "User not found"}
    role = user.get("role", "")

    # Top management / admins always have full access to every agent.
    if _role_matches(role, _full_access_roles()):
        return {"allowed": True, "role": role, "reason": "full access"}

    keys = _config_keys(agent)
    view_roles = _load_list(keys["viewer_roles"])
    view_users = _load_list(keys["viewer_users"])

    if not view_roles and not view_users:
        return check_agent_operator_permission(agent, user_id)

    if str(user_id) in view_users or _role_matches(role, view_roles):
        return {"allowed": True, "role": role, "reason": ""}

    # Operators implicitly have view access.
    op = check_agent_operator_permission(agent, user_id)
    if op.get("allowed"):
        return {"allowed": True, "role": role, "reason": ""}

    return {"allowed": False, "role": role, "reason": f"Not authorized to view {agent}."}
