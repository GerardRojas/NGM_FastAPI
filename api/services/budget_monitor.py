# ============================================================================
# NGM Hub - Budget Monitoring Service
# ============================================================================
# Background service that monitors budget vs actuals and sends alerts
# when thresholds are exceeded or expenses are made without budget.
#
# Run modes:
# 1. As a scheduled job (cron): python -m api.services.budget_monitor
# 2. Called from API endpoint: await run_budget_check()
# ============================================================================

import logging
import asyncio
from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta
from decimal import Decimal
import os

from supabase import create_client, Client

logger = logging.getLogger(__name__)

# ============================================================================
# Configuration
# ============================================================================

def get_supabase() -> Client:
    """Get Supabase client."""
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_KEY") or os.getenv("SUPABASE_KEY")
    return create_client(url, key)


# ============================================================================
# Data Fetching
# ============================================================================

async def get_alert_settings(project_id: Optional[str] = None) -> Dict[str, Any]:
    """
    Get alert settings for a project or global defaults.
    Project-specific settings override global settings.
    """
    try:
        supabase = get_supabase()

        # Try project-specific settings first
        if project_id:
            result = supabase.table("budget_alert_settings") \
                .select("*") \
                .eq("project_id", project_id) \
                .single() \
                .execute()
            if result.data:
                return result.data

        # Fall back to global settings
        result = supabase.table("budget_alert_settings") \
            .select("*") \
            .is_("project_id", "null") \
            .single() \
            .execute()

        if result.data:
            return result.data

        # Default settings if nothing in database
        return {
            "warning_threshold": 80,
            "critical_threshold": 95,
            "overspend_alert": True,
            "no_budget_alert": True,
            "is_enabled": True,
            "check_frequency_minutes": 60,
            "quiet_start_hour": 22,
            "quiet_end_hour": 7,
        }

    except Exception as e:
        logger.error(f"[BudgetMonitor] Error getting settings: {e}")
        return {
            "warning_threshold": 80,
            "critical_threshold": 95,
            "overspend_alert": True,
            "no_budget_alert": True,
            "is_enabled": True,
        }


async def get_alert_recipients(settings_id: Optional[str] = None) -> List[Dict]:
    """Get users who should receive budget alerts."""
    try:
        supabase = get_supabase()

        query = supabase.table("budget_alert_recipients") \
            .select("*, users(user_id, user_name, user_email)")

        if settings_id:
            query = query.eq("settings_id", settings_id)
        else:
            query = query.is_("settings_id", "null")

        result = query.execute()
        return result.data or []

    except Exception as e:
        logger.error(f"[BudgetMonitor] Error getting recipients: {e}")
        return []


async def get_active_projects() -> List[Dict]:
    """Get all active projects."""
    try:
        supabase = get_supabase()
        result = supabase.table("projects") \
            .select("project_id, project_name") \
            .execute()
        return result.data or []
    except Exception as e:
        logger.error(f"[BudgetMonitor] Error getting projects: {e}")
        return []


async def get_project_budgets(project_id: str) -> Dict[str, float]:
    """
    Get budgets by account name for a project.
    Returns: {account_name: budget_amount}
    """
    try:
        supabase = get_supabase()
        result = supabase.table("budgets_qbo") \
            .select("account_name, amount_sum") \
            .eq("ngm_project_id", project_id) \
            .eq("active", True) \
            .execute()

        budgets = {}
        for row in (result.data or []):
            account = row.get("account_name", "Unknown")
            amount = float(row.get("amount_sum") or 0)
            budgets[account] = budgets.get(account, 0) + amount

        return budgets

    except Exception as e:
        logger.error(f"[BudgetMonitor] Error getting budgets for {project_id}: {e}")
        return {}


async def get_project_actuals(project_id: str) -> Dict[str, float]:
    """
    Get authorized expenses by account name for a project.
    Returns: {account_name: actual_amount}
    """
    try:
        supabase = get_supabase()
        result = supabase.table("expenses") \
            .select("account_name, Amount, amount") \
            .eq("project", project_id) \
            .eq("auth_status", True) \
            .execute()

        actuals = {}
        for row in (result.data or []):
            account = row.get("account_name", "Unknown")
            amount = float(row.get("Amount") or row.get("amount") or 0)
            actuals[account] = actuals.get(account, 0) + amount

        return actuals

    except Exception as e:
        logger.error(f"[BudgetMonitor] Error getting actuals for {project_id}: {e}")
        return {}


