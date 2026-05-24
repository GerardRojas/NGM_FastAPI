"""
Agent Hub Router
================
Analytics for the Agent Hub: global usage across all agents, a per-user
breakdown of who runs which commands (manager/admin only), the current
user's own activity, and each agent's capability catalog.

Backed by agent_activity_log (one row per command execution, written by the
brain's _execute_function_call — see api/helpers/agent_activity.py).
"""

from fastapi import APIRouter, HTTPException, Depends, Query
from typing import Optional
from collections import defaultdict
from datetime import datetime, timezone, timedelta
import logging

from api.supabase_client import supabase
from api.auth import get_current_user
from api.services.agent_registry import AGENT_REGISTRY, get_functions
from api.services.agent_access import (
    check_agent_operator_permission,
    check_agent_viewer_permission,
)

router = APIRouter(prefix="/agent-hub", tags=["Agent Hub"])
logger = logging.getLogger(__name__)

_PAGE_SIZE = 1000

# Roles allowed to see the per-user breakdown (who runs which commands).
# Everyone else can still see global analytics + their own activity.
_MANAGER_ROLES = {"ceo", "coo", "admin", "administrator", "manager", "accounting manager", "owner"}


def _can_view_all_users(role: Optional[str]) -> bool:
    r = (role or "").strip().lower()
    if not r:
        return False
    if r in _MANAGER_ROLES:
        return True
    return "manager" in r or "admin" in r


def _cutoff_iso(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def _fetch_activity(days: int, user_id: Optional[str] = None) -> list[dict]:
    """Fetch activity rows since `days` ago (optionally scoped to one user)."""
    cutoff = _cutoff_iso(days)
    rows: list[dict] = []
    offset = 0
    while True:
        q = supabase.table("agent_activity_log") \
            .select("user_id, user_name, agent, function, project_id, source, status, latency_ms, created_at") \
            .gte("created_at", cutoff)
        if user_id:
            q = q.eq("user_id", user_id)
        q = q.order("created_at", desc=True).range(offset, offset + _PAGE_SIZE - 1)
        try:
            batch = q.execute().data or []
        except Exception as exc:
            logger.error("[AgentHub] fetch activity offset=%d: %s", offset, exc)
            break
        rows.extend(batch)
        if len(batch) < _PAGE_SIZE:
            break
        offset += _PAGE_SIZE
    return rows


def _summarize(rows: list[dict]) -> dict:
    """Build aggregate metrics from a list of activity rows."""
    total = len(rows)
    by_agent: dict = defaultdict(int)
    by_function: dict = defaultdict(int)            # key: f"{agent}:{function}"
    by_day: dict = defaultdict(int)
    errors = 0
    latency_sum = 0
    latency_n = 0

    for r in rows:
        agent = (r.get("agent") or "").lower()
        fn = r.get("function") or ""
        by_agent[agent] += 1
        by_function[f"{agent}:{fn}"] += 1
        if (r.get("status") or "ok") == "error":
            errors += 1
        lm = r.get("latency_ms")
        if isinstance(lm, (int, float)) and lm > 0:
            latency_sum += lm
            latency_n += 1
        created = (r.get("created_at") or "")[:10]
        if created:
            by_day[created] += 1

    top_commands = sorted(
        ({"agent": k.split(":", 1)[0], "function": k.split(":", 1)[1], "count": v}
         for k, v in by_function.items()),
        key=lambda x: x["count"], reverse=True,
    )[:10]

    return {
        "total_commands": total,
        "error_count": errors,
        "success_rate": round((total - errors) / total * 100, 1) if total else 0.0,
        "avg_latency_ms": round(latency_sum / latency_n) if latency_n else 0,
        "by_agent": dict(by_agent),
        "top_commands": top_commands,
        "timeseries": [{"date": d, "count": c} for d, c in sorted(by_day.items())],
    }


@router.get("/overview")
async def get_overview(
    days: int = Query(30, ge=1, le=365),
    current_user: dict = Depends(get_current_user),
):
    """Global analytics across all agents. Available to any authenticated user."""
    rows = _fetch_activity(days)
    summary = _summarize(rows)
    summary["period_days"] = days
    summary["can_view_users"] = _can_view_all_users(current_user.get("role"))
    return summary


@router.get("/by-user")
async def get_by_user(
    days: int = Query(30, ge=1, le=365),
    current_user: dict = Depends(get_current_user),
):
    """Per-user breakdown of who runs which commands. Manager/admin only."""
    if not _can_view_all_users(current_user.get("role")):
        raise HTTPException(status_code=403, detail="Not allowed to view per-user analytics")

    rows = _fetch_activity(days)

    users: dict = {}
    matrix: dict = defaultdict(int)  # key: (user_id, agent, function)
    for r in rows:
        uid = r.get("user_id") or "system"
        uname = r.get("user_name") or ("Scheduled" if uid == "system" else "Unknown")
        agent = (r.get("agent") or "").lower()
        fn = r.get("function") or ""
        created = r.get("created_at") or ""

        u = users.setdefault(uid, {
            "user_id": None if uid == "system" else uid,
            "user_name": uname,
            "total": 0,
            "by_agent": defaultdict(int),
            "last_used": "",
        })
        u["total"] += 1
        u["by_agent"][agent] += 1
        if created > u["last_used"]:
            u["last_used"] = created
        matrix[(uid, agent, fn)] += 1

    user_list = sorted(
        ({**u, "by_agent": dict(u["by_agent"])} for u in users.values()),
        key=lambda x: x["total"], reverse=True,
    )
    commands = sorted(
        ({"user_id": None if k[0] == "system" else k[0],
          "agent": k[1], "function": k[2], "count": v}
         for k, v in matrix.items()),
        key=lambda x: x["count"], reverse=True,
    )

    return {"period_days": days, "users": user_list, "commands": commands}


@router.get("/my-activity")
async def get_my_activity(
    days: int = Query(30, ge=1, le=365),
    current_user: dict = Depends(get_current_user),
):
    """The current user's own command activity."""
    user_id = current_user.get("user_id")
    rows = _fetch_activity(days, user_id=user_id)
    summary = _summarize(rows)
    summary["period_days"] = days
    summary["recent"] = [
        {"agent": r.get("agent"), "function": r.get("function"),
         "status": r.get("status"), "created_at": r.get("created_at"),
         "project_id": r.get("project_id")}
        for r in rows[:25]
    ]
    return summary


@router.get("/access")
async def get_access(current_user: dict = Depends(get_current_user)):
    """Per-agent access for the current user: can they command / view each agent?

    Used by the frontend to gate the operator console (modal) and whether an
    agent shows up at all. Art is the client-facing agent and is always open.
    """
    uid = current_user.get("user_id")
    access: dict = {"art": {"can_command": True, "can_view": True}}
    for agent in ("daneel", "andrew", "hari"):
        access[agent] = {
            "can_command": bool(check_agent_operator_permission(agent, uid).get("allowed")),
            "can_view": bool(check_agent_viewer_permission(agent, uid).get("allowed")),
        }
    return {"access": access}


@router.get("/agents")
async def get_agents(current_user: dict = Depends(get_current_user)):
    """Capability catalog per agent (from the function registry)."""
    out = []
    for agent in AGENT_REGISTRY:
        fns = get_functions(agent)
        out.append({
            "agent": agent,
            "functions": [
                {"name": f["name"], "description": (f.get("description") or "").split(". ")[0].rstrip(".")}
                for f in fns
            ],
        })
    return {"agents": out}
