# api/helpers/andrew_messenger.py
# ================================
# Andrew Bot Message Helper
# ================================
# Posts receipt processing messages to project receipts channels.
# Messages are inserted directly into the messages table;
# Supabase Realtime delivers them to connected frontends automatically.

from api.supabase_client import supabase
from typing import Optional, Dict, Any
from datetime import datetime

ANDREW_BOT_USER_ID = "00000000-0000-0000-0000-000000000003"

_bot_user_verified = False


def _ensure_bot_user_exists():
    """Create Andrew bot user if it doesn't exist (runs once per process)."""
    global _bot_user_verified
    if _bot_user_verified:
        return

    try:
        result = supabase.table("users") \
            .select("user_id") \
            .eq("user_id", ANDREW_BOT_USER_ID) \
            .execute()

        if not result.data or len(result.data) == 0:
            print("[AndrewMessenger] Andrew user not found, creating...")
            dummy_hash = "$2b$12$AndrewNoLoginAndrewNoLogAN3.3.3.3.3.3.3.3.3.3.3.3.3.3"

            supabase.table("users").insert({
                "user_id": ANDREW_BOT_USER_ID,
                "user_name": "Andrew",
                "avatar_color": 35,
                "password_hash": dummy_hash,
            }).execute()
            print("[AndrewMessenger] Andrew user created successfully")
        else:
            print("[AndrewMessenger] Andrew user already exists")

        _bot_user_verified = True
    except Exception as e:
        print(f"[AndrewMessenger] Error ensuring bot user exists: {e}")
        _bot_user_verified = True


def post_andrew_message(
    content: str,
    project_id: str = None,
    channel_type: str = "project_receipts",
    channel_id: str = None,
    metadata: Optional[Dict[str, Any]] = None
) -> Optional[Dict[str, Any]]:
    """
    Post a message to a project channel as the Andrew bot.

    For project channels: pass project_id (+ channel_type like project_receipts).
    For group channels: pass channel_id (+ channel_type="group").

    Args:
        content: Message text (markdown supported)
        project_id: Project UUID (for project channels)
        channel_type: Channel type (default: project_receipts)
        channel_id: Channel UUID (for group/custom channels, e.g. Payroll)
        metadata: Optional metadata dict (e.g. receipt_id, status)

    Returns:
        Created message record or None on failure
    """
    _ensure_bot_user_exists()

    try:
        message_data = {
            "content": content,
            "channel_type": channel_type,
            "user_id": ANDREW_BOT_USER_ID,
            "metadata": metadata or {},
            "created_at": datetime.utcnow().isoformat(),
        }

        if channel_id:
            message_data["channel_id"] = channel_id
        elif project_id:
            message_data["project_id"] = project_id
        else:
            print("[AndrewMessenger] WARNING: No project_id or channel_id provided")
            return None

        target = f"channel={channel_id}" if channel_id else f"project={project_id}"
        print(f"[AndrewMessenger] Inserting message | {target} | type={channel_type}")
        result = supabase.table("messages").insert(message_data).execute()

        if result.data and len(result.data) > 0:
            msg_id = result.data[0].get("id", "?")
            print(f"[AndrewMessenger] Message posted OK | id={msg_id} | {target}")
            return result.data[0]

        print(f"[AndrewMessenger] Insert returned no data | {target}")
        return None

    except Exception as e:
        # Never let bot message failures break the main pipeline
        print(f"[AndrewMessenger] ERROR posting message: {e}")
        return None
