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

    # Información de personalidad actual
    if topic == "personality" or "sarcasmo" in raw_text:
        level = get_personality_level(space_id)
        modes = {
            1: "modo corporativo (aburrido)",
            2: "modo amigable",
            3: "modo normal",
            4: "modo sarcástico",
            5: "modo ultra sarcástico"
        }
        return {
            "text": f"Estoy en nivel **{level}/5** - {modes.get(level, 'normal')}.\n\nSi quieres que sea más nice (o más pesado), usa `/sarcasmo 1-5`.",
            "action": "personality_info"
        }

    # Ayuda general (default)
    help_text = """A ver, te explico rápido qué puedo hacer:

**Navegación** (te llevo a donde quieras):
- "llévame a gastos" / "abre pipeline" / "ir a proyectos"

**Acciones** (hago cosas por ti):
- "agregar un gasto" / "crear tarea" / "escanear recibo"

**Copilot** (controlo la página actual):
- "filtrar por proyecto X" / "mostrar solo pendientes" / "limpiar filtros"

**Preguntas** (sé dónde están las cosas):
- "¿cómo agrego un gasto?" / "¿dónde veo mis tareas?"

**Bugs** (creo tickets):
- "tengo un bug" / "algo no funciona"

Si soy muy sarcástico usa `/sarcasmo 1-5` para bajarle.
Si soy muy aburrido... también."""

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
        "bot_name": "Arturito",
        "version": "2.0.0",
        "environment": os.getenv("ENVIRONMENT", "development"),
        "openai_configured": bool(os.getenv("OPENAI_API_KEY")),
        "supabase_configured": bool(os.getenv("SUPABASE_URL")),
    }