# ============================================================================
# Alert Detection
# ============================================================================

def detect_budget_alerts(
    project_id: str,
    project_name: str,
    budgets: Dict[str, float],
    actuals: Dict[str, float],
    settings: Dict[str, Any]
) -> List[Dict]:
    """
    Compare budgets vs actuals and generate alerts.

    Returns list of alerts:
    [{
        project_id, project_name, account_name,
        alert_type, budget_amount, actual_amount, percentage,
        title, message
    }]
    """
    alerts = []
    today = datetime.now().strftime("%Y-%m-%d")

    warning_threshold = settings.get("warning_threshold", 80)
    critical_threshold = settings.get("critical_threshold", 95)
    overspend_alert = settings.get("overspend_alert", True)
    no_budget_alert = settings.get("no_budget_alert", True)

    # Check each account with budget
    for account, budget in budgets.items():
        if budget <= 0:
            continue

        actual = actuals.get(account, 0)
        percentage = (actual / budget * 100) if budget > 0 else 0

        # Generate dedup key to prevent duplicate alerts on same day
        dedup_base = f"{project_id}:{account}:{today}"

        # Check thresholds (only the highest applicable)
        if percentage >= 100 and overspend_alert:
            over_amount = actual - budget
            alerts.append({
                "project_id": project_id,
                "project_name": project_name,
                "account_name": account,
                "alert_type": "overspend",
                "budget_amount": budget,
                "actual_amount": actual,
                "percentage": percentage,
                "title": f"Presupuesto Excedido: {account}",
                "message": f"El proyecto {project_name} ha excedido el presupuesto de {account} por ${over_amount:,.2f} ({percentage:.1f}% usado).",
                "dedup_key": f"{dedup_base}:overspend",
                "requires_acknowledgment": True,
                "status": "pending",
            })

        elif percentage >= critical_threshold:
            alerts.append({
                "project_id": project_id,
                "project_name": project_name,
                "account_name": account,
                "alert_type": "critical",
                "budget_amount": budget,
                "actual_amount": actual,
                "percentage": percentage,
                "title": f"Alerta Critica: {account}",
                "message": f"El proyecto {project_name} ha usado {percentage:.1f}% del presupuesto de {account}. Quedan ${budget - actual:,.2f}.",
                "dedup_key": f"{dedup_base}:critical",
            })

        elif percentage >= warning_threshold:
            alerts.append({
                "project_id": project_id,
                "project_name": project_name,
                "account_name": account,
                "alert_type": "warning",
                "budget_amount": budget,
                "actual_amount": actual,
                "percentage": percentage,
                "title": f"Advertencia: {account}",
                "message": f"El proyecto {project_name} se acerca al limite de {account} ({percentage:.1f}% usado). Quedan ${budget - actual:,.2f}.",
                "dedup_key": f"{dedup_base}:warning",
            })

    # Check for expenses without budget
    if no_budget_alert:
        for account, actual in actuals.items():
            if account not in budgets and actual > 0:
                alerts.append({
                    "project_id": project_id,
                    "project_name": project_name,
                    "account_name": account,
                    "alert_type": "no_budget",
                    "budget_amount": 0,
                    "actual_amount": actual,
                    "percentage": 0,
                    "title": f"Gasto sin Presupuesto: {account}",
                    "message": f"Se han registrado ${actual:,.2f} en {account} para {project_name}, pero no hay presupuesto asignado.",
                    "dedup_key": f"{project_id}:{account}:{today}:no_budget",
                    "requires_acknowledgment": True,
                    "status": "pending",
                })

    return alerts


# ============================================================================
# Alert Storage & Notification
# ============================================================================

async def is_alert_already_sent(dedup_key: str) -> bool:
    """Check if this alert was already sent (avoid duplicates)."""
    try:
        supabase = get_supabase()
        result = supabase.table("budget_alerts_log") \
            .select("id") \
            .eq("dedup_key", dedup_key) \
            .execute()
        return len(result.data or []) > 0
    except Exception as e:
        logger.error(f"[BudgetMonitor] Error checking dedup: {e}")
        return False


