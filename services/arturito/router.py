# services/arturito/router.py
# ================================
# Router Central de Arturito
# ================================
# Migrado desde Router.gs

import os
import time
import json
from typing import Dict, Any, Callable, Optional, List
from .handlers import (
    handle_budget_vs_actuals,
    handle_consulta_especifica,
    handle_pnl_cogs,
    handle_info,
    handle_scope_of_work,
    handle_ngm_help,
    handle_ngm_action,
    handle_bug_report,
    handle_copilot,
    handle_expense_reminder,
    handle_list_projects,
    handle_list_vendors,
    handle_create_vendor,
    handle_create_project,
    handle_search_expenses,
    handle_vault_search,
    handle_vault_list,
    handle_vault_create_folder,
    handle_vault_delete,
    handle_vault_organize,
    handle_vault_upload,
)
from .permissions import is_action_permitted, get_permission_denial_message, check_role_permission
from .persona import set_personality_level, get_identity_response
from .responder import generate_small_talk_response


# ================================
# User-facing capability summary
# ================================

# Grouped by category for readable display
_CAPABILITY_GROUPS = {
    "Reports": [
        ("bva / budget vs actuals", "Budget vs Actuals report for any project"),
        ("pnl", "P&L COGS report"),
        ("budget query", "Ask about a specific budget category (e.g. 'how much on framing?')"),
    ],
    "Navigation": [
        ("go to [page]", "Navigate to any module (expenses, pipeline, projects, etc.)"),
        ("open [modal]", "Open modals (new expense, new task, etc.)"),
    ],
    "Copilot": [
        ("filter by [column]", "Filter the current page table"),
        ("sort by [column]", "Sort the current page table"),
        ("search [term]", "Search on the current page"),
    ],
    "Data": [
        ("list projects / vendors", "Show all projects or vendors"),
        ("create vendor / project", "Create a new vendor or project"),
        ("search expenses", "Find expenses by amount, vendor, or category"),
    ],
    "Vault": [
        ("search files", "Find files in the vault"),
        ("list files", "Browse vault contents"),
        ("create folder", "Create a new vault folder"),
    ],
    "Other": [
        ("scope of work", "Ask about a project's scope"),
        ("report bug", "Report an issue"),
        ("expense reminder", "Send authorization reminders"),
    ],
}


def _format_capabilities_for_user(current_page: str = None) -> str:
    """Build a contextual capability list based on the user's current page."""
    lines = ["Here's what I can help with:\n"]

    # Prioritize relevant groups based on page
    page = (current_page or "").lower()
    priority_groups = []
    if "expense" in page:
        priority_groups = ["Reports", "Data", "Copilot"]
    elif "pipeline" in page:
        priority_groups = ["Copilot", "Navigation", "Data"]
    elif "vault" in page:
        priority_groups = ["Vault", "Navigation"]
    elif "budget" in page:
        priority_groups = ["Reports", "Data"]

    shown = set()
    # Show priority groups first
    for group_name in priority_groups:
        if group_name in _CAPABILITY_GROUPS:
            lines.append(f"**{group_name}**")
            for cmd, desc in _CAPABILITY_GROUPS[group_name]:
                lines.append(f"  - `{cmd}` - {desc}")
            shown.add(group_name)

    # Then show remaining groups
    for group_name, items in _CAPABILITY_GROUPS.items():
        if group_name not in shown:
            lines.append(f"**{group_name}**")
            for cmd, desc in items:
                lines.append(f"  - `{cmd}` - {desc}")

    lines.append("\nFor receipt processing try @Andrew. For expense authorization try @Daneel.")
    return "\n".join(lines)


# ================================
# Intent Logging (fire-and-forget)
# ================================

