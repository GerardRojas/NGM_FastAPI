# api/services/hari_coordinator.py
# ================================
# Hari Coordinator Service
# ================================
# Core task management: CRUD, user resolution, deadline parsing,
# follow-up engine, and weekly summary generation.

import logging
import json
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional, List
from uuid import uuid4

from api.supabase_client import supabase

logger = logging.getLogger("hari.coordinator")

HARI_BOT_USER_ID = "00000000-0000-0000-0000-000000000004"

# Default configuration keys and values
HARI_DEFAULTS = {
    "hari_coordinator_enabled": False,
    "hari_default_follow_up_hours": 2,
    "hari_escalation_interval_hours": 4,
    "hari_max_escalations": 3,
    "hari_stale_task_hours": 24,
    "hari_instructor_roles": '["CEO","COO","Coordinator","PM"]',
    "hari_viewer_roles": '["CEO","COO","Coordinator","PM","Bookkeeper"]',
    "hari_auto_confirm_users": "[]",
    "hari_notify_assignee_on_create": True,
    "hari_notify_channel": True,
}


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

def load_hari_config() -> Dict[str, Any]:
    """Load all hari_* configuration from agent_config table."""
    try:
        result = supabase.table("agent_config") \
            .select("key, value") \
            .like("key", "hari_%") \
            .execute()

        config = dict(HARI_DEFAULTS)
        for row in (result.data or []):
            key = row["key"]
            val = row["value"]
            # Parse JSON-stored values
            if val in ("true", "True"):
                config[key] = True
            elif val in ("false", "False"):
                config[key] = False
            else:
                try:
                    config[key] = json.loads(val)
                except (json.JSONDecodeError, TypeError):
                    config[key] = val
        return config
    except Exception as e:
        logger.error("[Hari] Config load error: %s", e)
        return dict(HARI_DEFAULTS)


def save_hari_config(key: str, value: Any) -> bool:
    """Save a single hari_* configuration key."""
    try:
        json_val = value if isinstance(value, str) else json.dumps(value)
        now = datetime.now(timezone.utc).isoformat()

        existing = supabase.table("agent_config") \
            .select("key") \
            .eq("key", key) \
            .execute()

        if existing.data:
            supabase.table("agent_config") \
                .update({"value": json_val, "updated_at": now}) \
                .eq("key", key) \
                .execute()
        else:
            supabase.table("agent_config") \
                .insert({"key": key, "value": json_val, "updated_at": now}) \
                .execute()
        return True
    except Exception as e:
        logger.error("[Hari] Config save error for %s: %s", key, e)
        return False


def is_hari_enabled() -> bool:
    """Check if Hari coordinator is enabled."""
    config = load_hari_config()
    return config.get("hari_coordinator_enabled", False)


# ---------------------------------------------------------------------------
# RBAC
# ---------------------------------------------------------------------------

def check_instructor_permission(user_id: str) -> Dict[str, Any]:
    """
    Check if a user has permission to give instructions to Hari.
    Returns {allowed: bool, role: str, reason: str}.
    """
    try:
        config = load_hari_config()
        allowed_roles = config.get("hari_instructor_roles", ["CEO", "COO", "Coordinator", "PM"])
        if isinstance(allowed_roles, str):
            allowed_roles = json.loads(allowed_roles)

        user_result = supabase.table("users") \
            .select("user_id, user_name, role") \
            .eq("user_id", user_id) \
            .execute()

        if not user_result.data:
            return {"allowed": False, "role": "unknown", "reason": "User not found"}

        user = user_result.data[0]
        user_role = user.get("role", "")

        if user_role in allowed_roles:
            return {"allowed": True, "role": user_role, "reason": ""}
        else:
            return {
                "allowed": False,
                "role": user_role,
                "reason": f"Role '{user_role}' is not authorized. Instructor roles: {', '.join(allowed_roles)}",
            }
    except Exception as e:
        logger.error("[Hari] RBAC check error: %s", e)
        return {"allowed": False, "role": "unknown", "reason": str(e)}


# ---------------------------------------------------------------------------
# User resolution
# ---------------------------------------------------------------------------

