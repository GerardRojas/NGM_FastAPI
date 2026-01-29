"""
Budget Alerts Router
Handles budget alert settings, recipients, and manual triggers
"""

from fastapi import APIRouter, HTTPException, Depends, BackgroundTasks
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime

from api.auth import get_current_user
from api.supabase_client import supabase

router = APIRouter(prefix="/budget-alerts", tags=["budget-alerts"])


# ================================
# MODELS
# ================================

class AlertSettingsUpdate(BaseModel):
    """Update alert settings"""
    warning_threshold: Optional[int] = None      # 0-100
    critical_threshold: Optional[int] = None     # 0-100
    overspend_alert: Optional[bool] = None
    no_budget_alert: Optional[bool] = None
    is_enabled: Optional[bool] = None
    check_frequency_minutes: Optional[int] = None
    quiet_start_hour: Optional[int] = None       # 0-23
    quiet_end_hour: Optional[int] = None         # 0-23


class AlertRecipientCreate(BaseModel):
    """Add a recipient to budget alerts"""
    user_id: str
    receive_warning: bool = True
    receive_critical: bool = True
    receive_overspend: bool = True
    receive_no_budget: bool = True
    notify_push: bool = True
    notify_dashboard: bool = True
    notify_email: bool = False


class AlertRecipientUpdate(BaseModel):
    """Update recipient preferences"""
    receive_warning: Optional[bool] = None
    receive_critical: Optional[bool] = None
    receive_overspend: Optional[bool] = None
    receive_no_budget: Optional[bool] = None
    notify_push: Optional[bool] = None
    notify_dashboard: Optional[bool] = None
    notify_email: Optional[bool] = None


class AcknowledgeAlertRequest(BaseModel):
    """Request to acknowledge a budget alert"""
    note: str                                    # Required justification note
    action: str = "acknowledged"                 # acknowledged, dismissed, resolved


# ================================
# SETTINGS ENDPOINTS
# ================================

