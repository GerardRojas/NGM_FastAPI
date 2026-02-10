# ============================================================================
# NGM Hub - Firebase Push Notifications Service
# ============================================================================
# Sends push notifications via Firebase Cloud Messaging (FCM)
# Used to notify users when they are @mentioned in messages

import os
import logging
from typing import List, Optional
import firebase_admin
from firebase_admin import credentials, messaging
from supabase import create_client, Client

logger = logging.getLogger(__name__)

# ============================================================================
# Firebase Admin SDK Initialization
# ============================================================================

_firebase_initialized = False

def initialize_firebase():
    """Initialize Firebase Admin SDK (only once)."""
    global _firebase_initialized

    if _firebase_initialized:
        return True

    try:
        # Check if already initialized
        firebase_admin.get_app()
        _firebase_initialized = True
        return True
    except ValueError:
        pass

    # Try to initialize with service account
    service_account_path = os.getenv("FIREBASE_SERVICE_ACCOUNT_PATH")

    if service_account_path and os.path.exists(service_account_path):
        cred = credentials.Certificate(service_account_path)
        firebase_admin.initialize_app(cred)
        _firebase_initialized = True
        logger.info("[Firebase] Initialized with service account file")
        return True

    # Try with JSON string from env
    service_account_json = os.getenv("FIREBASE_SERVICE_ACCOUNT_JSON")
    if service_account_json:
        import json
        cred = credentials.Certificate(json.loads(service_account_json))
        firebase_admin.initialize_app(cred)
        _firebase_initialized = True
        logger.info("[Firebase] Initialized with service account JSON")
        return True

    logger.error("[Firebase] No service account credentials found")
    return False


# ============================================================================
# Supabase Client
# ============================================================================

def get_supabase() -> Client:
    """Get Supabase client for database operations."""
    from api.supabase_client import supabase
    return supabase


# ============================================================================
# Token Management
# ============================================================================

async def get_user_push_tokens(user_id: str) -> List[str]:
    """Get all active FCM tokens for a user."""
    try:
        supabase = get_supabase()
        result = supabase.table("push_tokens") \
            .select("fcm_token") \
            .eq("user_id", user_id) \
            .eq("is_active", True) \
            .execute()

        return [row["fcm_token"] for row in result.data]
    except Exception as e:
        logger.error(f"[Firebase] Error getting tokens for user {user_id}: {e}")
        return []


async def deactivate_invalid_token(fcm_token: str):
    """Mark a token as inactive (when it's no longer valid)."""
    try:
        supabase = get_supabase()
        supabase.table("push_tokens") \
            .update({"is_active": False}) \
            .eq("fcm_token", fcm_token) \
            .execute()
        logger.info(f"[Firebase] Deactivated invalid token")
    except Exception as e:
        logger.error(f"[Firebase] Error deactivating token: {e}")


# ============================================================================
# Send Push Notification
# ============================================================================

async def send_push_notification(
    user_id: str,
    title: str,
    body: str,
    data: Optional[dict] = None,
    sender_name: Optional[str] = None,
    avatar_color: Optional[str] = None,
    tag: str = "ngm-mention"
) -> bool:
    """
    Send a push notification to all devices of a user.

    Args:
        user_id: The UUID of the user to notify
        title: Notification title
        body: Notification body text
        data: Optional custom data payload
        sender_name: Name of the sender (for avatar)
        avatar_color: Color for the avatar
        tag: Webpush notification tag (notifications with same tag replace each other)

    Returns:
        True if at least one notification was sent successfully
    """
    if not initialize_firebase():
        logger.error("[Firebase] Cannot send notification - not initialized")
        return False

    tokens = await get_user_push_tokens(user_id)

    if not tokens:
        logger.info(f"[Firebase] No push tokens for user {user_id}")
        return False

    # Build notification payload
    notification = messaging.Notification(
        title=title,
        body=body
    )

    # Build data payload
    payload_data = data or {}
    if sender_name:
        payload_data["sender_name"] = sender_name
    if avatar_color:
        payload_data["avatar_color"] = avatar_color

    # Ensure all values are strings (FCM requirement)
    payload_data = {k: str(v) for k, v in payload_data.items()}

    success_count = 0

    for token in tokens:
        try:
            message = messaging.Message(
                notification=notification,
                data=payload_data,
                token=token,
                webpush=messaging.WebpushConfig(
                    notification=messaging.WebpushNotification(
                        icon="/assets/img/greenblack_icon.png",
                        badge="/assets/img/greenblack_icon.png",
                        tag=tag,
                        require_interaction=True
                    ),
                    fcm_options=messaging.WebpushFCMOptions(
                        link=payload_data.get("url", "/messages.html")
                    )
                )
            )

            response = messaging.send(message)
            logger.info(f"[Firebase] Notification sent: {response}")
            success_count += 1

        except messaging.UnregisteredError:
            logger.warning(f"[Firebase] Token unregistered, deactivating")
            await deactivate_invalid_token(token)

        except messaging.SenderIdMismatchError:
            logger.error(f"[Firebase] Sender ID mismatch for token")
            await deactivate_invalid_token(token)

        except Exception as e:
            logger.error(f"[Firebase] Error sending to token: {e}")

    return success_count > 0


# ============================================================================
# Notify Mentioned Users
# ============================================================================