def resolve_user_by_name(name: str) -> Optional[Dict[str, Any]]:
    """
    Resolve a user by name (fuzzy match).
    Returns {user_id, user_name, role} or None.
    """
    if not name:
        return None

    try:
        # Try exact match first
        result = supabase.table("users") \
            .select("user_id, user_name, role") \
            .ilike("user_name", name) \
            .execute()

        if result.data:
            return result.data[0]

        # Try partial match
        result = supabase.table("users") \
            .select("user_id, user_name, role") \
            .ilike("user_name", f"%{name}%") \
            .execute()

        if result.data and len(result.data) == 1:
            return result.data[0]
        elif result.data and len(result.data) > 1:
            # Multiple matches - return best match (shortest name containing query)
            sorted_matches = sorted(result.data, key=lambda u: len(u["user_name"]))
            return sorted_matches[0]

        return None
    except Exception as e:
        logger.error("[Hari] User resolution error for '%s': %s", name, e)
        return None


# ---------------------------------------------------------------------------
# Deadline parsing
# ---------------------------------------------------------------------------

def parse_deadline(deadline_str: Optional[str]) -> Optional[str]:
    """
    Parse natural language deadline into ISO 8601 string.
    Uses GPT for complex expressions, simple parsing for common patterns.
    Returns ISO string or None.
    """
    if not deadline_str:
        return None

    now = datetime.now(timezone.utc)
    dl = deadline_str.lower().strip()

    # Common patterns
    if dl in ("today", "hoy"):
        return now.replace(hour=17, minute=0, second=0, microsecond=0).isoformat()
    if dl in ("tomorrow", "manana", "mañana"):
        return (now + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0).isoformat()
    if dl == "eod":
        return now.replace(hour=17, minute=0, second=0, microsecond=0).isoformat()

    # Day of week
    days_map = {
        "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
        "friday": 4, "saturday": 5, "sunday": 6,
    }
    for day_name, day_num in days_map.items():
        if day_name in dl:
            current_day = now.weekday()
            days_ahead = day_num - current_day
            if days_ahead <= 0:
                days_ahead += 7
            target = now + timedelta(days=days_ahead)
            # Try to extract time
            hour = 9  # default 9am
            if "pm" in dl or "afternoon" in dl:
                hour = 15
            if "eod" in dl or "end of day" in dl:
                hour = 17
            # Try extracting specific hour
            import re
            time_match = re.search(r"(\d{1,2})\s*(?::(\d{2}))?\s*(am|pm)", dl)
            if time_match:
                h = int(time_match.group(1))
                m = int(time_match.group(2) or 0)
                if time_match.group(3) == "pm" and h != 12:
                    h += 12
                if time_match.group(3) == "am" and h == 12:
                    h = 0
                hour = h
                target = target.replace(hour=hour, minute=m, second=0, microsecond=0)
            else:
                target = target.replace(hour=hour, minute=0, second=0, microsecond=0)
            return target.isoformat()

    # Try "tomorrow Xam/pm" pattern
    import re
    tomorrow_time = re.search(r"tomorrow\s+(?:at\s+)?(\d{1,2})\s*(?::(\d{2}))?\s*(am|pm)", dl)
    if tomorrow_time:
        h = int(tomorrow_time.group(1))
        m = int(tomorrow_time.group(2) or 0)
        if tomorrow_time.group(3) == "pm" and h != 12:
            h += 12
        target = (now + timedelta(days=1)).replace(hour=h, minute=m, second=0, microsecond=0)
        return target.isoformat()

    # Try ISO format directly
    try:
        parsed = datetime.fromisoformat(deadline_str.replace("Z", "+00:00"))
        return parsed.isoformat()
    except (ValueError, TypeError):
        pass

    # Fallback: return as-is and let it be stored as text in metadata
    logger.info("[Hari] Could not parse deadline '%s', storing as text", deadline_str)
    return None


# ---------------------------------------------------------------------------
# Task CRUD
# ---------------------------------------------------------------------------

