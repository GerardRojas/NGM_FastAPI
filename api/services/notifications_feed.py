# api/services/notifications_feed.py
# ================================
# Unified in-app notifications feed
# ================================
# Helpers to write rows into the `notifications` table from any tagging point
# (chat @mentions, cell comments, future estimator/vault comments). The dashboard
# Mentions widget reads these. Best-effort: a logging failure must never break the
# action that triggered it.

import logging
from typing import Any, Dict, Iterable, List, Optional

from api.supabase_client import supabase

logger = logging.getLogger(__name__)

_NAME_CACHE: Dict[str, str] = {}


def _resolve_name(user_id: Optional[str]) -> Optional[str]:
    if not user_id:
        return None
    if user_id in _NAME_CACHE:
        return _NAME_CACHE[user_id]
    try:
        res = supabase.table("users").select("user_name").eq("user_id", user_id).execute()
        name = (res.data[0].get("user_name") if res.data else None) or None
        if name:
            _NAME_CACHE[user_id] = name
        return name
    except Exception:
        return None


def create_notifications(
    recipient_ids: Iterable[str],
    *,
    type: str,
    module: str,
    actor_id: Optional[str] = None,
    actor_name: Optional[str] = None,
    reference_type: Optional[str] = None,
    reference_id: Optional[str] = None,
    deep_link: Optional[str] = None,
    preview: Optional[str] = None,
    context: Optional[Dict[str, Any]] = None,
) -> int:
    """Insert one notification per recipient (skipping the actor / blanks / dupes).

    Returns the number of rows inserted. Never raises.
    """
    try:
        actor = str(actor_id) if actor_id else None
        seen = set()
        targets: List[str] = []
        for rid in (recipient_ids or []):
            rid = str(rid).strip()
            if not rid or rid == actor or rid in seen:
                continue
            seen.add(rid)
            targets.append(rid)
        if not targets:
            return 0

        if actor_name is None and actor:
            actor_name = _resolve_name(actor)

        rows = [{
            "user_id": rid,
            "type": type,
            "module": module,
            "reference_type": reference_type,
            "reference_id": str(reference_id) if reference_id else None,
            "deep_link": deep_link,
            "preview": (preview or "")[:280] or None,
            "actor_id": actor,
            "actor_name": actor_name,
            "context": context or {},
        } for rid in targets]

        supabase.table("notifications").insert(rows).execute()
        return len(rows)
    except Exception as e:
        logger.debug("[Notifications] create failed (non-blocking): %s", e)
        return 0


def create_notification(recipient_id: str, **kwargs) -> int:
    """Single-recipient convenience wrapper around create_notifications."""
    return create_notifications([recipient_id], **kwargs)


def cleanup_notifications() -> int:
    """Delete stale notifications (unread > 30d, read > 7d). Returns count or -1."""
    try:
        res = supabase.rpc("cleanup_notifications", {}).execute()
        return int(res.data) if isinstance(res.data, int) else (res.data or 0)
    except Exception as e:
        logger.warning("[Notifications] cleanup failed: %s", e)
        return -1
