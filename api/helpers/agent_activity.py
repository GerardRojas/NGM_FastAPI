# api/helpers/agent_activity.py
# ================================
# Agent Activity Logger
# ================================
# Records one row per agent COMMAND execution (a registered function) into
# agent_activity_log. Powers the Agent Hub analytics (global + per-user).
# Best-effort: logging failures must never break the agent pipeline.

import logging
from typing import Optional
from api.supabase_client import supabase

logger = logging.getLogger(__name__)


def source_from_channel(channel_type: Optional[str]) -> str:
    """Map a channel_type to an activity source label.

    Operator modal uses a 'direct' channel; project channels are normal chat.
    """
    ct = (channel_type or "").lower()
    if ct == "direct":
        return "modal"
    if ct.startswith("project"):
        return "chat"
    return ct or "chat"


def log_agent_activity(
    agent: str,
    function: str,
    *,
    user_id: Optional[str] = None,
    user_name: Optional[str] = None,
    project_id: Optional[str] = None,
    source: str = "chat",
    status: str = "ok",
    latency_ms: int = 0,
    error: Optional[str] = None,
) -> None:
    """Insert a single command-execution record. Never raises."""
    try:
        row = {
            "agent": (agent or "").lower(),
            "function": function or "",
            "source": source or "chat",
            "status": status or "ok",
            "latency_ms": int(latency_ms or 0),
        }
        if user_id:
            row["user_id"] = str(user_id)
        if user_name:
            row["user_name"] = user_name
        if project_id:
            row["project_id"] = str(project_id)
        if error:
            row["error"] = str(error)[:500]

        supabase.table("agent_activity_log").insert(row).execute()
    except Exception as e:
        # Analytics logging is best-effort; do not disrupt the agent run.
        logger.debug("[AgentActivity] log failed (non-blocking): %s", e)