def create_task(
    instruction_text: str,
    description: str,
    created_by: str,
    assigned_to: Optional[str],
    project_id: Optional[str],
    channel_key: str,
    deadline: Optional[str] = None,
    location: Optional[str] = None,
    metadata: Optional[Dict] = None,
) -> Dict[str, Any]:
    """
    Create a new coordinator task.
    Returns the created task record.
    """
    try:
        now = datetime.now(timezone.utc).isoformat()
        parsed_deadline = parse_deadline(deadline) if deadline else None

        # Calculate follow_up_at based on config
        follow_up_at = None
        if parsed_deadline:
            config = load_hari_config()
            follow_up_hours = config.get("hari_default_follow_up_hours", 2)
            try:
                dl_dt = datetime.fromisoformat(parsed_deadline)
                follow_up_at = (dl_dt + timedelta(hours=follow_up_hours)).isoformat()
            except (ValueError, TypeError):
                pass

        task_data = {
            "instruction_text": instruction_text,
            "description": description,
            "created_by": created_by,
            "assigned_to": assigned_to,
            "project_id": project_id,
            "channel_key": channel_key,
            "deadline": parsed_deadline,
            "follow_up_at": follow_up_at,
            "status": "pending_confirmation",
            "escalation_count": 0,
            "metadata": metadata or {},
            "created_at": now,
            "updated_at": now,
        }

        # Add location to metadata if provided
        if location:
            task_data["metadata"]["location"] = location

        result = supabase.table("coordinator_tasks") \
            .insert(task_data) \
            .execute()

        if result.data:
            logger.info("[Hari] Task created: %s | assigned=%s | deadline=%s",
                       result.data[0].get("id"), assigned_to, parsed_deadline)
            return result.data[0]

        return {"error": "Failed to create task - no data returned"}

    except Exception as e:
        logger.error("[Hari] Task creation error: %s", e)
        return {"error": str(e)}


def confirm_task(task_id: str) -> Dict[str, Any]:
    """Confirm a pending task, setting status to active."""
    try:
        now = datetime.now(timezone.utc).isoformat()
        result = supabase.table("coordinator_tasks") \
            .update({"status": "active", "updated_at": now}) \
            .eq("id", task_id) \
            .eq("status", "pending_confirmation") \
            .execute()

        if result.data:
            return result.data[0]
        return {"error": "Task not found or already confirmed"}
    except Exception as e:
        logger.error("[Hari] Task confirm error: %s", e)
        return {"error": str(e)}


def cancel_task(task_id: str) -> Dict[str, Any]:
    """Cancel a task."""
    try:
        now = datetime.now(timezone.utc).isoformat()
        result = supabase.table("coordinator_tasks") \
            .update({"status": "cancelled", "updated_at": now}) \
            .eq("id", task_id) \
            .execute()

        if result.data:
            return result.data[0]
        return {"error": "Task not found"}
    except Exception as e:
        logger.error("[Hari] Task cancel error: %s", e)
        return {"error": str(e)}


def complete_task(task_id: str, completed_by: str, notes: Optional[str] = None) -> Dict[str, Any]:
    """Mark a task as completed."""
    try:
        now = datetime.now(timezone.utc).isoformat()
        update = {
            "status": "completed",
            "completed_at": now,
            "completed_by": completed_by,
            "updated_at": now,
        }
        if notes:
            update["completion_notes"] = notes

        result = supabase.table("coordinator_tasks") \
            .update(update) \
            .eq("id", task_id) \
            .execute()

        if result.data:
            return result.data[0]
        return {"error": "Task not found"}
    except Exception as e:
        logger.error("[Hari] Task complete error: %s", e)
        return {"error": str(e)}


def get_tasks(
    project_id: Optional[str] = None,
    assigned_to: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 20,
) -> List[Dict[str, Any]]:
    """
    Query tasks with optional filters.
    Status can be: active, overdue, completed, blocked, cancelled, all.
    """
    try:
        query = supabase.table("coordinator_tasks") \
            .select("*") \
            .order("created_at", desc=True) \
            .limit(limit)

        if project_id:
            query = query.eq("project_id", project_id)
        if assigned_to:
            query = query.eq("assigned_to", assigned_to)

        if status and status != "all":
            if status == "overdue":
                # Overdue = active/in_progress tasks past deadline
                now = datetime.now(timezone.utc).isoformat()
                query = query.in_("status", ["active", "in_progress"]) \
                    .lt("deadline", now) \
                    .not_.is_("deadline", "null")
            else:
                query = query.eq("status", status)
        elif not status:
            # Default: show non-terminal tasks
            query = query.in_("status", ["pending_confirmation", "active", "in_progress", "overdue", "blocked"])

        result = query.execute()
        return result.data or []

    except Exception as e:
        logger.error("[Hari] Get tasks error: %s", e)
        return []


def update_task_field(task_id: str, updates: Dict[str, Any]) -> Dict[str, Any]:
    """Generic task update."""
    try:
        updates["updated_at"] = datetime.now(timezone.utc).isoformat()
        result = supabase.table("coordinator_tasks") \
            .update(updates) \
            .eq("id", task_id) \
            .execute()

        if result.data:
            return result.data[0]
        return {"error": "Task not found"}
    except Exception as e:
        logger.error("[Hari] Task update error: %s", e)
        return {"error": str(e)}


