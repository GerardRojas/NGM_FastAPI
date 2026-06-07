"""
═══════════════════════════════════════════════════════════════════════════════
 NGM HUB Handler for Art
═══════════════════════════════════════════════════════════════════════════════
 Handles:
 - Help/FAQ questions about NGM HUB modules
 - Action execution with permission checking
 - Bug reporting and task creation
═══════════════════════════════════════════════════════════════════════════════
"""

import logging
from typing import Optional
from datetime import datetime

from ..ngm_knowledge import (
    NGM_MODULES,
    NGM_ACTIONS,
    HELPER_ROLES,
    find_answer_for_question,
    find_feature_by_keywords,
    find_action_by_intent,
)

logger = logging.getLogger(__name__)

# Maps a knowledge permission action ("module:action") to its real
# role_permissions column. "approve" → can_authorize keeps Art in sync with the
# expense-authorize permission managed in Roles Management.
_PERMISSION_ACTION_COLUMN = {
    "view": "can_view",
    "edit": "can_edit",
    "delete": "can_delete",
    "approve": "can_authorize",
}


# ─────────────────────────────────────────────────────────────────────────────
# PERMISSION CHECKING
# ─────────────────────────────────────────────────────────────────────────────

async def check_user_permission(
    user_id: str,
    permission: str,
    db_client=None
) -> tuple[bool, Optional[dict]]:
    """
    Check if user has a specific permission.

    Returns:
        (has_permission, user_info)
    """
    if not db_client:
        # If no DB client, assume permission (will be checked on frontend)
        return True, None

    try:
        # Get user with role + its real role_permissions rows (module_key + can_*).
        result = db_client.table("users").select(
            "user_id, user_name, email, user_rol, "
            "rols!users_user_rol_fkey(rol_name, role_permissions(module_key, can_view, can_edit, can_delete, can_authorize))"
        ).eq("user_id", user_id).single().execute()

        if not result.data:
            return False, None

        user = result.data
        rols_data = user.get("rols") or {}
        role_name = rols_data.get("rol_name")

        # CEO and COO have all permissions (leadership failsafe).
        if role_name in ("CEO", "COO"):
            return True, user

        if ":" not in (permission or ""):
            return False, user
        module, action = permission.split(":", 1)
        col = _PERMISSION_ACTION_COLUMN.get(action, "can_view")

        perms = rols_data.get("role_permissions") or []
        perm = next((p for p in perms if p.get("module_key") == module), None)
        return bool(perm and perm.get(col)), user

    except Exception as e:
        logger.error(f"Error checking permission: {e}")
        return False, None


async def get_users_with_permission(
    permission: str,
    db_client=None
) -> list[dict]:
    """
    Get list of users who have a specific permission.
    Used to suggest who can help when user doesn't have access.
    """
    if not db_client:
        # Return role names that typically have this permission
        helper_roles = HELPER_ROLES.get(permission, ["CEO", "COO"])
        return [{"role": role} for role in helper_roles]

    try:
        if ":" not in (permission or ""):
            return [{"role": r} for r in HELPER_ROLES.get(permission, ["CEO", "COO"])]
        module, action = permission.split(":", 1)
        col = _PERMISSION_ACTION_COLUMN.get(action, "can_view")

        # Roles that hold this capability on the module (real schema).
        perm_rows = db_client.table("role_permissions").select("rol_id") \
            .eq("module_key", module).eq(col, True).execute()
        role_ids = [r["rol_id"] for r in (perm_rows.data or []) if r.get("rol_id")]

        if not role_ids:
            # No one configured: fall back to the static role-name hints.
            return [{"role": r} for r in HELPER_ROLES.get(permission, ["CEO", "COO"])]

        users_result = db_client.table("users").select(
            "user_id, user_name, email, user_rol"
        ).in_("user_rol", role_ids).limit(5).execute()

        return users_result.data or []

    except Exception as e:
        logger.error(f"Error getting users with permission: {e}")
        return []


# ─────────────────────────────────────────────────────────────────────────────
# HELP HANDLER
# ─────────────────────────────────────────────────────────────────────────────