def _log_intent(context: Dict[str, Any], intent_obj: Dict[str, Any],
                action_result: str, processing_ms: int = 0,
                delegated_to: str = None) -> None:
    """Log an intent to arturito_intent_log. Non-blocking, never fails the main flow."""
    try:
        from supabase import create_client
        url = os.getenv("SUPABASE_URL")
        key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
        if not url or not key:
            return

        sb = create_client(url, key)
        sb.rpc("log_arturito_intent", {
            "p_user_email": context.get("user_email", ""),
            "p_user_role": context.get("user_role", ""),
            "p_space_id": context.get("space_id", ""),
            "p_current_page": context.get("current_page", ""),
            "p_raw_text": intent_obj.get("raw_text", "")[:500],
            "p_intent": intent_obj.get("intent", "UNKNOWN"),
            "p_confidence": float(intent_obj.get("confidence", 0.0)),
            "p_source": intent_obj.get("source", "unknown"),
            "p_entities": json.dumps(intent_obj.get("entities", {})),
            "p_action_result": action_result,
            "p_delegated_to": delegated_to,
            "p_processing_ms": processing_ms,
        }).execute()
    except Exception as e:
        print(f"[Arturito] Intent log error (non-fatal): {e}")


# ================================
# Tabla de Rutas (ROUTES)
# ================================