async def save_alert_log(alert: Dict, recipients: List[str]) -> Optional[str]:
    """Save alert to log and return the log ID."""
    try:
        supabase = get_supabase()

        log_entry = {
            "project_id": alert["project_id"],
            "account_name": alert["account_name"],
            "alert_type": alert["alert_type"],
            "budget_amount": alert["budget_amount"],
            "actual_amount": alert["actual_amount"],
            "percentage_used": alert["percentage"],
            "title": alert["title"],
            "message": alert["message"],
            "dedup_key": alert["dedup_key"],
            "recipients_notified": recipients,
            "notification_channels": {"push": True, "dashboard": True},
            # Acknowledgment workflow fields (for overspend and no_budget alerts)
            "requires_acknowledgment": alert.get("requires_acknowledgment", False),
            "status": alert.get("status", "pending" if alert.get("requires_acknowledgment") else None),
        }

        result = supabase.table("budget_alerts_log").insert(log_entry).execute()

        if result.data:
            return result.data[0].get("id")
        return None

    except Exception as e:
        # May fail on duplicate dedup_key - that's OK
        if "duplicate" not in str(e).lower():
            logger.error(f"[BudgetMonitor] Error saving alert log: {e}")
        return None


async def create_dashboard_notification(
    user_id: str,
    alert: Dict,
    alert_log_id: Optional[str] = None
):
    """Create an in-app notification for the user's dashboard."""
    try:
        supabase = get_supabase()

        # Determine icon and color based on alert type
        icon_map = {
            "warning": "alert-triangle",
            "critical": "alert-circle",
            "overspend": "trending-up",
            "no_budget": "help-circle",
        }
        color_map = {
            "warning": "warning",
            "critical": "danger",
            "overspend": "danger",
            "no_budget": "info",
        }

        notification = {
            "user_id": user_id,
            "title": alert["title"],
            "message": alert["message"],
            "icon": icon_map.get(alert["alert_type"], "bell"),
            "color": color_map.get(alert["alert_type"], "warning"),
            "action_url": f"/expenses.html?project={alert['project_id']}",
            "action_data": {
                "project_id": alert["project_id"],
                "account_name": alert["account_name"],
                "alert_type": alert["alert_type"],
            },
            "source_type": "budget_alert",
            "source_id": alert_log_id,
        }

        supabase.table("dashboard_notifications").insert(notification).execute()

    except Exception as e:
        logger.error(f"[BudgetMonitor] Error creating dashboard notification: {e}")


async def send_push_notification_for_alert(user_id: str, alert: Dict):
    """Send Firebase push notification for budget alert."""
    try:
        from api.services.firebase_notifications import send_push_notification

        # Emoji based on alert type
        emoji_map = {
            "warning": "⚠️",
            "critical": "🚨",
            "overspend": "💸",
            "no_budget": "❓",
        }
        emoji = emoji_map.get(alert["alert_type"], "📊")

        await send_push_notification(
            user_id=user_id,
            title=f"{emoji} {alert['title']}",
            body=alert["message"][:200],
            data={
                "type": "budget_alert",
                "alert_type": alert["alert_type"],
                "project_id": alert["project_id"],
                "url": f"/expenses.html?project={alert['project_id']}",
            }
        )

    except Exception as e:
        logger.error(f"[BudgetMonitor] Error sending push notification: {e}")


async def notify_recipients(alert: Dict, recipients: List[Dict]):
    """Send notifications to all recipients based on their preferences."""
    notified_user_ids = []

    for recipient in recipients:
        user_id = recipient.get("user_id")
        if not user_id:
            continue

        # Check if recipient wants this type of alert
        alert_type = alert["alert_type"]
        type_map = {
            "warning": "receive_warning",
            "critical": "receive_critical",
            "overspend": "receive_overspend",
            "no_budget": "receive_no_budget",
        }
        pref_key = type_map.get(alert_type, "receive_warning")

        if not recipient.get(pref_key, True):
            continue

        notified_user_ids.append(user_id)

        # Send push notification if enabled
        if recipient.get("notify_push", True):
            await send_push_notification_for_alert(user_id, alert)

        # Create dashboard notification if enabled
        if recipient.get("notify_dashboard", True):
            await create_dashboard_notification(user_id, alert)

    return notified_user_ids


