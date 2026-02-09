# api/helpers/bot_messenger.py
# ================================
# Arturito Bot Message Helper
# ================================
# Posts messages to project channels as the Arturito bot user.
# Messages are inserted directly into the messages table;
# Supabase Realtime delivers them to connected frontends automatically.

from api.supabase_client import supabase
from typing import Optional, Dict, Any
from datetime import datetime

ARTURITO_BOT_USER_ID = "00000000-0000-0000-0000-000000000001"

_bot_user_verified = False


def _ensure_bot_user_exists():
    """Create Arturito bot user if it doesn't exist (runs once per process)."""
    global _bot_user_verified
    if _bot_user_verified:
        return

    try:
        result = supabase.table("users") \
            .select("user_id") \
            .eq("user_id", ARTURITO_BOT_USER_ID) \
            .execute()

        if not result.data or len(result.data) == 0:
            print("[BotMessenger] Arturito user not found, creating...")
            supabase.table("users").insert({
                "user_id": ARTURITO_BOT_USER_ID,
                "user_name": "Arturito",
                "avatar_color": 35,
            }).execute()
            print("[BotMessenger] Arturito user created successfully")

        _bot_user_verified = True
    except Exception as e:
        print(f"[BotMessenger] Error ensuring bot user exists: {e}")
        # Still mark as verified to avoid retrying every message
        _bot_user_verified = True


def post_bot_message(
    content: str,
    project_id: str,
    channel_type: str = "project_receipts",
    metadata: Optional[Dict[str, Any]] = None
) -> Optional[Dict[str, Any]]:
    """
    Post a message to a project channel as the Arturito bot.

    Args:
        content: Message text (markdown supported)
        project_id: Project UUID
        channel_type: Channel type (default: project_receipts)
        metadata: Optional metadata dict (e.g. receipt_id, status)

    Returns:
        Created message record or None on failure
    """
    _ensure_bot_user_exists()

    try:
        message_data = {
            "content": content,
            "channel_type": channel_type,
            "project_id": project_id,
            "user_id": ARTURITO_BOT_USER_ID,
            "metadata": metadata or {},
            "created_at": datetime.utcnow().isoformat(),
        }

        result = supabase.table("messages").insert(message_data).execute()

        if result.data and len(result.data) > 0:
            print(f"[BotMessenger] Message posted OK | project={project_id}")
            return result.data[0]

        print(f"[BotMessenger] Insert returned no data | project={project_id}")
        return None

    except Exception as e:
        # Never let bot message failures break the main pipeline
        print(f"[BotMessenger] Error posting message: {e}")
        return None