@router.get("/settings")
async def get_alert_settings(
    project_id: Optional[str] = None,
    current_user: dict = Depends(get_current_user)
):
    """
    Get alert settings (global or project-specific).
    If project_id is provided, returns project settings or falls back to global.
    """
    try:
        # Try project-specific settings first
        if project_id:
            result = supabase.table("budget_alert_settings") \
                .select("*") \
                .eq("project_id", project_id) \
                .execute()

            if result.data:
                return {"data": result.data[0], "scope": "project"}

        # Fall back to global settings
        result = supabase.table("budget_alert_settings") \
            .select("*") \
            .is_("project_id", "null") \
            .execute()

        if result.data:
            return {"data": result.data[0], "scope": "global"}

        # Return defaults if nothing exists
        return {
            "data": {
                "warning_threshold": 80,
                "critical_threshold": 95,
                "overspend_alert": True,
                "no_budget_alert": True,
                "is_enabled": True,
                "check_frequency_minutes": 60,
                "quiet_start_hour": 22,
                "quiet_end_hour": 7,
            },
            "scope": "default"
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching settings: {str(e)}")


@router.put("/settings")
async def update_alert_settings(
    settings: AlertSettingsUpdate,
    project_id: Optional[str] = None,
    current_user: dict = Depends(get_current_user)
):
    """
    Update alert settings (global or project-specific).
    Creates settings if they don't exist.
    """
    try:
        # Build update data (only non-None fields)
        update_data = {k: v for k, v in settings.dict().items() if v is not None}
        update_data["updated_at"] = datetime.utcnow().isoformat()

        # Check if settings exist
        if project_id:
            existing = supabase.table("budget_alert_settings") \
                .select("id") \
                .eq("project_id", project_id) \
                .execute()
        else:
            existing = supabase.table("budget_alert_settings") \
                .select("id") \
                .is_("project_id", "null") \
                .execute()

        if existing.data:
            # Update existing
            settings_id = existing.data[0]["id"]
            result = supabase.table("budget_alert_settings") \
                .update(update_data) \
                .eq("id", settings_id) \
                .execute()
        else:
            # Create new
            update_data["project_id"] = project_id
            update_data["created_by"] = current_user.get("user_id")
            result = supabase.table("budget_alert_settings") \
                .insert(update_data) \
                .execute()

        return {"message": "Settings updated", "data": result.data[0] if result.data else update_data}

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error updating settings: {str(e)}")


# ================================
# RECIPIENTS ENDPOINTS
# ================================

@router.get("/recipients")
async def get_alert_recipients(
    project_id: Optional[str] = None,
    current_user: dict = Depends(get_current_user)
):
    """
    Get all recipients for budget alerts.
    Includes user details.
    """
    try:
        # First get the settings ID
        if project_id:
            settings_result = supabase.table("budget_alert_settings") \
                .select("id") \
                .eq("project_id", project_id) \
                .execute()
        else:
            settings_result = supabase.table("budget_alert_settings") \
                .select("id") \
                .is_("project_id", "null") \
                .execute()

        settings_id = settings_result.data[0]["id"] if settings_result.data else None

        # Get recipients
        if settings_id:
            result = supabase.table("budget_alert_recipients") \
                .select("*, users(user_id, user_name, user_email, avatar_color)") \
                .eq("settings_id", settings_id) \
                .execute()
        else:
            result = supabase.table("budget_alert_recipients") \
                .select("*, users(user_id, user_name, user_email, avatar_color)") \
                .is_("settings_id", "null") \
                .execute()

        return {"data": result.data or []}

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching recipients: {str(e)}")


@router.post("/recipients")
async def add_alert_recipient(
    recipient: AlertRecipientCreate,
    project_id: Optional[str] = None,
    current_user: dict = Depends(get_current_user)
):
    """
    Add a user as a budget alert recipient.
    """
    try:
        # Get settings ID (or create settings if they don't exist)
        if project_id:
            settings_result = supabase.table("budget_alert_settings") \
                .select("id") \
                .eq("project_id", project_id) \
                .execute()
        else:
            settings_result = supabase.table("budget_alert_settings") \
                .select("id") \
                .is_("project_id", "null") \
                .execute()

        if settings_result.data:
            settings_id = settings_result.data[0]["id"]
        else:
            # Create default settings
            new_settings = supabase.table("budget_alert_settings") \
                .insert({"project_id": project_id, "created_by": current_user.get("user_id")}) \
                .execute()
            settings_id = new_settings.data[0]["id"] if new_settings.data else None

        # Add recipient
        recipient_data = recipient.dict()
        recipient_data["settings_id"] = settings_id

        result = supabase.table("budget_alert_recipients") \
            .insert(recipient_data) \
            .execute()

        return {"message": "Recipient added", "data": result.data[0] if result.data else recipient_data}

    except Exception as e:
        if "duplicate" in str(e).lower():
            raise HTTPException(status_code=400, detail="User is already a recipient")
        raise HTTPException(status_code=500, detail=f"Error adding recipient: {str(e)}")


@router.put("/recipients/{recipient_id}")
async def update_alert_recipient(
    recipient_id: str,
    update: AlertRecipientUpdate,
    current_user: dict = Depends(get_current_user)
):
    """
    Update a recipient's notification preferences.
    """
    try:
        update_data = {k: v for k, v in update.dict().items() if v is not None}

        result = supabase.table("budget_alert_recipients") \
            .update(update_data) \
            .eq("id", recipient_id) \
            .execute()

        if not result.data:
            raise HTTPException(status_code=404, detail="Recipient not found")

        return {"message": "Recipient updated", "data": result.data[0]}

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error updating recipient: {str(e)}")


@router.delete("/recipients/{recipient_id}")
async def remove_alert_recipient(
    recipient_id: str,
    current_user: dict = Depends(get_current_user)
):
    """
    Remove a user from budget alert recipients.
    """
    try:
        result = supabase.table("budget_alert_recipients") \
            .delete() \
            .eq("id", recipient_id) \
            .execute()

        return {"message": "Recipient removed"}

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error removing recipient: {str(e)}")


# ================================
# ALERTS LOG ENDPOINTS
# ================================

@router.get("/history")
async def get_alert_history(
    project_id: Optional[str] = None,
    alert_type: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    current_user: dict = Depends(get_current_user)
):
    """
    Get history of sent alerts.
    """
    try:
        query = supabase.table("budget_alerts_log") \
            .select("*, projects(project_name)") \
            .order("created_at", desc=True) \
            .range(offset, offset + limit - 1)

        if project_id:
            query = query.eq("project_id", project_id)

        if alert_type:
            query = query.eq("alert_type", alert_type)

        result = query.execute()

        return {"data": result.data or [], "total": len(result.data or [])}

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching alert history: {str(e)}")


@router.put("/history/{alert_id}/read")
async def mark_alert_read(
    alert_id: str,
    current_user: dict = Depends(get_current_user)
):
    """
    Mark an alert as read.
    """
    try:
        result = supabase.table("budget_alerts_log") \
            .update({
                "is_read": True,
                "read_at": datetime.utcnow().isoformat(),
                "read_by": current_user.get("user_id")
            }) \
            .eq("id", alert_id) \
            .execute()

        return {"message": "Alert marked as read"}

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error marking alert as read: {str(e)}")


# ================================
# DASHBOARD NOTIFICATIONS
# ================================

@router.get("/notifications")
async def get_dashboard_notifications(
    unread_only: bool = True,
    limit: int = 20,
    current_user: dict = Depends(get_current_user)
):
    """
    Get dashboard notifications for the current user.
    """
    try:
        user_id = current_user.get("user_id")

        query = supabase.table("dashboard_notifications") \
            .select("*") \
            .eq("user_id", user_id) \
            .order("created_at", desc=True) \
            .limit(limit)

        if unread_only:
            query = query.eq("is_read", False)

        result = query.execute()

        return {"data": result.data or []}

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching notifications: {str(e)}")


@router.put("/notifications/{notification_id}/read")
async def mark_notification_read(
    notification_id: str,
    current_user: dict = Depends(get_current_user)
):
    """
    Mark a dashboard notification as read.
    """
    try:
        result = supabase.table("dashboard_notifications") \
            .update({
                "is_read": True,
                "read_at": datetime.utcnow().isoformat()
            }) \
            .eq("id", notification_id) \
            .eq("user_id", current_user.get("user_id")) \
            .execute()

        return {"message": "Notification marked as read"}

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error marking notification as read: {str(e)}")


@router.put("/notifications/read-all")
async def mark_all_notifications_read(
    current_user: dict = Depends(get_current_user)
):
    """
    Mark all notifications as read for the current user.
    """
    try:
        user_id = current_user.get("user_id")

        supabase.table("dashboard_notifications") \
            .update({
                "is_read": True,
                "read_at": datetime.utcnow().isoformat()
            }) \
            .eq("user_id", user_id) \
            .eq("is_read", False) \
            .execute()

        return {"message": "All notifications marked as read"}

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error marking notifications as read: {str(e)}")


# ================================
# MANUAL TRIGGER
# ================================

@router.post("/check")
async def trigger_budget_check(
    background_tasks: BackgroundTasks,
    project_id: Optional[str] = None,
    current_user: dict = Depends(get_current_user)
):
    """
    Manually trigger a budget check.
    Runs in background and returns immediately.
    """
    try:
        from api.services.budget_monitor import run_budget_check

        # Run check in background
        background_tasks.add_task(run_budget_check)

        return {
            "message": "Budget check started",
            "status": "running",
            "triggered_by": current_user.get("user_name")
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error triggering budget check: {str(e)}")


# ================================
# SUMMARY ENDPOINT
# ================================

@router.get("/summary")
async def get_budget_alert_summary(
    project_id: Optional[str] = None,
    current_user: dict = Depends(get_current_user)
):
    """
    Get a summary of budget alert status.
    """
    try:
        # Count unread alerts
        unread_query = supabase.table("budget_alerts_log") \
            .select("id", count="exact") \
            .eq("is_read", False)

        if project_id:
            unread_query = unread_query.eq("project_id", project_id)

        unread_result = unread_query.execute()

        # Count by type (last 7 days)
        from datetime import timedelta
        week_ago = (datetime.utcnow() - timedelta(days=7)).isoformat()

        recent_query = supabase.table("budget_alerts_log") \
            .select("alert_type") \
            .gte("created_at", week_ago)

        if project_id:
            recent_query = recent_query.eq("project_id", project_id)

        recent_result = recent_query.execute()

        # Count by type
        type_counts = {}
        for alert in (recent_result.data or []):
            alert_type = alert.get("alert_type", "unknown")
            type_counts[alert_type] = type_counts.get(alert_type, 0) + 1

        return {
            "unread_count": len(unread_result.data or []),
            "alerts_last_7_days": len(recent_result.data or []),
            "by_type": type_counts
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error getting summary: {str(e)}")


# ================================
# ACKNOWLEDGMENT ENDPOINTS
# ================================

@router.get("/pending")
async def get_pending_acknowledgments(
    project_id: Optional[str] = None,
    current_user: dict = Depends(get_current_user)
):
    """
    Get alerts pending acknowledgment.
    These are overspend/no_budget alerts that require review.
    """
    try:
        query = supabase.table("budget_alerts_log") \
            .select("*, projects(project_name)") \
            .eq("requires_acknowledgment", True) \
            .eq("status", "pending") \
            .order("created_at", desc=False)

        if project_id:
            query = query.eq("project_id", project_id)

        result = query.execute()

        # Add days pending calculation
        alerts = result.data or []
        for alert in alerts:
            created = datetime.fromisoformat(alert["created_at"].replace("Z", "+00:00"))
            days_pending = (datetime.now(created.tzinfo) - created).days
            alert["days_pending"] = days_pending

        return {"data": alerts, "count": len(alerts)}

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching pending acknowledgments: {str(e)}")


@router.get("/pending/count")
async def get_pending_acknowledgment_count(
    project_id: Optional[str] = None,
    current_user: dict = Depends(get_current_user)
):
    """
    Get count of alerts pending acknowledgment.
    Used for badge display in UI.
    """
    try:
        query = supabase.table("budget_alerts_log") \
            .select("id", count="exact") \
            .eq("requires_acknowledgment", True) \
            .eq("status", "pending")

        if project_id:
            query = query.eq("project_id", project_id)

        result = query.execute()

        return {"count": len(result.data or [])}

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error counting pending: {str(e)}")


@router.put("/acknowledge/{alert_id}")
async def acknowledge_alert(
    alert_id: str,
    request: AcknowledgeAlertRequest,
    current_user: dict = Depends(get_current_user)
):
    """
    Acknowledge a budget alert with a note.

    Actions:
    - acknowledged: Accept the overspend with justification
    - dismissed: Dismiss as false positive
    - resolved: Mark as resolved (budget adjusted or expense corrected)
    """
    try:
        user_id = current_user.get("user_id")
        user_name = current_user.get("user_name", "Unknown")

        # Validate action
        valid_actions = ["acknowledged", "dismissed", "resolved"]
        if request.action not in valid_actions:
            raise HTTPException(status_code=400, detail=f"Invalid action. Must be one of: {valid_actions}")

        # Validate note is provided for acknowledgment
        if request.action == "acknowledged" and not request.note.strip():
            raise HTTPException(status_code=400, detail="A justification note is required for acknowledgment")

        # Get current alert
        alert_result = supabase.table("budget_alerts_log") \
            .select("*") \
            .eq("id", alert_id) \
            .single() \
            .execute()

        if not alert_result.data:
            raise HTTPException(status_code=404, detail="Alert not found")

        alert = alert_result.data
        previous_status = alert.get("status", "pending")

        # Check if already processed
        if previous_status != "pending":
            raise HTTPException(
                status_code=400,
                detail=f"Alert already {previous_status}. Cannot modify."
            )

        # Update the alert
        update_data = {
            "status": request.action,
            "acknowledged_at": datetime.utcnow().isoformat(),
            "acknowledged_by": user_id,
            "acknowledgment_note": request.note,
        }

        supabase.table("budget_alerts_log") \
            .update(update_data) \
            .eq("id", alert_id) \
            .execute()

        # Log the action
        action_log = {
            "alert_id": alert_id,
            "user_id": user_id,
            "action": request.action,
            "note": request.note,
            "previous_status": previous_status,
            "new_status": request.action,
        }

        supabase.table("budget_alert_actions").insert(action_log).execute()

        # Build response message
        action_messages = {
            "acknowledged": f"Alert acknowledged by {user_name}",
            "dismissed": f"Alert dismissed by {user_name}",
            "resolved": f"Alert marked as resolved by {user_name}",
        }

        return {
            "message": action_messages.get(request.action, "Alert updated"),
            "alert_id": alert_id,
            "new_status": request.action,
            "acknowledged_by": user_name,
            "note": request.note,
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error acknowledging alert: {str(e)}")


@router.get("/acknowledge/{alert_id}/history")
async def get_alert_action_history(
    alert_id: str,
    current_user: dict = Depends(get_current_user)
):
    """
    Get the action history for a specific alert.
    Shows who viewed, acknowledged, or modified it.
    """
    try:
        result = supabase.table("budget_alert_actions") \
            .select("*, users(user_name, avatar_color)") \
            .eq("alert_id", alert_id) \
            .order("created_at", desc=True) \
            .execute()

        return {"data": result.data or []}

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching action history: {str(e)}")


@router.post("/acknowledge/{alert_id}/reopen")
async def reopen_alert(
    alert_id: str,
    note: Optional[str] = None,
    current_user: dict = Depends(get_current_user)
):
    """
    Reopen a previously acknowledged/resolved alert.
    Useful if the issue recurs or was incorrectly closed.
    """
    try:
        user_id = current_user.get("user_id")

        # Get current alert
        alert_result = supabase.table("budget_alerts_log") \
            .select("status") \
            .eq("id", alert_id) \
            .single() \
            .execute()

        if not alert_result.data:
            raise HTTPException(status_code=404, detail="Alert not found")

        previous_status = alert_result.data.get("status")

        if previous_status == "pending":
            raise HTTPException(status_code=400, detail="Alert is already pending")

        # Reopen the alert
        supabase.table("budget_alerts_log") \
            .update({
                "status": "pending",
                "acknowledged_at": None,
                "acknowledged_by": None,
                "acknowledgment_note": None,
            }) \
            .eq("id", alert_id) \
            .execute()

        # Log the action
        action_log = {
            "alert_id": alert_id,
            "user_id": user_id,
            "action": "reopened",
            "note": note or "Alert reopened for review",
            "previous_status": previous_status,
            "new_status": "pending",
        }

        supabase.table("budget_alert_actions").insert(action_log).execute()

        return {"message": "Alert reopened", "alert_id": alert_id}

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error reopening alert: {str(e)}")


# ================================
# PERMISSIONS ENDPOINTS
# ================================

@router.get("/permissions")
async def get_acknowledgment_permissions(
    current_user: dict = Depends(get_current_user)
):
    """
    Get acknowledgment permissions for the current user.
    Returns what types of alerts they can acknowledge.
    """
    try:
        user_id = current_user.get("user_id")
        user_role = current_user.get("rol_id") or current_user.get("user_rol")

        # Check user-specific permissions
        user_perms = supabase.table("budget_alert_permissions") \
            .select("*") \
            .eq("user_id", user_id) \
            .execute()

        # Check role-based permissions
        role_perms = []
        if user_role:
            role_result = supabase.table("budget_alert_permissions") \
                .select("*") \
                .eq("role_id", user_role) \
                .execute()
            role_perms = role_result.data or []

        # Merge permissions (user-specific override role-based)
        all_perms = (user_perms.data or []) + role_perms

        # Build permission summary
        can_acknowledge = any(p.get("can_acknowledge", False) for p in all_perms)
        can_dismiss = any(p.get("can_dismiss", False) for p in all_perms)
        can_resolve = any(p.get("can_resolve", False) for p in all_perms)

        # Get allowed alert types
        allowed_types = set()
        for p in all_perms:
            if p.get("can_acknowledge"):
                alert_type = p.get("alert_type", "all")
                if alert_type == "all":
                    allowed_types = {"warning", "critical", "overspend", "no_budget"}
                    break
                else:
                    allowed_types.add(alert_type)

        return {
            "can_acknowledge": can_acknowledge,
            "can_dismiss": can_dismiss,
            "can_resolve": can_resolve,
            "allowed_alert_types": list(allowed_types),
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching permissions: {str(e)}")
