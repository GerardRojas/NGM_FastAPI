# ============================================================================
# NGM Hub - Receipt Monitor Service
# ============================================================================
# Background service that checks for stale receipts (waiting for user
# response) and sends reminders to the bookkeeping team via Andrew.
#
# Run modes:
# 1. As a scheduled job (cron): python -m api.services.receipt_monitor
# 2. Called from API endpoint: await check_stale_receipts()
# ============================================================================

import logging
import asyncio
from typing import Dict, Any, List
from datetime import datetime, timedelta, timezone
import os
import json

from supabase import create_client, Client

from api.helpers.andrew_messenger import post_andrew_message

logger = logging.getLogger(__name__)

# ============================================================================
# Configuration
# ============================================================================

# Statuses that indicate a receipt is waiting for user action
STALE_STATUSES = ("ready", "check_review", "duplicate")


def get_supabase() -> Client:
    """Get Supabase client."""
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_KEY") or os.getenv("SUPABASE_KEY")
    return create_client(url, key)


def _get_agent_config(supabase: Client) -> Dict[str, Any]:
    """Fetch agent_config key-value pairs."""
    try:
        result = supabase.table("agent_config").select("key, value").execute()
        return {row["key"]: row["value"] for row in (result.data or [])}
    except Exception as e:
        logger.warning(f"[ReceiptMonitor] Failed to fetch agent_config: {e}")
        return {}


def _get_bookkeeping_mentions(supabase: Client) -> str:
    """Get @mention strings for all Bookkeeper/Accounting Manager users."""
    try:
        result = supabase.table("users") \
            .select("user_name, rols!users_user_rol_fkey(rol_name)") \
            .execute()
        bookkeeping_roles = {"Bookkeeper", "Accounting Manager"}
        mentions = []
        for u in (result.data or []):
            role = u.get("rols") or {}
            if role.get("rol_name") in bookkeeping_roles:
                name = (u.get("user_name") or "").replace(" ", "")
                if name:
                    mentions.append(f"@{name}")
        return " ".join(mentions) if mentions else ""
    except Exception as e:
        logger.warning(f"[ReceiptMonitor] Error fetching bookkeeping mentions: {e}")
        return ""


# ============================================================================
# Core Logic
# ============================================================================

async def check_stale_receipts() -> Dict[str, Any]:
    """
    Main function: find stale receipts and send reminders.
    Call periodically (e.g. every hour via cron).

    Returns summary of check results.
    """
    start_time = datetime.now(timezone.utc)
    logger.info("[ReceiptMonitor] Starting stale receipt check...")

    supabase = get_supabase()
    config = _get_agent_config(supabase)

    reminder_hours = float(config.get("receipt_reminder_hours", 4))
    max_reminders = int(config.get("receipt_max_reminders", 3))
    cutoff = (start_time - timedelta(hours=reminder_hours)).isoformat()

    logger.info(f"[ReceiptMonitor] Config: reminder_hours={reminder_hours}, max_reminders={max_reminders}")

    # Fetch receipts that have been waiting too long
    try:
        result = supabase.table("pending_receipts") \
            .select("id, project_id, vendor_name, amount, status, parsed_data, updated_at") \
            .in_("status", list(STALE_STATUSES)) \
            .lt("updated_at", cutoff) \
            .execute()
    except Exception as e:
        logger.error(f"[ReceiptMonitor] Query error: {e}")
        return {"status": "error", "error": str(e)}

    stale_receipts = result.data or []
    logger.info(f"[ReceiptMonitor] Found {len(stale_receipts)} stale receipt(s)")

    if not stale_receipts:
        return {"status": "ok", "stale_found": 0, "reminders_sent": 0}

    mentions = _get_bookkeeping_mentions(supabase)
    reminders_sent = 0

    for receipt in stale_receipts:
        receipt_id = receipt["id"]
        project_id = receipt.get("project_id")
        parsed_data = receipt.get("parsed_data") or {}

        # Check reminder count
        reminder_count = parsed_data.get("reminder_count", 0)
        if reminder_count >= max_reminders:
            logger.info(f"[ReceiptMonitor] Receipt {receipt_id}: max reminders reached ({reminder_count}), skipping")
            continue

        # Check last reminder time (don't spam)
        last_reminder = parsed_data.get("last_reminder_at")
        if last_reminder:
            try:
                last_dt = datetime.fromisoformat(last_reminder.replace("Z", "+00:00"))
                if start_time - last_dt < timedelta(hours=reminder_hours):
                    continue
            except (ValueError, TypeError):
                pass

        # Build and send reminder
        vendor = receipt.get("vendor_name") or "Unknown"
        amount = receipt.get("amount") or 0
        status = receipt.get("status", "unknown")
        updated_at = receipt.get("updated_at", "")

        # Calculate hours waiting
        hours_waiting = 0
        try:
            updated_dt = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
            hours_waiting = round((start_time - updated_dt).total_seconds() / 3600, 1)
        except (ValueError, TypeError):
            pass

        status_labels = {
            "ready": "awaiting project assignment",
            "check_review": "awaiting check confirmation",
            "duplicate": "awaiting duplicate confirmation",
        }
        status_label = status_labels.get(status, status)

        reminder_msg = (
            f"{mentions} -- Reminder: Receipt from **{vendor}** "
            f"(${amount:,.2f}) has been waiting for {hours_waiting}h. "
            f"Status: {status_label}."
        )

        try:
            post_andrew_message(
                content=reminder_msg,
                project_id=project_id,
                metadata={
                    "agent_message": True,
                    "pending_receipt_id": receipt_id,
                    "reminder": True,
                    "reminder_count": reminder_count + 1,
                }
            )

            # Update receipt with reminder tracking
            parsed_data["last_reminder_at"] = start_time.isoformat()
            parsed_data["reminder_count"] = reminder_count + 1
            supabase.table("pending_receipts").update({
                "parsed_data": parsed_data,
            }).eq("id", receipt_id).execute()

            reminders_sent += 1
            logger.info(f"[ReceiptMonitor] Reminder sent for receipt {receipt_id} (#{reminder_count + 1})")

        except Exception as e:
            logger.error(f"[ReceiptMonitor] Failed to send reminder for {receipt_id}: {e}")

    elapsed = (datetime.now(timezone.utc) - start_time).total_seconds()
    logger.info(f"[ReceiptMonitor] Done. {reminders_sent} reminder(s) sent in {elapsed:.2f}s")

    return {
        "status": "completed",
        "stale_found": len(stale_receipts),
        "reminders_sent": reminders_sent,
        "elapsed_seconds": elapsed,
        "timestamp": start_time.isoformat(),
    }


# ============================================================================
# CLI Entry Point
# ============================================================================

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    result = asyncio.run(check_stale_receipts())
    print(f"Receipt Monitor Result: {json.dumps(result, indent=2)}")
