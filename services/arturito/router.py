# services/arturito/router.py
# ================================
# Router Central de Arturito
# ================================
# Migrado desde Router.gs

from typing import Dict, Any, Callable, Optional, List
from .handlers import (
    handle_budget_vs_actuals,
    handle_info,
    handle_scope_of_work,
)
from .persona import set_personality_level, get_identity_response
from .responder import generate_small_talk_response

# ================================
# Tabla de Rutas (ROUTES)
# ================================

ROUTES: Dict[str, Dict[str, Any]] = {
    "BUDGET_VS_ACTUALS": {
        "handler": handle_budget_vs_actuals,
        "required_entities": ["project"],
        "optional_entities": ["category"],
        "description": "Genera reporte Budget vs Actuals de un proyecto",
    },

    "CONSULTA_ESPECIFICA": {
        "handler": None,  # TODO: Implementar handler específico
        "required_entities": ["project"],
        "optional_entities": ["topic", "category", "question"],
        "description": "Responde consultas sobre categorías específicas del BVA",
    },

    "SCOPE_OF_WORK": {
        "handler": handle_scope_of_work,
        "required_entities": [],
        "optional_entities": ["project", "question"],
        "description": "Consultas sobre el Scope of Work de un proyecto",
    },

    "INFO": {
        "handler": handle_info,
        "required_entities": [],
        "optional_entities": ["topic"],
        "description": "Información del sistema, ayuda, identidad",
    },

    "SET_PERSONALITY": {
        "handler": None,  # Manejado directamente en route()
        "required_entities": ["level"],
        "optional_entities": [],
        "description": "Cambia el nivel de personalidad del bot",
    },

    "GREETING": {
        "handler": None,  # Manejado directamente en route()
        "required_entities": [],
        "optional_entities": [],
        "description": "Responde a saludos",
    },

    "SMALL_TALK": {
        "handler": None,  # Usa responder con GPT
        "required_entities": [],
        "optional_entities": [],
        "description": "Conversación general",
    },
}

# Aliases de intents (mapeo de nombres alternativos)
INTENT_ALIASES = {
    "SOW": "SCOPE_OF_WORK",
    "SCOPE OF WORK": "SCOPE_OF_WORK",
    "HELP": "INFO",
    "AYUDA": "INFO",
    "IDENTITY": "INFO",
    "BVA": "BUDGET_VS_ACTUALS",
}


def _canonicalize_intent(intent: str) -> str:
    """Normaliza el nombre del intent usando aliases"""
    if not intent:
        return "UNKNOWN"
    upper = intent.upper().strip()
    return INTENT_ALIASES.get(upper, upper)


def _check_required_entities(
    route_def: Dict[str, Any],
    entities: Dict[str, Any]
) -> List[str]:
    """Verifica que estén presentes las entidades requeridas"""
    required = route_def.get("required_entities", [])
    missing = []

    for key in required:
        value = entities.get(key)
        if value is None or (isinstance(value, str) and not value.strip()):
            missing.append(key)

    return missing


# ================================
# Función Principal de Routing
# ================================

