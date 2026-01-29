"""
===============================================================================
 NGM HUB - Slack Notifications Service
===============================================================================
 Sends Slack DMs when users are @mentioned in NGM Hub Messages.

 Setup:
 1. Create Slack App at https://api.slack.com/apps
 2. Add Bot Token Scopes: chat:write, users:read
 3. Install to workspace, save Bot Token in slack_config table
 4. Map users via slack_user_mappings table
===============================================================================
"""

import os
import re
import httpx
from typing import List, Dict, Optional, Any
from api.supabase_client import supabase

# Slack API base URL
SLACK_API_URL = "https://slack.com/api"


def get_slack_config() -> Optional[Dict[str, Any]]:
    """Get active Slack workspace configuration"""
    try:
        result = supabase.table("slack_config") \
            .select("*") \
            .eq("is_active", True) \
            .limit(1) \
            .execute()

        if result.data:
            return result.data[0]
        return None
    except Exception as e:
        print(f"[Slack] Error getting config: {e}")
        return None


def get_slack_user_id(user_id: str) -> Optional[str]:
    """Get Slack user ID for an NGM Hub user"""
    try:
        result = supabase.table("slack_user_mappings") \
            .select("slack_user_id, notifications_enabled") \
            .eq("user_id", user_id) \
            .single() \
            .execute()

        if result.data and result.data.get("notifications_enabled", True):
            return result.data.get("slack_user_id")
        return None
    except Exception:
        return None


def extract_mentioned_usernames(content: str) -> List[str]:
    """Extract @mentions from message content"""
    # Match @username (alphanumeric, no spaces)
    pattern = r"@(\w+)"
    matches = re.findall(pattern, content)
    return matches


def get_users_by_usernames(usernames: List[str]) -> List[Dict[str, Any]]:
    """Get user records by their usernames (case-insensitive, space-removed)"""
    if not usernames:
        return []

    try:
        # Get all users and filter by normalized username match
        result = supabase.table("users") \
            .select("user_id, user_name") \
            .execute()

        users = []
        for row in (result.data or []):
            user_name = row.get("user_name", "")
            # Normalize: lowercase and remove spaces
            normalized = user_name.lower().replace(" ", "")
            for mention in usernames:
                if mention.lower() == normalized:
                    users.append(row)
                    break

        return users
    except Exception as e:
        print(f"[Slack] Error getting users: {e}")
        return []


async def send_slack_dm(
    slack_user_id: str,
    bot_token: str,
    message: str,
    blocks: Optional[List[Dict]] = None
) -> bool:
    """
    Send a direct message to a Slack user.

    Args:
        slack_user_id: Slack member ID (e.g., U01ABC123)
        bot_token: Slack bot token (xoxb-...)
        message: Plain text message (fallback)
        blocks: Optional Block Kit blocks for rich formatting

    Returns:
        True if message was sent successfully
    """
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{SLACK_API_URL}/chat.postMessage",
                headers={
                    "Authorization": f"Bearer {bot_token}",
                    "Content-Type": "application/json"
                },
                json={
                    "channel": slack_user_id,  # DM by user ID
                    "text": message,
                    "blocks": blocks,
                    "unfurl_links": False,
                    "unfurl_media": False
                }
            )

            data = response.json()

            if data.get("ok"):
                return True
            else:
                print(f"[Slack] API error: {data.get('error')}")
                return False

    except Exception as e:
        print(f"[Slack] Send error: {e}")
        return False


def send_slack_dm_sync(
    slack_user_id: str,
    bot_token: str,
    message: str,
    blocks: Optional[List[Dict]] = None
) -> bool:
    """Synchronous version of send_slack_dm for non-async contexts"""
    try:
        with httpx.Client() as client:
            response = client.post(
                f"{SLACK_API_URL}/chat.postMessage",
                headers={
                    "Authorization": f"Bearer {bot_token}",
                    "Content-Type": "application/json"
                },
                json={
                    "channel": slack_user_id,
                    "text": message,
                    "blocks": blocks,
                    "unfurl_links": False,
                    "unfurl_media": False
                }
            )

            data = response.json()

            if data.get("ok"):
                return True
            else:
                print(f"[Slack] API error: {data.get('error')}")
                return False

    except Exception as e:
        print(f"[Slack] Send error: {e}")
        return False


