# ============================================================================
# NGM Hub - Push Notifications Router
# ============================================================================
# Endpoints for managing push notification tokens

import os
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional
from supabase import create_client, Client

from api.auth import get_current_user

router = APIRouter(prefix="/notifications", tags=["notifications"])

# ============================================================================
# Supabase Client
# ============================================================================

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    raise RuntimeError("SUPABASE_URL or SUPABASE_KEY not defined in .env")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)


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
