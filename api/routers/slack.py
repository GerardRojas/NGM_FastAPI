"""
===============================================================================
 NGM HUB - Slack Integration Router
===============================================================================
 Endpoints for managing Slack integration:
 - Connect/disconnect user's Slack account
 - Toggle notifications on/off
 - Admin endpoints for workspace config
===============================================================================
"""

from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel, Field
from typing import Optional
from api.supabase_client import supabase
from api.auth import get_current_user

router = APIRouter(prefix="/slack", tags=["slack"])


# ═══════════════════════════════════════════════════════════════════════════════
# PYDANTIC MODELS
# ═══════════════════════════════════════════════════════════════════════════════

class SlackConnect(BaseModel):
    slack_user_id: str = Field(..., min_length=1, description="Slack member ID (e.g., U01ABC123)")
    slack_username: Optional[str] = None


class SlackConfigCreate(BaseModel):
    workspace_name: str
    bot_token: str = Field(..., min_length=1)
    notification_channel: Optional[str] = None


class NotificationToggle(BaseModel):
    enabled: bool


# ═══════════════════════════════════════════════════════════════════════════════
# USER ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/status")
def get_slack_status(
    current_user: dict = Depends(get_current_user)
):
    """Get current user's Slack connection status"""
    try:
        user_id = current_user["user_id"]

        result = supabase.table("slack_user_mappings") \
            .select("slack_user_id, slack_username, notifications_enabled, created_at") \
            .eq("user_id", user_id) \
            .execute()

        if result.data:
            mapping = result.data[0]
            return {
                "connected": True,
                "slack_user_id": mapping.get("slack_user_id"),
                "slack_username": mapping.get("slack_username"),
                "notifications_enabled": mapping.get("notifications_enabled", True),
                "connected_at": mapping.get("created_at")
            }

        return {
            "connected": False,
            "notifications_enabled": False
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")


@router.post("/connect")
def connect_slack(
    payload: SlackConnect,
    current_user: dict = Depends(get_current_user)
):
    """
    Connect user's NGM Hub account to their Slack account.

    The user needs to provide their Slack member ID, which can be found in
    Slack by clicking on their profile > More > Copy member ID.
    """
    try:
        user_id = current_user["user_id"]

        # Check if already connected
        existing = supabase.table("slack_user_mappings") \
            .select("id") \
            .eq("user_id", user_id) \
            .execute()

        if existing.data:
            # Update existing mapping
            result = supabase.table("slack_user_mappings") \
                .update({
                    "slack_user_id": payload.slack_user_id,
                    "slack_username": payload.slack_username,
                    "notifications_enabled": True
                }) \
                .eq("user_id", user_id) \
                .execute()
        else:
            # Create new mapping
            result = supabase.table("slack_user_mappings") \
                .insert({
                    "user_id": user_id,
                    "slack_user_id": payload.slack_user_id,
                    "slack_username": payload.slack_username,
                    "notifications_enabled": True
                }) \
                .execute()

        if not result.data:
            raise HTTPException(status_code=500, detail="Failed to save Slack connection")

        return {
            "ok": True,
            "message": "Slack connected successfully. You'll now receive notifications when mentioned."
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")


@router.delete("/disconnect")
def disconnect_slack(
    current_user: dict = Depends(get_current_user)
):
    """Disconnect Slack from user's account"""
    try:
        user_id = current_user["user_id"]

        supabase.table("slack_user_mappings") \
            .delete() \
            .eq("user_id", user_id) \
            .execute()

        return {
            "ok": True,
            "message": "Slack disconnected. You will no longer receive Slack notifications."
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")


@router.patch("/notifications")
def toggle_notifications(
    payload: NotificationToggle,
    current_user: dict = Depends(get_current_user)
):
    """Enable or disable Slack notifications without disconnecting"""
    try:
        user_id = current_user["user_id"]

        result = supabase.table("slack_user_mappings") \
            .update({"notifications_enabled": payload.enabled}) \
            .eq("user_id", user_id) \
            .execute()

        if not result.data:
            raise HTTPException(
                status_code=404,
                detail="Slack not connected. Connect Slack first."
            )

        status = "enabled" if payload.enabled else "disabled"
        return {
            "ok": True,
            "message": f"Slack notifications {status}."
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")


# ═══════════════════════════════════════════════════════════════════════════════
# ADMIN ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/config")
def get_slack_config(
    current_user: dict = Depends(get_current_user)
):
    """Get Slack workspace configuration (admin only)"""
    try:
        # TODO: Add admin role check

        result = supabase.table("slack_config") \
            .select("id, workspace_name, notification_channel, is_active, created_at") \
            .execute()

        configs = result.data or []

        # Don't expose the actual bot token
        return {"configs": configs}

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")


@router.post("/config")
def create_slack_config(
    payload: SlackConfigCreate,
    current_user: dict = Depends(get_current_user)
):
    """
    Create/update Slack workspace configuration.

    This stores the bot token needed to send messages.
    Should only be accessible by admins.
    """
    try:
        # TODO: Add admin role check

        # Deactivate any existing configs
        supabase.table("slack_config") \
            .update({"is_active": False}) \
            .eq("is_active", True) \
            .execute()

        # Create new config
        result = supabase.table("slack_config") \
            .insert({
                "workspace_name": payload.workspace_name,
                "bot_token": payload.bot_token,
                "notification_channel": payload.notification_channel,
                "is_active": True
            }) \
            .execute()

        if not result.data:
            raise HTTPException(status_code=500, detail="Failed to save configuration")

        return {
            "ok": True,
            "message": "Slack configuration saved successfully."
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")


# ═══════════════════════════════════════════════════════════════════════════════
# NOTIFICATION LOG (DEBUG)
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/logs")
def get_notification_logs(
    limit: int = Query(50, ge=1, le=200),
    current_user: dict = Depends(get_current_user)
):
    """Get recent notification logs for debugging"""
    try:
        user_id = current_user["user_id"]

        result = supabase.table("slack_notification_log") \
            .select("*") \
            .eq("recipient_user_id", user_id) \
            .order("created_at", desc=True) \
            .limit(limit) \
            .execute()

        return {"logs": result.data or []}

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")


@router.post("/test")
def send_test_notification(
    current_user: dict = Depends(get_current_user)
):
    """Send a test notification to verify Slack is working"""
    try:
        from api.services.slack_notifications import (
            get_slack_config,
            get_slack_user_id,
            send_slack_dm_sync
        )

        user_id = current_user["user_id"]
        user_name = current_user.get("user_name", "User")

        # Get Slack config
        config = get_slack_config()
        if not config:
            raise HTTPException(
                status_code=400,
                detail="Slack not configured. Ask an admin to set up the Slack integration."
            )

        # Get user's Slack ID
        slack_user_id = get_slack_user_id(str(user_id))
        if not slack_user_id:
            raise HTTPException(
                status_code=400,
                detail="Your Slack account is not connected. Go to Settings to connect."
            )

        # Send test message
        bot_token = config.get("bot_token")
        success = send_slack_dm_sync(
            slack_user_id=slack_user_id,
            bot_token=bot_token,
            message=f"Test notification from NGM Hub. Your Slack integration is working, {user_name}!"
        )

        if success:
            return {
                "ok": True,
                "message": "Test notification sent! Check your Slack DMs."
            }
        else:
            raise HTTPException(
                status_code=500,
                detail="Failed to send test notification. Check the Slack configuration."
            )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")