def log_notification(
    message_id: str,
    recipient_user_id: str,
    slack_user_id: str,
    status: str,
    error_message: Optional[str] = None
) -> None:
    """Log notification attempt to database"""
    try:
        supabase.table("slack_notification_log").insert({
            "message_id": message_id,
            "recipient_user_id": recipient_user_id,
            "slack_user_id": slack_user_id,
            "notification_type": "mention",
            "status": status,
            "error_message": error_message,
            "sent_at": "now()" if status == "sent" else None
        }).execute()
    except Exception as e:
        print(f"[Slack] Log error: {e}")


def build_mention_notification(
    sender_name: str,
    channel_name: str,
    content: str,
    ngm_hub_url: str = "https://ngm-hub-frontend.onrender.com"
) -> tuple[str, List[Dict]]:
    """
    Build Slack message with Block Kit formatting for mention notification.

    Returns:
        Tuple of (plain_text_fallback, blocks)
    """
    # Truncate content for preview
    preview = content[:200] + "..." if len(content) > 200 else content

    plain_text = f"{sender_name} mentioned you in {channel_name}: {preview}"

    blocks = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*{sender_name}* mentioned you in *{channel_name}*"
            }
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f">{preview}"
            }
        },
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {
                        "type": "plain_text",
                        "text": "View in NGM Hub"
                    },
                    "url": f"{ngm_hub_url}/messages.html",
                    "action_id": "view_message"
                }
            ]
        }
    ]

    return plain_text, blocks


def notify_mentioned_users(
    message_id: str,
    content: str,
    sender_id: str,
    sender_name: str,
    channel_name: str
) -> Dict[str, Any]:
    """
    Send Slack notifications to all mentioned users in a message.

    This is the main function to call after a message is created.

    Args:
        message_id: ID of the message
        content: Message content (to extract @mentions)
        sender_id: User ID of message sender (to exclude from notifications)
        sender_name: Display name of sender
        channel_name: Name of the channel/project for context

    Returns:
        Dict with notification results
    """
    # Get Slack config
    config = get_slack_config()
    if not config:
        return {"sent": 0, "skipped": 0, "error": "Slack not configured"}

    bot_token = config.get("bot_token")
    if not bot_token:
        return {"sent": 0, "skipped": 0, "error": "Bot token missing"}

    # Extract mentioned usernames
    mentioned_usernames = extract_mentioned_usernames(content)
    if not mentioned_usernames:
        return {"sent": 0, "skipped": 0, "message": "No mentions found"}

    # Get user records for mentioned usernames
    mentioned_users = get_users_by_usernames(mentioned_usernames)

    sent_count = 0
    skipped_count = 0

    for user in mentioned_users:
        user_id = user.get("user_id")

        # Don't notify sender of their own mention
        if str(user_id) == str(sender_id):
            continue

        # Get Slack user ID
        slack_user_id = get_slack_user_id(str(user_id))
        if not slack_user_id:
            skipped_count += 1
            continue

        # Build and send notification
        plain_text, blocks = build_mention_notification(
            sender_name=sender_name,
            channel_name=channel_name,
            content=content
        )

        success = send_slack_dm_sync(
            slack_user_id=slack_user_id,
            bot_token=bot_token,
            message=plain_text,
            blocks=blocks
        )

        if success:
            sent_count += 1
            log_notification(message_id, str(user_id), slack_user_id, "sent")
        else:
            skipped_count += 1
            log_notification(message_id, str(user_id), slack_user_id, "failed", "API error")

    return {
        "sent": sent_count,
        "skipped": skipped_count,
        "message": f"Notified {sent_count} users via Slack"
    }
