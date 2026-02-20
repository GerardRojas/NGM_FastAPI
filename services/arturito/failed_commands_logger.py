"""
===============================================================================
 Failed Commands Logger for Arturito
===============================================================================
 Logs copilot commands that fail to help improve the system over time.
===============================================================================
"""

import logging
from typing import Dict, Any, Optional
from datetime import datetime
from supabase import Client

logger = logging.getLogger(__name__)


async def log_failed_command(
    supabase: Client,
    user_id: str,
    command_text: str,
    current_page: str,
    intent_detected: Optional[str] = None,
    entities_detected: Optional[Dict] = None,
    error_reason: str = "unknown",
    gpt_attempted: bool = False,
    gpt_response: Optional[Dict] = None,
    gpt_confidence: Optional[float] = None,
    user_agent: Optional[str] = None
) -> bool:
    """
    Log a failed copilot command to the database for analytics.

    Args:
        supabase: Supabase client
        user_id: ID of the user who issued the command
        command_text: The original command text from the user
        current_page: The page where the user was (e.g., 'expenses.html')
        intent_detected: The intent detected by NLU (if any)
        entities_detected: Entities extracted by NLU
        error_reason: Why the command failed (no_exact_match, gpt_failed, etc.)
        gpt_attempted: Whether GPT was tried to interpret the command
        gpt_response: GPT's response if attempted
        gpt_confidence: Confidence level from GPT (0.0-1.0)
        user_agent: User agent string from request

    Returns:
        True if logged successfully, False otherwise
    """
    try:
        data = {
            "user_id": user_id,
            "command_text": command_text,
            "current_page": current_page,
            "intent_detected": intent_detected,
            "entities_detected": entities_detected or {},
            "error_reason": error_reason,
            "gpt_attempted": gpt_attempted,
            "gpt_response": gpt_response,
            "gpt_confidence": gpt_confidence,
            "user_agent": user_agent,
        }

        result = supabase.table("arturito_failed_commands").insert(data).execute()

        if result.data:
            logger.info(
                f"Logged failed command for user {user_id}: '{command_text[:50]}...' "
                f"(reason: {error_reason})"
            )
            return True
        else:
            logger.warning(f"Failed to log command (no data returned): {command_text}")
            return False

    except Exception as e:
        logger.error(f"Error logging failed command: {e}", exc_info=True)
        return False


async def get_failed_commands(
    supabase: Client,
    user_id: Optional[str] = None,
    page: int = 1,
    page_size: int = 50,
    current_page: Optional[str] = None,
    error_reason: Optional[str] = None,
    days_back: int = 30
) -> Dict[str, Any]:
    """
    Get failed commands with filters and pagination.

    Args:
        supabase: Supabase client
        user_id: Filter by user (None for all users - admin only)
        page: Page number (1-indexed)
        page_size: Number of results per page
        current_page: Filter by page (e.g., 'expenses.html')
        error_reason: Filter by error reason
        days_back: How many days back to look

    Returns:
        Dict with commands, total count, and pagination info
    """
    try:
        from datetime import timedelta

        # Build query
        query = supabase.table("arturito_failed_commands").select(
            "*, users!inner(user_name, email)", count="exact"
        )

        # Apply filters
        if user_id:
            query = query.eq("user_id", user_id)

        if current_page:
            query = query.eq("current_page", current_page)

        if error_reason:
            query = query.eq("error_reason", error_reason)

        # Date filter
        cutoff_date = datetime.now() - timedelta(days=days_back)
        query = query.gte("created_at", cutoff_date.isoformat())

        # Order and paginate
        offset = (page - 1) * page_size
        query = query.order("created_at", desc=True).range(offset, offset + page_size - 1)

        result = query.execute()

        total = result.count if hasattr(result, "count") else len(result.data)

        return {
            "commands": result.data,
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": (total + page_size - 1) // page_size if total else 0,
        }

    except Exception as e:
        logger.error(f"Error fetching failed commands: {e}", exc_info=True)
        return {
            "commands": [],
            "total": 0,
            "page": page,
            "page_size": page_size,
            "total_pages": 0,
            "error": str(e),
        }


async def get_failed_commands_stats(
    supabase: Client,
    user_id: Optional[str] = None,
    days_back: int = 30,
    since_override: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Get aggregated statistics about failed commands.

    Args:
        supabase: Supabase client
        user_id: Filter by user (None for all users - admin only)
        days_back: How many days back to analyze
        since_override: ISO timestamp — if set, clamp days_back so the window
                        starts no earlier than this date (non-destructive reset)

    Returns:
        Dict with statistics (total failures, top pages, top errors, etc.)
    """
    try:
        # Clamp days_back if a reset timestamp is provided
        effective_days = days_back
        if since_override:
            try:
                reset_dt = datetime.fromisoformat(since_override)
                delta = datetime.now() - reset_dt
                if delta.days < effective_days:
                    effective_days = max(delta.days, 1)
            except Exception:
                pass

        # Call the PostgreSQL function we created
        result = supabase.rpc(
            "get_failed_commands_stats",
            {"p_user_id": user_id, "p_days_back": effective_days}
        ).execute()

        if result.data and len(result.data) > 0:
            stats = result.data[0]
            return {
                "total_failures": stats.get("total_failures", 0),
                "unique_commands": stats.get("unique_commands", 0),
                "gpt_attempt_rate": float(stats.get("gpt_attempt_rate", 0)),
                "top_pages": stats.get("top_pages", []),
                "top_errors": stats.get("top_errors", []),
                "most_common_commands": stats.get("most_common_commands", []),
                "period_days": days_back,
            }
        else:
            return {
                "total_failures": 0,
                "unique_commands": 0,
                "gpt_attempt_rate": 0.0,
                "top_pages": [],
                "top_errors": [],
                "most_common_commands": [],
                "period_days": days_back,
            }

    except Exception as e:
        logger.error(f"Error fetching failed commands stats: {e}", exc_info=True)
        return {
            "error": str(e),
            "total_failures": 0,
            "unique_commands": 0,
            "gpt_attempt_rate": 0.0,
            "top_pages": [],
            "top_errors": [],
            "most_common_commands": [],
            "period_days": days_back,
        }