ROUTES: Dict[str, Dict[str, Any]] = {
    "BUDGET_VS_ACTUALS": {
        "handler": handle_budget_vs_actuals,
        "required_entities": [],  # Handler manages missing project internally (ask_project flow)
        "optional_entities": ["project", "category"],
        "description": "Genera reporte Budget vs Actuals de un proyecto",
    },

    "PNL_COGS": {
        "handler": handle_pnl_cogs,
        "required_entities": [],  # Handler manages missing project internally (ask_project flow)
        "optional_entities": ["project"],
        "description": "Genera reporte P&L COGS (actuals only, sin budget)",
    },

    "CONSULTA_ESPECIFICA": {
        "handler": handle_consulta_especifica,
        "required_entities": [],  # Handler maneja casos sin proyecto/categoría
        "optional_entities": ["project", "topic", "category", "trade", "question"],
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
        "description": "Conversacion general",
    },

    "NGM_HELP": {
        "handler": handle_ngm_help,
        "required_entities": [],
        "optional_entities": ["module", "topic", "query_type"],
        "description": "Preguntas sobre como usar NGM Hub",
    },

    "NGM_ACTION": {
        "handler": None,  # Manejado directamente en route() (async)
        "required_entities": [],
        "optional_entities": ["action"],
        "description": "Ejecutar acciones en NGM Hub (navegar, abrir modales)",
    },

    "REPORT_BUG": {
        "handler": None,  # Manejado directamente en route() (async)
        "required_entities": [],
        "optional_entities": ["description"],
        "description": "Reportar un bug o problema",
    },

    "COPILOT": {
        "handler": handle_copilot,
        "required_entities": [],
        "optional_entities": ["command_type", "raw_command"],
        "description": "Comandos copilot para controlar la pagina actual",
    },

    "EXPENSE_REMINDER": {
        "handler": None,  # Manejado en route_async
        "required_entities": [],
        "optional_entities": ["message"],
        "description": "Enviar recordatorio de gastos pendientes a autorizadores",
    },

    "LIST_PROJECTS": {
        "handler": handle_list_projects,
        "required_entities": [],
        "optional_entities": [],
        "description": "Lista todos los proyectos del sistema",
    },

    "LIST_VENDORS": {
        "handler": handle_list_vendors,
        "required_entities": [],
        "optional_entities": [],
        "description": "Lista todos los vendors/proveedores del sistema",
    },

    "CREATE_VENDOR": {
        "handler": handle_create_vendor,
        "required_entities": ["vendor_name"],
        "optional_entities": [],
        "description": "Crea un nuevo vendor/proveedor",
    },

    "CREATE_PROJECT": {
        "handler": handle_create_project,
        "required_entities": ["project_name"],
        "optional_entities": [],
        "description": "Crea un nuevo proyecto",
    },

    "SEARCH_EXPENSES": {
        "handler": handle_search_expenses,
        "required_entities": [],  # At least one of: amount, vendor, category, project
        "optional_entities": ["amount", "vendor", "category", "project"],
        "description": "Busca gastos por monto, vendor, categoría o proyecto",
    },

    # ---- Vault (Data Vault file storage) ----

    "VAULT_SEARCH": {
        "handler": handle_vault_search,
        "required_entities": [],
        "optional_entities": ["query", "file_name", "file_type", "project"],
        "description": "Search files in the vault by name, type, or project",
    },

    "VAULT_LIST": {
        "handler": handle_vault_list,
        "required_entities": [],
        "optional_entities": ["folder"],
        "description": "List files and folders in the vault",
    },

    "VAULT_CREATE_FOLDER": {
        "handler": handle_vault_create_folder,
        "required_entities": ["folder_name"],
        "optional_entities": [],
        "description": "Create a new folder in the vault",
    },

    "VAULT_DELETE": {
        "handler": handle_vault_delete,
        "required_entities": [],
        "optional_entities": ["file_name"],
        "description": "Delete a file from the vault",
    },

    "VAULT_ORGANIZE": {
        "handler": handle_vault_organize,
        "required_entities": [],
        "optional_entities": ["project_id"],
        "description": "Auto-organize vault files by type into subfolders",
    },

    "VAULT_UPLOAD": {
        "handler": handle_vault_upload,
        "required_entities": [],
        "optional_entities": [],
        "description": "Upload files to the vault (redirects to UI)",
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
    "PNL": "PNL_COGS",
    "P&L": "PNL_COGS",
    "P&L COGS": "PNL_COGS",
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
    _start = time.time()
    ctx = context or {}
    space_id = ctx.get("space_id", "default")

    # Extraer datos del intent
    raw_intent = intent_obj.get("intent", "UNKNOWN")
    intent = _canonicalize_intent(raw_intent)
    entities = intent_obj.get("entities", {})
    confidence = intent_obj.get("confidence", 0.0)
    raw_text = intent_obj.get("raw_text", "")

    # Verificar confianza minima
    if confidence < 0.5 and intent not in ["GREETING", "SMALL_TALK"]:
        current_page = ctx.get("current_page", "")
        capabilities = _format_capabilities_for_user(current_page)
        _log_intent(ctx, intent_obj, "low_confidence",
                     int((time.time() - _start) * 1000))
        return {
            "text": f"I'm not sure what you need. Could you be more specific?\n\n{capabilities}",
            "action": "low_confidence"
        }

    # ================================
    # Verificar permisos globales
    # ================================
    if not is_action_permitted(intent):
        return {
            "text": get_permission_denial_message(intent),
            "action": "permission_denied",
            "data": {"intent": intent}
        }

    # ================================
    # Verificar permisos basados en rol
    # ================================
    role_allowed, delegation_info = check_role_permission(intent, ctx)
    if not role_allowed and delegation_info:
        return {
            "text": delegation_info["message"],
            "action": "suggest_delegation",
            "data": {
                "intent": intent,
                "delegation": delegation_info,
                "raw_text": raw_text,
            }
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

    # GREETING - Use personality-based responses
    if intent == "GREETING":
        response = generate_small_talk_response(raw_text, space_id)
        return {
            "text": response,
            "action": "greeting"
        }

    # SMALL_TALK - Usa GPT para responder
    if intent == "SMALL_TALK":
        response = generate_small_talk_response(raw_text, space_id)
        return {
            "text": response,
            "action": "small_talk"
        }

    # NGM_ACTION - Needs async handling (delegated to async_route)
    if intent == "NGM_ACTION":
        # Return marker for async processing
        return {
            "text": None,
            "action": "ngm_action_pending",
            "data": {
                "intent": intent,
                "entities": entities,
                "raw_text": raw_text,
            },
            "requires_async": True
        }

    # REPORT_BUG - Needs async handling (delegated to async_route)
    if intent == "REPORT_BUG":
        # Return marker for async processing
        return {
            "text": None,
            "action": "report_bug_pending",
            "data": {
                "intent": intent,
                "entities": entities,
                "raw_text": raw_text,
            },
            "requires_async": True
        }

    # EXPENSE_REMINDER - Needs async handling (delegated to async_route)
    if intent == "EXPENSE_REMINDER":
        # Return marker for async processing
        return {
            "text": None,
            "action": "expense_reminder_pending",
            "data": {
                "intent": intent,
                "entities": entities,
                "raw_text": raw_text,
            },
            "requires_async": True
        }

    # ================================
    # Routing por tabla ROUTES
    # ================================

    route_def = ROUTES.get(intent)

    if not route_def:
        current_page = ctx.get("current_page", "")
        capabilities = _format_capabilities_for_user(current_page)
        _log_intent(ctx, intent_obj, "unknown_intent",
                     int((time.time() - _start) * 1000))
        return {
            "text": f"I don't have that capability yet.\n\n{capabilities}",
            "action": "unknown_intent"
        }

    # Verificar entidades requeridas
    missing = _check_required_entities(route_def, entities)
    if missing:
        return {
            "text": f"I need more information: {', '.join(missing)}",
            "action": "missing_entities",
            "data": {"missing": missing}
        }

    # Ejecutar handler
    handler = route_def.get("handler")
    if not handler:
        return {
            "text": f"That feature ({intent}) is coming soon but not ready yet.",
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

        # Log success
        _log_intent(ctx, intent_obj, result.get("action", "success"),
                     int((time.time() - _start) * 1000))
        return result

    except Exception as e:
        _log_intent(ctx, intent_obj, "handler_error",
                     int((time.time() - _start) * 1000))
        return {
            "text": f"Error running {intent}: {str(e)}",
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
                "text": "Specify a project. E.g.: `/BudgetvsActuals Del Rio`",
                "action": "missing_project"
            }

        fake_intent = {
            "intent": "BUDGET_VS_ACTUALS",
            "entities": {"project": project},
            "confidence": 1.0,
            "raw_text": f"/{command} {args}"
        }
        return route(fake_intent, ctx)

    if cmd == "pnl" or cmd == "pnlcogs":
        project = args.strip() if args else ctx.get("space_name", "")
        if not project or project.lower() in ["default", "general"]:
            return {
                "text": "Specify a project. E.g.: `/pnl Del Rio`",
                "action": "missing_project"
            }

        fake_intent = {
            "intent": "PNL_COGS",
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
        "text": f"Comando desconocido: /{command}",
        "action": "unknown_command"
    }


# ================================
# Async Router for NGM Hub Actions
# ================================

async def route_async(
    intent_obj: Dict[str, Any],
    context: Dict[str, Any] = None,
    db_client=None
) -> Dict[str, Any]:
    """
    Async version of route() that handles NGM_ACTION and REPORT_BUG intents.
    Falls back to sync route() for other intents.

    Args:
        intent_obj: Resultado de interpret_message() con intent, entities, confidence
        context: Contexto adicional (user, space_id, space_name, user_id, current_page, etc.)
        db_client: Supabase client for permission checking

    Returns:
        Dict con la respuesta formateada
    """
    ctx = context or {}
    intent = intent_obj.get("intent", "UNKNOWN").upper()
    entities = intent_obj.get("entities", {})
    raw_text = intent_obj.get("raw_text", "")

    # Verificar permisos globales
    if not is_action_permitted(intent):
        return {
            "text": get_permission_denial_message(intent),
            "action": "permission_denied",
            "data": {"intent": intent}
        }

    # Verificar permisos basados en rol
    role_allowed, delegation_info = check_role_permission(intent, ctx)
    if not role_allowed and delegation_info:
        return {
            "text": delegation_info["message"],
            "action": "suggest_delegation",
            "data": {
                "intent": intent,
                "delegation": delegation_info,
                "raw_text": raw_text,
            }
        }

    # Handle NGM_ACTION
    if intent == "NGM_ACTION":
        request = {
            "intent": intent,
            "entities": entities,
            "raw_text": raw_text,
        }
        result = await handle_ngm_action(request, context, db_client)
        return result

    # Handle REPORT_BUG
    if intent == "REPORT_BUG":
        request = {
            "intent": intent,
            "entities": entities,
            "raw_text": raw_text,
        }
        result = await handle_bug_report(request, context, db_client)
        return result

    # Handle EXPENSE_REMINDER
    if intent == "EXPENSE_REMINDER":
        request = {
            "intent": intent,
            "entities": entities,
            "raw_text": raw_text,
        }
        result = await handle_expense_reminder(request, context, db_client)
        return result

    # Fall back to sync route for all other intents
    return route(intent_obj, context)
