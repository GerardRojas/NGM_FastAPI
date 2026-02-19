# api/services/daneel_digest.py
# ============================================================================
# Daneel Digest: Consolidates auth results into periodic project messages
# ============================================================================
# Instead of posting per-expense messages, Daneel accumulates results in
# daneel_auth_reports and this module flushes them into ONE consolidated
# message per project.  Designed to be called via cron every N hours.
# ============================================================================

import json
import logging
from datetime import datetime, timezone
from typing import Optional

from api.helpers.daneel_messenger import post_daneel_message

logger = logging.getLogger(__name__)


def flush_digest(project_id: Optional[str] = None) -> dict:
    """
    Read un-digested auth reports, group by project, send ONE message per
    project, mark reports as digested.

    Args:
        project_id: Optional filter — only flush a specific project.

    Returns:
        {projects_notified, messages_sent, reports_digested}
    """
    from api.supabase_client import supabase as sb
    from api.services.daneel_auto_auth import (
        load_auto_auth_config, _load_lookups, _resolve_mentions,
    )
    from api.services.daneel_smart_layer import craft_digest_message

    cfg = load_auto_auth_config()
    if not cfg.get("daneel_digest_enabled", True):
        return {"projects_notified": 0, "messages_sent": 0, "reports_digested": 0}

    # Query un-digested reports
    query = sb.table("daneel_auth_reports") \
        .select("*") \
        .is_("digest_sent_at", "null") \
        .order("created_at", desc=False)

    if project_id:
        query = query.eq("project_id", project_id)

    result = query.execute()
    reports = result.data or []

    if not reports:
        return {"projects_notified": 0, "messages_sent": 0, "reports_digested": 0}

    # Group by project
    by_project: dict = {}
    for r in reports:
        pid = r.get("project_id")
        if pid:
            by_project.setdefault(pid, []).append(r)

    lookups = _load_lookups(sb)
    bookkeeping_mentions = _resolve_mentions(
        sb, cfg, "daneel_bookkeeping_users", "daneel_bookkeeping_role")
    escalation_mentions = _resolve_mentions(
        sb, cfg, "daneel_accounting_mgr_users", "daneel_accounting_mgr_role")

    # Resolve project names
    proj_names: dict = {}
    try:
        pn_result = sb.table("projects").select("project_id, project_name").execute()
        proj_names = {p["project_id"]: p["project_name"] for p in (pn_result.data or [])}
    except Exception:
        pass

    messages_sent = 0
    reports_digested = 0
    now = datetime.now(timezone.utc).isoformat()

    for pid, proj_reports in by_project.items():
        # Parse JSONB fields if stored as strings
        for r in proj_reports:
            if isinstance(r.get("summary"), str):
                try:
                    r["summary"] = json.loads(r["summary"])
                except Exception:
                    r["summary"] = {}
            if isinstance(r.get("decisions"), str):
                try:
                    r["decisions"] = json.loads(r["decisions"])
                except Exception:
                    r["decisions"] = []

        # Check if there is anything worth messaging about
        has_content = any(
            int((r.get("summary") or {}).get("authorized", 0)) > 0 or
            int((r.get("summary") or {}).get("missing_info", 0)) > 0 or
            int((r.get("summary") or {}).get("escalated", 0)) > 0 or
            int((r.get("summary") or {}).get("duplicates", 0)) > 0
            for r in proj_reports
        )

        if has_content:
            msg = craft_digest_message(
                proj_reports, lookups,
                bookkeeping_mentions, escalation_mentions,
                project_name=proj_names.get(pid, ""),
            )

            total_auth = sum(
                int((r.get("summary") or {}).get("authorized", 0))
                for r in proj_reports
            )
            total_flagged = sum(
                int((r.get("summary") or {}).get("missing_info", 0)) +
                int((r.get("summary") or {}).get("escalated", 0)) +
                int((r.get("summary") or {}).get("duplicates", 0))
                for r in proj_reports
            )

            post_daneel_message(
                content=msg,
                project_id=pid,
                channel_type="project_general",
                metadata={
                    "type": "auto_auth_digest",
                    "authorized": total_auth,
                    "flagged": total_flagged,
                    "reports_count": len(proj_reports),
                },
            )
            messages_sent += 1

        # Mark all reports as digested (even empty ones)
        report_ids = [r["report_id"] for r in proj_reports]
        for rid in report_ids:
            try:
                sb.table("daneel_auth_reports") \
                    .update({"digest_sent_at": now}) \
                    .eq("report_id", rid) \
                    .execute()
            except Exception as e:
                logger.error("[DaneelDigest] Failed to mark report %s: %s", rid, e)
        reports_digested += len(report_ids)

    logger.info(
        "[DaneelDigest] Digest flushed: %d projects, %d messages, %d reports",
        len(by_project), messages_sent, reports_digested,
    )

    return {
        "projects_notified": len(by_project),
        "messages_sent": messages_sent,
        "reports_digested": reports_digested,
    }