def route(
    intent_obj: Dict[str, Any],
    context: Dict[str, Any] = None
) -> Dict[str, Any]:
    """
    Recibe el resultado del NLU y despacha al handler correcto.

    Args:
        intent_obj: Resultado de interpret_message() con intent, entities, confidence
        context: Contexto adicional (user, space_id, space_name, etc.)

    Returns:
        Dict con la respuesta formateada para Google Chat
    """
    ctx = context or {}
    space_id = ctx.get("space_id", "default")

    # Extraer datos del intent
    raw_intent = intent_obj.get("intent", "UNKNOWN")
    intent = _canonicalize_intent(raw_intent)
    entities = intent_obj.get("entities", {})
    confidence = intent_obj.get("confidence", 0.0)
    raw_text = intent_obj.get("raw_text", "")

    # Verificar confianza mínima
    if confidence < 0.5 and intent not in ["GREETING", "SMALL_TALK"]:
        return {
            "text": "🤔 No estoy seguro de entender. ¿Podrías ser más específico?",
            "action": "low_confidence"
        }

    # ================================
    # Handlers especiales (sin tabla ROUTES)
    # ================================

    # SET_PERSONALITY
    if intent == "SET_PERSONALITY":
        level = entities.get("level", 3)
        result = set_personality_level(level, space_id)
        return {
            "text": result.get("message", "Personalidad actualizada."),
            "action": "set_personality",
            "data": {"level": level}
        }

    # GREETING
    if intent == "GREETING":
        user_name = ctx.get("user_name", "")
        greeting = f"¡Hola{', ' + user_name if user_name else ''}! "
        greeting += "Soy Arturito. ¿En qué puedo ayudarte?"
        return {
            "text": greeting,
            "action": "greeting"
        }

    # SMALL_TALK - Usa GPT para responder
    if intent == "SMALL_TALK":
        response = generate_small_talk_response(raw_text, space_id)
        return {
            "text": response,
            "action": "small_talk"
        }

    # ================================
    # Routing por tabla ROUTES
    # ================================

    route_def = ROUTES.get(intent)

    if not route_def:
        return {
            "text": f"🤔 No tengo una acción definida para: {intent}",
            "action": "unknown_intent"
        }

    # Verificar entidades requeridas
    missing = _check_required_entities(route_def, entities)
    if missing:
        return {
            "text": f"⚠️ Me falta información: {', '.join(missing)}",
            "action": "missing_entities",
            "data": {"missing": missing}
        }

    # Ejecutar handler
    handler = route_def.get("handler")
    if not handler:
        return {
            "text": f"⏳ El handler para '{intent}' aún no está implementado.",
            "action": "not_implemented"
        }

    try:
        # Construir request para el handler
        request = {
            "intent": intent,
            "entities": entities,
            "raw_text": raw_text,
            "confidence": confidence,
        }

        result = handler(request, ctx)

        # El handler retorna un dict con la respuesta
        return result

    except Exception as e:
        return {
            "text": f"⚠️ Error ejecutando {intent}: {str(e)}",
            "action": "handler_error",
            "error": str(e)
        }


# ================================
# Slash Commands Router
# ================================

def route_slash_command(
    command: str,
    args: str,
    context: Dict[str, Any] = None
) -> Dict[str, Any]:
    """
    Maneja slash commands directamente (sin pasar por NLU).

    Args:
        command: Nombre del comando sin "/" (ej: "ping", "budgetvsactuals")
        args: Argumentos después del comando
        context: Contexto del mensaje

    Returns:
        Dict con la respuesta
    """
    ctx = context or {}
    cmd = command.lower().strip()

    if cmd == "ping":
        return {
            "text": "🏓 Pong! Arturito está activo.",
            "action": "ping"
        }

    if cmd == "budgetvsactuals" or cmd == "bva":
        project = args.strip() if args else ctx.get("space_name", "")
        if not project or project.lower() in ["default", "general"]:
            return {
                "text": "⚠️ Especifica un proyecto. Ej: `/BudgetvsActuals Del Rio`",
                "action": "missing_project"
            }

        # Fabricar intent y delegar al router normal
        fake_intent = {
            "intent": "BUDGET_VS_ACTUALS",
            "entities": {"project": project},
            "confidence": 1.0,
            "raw_text": f"/{command} {args}"
        }
        return route(fake_intent, ctx)

    if cmd == "sarcasmo" or cmd == "personality":
        try:
            level = int(args.strip())
            fake_intent = {
                "intent": "SET_PERSONALITY",
                "entities": {"level": level},
                "confidence": 1.0,
                "raw_text": f"/{command} {args}"
            }
            return route(fake_intent, ctx)
        except ValueError:
            return {
                "text": "⚠️ Usa `/sarcasmo 1-5` para ajustar la personalidad.",
                "action": "invalid_args"
            }

    if cmd == "help" or cmd == "ayuda":
        return {
            "text": get_identity_response(ctx.get("space_id", "default")),
            "action": "help"
        }

    return {
        "text": f"❓ Comando desconocido: /{command}",
        "action": "unknown_command"
    }
