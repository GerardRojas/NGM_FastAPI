"""
===============================================================================
 Project Progress Report Handler for Arturito
===============================================================================
 Composite progress report combining:
 - Latest photo per milestone (from NGM Cam)
 - Budget health (total, spent, remaining, %)
 - Task summary (counts by status)
 - Pending items (unauthorized expenses, pending receipts)

 Each section is fetched independently with try/except.
 Missing sections are silently omitted.
===============================================================================
"""

import logging
from typing import Dict, Any, Optional, List

logger = logging.getLogger(__name__)

MAX_MILESTONES = 6


def handle_project_progress(
    request: Dict[str, Any],
    context: Dict[str, Any] = None,
) -> Dict[str, Any]:
    """
    Composite progress report for a project.
    Returns only sections that have data; gracefully skips the rest.
    """
    from .bva_handler import resolve_project, fetch_recent_projects, _gpt_ask_missing_entity
    from .health_summary_handler import (
        _fetch_budget_info,
        _fetch_pending_auth,
        _fetch_tasks_summary,
        _fetch_pending_receipts,
    )

    entities = request.get("entities", {})
    ctx = context or {}

    # ---- Resolve project ----
    project_input = entities.get("project")
    if project_input:
        project_input = str(project_input).strip()

    if not project_input:
        project_input = ctx.get("space_name", "")

    raw_text = request.get("raw_text", "")
    space_id = ctx.get("space_id", "default")

    if not project_input or project_input.lower() in (
        "default", "general", "random", "none", "ngm hub web",
    ):
        recent = fetch_recent_projects(limit=8)
        hint = ", ".join(p.get("project_name", "") for p in recent[:4]) if recent else ""
        data: Dict[str, Any] = {"command": "progress"}
        if recent:
            data["projects"] = [
                {"id": p.get("project_id"), "name": p.get("project_name")}
                for p in recent
            ]
        text = _gpt_ask_missing_entity(
            raw_text, "project", hint, space_id, report_type="progress report",
        )
        return {"ok": False, "text": text, "action": "ask_project", "data": data}

    project = resolve_project(project_input)
    if not project:
        return {
            "ok": False,
            "text": f"I could not find a project matching '{project_input}'.",
        }

    project_id = project.get("project_id") or project.get("id")
    project_name = project.get("project_name") or project.get("name") or project_input

    # ---- Fetch sections independently ----
    photos_data = _fetch_latest_photos(project_id)
    budget_data = _safe_fetch(_fetch_budget_info, project_id, "budget")
    tasks_data = _safe_fetch(_fetch_tasks_summary, project_id, "tasks")
    pending_auth = _safe_fetch(_fetch_pending_auth, project_id, "pending_auth")
    pending_rcpt = _safe_fetch(_fetch_pending_receipts, project_id, "pending_receipts")

    # ---- Normalize empty results to None ----
    if budget_data and not budget_data.get("total_budget") and not budget_data.get("total_actuals"):
        budget_data = None
    if tasks_data and not tasks_data.get("total"):
        tasks_data = None

    pending_data = None
    has_auth = pending_auth and pending_auth.get("count", 0) > 0
    has_rcpt = pending_rcpt and pending_rcpt.get("count", 0) > 0
    if has_auth or has_rcpt:
        pending_data = {}
        if has_auth:
            pending_data["unauthorized_expenses"] = pending_auth
        if has_rcpt:
            pending_data["pending_receipts"] = pending_rcpt

    # ---- All empty ----
    if not photos_data and not budget_data and not tasks_data and not pending_data:
        return {
            "ok": True,
            "text": f"I don't have enough information about {project_name} yet. "
                    "Try adding budget, tasks, or photos first.",
        }

    # ---- Build response ----
    text = _format_summary(project_name, photos_data, budget_data, tasks_data, pending_data)

    return {
        "ok": True,
        "text": text,
        "action": "progress_report",
        "data": {
            "project_id": str(project_id),
            "project_name": project_name,
            "photos": photos_data,
            "budget": budget_data,
            "tasks": tasks_data,
            "pending": pending_data,
        },
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_fetch(fn, project_id: str, label: str):
    """Call fn(project_id) with error isolation."""
    try:
        return fn(project_id)
    except Exception as e:
        logger.error("[Progress] %s fetch error: %s", label, e)
        return None


def _fetch_latest_photos(project_id: str) -> Optional[List[Dict]]:
    """
    Return the most recent photo for each unique milestone (max MAX_MILESTONES).
    Files ordered by name DESC → newest first (YYYYMMDD in filename).
    """
    try:
        from api.supabase_client import supabase, SUPABASE_URL
        from .cam_handler import _find_photos_folder, _parse_ngmcam_filename

        folder_id = _find_photos_folder(supabase, project_id)
        if not folder_id:
            return None

        result = (
            supabase.table("vault_files")
            .select("id, name, bucket_path")
            .eq("parent_id", folder_id)
            .eq("is_deleted", False)
            .eq("is_folder", False)
            .order("name", desc=True)
            .limit(500)
            .execute()
        )
        all_files = result.data or []

        seen: Dict[str, Dict] = {}
        for f in all_files:
            if not f.get("bucket_path"):
                continue
            parsed = _parse_ngmcam_filename(f.get("name", ""))
            ms = parsed.get("milestone")
            if not ms:
                continue
            key = ms.lower()
            if key not in seen:
                seen[key] = {
                    "id": f["id"],
                    "name": f["name"],
                    "thumbnail_url": (
                        f"{SUPABASE_URL}/storage/v1/object/public/vault/"
                        f"{f['bucket_path']}?width=200&height=200&resize=cover"
                    ),
                    "full_url": (
                        f"{SUPABASE_URL}/storage/v1/object/public/vault/{f['bucket_path']}"
                    ),
                    "milestone": ms,
                    "date": parsed.get("date"),
                    "time": parsed.get("time"),
                }
            if len(seen) >= MAX_MILESTONES:
                break

        return list(seen.values()) if seen else None

    except Exception as e:
        logger.error("[Progress] Photos fetch error: %s", e)
        return None


def _format_summary(
    project_name: str,
    photos: Optional[List],
    budget: Optional[Dict],
    tasks: Optional[Dict],
    pending: Optional[Dict],
) -> str:
    """Concise text summary accompanying the rich card."""
    def fmt(amount: float) -> str:
        return f"${abs(amount):,.2f}"

    lines = [f"Progress Report: {project_name}"]

    if budget:
        pct = budget.get("pct_used", 0)
        lines.append(
            f"Budget: {fmt(budget.get('total_actuals', 0))} of "
            f"{fmt(budget.get('total_budget', 0))} spent ({pct}%)"
        )

    if tasks:
        total = tasks.get("total", 0)
        completed = tasks.get("by_status", {}).get("Completed", 0)
        lines.append(f"Tasks: {completed}/{total} completed")

    if photos:
        lines.append(f"Photos: {len(photos)} milestone(s) documented")

    if pending:
        parts = []
        ua = pending.get("unauthorized_expenses", {})
        pr = pending.get("pending_receipts", {})
        if ua.get("count"):
            parts.append(f"{ua['count']} unauthorized expense(s) ({fmt(ua.get('total', 0))})")
        if pr.get("count"):
            parts.append(f"{pr['count']} pending receipt(s)")
        if parts:
            lines.append("Pending: " + ", ".join(parts))

    return "\n".join(lines)