def find_task_by_description(description: str, project_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Find a task by fuzzy description match."""
    try:
        query = supabase.table("coordinator_tasks") \
            .select("*") \
            .ilike("description", f"%{description}%") \
            .in_("status", ["active", "in_progress", "pending_confirmation", "overdue", "blocked"]) \
            .order("created_at", desc=True) \
            .limit(1)

        if project_id:
            query = query.eq("project_id", project_id)

        result = query.execute()
        return result.data[0] if result.data else None
    except Exception as e:
        logger.error("[Hari] Task search error: %s", e)
        return None


# ---------------------------------------------------------------------------
# Stats for Agent Hub
# ---------------------------------------------------------------------------

def get_task_stats(days: int = 30, project_id: Optional[str] = None) -> Dict[str, Any]:
    """Get aggregate task statistics for the Agent Hub dashboard."""
    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

        query = supabase.table("coordinator_tasks") \
            .select("id, status, deadline, created_at, completed_at, escalation_count") \
            .gte("created_at", cutoff)

        if project_id:
            query = query.eq("project_id", project_id)

        result = query.execute()
        tasks = result.data or []

        now = datetime.now(timezone.utc)
        stats = {
            "total_created": len(tasks),
            "active": 0,
            "completed": 0,
            "overdue": 0,
            "blocked": 0,
            "cancelled": 0,
            "on_time_completions": 0,
            "total_completions": 0,
            "total_escalations": 0,
            "avg_completion_hours": None,
        }

        completion_hours = []

        for t in tasks:
            s = t.get("status", "")
            if s in ("active", "in_progress"):
                # Check if overdue
                dl = t.get("deadline")
                if dl:
                    try:
                        dl_dt = datetime.fromisoformat(dl.replace("Z", "+00:00"))
                        if dl_dt.tzinfo is None:
                            dl_dt = dl_dt.replace(tzinfo=timezone.utc)
                        if now > dl_dt:
                            stats["overdue"] += 1
                        else:
                            stats["active"] += 1
                    except (ValueError, TypeError):
                        stats["active"] += 1
                else:
                    stats["active"] += 1
            elif s == "completed":
                stats["completed"] += 1
                stats["total_completions"] += 1
                # Check if completed on time
                dl = t.get("deadline")
                ca = t.get("completed_at")
                if dl and ca:
                    try:
                        dl_dt = datetime.fromisoformat(dl.replace("Z", "+00:00"))
                        ca_dt = datetime.fromisoformat(ca.replace("Z", "+00:00"))
                        if ca_dt <= dl_dt:
                            stats["on_time_completions"] += 1
                        # Track completion time
                        created = t.get("created_at")
                        if created:
                            cr_dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
                            hours = (ca_dt - cr_dt).total_seconds() / 3600
                            completion_hours.append(hours)
                    except (ValueError, TypeError):
                        pass
            elif s == "blocked":
                stats["blocked"] += 1
            elif s == "cancelled":
                stats["cancelled"] += 1

            stats["total_escalations"] += t.get("escalation_count", 0)

        if completion_hours:
            stats["avg_completion_hours"] = round(sum(completion_hours) / len(completion_hours), 1)

        # Rates
        if stats["total_completions"] > 0:
            stats["on_time_rate"] = round(stats["on_time_completions"] / stats["total_completions"] * 100)
        else:
            stats["on_time_rate"] = None

        if stats["total_created"] > 0:
            stats["escalation_rate"] = round(
                len([t for t in tasks if t.get("escalation_count", 0) > 0]) / stats["total_created"] * 100
            )
            stats["cancellation_rate"] = round(stats["cancelled"] / stats["total_created"] * 100)
        else:
            stats["escalation_rate"] = None
            stats["cancellation_rate"] = None

        return stats

    except Exception as e:
        logger.error("[Hari] Stats error: %s", e)
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# Weekly Summary
# ---------------------------------------------------------------------------

def generate_weekly_summary(project_id: Optional[str] = None, days: int = 7) -> Dict[str, Any]:
    """Generate a task digest for the given period."""
    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

        query = supabase.table("coordinator_tasks") \
            .select("*, users!coordinator_tasks_assigned_to_fkey(user_name)") \
            .gte("created_at", cutoff)

        if project_id:
            query = query.eq("project_id", project_id)

        result = query.execute()
        tasks = result.data or []

        now = datetime.now(timezone.utc)
        created = len(tasks)
        completed = [t for t in tasks if t.get("status") == "completed"]
        overdue = []
        blocked = [t for t in tasks if t.get("status") == "blocked"]

        for t in tasks:
            if t.get("status") in ("active", "in_progress") and t.get("deadline"):
                try:
                    dl = datetime.fromisoformat(t["deadline"].replace("Z", "+00:00"))
                    if dl.tzinfo is None:
                        dl = dl.replace(tzinfo=timezone.utc)
                    if now > dl:
                        overdue.append(t)
                except (ValueError, TypeError):
                    pass

        summary = {
            "period_days": days,
            "tasks_created": created,
            "tasks_completed": len(completed),
            "tasks_overdue": len(overdue),
            "tasks_blocked": len(blocked),
            "overdue_details": [
                {
                    "description": t.get("description", ""),
                    "assignee": (t.get("users") or {}).get("user_name", "Unassigned"),
                    "deadline": t.get("deadline"),
                }
                for t in overdue[:10]
            ],
            "completed_details": [
                {
                    "description": t.get("description", ""),
                    "assignee": (t.get("users") or {}).get("user_name", "Unassigned"),
                    "completed_at": t.get("completed_at"),
                }
                for t in completed[:10]
            ],
        }

        return summary

    except Exception as e:
        logger.error("[Hari] Weekly summary error: %s", e)
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# Follow-Up Engine
# ---------------------------------------------------------------------------

def run_follow_up_check() -> Dict[str, Any]:
    """
    Background job: check for tasks needing follow-up.
    Returns a summary of actions taken.
    """
    try:
        config = load_hari_config()
        if not config.get("hari_coordinator_enabled", False):
            return {"status": "disabled"}

        now = datetime.now(timezone.utc)
        max_escalations = config.get("hari_max_escalations", 3)
        escalation_interval = config.get("hari_escalation_interval_hours", 4)

        # Find tasks needing follow-up
        result = supabase.table("coordinator_tasks") \
            .select("*") \
            .in_("status", ["active", "in_progress"]) \
            .not_.is_("follow_up_at", "null") \
            .lte("follow_up_at", now.isoformat()) \
            .execute()

        tasks = result.data or []
        actions = {"reminders_sent": 0, "escalations": 0, "blocked": 0}

        from api.helpers.hari_messenger import post_hari_message

        for task in tasks:
            esc_count = task.get("escalation_count", 0)
            task_id = task["id"]

            if esc_count >= max_escalations:
                # Mark as blocked
                update_task_field(task_id, {"status": "blocked"})
                # Notify instructor
                post_hari_message(
                    content=(
                        f"**Task Blocked** (max escalations reached)\n"
                        f"- **Task:** {task.get('description', '')}\n"
                        f"- **Assigned to:** {task.get('assigned_to', 'Unknown')}\n"
                        f"- **Deadline:** {task.get('deadline', 'None')}\n"
                        f"- Escalated {esc_count} times with no response."
                    ),
                    project_id=task.get("project_id"),
                    channel_type="project_general",
                    channel_id=task.get("channel_key") if task.get("channel_key", "").startswith("direct_") else None,
                    metadata={"task_update": True, "task_id": task_id, "update_type": "blocked"},
                )
                actions["blocked"] += 1
            else:
                # Send reminder/escalation
                next_follow_up = (now + timedelta(hours=escalation_interval)).isoformat()
                update_task_field(task_id, {
                    "escalation_count": esc_count + 1,
                    "last_escalated_at": now.isoformat(),
                    "follow_up_at": next_follow_up,
                })

                if esc_count == 0:
                    # First follow-up: remind assignee
                    msg = (
                        f"**Reminder:** {task.get('description', '')}\n"
                        f"- **Deadline:** {task.get('deadline', 'No deadline')}\n"
                        f"- Status update needed."
                    )
                    actions["reminders_sent"] += 1
                else:
                    # Escalation: notify instructor
                    msg = (
                        f"**Overdue** (escalation {esc_count + 1}/{max_escalations})\n"
                        f"- **Task:** {task.get('description', '')}\n"
                        f"- **Assigned to:** {task.get('assigned_to', 'Unknown')}\n"
                        f"- No update received."
                    )
                    actions["escalations"] += 1

                post_hari_message(
                    content=msg,
                    project_id=task.get("project_id"),
                    channel_type="project_general",
                    channel_id=task.get("channel_key") if task.get("channel_key", "").startswith("direct_") else None,
                    metadata={"task_update": True, "task_id": task_id, "update_type": "reminder"},
                )

        return {"status": "ok", **actions, "tasks_checked": len(tasks)}

    except Exception as e:
        logger.error("[Hari] Follow-up engine error: %s", e)
        return {"status": "error", "error": str(e)}
