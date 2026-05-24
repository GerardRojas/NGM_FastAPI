# api/helpers/daneel_messenger.py
# ================================
# Daneel Bot Message Helper
# ================================
# Posts budget alert messages to project accounting channels.
# Messages are inserted directly into the messages table;
# Supabase Realtime delivers them to connected frontends automatically.

import logging
from api.supabase_client import supabase
from typing import Optional, Dict, Any
from datetime import datetime

logger = logging.getLogger(__name__)

DANEEL_BOT_USER_ID = "00000000-0000-0000-0000-000000000002"

_bot_user_verified = False


def _ensure_bot_user_exists():
    """Create Daneel bot user if it doesn't exist (runs once per process)."""
    global _bot_user_verified
    if _bot_user_verified:
        return

    try:
        result = supabase.table("users") \
            .select("user_id") \
            .eq("user_id", DANEEL_BOT_USER_ID) \
            .execute()

        if not result.data or len(result.data) == 0:
            logger.info("[DaneelMessenger] Daneel user not found, creating...")
            dummy_hash = "$2b$12$DaneelNoLoginDaneelNoLogDN2.2.2.2.2.2.2.2.2.2.2.2.2.2"

            supabase.table("users").insert({
                "user_id": DANEEL_BOT_USER_ID,
                "user_name": "Daneel",
                "avatar_color": 210,
                "password_hash": dummy_hash,
            }).execute()
            logger.info("[DaneelMessenger] Daneel user created successfully")
        else:
            logger.info("[DaneelMessenger] Daneel user already exists")

        _bot_user_verified = True
    except Exception as e:
        logger.error("[DaneelMessenger] Error ensuring bot user exists: %s", e)
        # Do NOT mark as verified on failure — allow retry on next call


def post_daneel_message(
    content: str,
    project_id: str = None,
    channel_type: str = "project_general",
    channel_id: str = None,
    metadata: Optional[Dict[str, Any]] = None
) -> Optional[Dict[str, Any]]:
    """
    Post a message to a project channel as the Daneel bot.

    For project channels: pass project_id (+ channel_type like project_general).
    For group channels: pass channel_id (+ channel_type="group").

    Args:
        content: Message text (markdown supported)
        project_id: Project UUID (for project channels)
        channel_type: Channel type (default: project_general)
        channel_id: Channel UUID (for group/custom channels)
        metadata: Optional metadata dict (e.g. alert_type, account_name)

    Returns:
        Created message record or None on failure
    """
    _ensure_bot_user_exists()

    try:
        message_data = {
            "content": content,
            "channel_type": channel_type,
            "user_id": DANEEL_BOT_USER_ID,
            "metadata": metadata or {},
            "created_at": datetime.utcnow().isoformat(),
        }

        if channel_id:
            message_data["channel_id"] = channel_id
        elif project_id:
            message_data["project_id"] = project_id
        else:
            logger.warning("[DaneelMessenger] WARNING: No project_id or channel_id provided")
            return None

        target = f"channel={channel_id}" if channel_id else f"project={project_id}"
        logger.info("[DaneelMessenger] Inserting message | %s | type=%s", target, channel_type)
        result = supabase.table("messages").insert(message_data).execute()

        if result.data and len(result.data) > 0:
            msg_id = result.data[0].get("id", "?")
            logger.info("[DaneelMessenger] Message posted OK | id=%s | %s", msg_id, target)
            return result.data[0]

        logger.warning("[DaneelMessenger] Insert returned no data | %s", target)
        return None

    except Exception as e:
        # Never let bot message failures break the main pipeline
        logger.error("[DaneelMessenger] ERROR posting message: %s", e)
        return None


def get_or_create_daneel_dm(user_id: str) -> Optional[str]:
    """Find-or-create a direct (DM) channel between Daneel and `user_id`.

    Returns the channel_id, or None on failure. Used to deliver Daneel's
    private operator reports: a human orchestrates Daneel, so routine activity
    and diagnostics are reported here instead of the public project channel.
    Mirrors the dedup logic of POST /messages/channels (exact member-set match).
    """
    _ensure_bot_user_exists()
    if not user_id:
        return None
    try:
        members = {str(user_id), DANEEL_BOT_USER_ID}

        existing = supabase.table("channels").select("id").eq("type", "direct").execute()
        for ch in (existing.data or []):
            mres = supabase.table("channel_members") \
                .select("user_id").eq("channel_id", ch["id"]).execute()
            ids = {str(m["user_id"]) for m in (mres.data or [])}
            if ids == members:
                return ch["id"]

        created = supabase.table("channels").insert({
            "type": "direct",
            "created_by": DANEEL_BOT_USER_ID,
        }).execute()
        if not created.data:
            logger.warning("[DaneelMessenger] Failed to create DM channel for user %s", user_id)
            return None

        channel_id = created.data[0]["id"]
        supabase.table("channel_members").insert([
            {"channel_id": channel_id, "user_id": DANEEL_BOT_USER_ID, "role": "member"},
            {"channel_id": channel_id, "user_id": str(user_id), "role": "member"},
        ]).execute()
        return channel_id

    except Exception as e:
        logger.error("[DaneelMessenger] get_or_create_daneel_dm error: %s", e)
        return None


def post_daneel_operator_report(
    content: str,
    operator_user_ids,
    metadata: Optional[Dict[str, Any]] = None,
) -> int:
    """Post a PRIVATE report to each operator's Daneel DM. Returns DMs delivered.

    Operators are Daneel's "superiors" (e.g. accounting managers, or the human
    who triggered a run). Routine activity/diagnostics go here so Daneel no
    longer narrates everything in the public project channel; only alerts and
    escalations are posted publicly (via post_daneel_message).
    """
    sent = 0
    for uid in (operator_user_ids or []):
        channel_id = get_or_create_daneel_dm(str(uid))
        if not channel_id:
            continue
        meta = dict(metadata or {})
        meta["operator_report"] = True
        if post_daneel_message(
            content=content,
            channel_type="direct",
            channel_id=channel_id,
            metadata=meta,
        ):
            sent += 1
    return sent
