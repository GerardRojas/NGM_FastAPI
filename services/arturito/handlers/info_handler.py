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

    topic = entities.get("topic", "").lower()

    # Identidad del bot
    if topic == "identity" or "quien" in request.get("raw_text", "").lower():
        return {
            "text": get_identity_response(space_id),
            "action": "identity"
        }

    # Información de personalidad actual
    if topic == "personality" or "sarcasmo" in request.get("raw_text", "").lower():
        level = get_personality_level(space_id)
        return {
            "text": f"🎛️ Mi nivel de personalidad actual es *{level}/5*.\n\nUsa `/sarcasmo 1-5` para cambiarlo.",
            "action": "personality_info"
        }

    # Ayuda general (default)
    help_text = """🤖 *Arturito - Asistente NGM*

📋 *Comandos disponibles:*

*Reportes:*
• `/BudgetvsActuals [proyecto]` - Genera reporte BVA en PDF
• `@Arturito ¿cuánto tengo en HVAC?` - Consulta específica

*Sistema:*
• `/ping` - Verificar que estoy activo
• `/sarcasmo 1-5` - Ajustar mi personalidad
• `/help` - Ver esta ayuda

*Consultas naturales:*
• Menciónme con `@Arturito` seguido de tu pregunta
• Puedo responder sobre presupuestos, gastos, y proyectos

💡 *Tip:* Si estás en un espacio de proyecto, no necesitas especificar el nombre del proyecto."""

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