# ============================================================================
# Main Check Function
# ============================================================================

async def check_project_budgets(
    project_id: str,
    project_name: str,
    settings: Dict[str, Any],
    recipients: List[Dict]
) -> int:
    """
    Check budgets for a single project and send alerts.
    Returns number of alerts sent.
    """
    # Get budget and actual data
    budgets = await get_project_budgets(project_id)
    actuals = await get_project_actuals(project_id)

    if not budgets and not actuals:
        return 0

    # Detect alerts
    alerts = detect_budget_alerts(
        project_id=project_id,
        project_name=project_name,
        budgets=budgets,
        actuals=actuals,
        settings=settings
    )

    alerts_sent = 0

    for alert in alerts:
        # Check if already sent today
        if await is_alert_already_sent(alert["dedup_key"]):
            continue

        # Notify recipients
        notified = await notify_recipients(alert, recipients)

        if notified:
            # Save to log
            await save_alert_log(alert, notified)
            alerts_sent += 1
            logger.info(f"[BudgetMonitor] Alert sent: {alert['title']} to {len(notified)} users")

    return alerts_sent


def is_quiet_hours(settings: Dict[str, Any]) -> bool:
    """Check if current time is within quiet hours."""
    current_hour = datetime.now().hour
    quiet_start = settings.get("quiet_start_hour", 22)
    quiet_end = settings.get("quiet_end_hour", 7)

    if quiet_start > quiet_end:
        # Overnight quiet hours (e.g., 22:00 - 07:00)
        return current_hour >= quiet_start or current_hour < quiet_end
    else:
        # Same-day quiet hours
        return quiet_start <= current_hour < quiet_end


async def run_budget_check() -> Dict[str, Any]:
    """
    Main function to run budget monitoring check.
    Call this periodically (e.g., via cron or scheduled task).

    Returns summary of check results.
    """
    start_time = datetime.now()
    logger.info("[BudgetMonitor] Starting budget check...")

    # Get global settings
    settings = await get_alert_settings()

    # Check if alerts are enabled
    if not settings.get("is_enabled", True):
        logger.info("[BudgetMonitor] Alerts are disabled globally")
        return {"status": "disabled", "alerts_sent": 0}

    # Check quiet hours
    if is_quiet_hours(settings):
        logger.info("[BudgetMonitor] Currently in quiet hours, skipping notifications")
        return {"status": "quiet_hours", "alerts_sent": 0}

    # Get recipients
    recipients = await get_alert_recipients(settings.get("id"))

    if not recipients:
        logger.warning("[BudgetMonitor] No alert recipients configured")
        return {"status": "no_recipients", "alerts_sent": 0}

    # Get all projects
    projects = await get_active_projects()
    logger.info(f"[BudgetMonitor] Checking {len(projects)} projects...")

    total_alerts = 0

    for project in projects:
        project_id = project.get("project_id")
        project_name = project.get("project_name", "Unknown")

        # Check for project-specific settings override
        project_settings = await get_alert_settings(project_id)
        if not project_settings.get("is_enabled", True):
            continue

        # Check for project-specific recipients, fall back to global
        project_recipients = await get_alert_recipients(project_settings.get("id"))
        if not project_recipients:
            project_recipients = recipients

        alerts = await check_project_budgets(
            project_id=project_id,
            project_name=project_name,
            settings=project_settings,
            recipients=project_recipients
        )

        total_alerts += alerts

    elapsed = (datetime.now() - start_time).total_seconds()
    logger.info(f"[BudgetMonitor] Check complete. {total_alerts} alerts sent in {elapsed:.2f}s")

    return {
        "status": "completed",
        "projects_checked": len(projects),
        "alerts_sent": total_alerts,
        "elapsed_seconds": elapsed,
        "timestamp": start_time.isoformat(),
    }


# ============================================================================
# CLI Entry Point
# ============================================================================

if __name__ == "__main__":
    import sys

    # Load environment variables
    from dotenv import load_dotenv
    load_dotenv()

    # Configure logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )

    # Run the check
    result = asyncio.run(run_budget_check())
    print(f"\nBudget Check Result: {result}")

    sys.exit(0 if result.get("status") == "completed" else 1)