def handle_ngm_help(
    question: str,
    entities: dict = None,
    context: dict = None
) -> dict:
    """
    Handle help/FAQ questions about NGM HUB.

    Returns:
        {
            "text": str,       # Response text
            "action": str,     # "help_response"
            "data": dict       # Additional data (module info, etc.)
        }
    """
    question_lower = question.lower()

    # 1. Check for direct answer in common questions
    direct_answer = find_answer_for_question(question)
    if direct_answer:
        return {
            "text": direct_answer,
            "action": "help_response",
            "data": {"source": "faq"},
        }

    # 2. Extract keywords and find matching feature
    keywords = extract_keywords(question_lower)
    feature = find_feature_by_keywords(keywords)

    if feature:
        response = f"**{feature['feature']}** ({feature['module_name']})\n\n"
        response += f"{feature['how']}\n\n"
        response += f"📍 Puedes encontrarlo en: `{feature['url']}`"

        return {
            "text": response,
            "action": "help_response",
            "data": {
                "source": "feature_match",
                "module": feature["module"],
                "url": feature["url"],
            },
        }

    # 3. Check if asking about a specific module
    for module_id, module in NGM_MODULES.items():
        module_name_lower = module["name"].lower()
        if module_name_lower in question_lower or module_id in question_lower:
            response = f"**{module['name']}**\n\n"
            response += f"{module['description']}\n\n"
            response += f"📍 URL: `{module['url']}`\n\n"

            if module.get("features"):
                response += "**Funcionalidades principales:**\n"
                for feat in module["features"][:5]:
                    response += f"• {feat['name']}\n"

            return {
                "text": response,
                "action": "help_response",
                "data": {
                    "source": "module_info",
                    "module": module_id,
                    "url": module["url"],
                },
            }

    # 4. General help - list modules
    response = "**NGM HUB - Módulos disponibles:**\n\n"
    for module_id, module in NGM_MODULES.items():
        response += f"• **{module['name']}**: {module['description'][:60]}...\n"

    response += "\n¿Sobre cuál módulo te gustaría saber más?"

    return {
        "text": response,
        "action": "help_response",
        "data": {"source": "general"},
    }


def extract_keywords(text: str) -> list[str]:
    """Extract meaningful keywords from text."""
    # Remove common words
    stop_words = {
        "el", "la", "los", "las", "un", "una", "unos", "unas",
        "de", "del", "en", "a", "al", "y", "o", "que", "como",
        "donde", "puedo", "ver", "hay", "esta", "estan", "hacer",
        "the", "a", "an", "in", "on", "at", "to", "for", "of",
        "is", "are", "how", "can", "i", "where", "what",
    }

    words = text.lower().split()
    return [w for w in words if w not in stop_words and len(w) > 2]


# ─────────────────────────────────────────────────────────────────────────────
# ACTION HANDLER
# ─────────────────────────────────────────────────────────────────────────────

async def handle_ngm_action(
    message: str,
    user_id: str = None,
    entities: dict = None,
    context: dict = None,
    db_client=None
) -> dict:
    """
    Handle action requests (navigate, open modal, etc.)
    with permission checking.

    Returns:
        {
            "text": str,           # Response text
            "action": str,         # Action type for frontend
            "data": dict,          # Action parameters
            "requires_redirect": bool,  # If user needs to go to another page
            "permission_denied": bool,  # If user lacks permission
            "helpers": list        # Users who can help (if denied)
        }
    """
    # Find matching action
    action_match = find_action_by_intent(message)

    if not action_match:
        return None  # No action found, let other handlers process

    action_id = action_match["action_id"]
    action_type = action_match["action_type"]
    permission = action_match["permission"]
    target = action_match.get("target")

    # Check permission
    has_permission = True
    helpers = []

    if user_id and db_client:
        has_permission, user_info = await check_user_permission(
            user_id, permission, db_client
        )

        if not has_permission:
            helpers = await get_users_with_permission(permission, db_client)

    if not has_permission:
        # User doesn't have permission
        helper_names = [h.get("user_name", h.get("role", "")) for h in helpers[:3]]
        helper_text = ", ".join(helper_names) if helper_names else "un administrador"

        response = f"⚠️ No tienes acceso para realizar esta acción.\n\n"
        response += f"**{helper_text}** puede ayudarte con esto.\n\n"
        response += "¿Quieres que le envíe un mensaje para solicitarlo?"

        return {
            "text": response,
            "action": "permission_denied",
            "data": {
                "requested_action": action_id,
                "required_permission": permission,
                "helpers": helpers,
            },
            "permission_denied": True,
            "helpers": helpers,
        }

    # User has permission - prepare action response
    if action_type == "navigate":
        return {
            "text": f"Te llevo a {action_match['description']}...",
            "action": "navigate",
            "data": {
                "url": target,
                "action_id": action_id,
            },
        }

    elif action_type == "open_modal":
        required_page = action_match.get("required_page")
        current_page = context.get("current_page") if context else None

        if required_page and current_page and required_page not in current_page:
            # Need to navigate first
            return {
                "text": f"Para {action_match['description'].lower()}, primero necesitas ir a {required_page}.",
                "action": "navigate_then_action",
                "data": {
                    "url": required_page,
                    "then_action": "open_modal",
                    "modal_id": target,
                    "action_id": action_id,
                },
                "requires_redirect": True,
            }

        return {
            "text": f"Abriendo {action_match['description'].lower()}...",
            "action": "open_modal",
            "data": {
                "modal_id": target,
                "action_id": action_id,
            },
        }

    elif action_type == "send_message":
        # Extract target user from message
        target_user = entities.get("user_name") if entities else None

        if not target_user:
            return {
                "text": "¿A quién quieres enviarle un mensaje?",
                "action": "ask_clarification",
                "data": {
                    "missing": "user_name",
                    "action_id": action_id,
                },
            }

        return {
            "text": f"Abriendo chat con {target_user}...",
            "action": "send_message",
            "data": {
                "target_user": target_user,
                "action_id": action_id,
            },
        }

    elif action_type == "create_task":
        # Bug report or task creation
        return await handle_bug_report(message, user_id, context, db_client)

    return None