async def notify_mentioned_users(
    mentioned_user_ids: List[str],
    sender_name: str,
    message_preview: str,
    channel_name: str,
    message_url: Optional[str] = None,
    avatar_color: Optional[str] = None
) -> int:
    """
    Send push notifications to all mentioned users.

    Args:
        mentioned_user_ids: List of user UUIDs that were mentioned
        sender_name: Name of the person who sent the message
        message_preview: Preview of the message content
        channel_name: Name of the channel/thread
        message_url: URL to open when notification is clicked
        avatar_color: Color for sender's avatar

    Returns:
        Number of users successfully notified
    """
    if not mentioned_user_ids:
        return 0

    title = f"{sender_name} mentioned you"
    body = f"in #{channel_name}: {message_preview[:100]}"

    if len(message_preview) > 100:
        body = body + "..."

    data = {
        "type": "mention",
        "channel": channel_name,
        "url": message_url or "/messages.html"
    }

    notified_count = 0

    for user_id in mentioned_user_ids:
        success = await send_push_notification(
            user_id=user_id,
            title=title,
            body=body,
            data=data,
            sender_name=sender_name,
            avatar_color=avatar_color
        )
        if success:
            notified_count += 1

    logger.info(f"[Firebase] Notified {notified_count}/{len(mentioned_user_ids)} mentioned users")
    return notified_count


# ============================================================================
# Notify Message Recipients (DM / Group)
# ============================================================================

async def notify_message_recipients(
    recipient_user_ids: List[str],
    sender_name: str,
    message_preview: str,
    channel_name: str,
    channel_type: str,
    message_url: Optional[str] = None,
    avatar_color: Optional[str] = None
) -> int:
    """
    Send push notifications to DM/group message recipients.

    Args:
        recipient_user_ids: List of user UUIDs to notify (sender already excluded)
        sender_name: Name of the person who sent the message
        message_preview: Preview of the message content
        channel_name: Name of the channel (for group) or sender name (for DM)
        channel_type: "direct" or "group"
        message_url: URL to open when notification is clicked
        avatar_color: Color for sender's avatar

    Returns:
        Number of users successfully notified
    """
    if not recipient_user_ids:
        return 0

    preview = message_preview[:100]
    if len(message_preview) > 100:
        preview += "..."

    if channel_type == "direct":
        title = sender_name
        body = preview
    else:
        title = f"{sender_name} in {channel_name}"
        body = preview

    data = {
        "type": "message",
        "channel": channel_name,
        "url": message_url or "/messages.html"
    }

    notified_count = 0

    for user_id in recipient_user_ids:
        success = await send_push_notification(
            user_id=user_id,
            title=title,
            body=body,
            data=data,
            sender_name=sender_name,
            avatar_color=avatar_color,
            tag=f"ngm-msg-{channel_name[:20]}"
        )
        if success:
            notified_count += 1

    logger.info(f"[Firebase] Notified {notified_count}/{len(recipient_user_ids)} {channel_type} recipients")
    return notified_count


# ============================================================================
# Notify Expense Authorizers
# ============================================================================

async def get_expense_authorizers() -> List[dict]:
    """
    Get all users who can authorize expenses.
    These are users with roles: CEO, COO, CFO, Admin
    or users with can_edit permission on expenses module.
    """
    try:
        supabase = get_supabase()

        # Get users with authorizing roles
        authorizing_roles = ['CEO', 'COO', 'CFO', 'Admin', 'Accounting Manager']

        # First get the role IDs for these roles
        roles_result = supabase.table("rols") \
            .select("rol_id, rol_name") \
            .in_("rol_name", authorizing_roles) \
            .execute()

        role_ids = [r["rol_id"] for r in (roles_result.data or [])]

        if not role_ids:
            logger.warning("[Firebase] No authorizing roles found")
            return []

        # Get users with these roles
        users_result = supabase.table("users") \
            .select("user_id, user_name, user_email") \
            .in_("user_rol", role_ids) \
            .execute()

        authorizers = users_result.data or []
        logger.info(f"[Firebase] Found {len(authorizers)} expense authorizers")

        return authorizers

    except Exception as e:
        logger.error(f"[Firebase] Error getting expense authorizers: {e}")
        return []


async def notify_expense_authorizers(
    sender_name: str,
    pending_count: int,
    message: str,
    project_name: Optional[str] = None
) -> dict:
    """
    Send push notifications to all expense authorizers.

    Args:
        sender_name: Name of the person requesting the reminder
        pending_count: Number of pending expenses (0 if unknown)
        message: Custom message from the user
        project_name: Optional project name to scope the reminder

    Returns:
        Dict with success status and count of notified users
    """
    authorizers = await get_expense_authorizers()

    if not authorizers:
        return {
            "success": False,
            "notified_count": 0,
            "error": "No expense authorizers found"
        }

    # Build notification
    title = "⚠️ Recordatorio de Gastos Pendientes"

    if pending_count > 0:
        body = f"{sender_name} reporta: {pending_count} gastos esperando autorización"
    else:
        body = f"{sender_name}: {message[:100]}"

    if project_name:
        body += f" (Proyecto: {project_name})"

    data = {
        "type": "expense_reminder",
        "url": "/expenses.html",
        "sender": sender_name,
    }

    if pending_count > 0:
        data["pending_count"] = str(pending_count)

    notified_count = 0
    notified_users = []

    for user in authorizers:
        user_id = user.get("user_id")
        if not user_id:
            continue

        success = await send_push_notification(
            user_id=user_id,
            title=title,
            body=body,
            data=data,
            sender_name=sender_name
        )

        if success:
            notified_count += 1
            notified_users.append(user.get("user_name", "Unknown"))

    logger.info(f"[Firebase] Expense reminder sent to {notified_count}/{len(authorizers)} authorizers")

    return {
        "success": notified_count > 0,
        "notified_count": notified_count,
        "total_authorizers": len(authorizers),
        "notified_users": notified_users
    }
