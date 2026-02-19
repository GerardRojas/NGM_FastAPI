# ============================================================================
# NGM Hub - Push Notifications Router
# ============================================================================
# Endpoints for managing push notification tokens

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional

from api.auth import get_current_user
from api.supabase_client import supabase
from api.services.firebase_notifications import send_push_notification

router = APIRouter(prefix="/notifications", tags=["notifications"])


# ============================================================================
# Pydantic Models
# ============================================================================

class RegisterTokenRequest(BaseModel):
    fcm_token: str
    device_info: Optional[str] = None


class TokenResponse(BaseModel):
    success: bool
    message: str


# ============================================================================
# Endpoints
# ============================================================================

@router.post("/token", response_model=TokenResponse)
def register_push_token(
    payload: RegisterTokenRequest,
    current_user: dict = Depends(get_current_user)
):
    """
    Register or update an FCM push token for the current user.
    Called by the frontend when Firebase provides a new token.
    """
    user_id = current_user["user_id"]
    fcm_token = payload.fcm_token
    device_info = payload.device_info

    try:
        # Check if token already exists
        existing = supabase.table("push_tokens") \
            .select("id, user_id") \
            .eq("fcm_token", fcm_token) \
            .execute()

        if existing.data:
            # Token exists - update it (might be for different user or reactivate)
            token_record = existing.data[0]

            supabase.table("push_tokens") \
                .update({
                    "user_id": user_id,
                    "device_info": device_info,
                    "is_active": True
                }) \
                .eq("id", token_record["id"]) \
                .execute()

            return TokenResponse(success=True, message="Token updated")

        else:
            # New token - insert it
            supabase.table("push_tokens") \
                .insert({
                    "user_id": user_id,
                    "fcm_token": fcm_token,
                    "device_info": device_info,
                    "is_active": True
                }) \
                .execute()

            return TokenResponse(success=True, message="Token registered")

    except Exception as e:
        print(f"[Notifications] Error registering token: {repr(e)}")
        raise HTTPException(status_code=500, detail="Failed to register token")


@router.delete("/token", response_model=TokenResponse)
def unregister_push_token(
    payload: RegisterTokenRequest,
    current_user: dict = Depends(get_current_user)
):
    """
    Unregister/deactivate an FCM push token.
    Called when user logs out or disables notifications.
    """
    user_id = current_user["user_id"]
    fcm_token = payload.fcm_token

    try:
        # Deactivate the token (don't delete, for audit trail)
        supabase.table("push_tokens") \
            .update({"is_active": False}) \
            .eq("fcm_token", fcm_token) \
            .eq("user_id", user_id) \
            .execute()

        return TokenResponse(success=True, message="Token unregistered")

    except Exception as e:
        print(f"[Notifications] Error unregistering token: {repr(e)}")
        raise HTTPException(status_code=500, detail="Failed to unregister token")


@router.get("/status")
def get_notification_status(current_user: dict = Depends(get_current_user)):
    """
    Check if the current user has any active push tokens registered.
    """
    user_id = current_user["user_id"]

    try:
        result = supabase.table("push_tokens") \
            .select("id, device_info, created_at") \
            .eq("user_id", user_id) \
            .eq("is_active", True) \
            .execute()

        return {
            "has_tokens": len(result.data) > 0,
            "token_count": len(result.data),
            "devices": [
                {
                    "id": t["id"],
                    "device_info": t["device_info"],
                    "registered_at": t["created_at"]
                }
                for t in result.data
            ]
        }

    except Exception as e:
        print(f"[Notifications] Error getting status: {repr(e)}")
        raise HTTPException(status_code=500, detail="Failed to get notification status")


# ============================================================================
# Test Endpoint
# ============================================================================

class TestNotificationRequest(BaseModel):
    title: Optional[str] = "🔔 Test Notification"
    body: Optional[str] = "This is a test push notification from NGM Hub!"


@router.post("/test")
async def send_test_notification(
    payload: TestNotificationRequest = TestNotificationRequest(),
    current_user: dict = Depends(get_current_user)
):
    """
    Send a test push notification to the current user.
    Useful for verifying that push notifications are working.
    """
    user_id = current_user["user_id"]
    user_name = current_user.get("user_name", "User")

    # Check if user has any tokens
    try:
        tokens_result = supabase.table("push_tokens") \
            .select("id") \
            .eq("user_id", user_id) \
            .eq("is_active", True) \
            .execute()

        if not tokens_result.data:
            raise HTTPException(
                status_code=400,
                detail="No push tokens registered. Please allow notifications in your browser first."
            )
    except HTTPException:
        raise
    except Exception as e:
        print(f"[Notifications] Error checking tokens: {repr(e)}")
        raise HTTPException(status_code=500, detail="Failed to check tokens")

    # Send test notification
    try:
        success = await send_push_notification(
            user_id=user_id,
            title=payload.title,
            body=payload.body,
            data={
                "type": "test",
                "url": "/dashboard.html"
            },
            sender_name="NGM Hub"
        )

        if success:
            return {
                "success": True,
                "message": f"Test notification sent to {user_name}",
                "tokens_count": len(tokens_result.data)
            }
        else:
            raise HTTPException(
                status_code=500,
                detail="Failed to send notification. Check Firebase configuration."
            )

    except HTTPException:
        raise
    except Exception as e:
        print(f"[Notifications] Error sending test: {repr(e)}")
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")