# ─────────────────────────────────────────────────────────────────────────────
# BUG REPORT HANDLER
# ─────────────────────────────────────────────────────────────────────────────

async def handle_bug_report(
    message: str,
    user_id: str = None,
    context: dict = None,
    db_client=None
) -> dict:
    """
    Handle bug report requests.
    Creates a task in Pipeline Manager.
    """
    # Extract bug details from message
    bug_keywords = ["error", "bug", "problema", "no funciona", "falla", "roto", "crashed"]
    is_bug_report = any(kw in message.lower() for kw in bug_keywords)

    if not is_bug_report:
        return None

    # Check if we have enough details
    if len(message.split()) < 5:
        return {
            "text": "Para reportar el problema, necesito más detalles:\n\n"
                    "1. ¿Qué estabas intentando hacer?\n"
                    "2. ¿Qué pasó exactamente?\n"
                    "3. ¿Qué esperabas que pasara?\n\n"
                    "Cuéntame más y creo un ticket para el equipo técnico.",
            "action": "ask_bug_details",
            "data": {
                "partial_report": message,
            },
        }

    # We have details - offer to create task
    current_page = context.get("current_page", "unknown") if context else "unknown"
    user_name = context.get("user_name", "Usuario") if context else "Usuario"
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")

    task_title = f"[BUG] Reportado por {user_name} - {timestamp}"
    task_description = f"""**Bug reportado via Art**

**Reportado por:** {user_name}
**Página:** {current_page}
**Fecha:** {timestamp}

**Descripción:**
{message}

---
*Este ticket fue creado automáticamente por Art*
"""

    return {
        "text": f"Entendido. Voy a crear un ticket con estos detalles:\n\n"
                f"**Título:** {task_title}\n"
                f"**Descripción:** {message[:100]}...\n\n"
                f"¿Confirmo la creación del ticket?",
        "action": "confirm_bug_report",
        "data": {
            "task_title": task_title,
            "task_description": task_description,
            "task_type": "bug",
            "priority": "high",
            "reported_by": user_id,
            "page": current_page,
        },
    }


async def create_bug_task(
    task_data: dict,
    db_client=None
) -> dict:
    """
    Actually create the bug task in the database.
    """
    if not db_client:
        return {
            "text": "No pude conectar con la base de datos. Por favor, crea el ticket manualmente en Pipeline.",
            "action": "error",
            "error": "no_db_connection",
        }

    try:
        # Create task
        task = {
            "title": task_data["task_title"],
            "description": task_data["task_description"],
            "status": "todo",
            "priority": task_data.get("priority", "medium"),
            "task_type": "bug",
            "created_by": task_data.get("reported_by"),
            "created_at": datetime.now().isoformat(),
            # Assign to development team if known
            # "assigned_to": ["dev_team_user_id"],
        }

        result = db_client.table("pipeline_tasks").insert(task).execute()

        if result.data:
            task_id = result.data[0].get("id")
            return {
                "text": f"✅ Ticket creado exitosamente (ID: {task_id}).\n\n"
                        f"El equipo técnico lo revisará pronto. "
                        f"Puedes ver el estado en Pipeline Manager.",
                "action": "bug_created",
                "data": {
                    "task_id": task_id,
                },
            }

    except Exception as e:
        logger.error(f"Error creating bug task: {e}")

    return {
        "text": "Hubo un error al crear el ticket. Por favor, créalo manualmente en Pipeline.",
        "action": "error",
        "error": str(e),
    }
