# api/helpers/hari_messenger.py
# ================================
# Hari Bot Message Helper
# ================================
# Posts coordination messages to project channels.
# Messages are inserted directly into the messages table;
# Supabase Realtime delivers them to connected frontends automatically.

import logging
from api.supabase_client import supabase
from typing import Optional, Dict, Any
from datetime import datetime

logger = logging.getLogger(__name__)

HARI_BOT_USER_ID = "00000000-0000-0000-0000-000000000004"

_bot_user_verified = False


def _ensure_bot_user_exists():
    """Create Hari bot user if it doesn't exist (runs once per process)."""
    global _bot_user_verified
    if _bot_user_verified:
        return

    try:
        result = supabase.table("users") \
            .select("user_id") \
            .eq("user_id", HARI_BOT_USER_ID) \
            .execute()

        if not result.data or len(result.data) == 0:
            logger.info("[HariMessenger] Hari user not found, creating...")
            dummy_hash = "$2b$12$HariNoLoginHariNoLoginAN3.3.3.3.3.3.3.3.3.3.3.3.3.33"

            supabase.table("users").insert({
                "user_id": HARI_BOT_USER_ID,
                "user_name": "Hari",
                "avatar_color": 280,
                "password_hash": dummy_hash,
            }).execute()
            logger.info("[HariMessenger] Hari user created successfully")
        else:
            logger.info("[HariMessenger] Hari user already exists")

        _bot_user_verified = True
    except Exception as e:
        logger.error("[HariMessenger] Error ensuring bot user exists: %s", e)


def post_hari_message(
    content: str,
    project_id: str = None,
    channel_type: str = "project_general",
    channel_id: str = None,
    metadata: Optional[Dict[str, Any]] = None
) -> Optional[Dict[str, Any]]:
    """
    Post a message to a channel as the Hari bot.

    For project channels: pass project_id (+ channel_type).
    For DM/group channels: pass channel_id (+ channel_type="direct" or "group").

    Args:
        content: Message text (markdown supported)
        project_id: Project UUID (for project channels)
        channel_type: Channel type (default: project_general)
        channel_id: Channel UUID (for direct/group/custom channels)
        metadata: Optional metadata dict (e.g. task_card, task_id)

    Returns:
        Created message record or None on failure
    """
    _ensure_bot_user_exists()

    try:
        message_data = {
            "content": content,
            "channel_type": channel_type,
            "user_id": HARI_BOT_USER_ID,
            "metadata": metadata or {},
            "created_at": datetime.utcnow().isoformat(),
        }

        if channel_id:
            message_data["channel_id"] = channel_id
        elif project_id:
            message_data["project_id"] = project_id
        else:
            logger.warning("[HariMessenger] WARNING: No project_id or channel_id provided")
            return None

        target = f"channel={channel_id}" if channel_id else f"project={project_id}"
        logger.info("[HariMessenger] Inserting message | %s | type=%s", target, channel_type)
        result = supabase.table("messages").insert(message_data).execute()

        if result.data and len(result.data) > 0:
            msg_id = result.data[0].get("id", "?")
            logger.info("[HariMessenger] Message posted OK | id=%s | %s", msg_id, target)
            return result.data[0]

        logger.warning("[HariMessenger] Insert returned no data | %s", target)
        return None

    except Exception as e:
        logger.error("[HariMessenger] ERROR posting message: %s", e)
        return None
