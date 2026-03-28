# services/arturito/handlers/info_handler.py
# ================================
# Handler: Información del Sistema
# ================================
# Migrado desde HandleInfo.gs

from typing import Dict, Any
from ..persona import get_identity_response, get_personality_level


def handle_info(
    request: Dict[str, Any],
    context: Dict[str, Any] = None
) -> Dict[str, Any]:
    """
    Responde preguntas sobre el sistema, ayuda, identidad del bot.

    Args:
        request: {intent, entities: {topic?}, raw_text}
        context: {user, space_id, space_name}

    Returns:
        Dict con text y action
    """
    entities = request.get("entities", {})
    ctx = context or {}
    space_id = ctx.get("space_id", "default")

    # Handle None values gracefully (entities.get can return None if key exists with None value)
    topic = (entities.get("topic") or "").lower()
    raw_text = (request.get("raw_text") or "").lower()

    # Identidad del bot
    if topic == "identity" or "quien" in raw_text:
        return {
            "text": get_identity_response(space_id),
            "action": "identity"
        }

    # Current personality info
    if topic == "personality" or "sarcasm" in raw_text or "sarcasmo" in raw_text:
        level = get_personality_level(space_id)
        modes = {
            1: "corporate mode (boring)",
            2: "friendly mode",
            3: "normal mode",
            4: "sarcastic mode",
            5: "ultra sarcastic mode"
        }
        return {
            "text": f"I'm at level **{level}/5** - {modes.get(level, 'normal')}.\n\nUse `/sarcasm 1-5` to adjust.",
            "action": "personality_info"
        }

    # General help (default)
    help_text = """Here's what I can do:

**Navigation** - I'll take you where you need to go:
- "go to expenses" / "open pipeline" / "take me to projects"

**Actions** - I get things done:
- "add an expense" / "create a task" / "scan a receipt"

**Copilot** - I control the current page:
- "filter by project X" / "show only pending" / "clear filters"

**Questions** - I know where things are:
- "how do I add an expense?" / "where are my tasks?"

**Reports** - Project data at your fingertips:
- "BVA for [project]" / "show budget vs actuals"

**Bugs** - I'll create a ticket:
- "I found a bug" / "something is broken"

Use `/sarcasm 1-5` to adjust my personality level."""

    return {
        "text": help_text,
        "action": "help"
    }


def get_system_status() -> Dict[str, Any]:
    """
    Retorna información del estado del sistema.
    Útil para debugging y monitoreo.
    """
    import os

    return {
        "bot_name": "Art",
        "version": "2.0.0",
        "environment": os.getenv("ENVIRONMENT", "development"),
        "openai_configured": bool(os.getenv("OPENAI_API_KEY")),
        "supabase_configured": bool(os.getenv("SUPABASE_URL")),
    }
