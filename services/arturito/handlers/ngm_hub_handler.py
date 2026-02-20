"""
===============================================================================
 NGM HUB Handler for Arturito
===============================================================================
 Handles:
 - Help/FAQ questions about NGM HUB modules
 - Action execution with permission checking
 - Bug reporting and task creation
===============================================================================
"""

import logging
from difflib import SequenceMatcher
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


def _fuzzy_find(query: str, candidates: dict[str, any], threshold: float = 0.55) -> tuple[str | None, any]:
    """
    Fuzzy-match *query* against a dict {name_lower: id}.

    Returns (matched_name, matched_id) or (None, None).
    Tries, in order:
      1. Exact match
      2. Substring (bidirectional)
      3. Token overlap (all query tokens appear in candidate)
      4. SequenceMatcher ratio >= threshold
    """
    q = query.lower().strip()
    if not q:
        return None, None

    # 1 — exact
    if q in candidates:
        return q, candidates[q]

    # 2 — substring (bidirectional)
    for name, id_ in candidates.items():
        if q in name or name in q:
            return name, id_

    # 3 — token overlap: all query tokens present in candidate
    q_tokens = set(q.split())
    for name, id_ in candidates.items():
        name_tokens = set(name.split())
        if q_tokens and q_tokens.issubset(name_tokens):
            return name, id_

    # 4 — SequenceMatcher (best ratio wins)
    best_name, best_id, best_ratio = None, None, 0.0
    for name, id_ in candidates.items():
        ratio = SequenceMatcher(None, q, name).ratio()
        if ratio > best_ratio:
            best_name, best_id, best_ratio = name, id_, ratio

    if best_ratio >= threshold:
        return best_name, best_id

    return None, None


# -----------------------------------------------------------------------------
# PERMISSION CHECKING
# -----------------------------------------------------------------------------
# Adapted to work with NGM Hub's role_permissions schema:
# role_permissions(rol_id, module_key, module_name, module_url, can_view, can_edit, can_delete)
# Joined with rols(rol_id, rol_name)

async def check_user_permission(
    user_id: str,
    permission: str,
    db_client=None
) -> tuple[bool, Optional[dict]]:
    """
    Check if user has a specific permission.

    Permission format: "module:action" (e.g., "expenses:edit", "pipeline:view")
    Actions map to: view -> can_view, edit -> can_edit, delete -> can_delete

    Returns:
        (has_permission, user_info)
    """
    if not db_client:
        # If no DB client, assume permission (will be checked on frontend)
        return True, None

    try:
        # Get user with role
        result = db_client.table("users").select(
            "user_id, user_name, email, role, rol_id"
        ).eq("user_id", user_id).single().execute()

        if not result.data:
            return False, None

        user = result.data
        role_name = user.get("role")
        rol_id = user.get("rol_id")

        # CEO and COO have all permissions
        if role_name in ["CEO", "COO"]:
            return True, user

        # Parse permission string
        parts = permission.split(":")
        module_key = parts[0] if len(parts) > 0 else ""
        action = parts[1] if len(parts) > 1 else "view"

        # Map action to column name
        action_column_map = {
            "view": "can_view",
            "edit": "can_edit",
            "delete": "can_delete",
        }
        column = action_column_map.get(action, "can_view")

        # Query role_permissions for this user's role and module
        if rol_id:
            perm_result = db_client.table("role_permissions").select(
                f"module_key, {column}"
            ).eq("rol_id", rol_id).eq("module_key", module_key).single().execute()

            if perm_result.data:
                has_perm = perm_result.data.get(column, False)
                return has_perm, user

        return False, user

    except Exception as e:
        logger.error(f"Error checking permission: {e}")
        # On error, be permissive to avoid blocking users
        return True, None


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
        # Parse permission string
        parts = permission.split(":")
        module_key = parts[0] if len(parts) > 0 else ""
        action = parts[1] if len(parts) > 1 else "edit"

        # Map action to column name
        action_column_map = {
            "view": "can_view",
            "edit": "can_edit",
            "delete": "can_delete",
        }
        column = action_column_map.get(action, "can_edit")

        # Get role IDs that have this permission
        perm_result = db_client.table("role_permissions").select(
            "rol_id"
        ).eq("module_key", module_key).eq(column, True).execute()

        if not perm_result.data:
            # Fallback to helper roles
            helper_roles = HELPER_ROLES.get(permission, ["CEO", "COO"])
            return [{"role": role} for role in helper_roles]

        rol_ids = [row["rol_id"] for row in perm_result.data]

        # Get users with these role IDs
        users_result = db_client.table("users").select(
            "user_id, user_name, email, role"
        ).in_("rol_id", rol_ids).eq("status", "active").limit(5).execute()

        return users_result.data or []

    except Exception as e:
        logger.error(f"Error getting users with permission: {e}")
        # Fallback to helper roles
        helper_roles = HELPER_ROLES.get(permission, ["CEO", "COO"])
        return [{"role": role} for role in helper_roles]


# -----------------------------------------------------------------------------
# HELP HANDLER
# -----------------------------------------------------------------------------

def handle_ngm_help(
    request: dict,
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
    question = request.get("raw_text", "")
    entities = request.get("entities", {})
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
        response += f"Puedes encontrarlo en: `{feature['url']}`"

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
            response += f"URL: `{module['url']}`\n\n"

            if module.get("features"):
                response += "**Funcionalidades principales:**\n"
                for feat in module["features"][:5]:
                    response += f"- {feat['name']}\n"

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
    response = "**NGM HUB - Modulos disponibles:**\n\n"
    for module_id, module in NGM_MODULES.items():
        response += f"- **{module['name']}**: {module['description'][:60]}...\n"

    response += "\nSobre cual modulo te gustaria saber mas?"

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


# -----------------------------------------------------------------------------
# ACTION HANDLER
# -----------------------------------------------------------------------------

async def handle_ngm_action(
    request: dict,
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
    message = request.get("raw_text", "")
    entities = request.get("entities", {})
    user_id = context.get("user_id") if context else None

    # Find matching action
    action_match = find_action_by_intent(message)

    if not action_match:
        return {
            "text": "No encontre una accion especifica. Puedo ayudarte a navegar o abrir funciones de NGM Hub. Por ejemplo: 'llevame a gastos' o 'agregar un gasto'.",
            "action": "no_action_match",
        }

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

        response = f"No tienes acceso para realizar esta accion.\n\n"
        response += f"**{helper_text}** puede ayudarte con esto.\n\n"
        response += "Quieres que le envie un mensaje para solicitarlo?"

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
                "text": "A quien quieres enviarle un mensaje?",
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
        return await handle_bug_report(request, context, db_client)

    return {
        "text": "Entendido, pero no se como ejecutar esa accion aun.",
        "action": "action_not_implemented",
    }


# -----------------------------------------------------------------------------
# BUG REPORT HANDLER
# -----------------------------------------------------------------------------

async def handle_bug_report(
    request: dict,
    context: dict = None,
    db_client=None
) -> dict:
    """
    Handle bug report requests.
    Creates a task in Pipeline Manager.
    """
    message = request.get("raw_text", "")
    user_id = context.get("user_id") if context else None

    # Check if we have enough details
    if len(message.split()) < 5:
        return {
            "text": "Para reportar el problema, necesito mas detalles:\n\n"
                    "1. Que estabas intentando hacer?\n"
                    "2. Que paso exactamente?\n"
                    "3. Que esperabas que pasara?\n\n"
                    "Cuentame mas y creo un ticket para el equipo tecnico.",
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
    task_description = f"""**Bug reportado via Arturito**

**Reportado por:** {user_name}
**Pagina:** {current_page}
**Fecha:** {timestamp}

**Descripcion:**
{message}

---
*Este ticket fue creado automaticamente por Arturito*
"""

    return {
        "text": f"Entendido. Voy a crear un ticket con estos detalles:\n\n"
                f"**Titulo:** {task_title}\n"
                f"**Descripcion:** {message[:100]}...\n\n"
                f"Confirmo la creacion del ticket?",
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
    Uses the 'tasks' table with the correct schema for NGM Hub Pipeline.
    """
    if not db_client:
        return {
            "text": "No pude conectar con la base de datos. Por favor, crea el ticket manualmente en Pipeline.",
            "action": "error",
            "error": "no_db_connection",
        }

    try:
        # Get the "Not Started" status ID
        status_result = db_client.table("tasks_status").select(
            "task_status_id"
        ).ilike("task_status", "not started").single().execute()

        status_id = status_result.data.get("task_status_id") if status_result.data else None

        # Get high priority ID for bugs
        priority_result = db_client.table("tasks_priority").select(
            "priority_id"
        ).ilike("priority", "high").single().execute()

        priority_id = priority_result.data.get("priority_id") if priority_result.data else None

        # Create task with correct schema
        task = {
            "task_description": task_data["task_title"],
            "task_notes": task_data["task_description"],
            "task_status_id": status_id,
            "task_priority": priority_id,
            "type": "Bug Report",
            "department": "IT",
            "created_at": datetime.now().isoformat(),
        }

        # Add owner if provided
        if task_data.get("reported_by"):
            task["owner_id"] = task_data["reported_by"]

        result = db_client.table("tasks").insert(task).execute()

        if result.data:
            task_id = result.data[0].get("task_id")
            return {
                "text": f"Ticket creado exitosamente (ID: {task_id}).\n\n"
                        f"El equipo tecnico lo revisara pronto. "
                        f"Puedes ver el estado en Pipeline Manager.",
                "action": "bug_created",
                "data": {
                    "task_id": task_id,
                },
            }

    except Exception as e:
        logger.error(f"Error creating bug task: {e}")
        return {
            "text": "Hubo un error al crear el ticket. Por favor, crealo manualmente en Pipeline.",
            "action": "error",
            "error": str(e),
        }

    return {
        "text": "Hubo un error inesperado. Por favor, crea el ticket manualmente en Pipeline.",
        "action": "error",
    }


# -----------------------------------------------------------------------------
# LIST PROJECTS HANDLER
# -----------------------------------------------------------------------------

def handle_list_projects(
    request: dict,
    context: dict = None
) -> dict:
    """
    Handle requests to list all projects.

    Returns a formatted list of projects with their status.
    """
    from api.supabase_client import supabase

    try:
        # Query projects with related data (same as projects router)
        resp = (
            supabase
            .table("projects")
            .select(
                """
                project_id,
                project_name,
                status,
                city,
                project_status(status),
                companies(name)
                """
            )
            .order("project_name")
            .limit(50)
            .execute()
        )

        raw_projects = resp.data or []

        if not raw_projects:
            return {
                "text": "No encontré proyectos registrados en el sistema.",
                "action": "list_projects",
                "data": {"projects": [], "count": 0},
            }

        # Process projects
        projects = []
        for row in raw_projects:
            # Extract status name from relation
            status_rel = row.get("project_status")
            status_name = None
            if isinstance(status_rel, dict):
                status_name = status_rel.get("status")
            elif isinstance(status_rel, list) and status_rel:
                status_name = status_rel[0].get("status")

            # Extract company name from relation
            company_rel = row.get("companies")
            company_name = None
            if isinstance(company_rel, dict):
                company_name = company_rel.get("name")
            elif isinstance(company_rel, list) and company_rel:
                company_name = company_rel[0].get("name")

            projects.append({
                "project_id": row.get("project_id"),
                "name": row.get("project_name"),
                "status": status_name or "Sin estado",
                "city": row.get("city") or "",
                "company": company_name or "",
            })

        # Build response text
        response = f"**Proyectos ({len(projects)})**\n\n"

        for p in projects:
            status_emoji = "🟢" if p["status"] and "active" in p["status"].lower() else "⚪"
            city_text = f" - {p['city']}" if p["city"] else ""
            response += f"{status_emoji} **{p['name']}**{city_text}\n"
            if p["status"]:
                response += f"   Status: {p['status']}\n"

        response += f"\n*Total: {len(projects)} proyectos*"

        return {
            "text": response,
            "action": "list_projects",
            "data": {
                "projects": projects,
                "count": len(projects),
            },
        }

    except Exception as e:
        logger.error(f"Error listing projects: {e}")
        return {
            "text": f"Hubo un error al obtener los proyectos: {str(e)}",
            "action": "error",
            "error": str(e),
        }


# -----------------------------------------------------------------------------
# LIST VENDORS HANDLER
# -----------------------------------------------------------------------------

def handle_list_vendors(
    request: dict,
    context: dict = None
) -> dict:
    """
    Handle requests to list all vendors.

    Returns a formatted list of vendors.
    """
    from api.supabase_client import supabase

    try:
        # Query vendors ordered by name
        resp = (
            supabase
            .table("Vendors")
            .select("id, vendor_name")
            .order("vendor_name")
            .limit(100)
            .execute()
        )

        vendors = resp.data or []

        if not vendors:
            return {
                "text": "No hay vendors registrados en el sistema.",
                "action": "list_vendors",
                "data": {"vendors": [], "count": 0},
            }

        # Build response text
        response = f"**Vendors ({len(vendors)})**\n\n"

        for v in vendors:
            response += f"• {v['vendor_name']}\n"

        response += f"\n*Total: {len(vendors)} vendors*"

        return {
            "text": response,
            "action": "list_vendors",
            "data": {
                "vendors": vendors,
                "count": len(vendors),
            },
        }

    except Exception as e:
        logger.error(f"Error listing vendors: {e}")
        return {
            "text": f"Hubo un error al obtener los vendors: {str(e)}",
            "action": "error",
            "error": str(e),
        }


# -----------------------------------------------------------------------------
# CREATE VENDOR HANDLER
# -----------------------------------------------------------------------------

def handle_create_vendor(
    request: dict,
    context: dict = None
) -> dict:
    """
    Handle requests to create a new vendor.

    Expects 'vendor_name' in entities.
    """
    from api.supabase_client import supabase

    entities = request.get("entities", {})
    vendor_name = entities.get("vendor_name", "").strip()

    if not vendor_name:
        return {
            "text": "Necesito el nombre del vendor. Ejemplo: *agregar vendor Home Depot*",
            "action": "missing_vendor_name",
        }

    try:
        # Check if vendor already exists
        existing = (
            supabase
            .table("Vendors")
            .select("id, vendor_name")
            .ilike("vendor_name", vendor_name)
            .execute()
        )

        if existing.data:
            return {
                "text": f"Ya existe un vendor con el nombre **{existing.data[0]['vendor_name']}**.",
                "action": "vendor_exists",
                "data": {"existing_vendor": existing.data[0]},
            }

        # Create the vendor
        result = (
            supabase
            .table("Vendors")
            .insert({"vendor_name": vendor_name})
            .execute()
        )

        if result.data:
            new_vendor = result.data[0]
            return {
                "text": f"✅ Vendor **{vendor_name}** creado exitosamente.",
                "action": "vendor_created",
                "data": {"vendor": new_vendor},
            }
        else:
            return {
                "text": "No se pudo crear el vendor. Intenta de nuevo.",
                "action": "error",
            }

    except Exception as e:
        logger.error(f"Error creating vendor: {e}")
        return {
            "text": f"Hubo un error al crear el vendor: {str(e)}",
            "action": "error",
            "error": str(e),
        }


# -----------------------------------------------------------------------------
# CREATE PROJECT HANDLER
# -----------------------------------------------------------------------------

def handle_create_project(
    request: dict,
    context: dict = None
) -> dict:
    """
    Handle requests to create a new project.

    Expects 'project_name' in entities.
    Since source_company is required, we'll use a default or ask user.
    """
    from api.supabase_client import supabase
    import uuid

    entities = request.get("entities", {})
    project_name = entities.get("project_name", "").strip()

    if not project_name:
        return {
            "text": "Necesito el nombre del proyecto. Ejemplo: *crear proyecto Del Rio*",
            "action": "missing_project_name",
        }

    try:
        # Check if project already exists
        existing = (
            supabase
            .table("projects")
            .select("project_id, project_name")
            .ilike("project_name", project_name)
            .execute()
        )

        if existing.data:
            return {
                "text": f"Ya existe un proyecto con el nombre **{existing.data[0]['project_name']}**.",
                "action": "project_exists",
                "data": {"existing_project": existing.data[0]},
            }

        # Get default company (first one, or NGM)
        companies = (
            supabase
            .table("companies")
            .select("id, name")
            .limit(1)
            .execute()
        )

        if not companies.data:
            return {
                "text": "No hay compañías registradas en el sistema. Necesitas crear una compañía primero.",
                "action": "no_companies",
            }

        default_company = companies.data[0]

        # Get default status (first active status)
        statuses = (
            supabase
            .table("project_status")
            .select("status_id, status")
            .limit(1)
            .execute()
        )

        default_status = statuses.data[0]["status_id"] if statuses.data else None

        # Create the project
        project_data = {
            "project_id": str(uuid.uuid4()),
            "project_name": project_name,
            "source_company": default_company["id"],
            "status": default_status,
        }

        result = (
            supabase
            .table("projects")
            .insert(project_data)
            .execute()
        )

        if result.data:
            new_project = result.data[0]
            return {
                "text": f"✅ Proyecto **{project_name}** creado exitosamente.\n\nCompañía: {default_company['name']}\n\nPuedes editar los detalles del proyecto en la sección de Projects.",
                "action": "project_created",
                "data": {"project": new_project},
            }
        else:
            return {
                "text": "No se pudo crear el proyecto. Intenta de nuevo.",
                "action": "error",
            }

    except Exception as e:
        logger.error(f"Error creating project: {e}")
        return {
            "text": f"Hubo un error al crear el proyecto: {str(e)}",
            "action": "error",
            "error": str(e),
        }


# -----------------------------------------------------------------------------
# SEARCH EXPENSES HANDLER
# -----------------------------------------------------------------------------

def handle_search_expenses(
    request: dict,
    context: dict = None
) -> dict:
    """
    Handle requests to search for expenses.

    Supports searching by:
    - amount: Approximate amount (will search +/- 10%)
    - vendor: Vendor name (partial match)
    - category: Account/category name (partial match)
    - project: Project name (partial match)

    Example queries:
    - "busca un expense de 1000 dlls que se le pagó a Home Depot para rough framing"
    - "encuentra el gasto de $500 a Lowes"
    - "gastos de electrical en Del Rio"
    """
    from api.supabase_client import supabase

    entities = request.get("entities", {})
    amount = entities.get("amount")
    vendor_name = entities.get("vendor")
    category = entities.get("category")
    project_name = entities.get("project")

    # Need at least one search criteria
    if not any([amount, vendor_name, category, project_name]):
        return {
            "text": "Necesito al menos un criterio de búsqueda. Puedes buscar por:\n"
                    "• **Monto**: _busca gasto de $1000_\n"
                    "• **Vendor**: _gasto pagado a Home Depot_\n"
                    "• **Categoría**: _gastos de electrical_\n"
                    "• **Proyecto**: _gastos en Del Rio_",
            "action": "missing_search_criteria",
        }

    try:
        # Build search criteria description
        criteria_parts = []
        if amount:
            criteria_parts.append(f"monto ~${amount:,.2f}")
        if vendor_name:
            criteria_parts.append(f"vendor '{vendor_name}'")
        if category:
            criteria_parts.append(f"categoría '{category}'")
        if project_name:
            criteria_parts.append(f"proyecto '{project_name}'")

        criteria_desc = ", ".join(criteria_parts)

        # Get vendors map for name matching
        vendors_resp = supabase.table("Vendors").select("id, vendor_name").execute()
        vendors_map = {v["id"]: v["vendor_name"] for v in (vendors_resp.data or [])}
        vendors_by_name = {v["vendor_name"].lower(): v["id"] for v in (vendors_resp.data or [])}

        # Get projects map for name matching
        projects_resp = supabase.table("projects").select("project_id, project_name").execute()
        projects_map = {p["project_id"]: p["project_name"] for p in (projects_resp.data or [])}
        projects_by_name = {p["project_name"].lower(): p["project_id"] for p in (projects_resp.data or [])}

        # Get accounts map for category matching
        accounts_resp = supabase.table("accounts").select("account_id, Name").execute()
        accounts_map = {a["account_id"]: a["Name"] for a in (accounts_resp.data or [])}

        # Start building query
        query = supabase.table("expenses_manual_COGS").select("*")

        # Filter by vendor if specified (fuzzy match)
        matched_vendor_id = None
        if vendor_name:
            _, matched_vendor_id = _fuzzy_find(vendor_name, vendors_by_name)
            if matched_vendor_id:
                query = query.eq("vendor_id", matched_vendor_id)

        # Filter by project if specified (fuzzy match)
        matched_project_id = None
        if project_name:
            _, matched_project_id = _fuzzy_find(project_name, projects_by_name)
            if matched_project_id:
                query = query.eq("project", matched_project_id)

        # Filter by amount range if specified (+/- 15%)
        if amount:
            min_amount = amount * 0.85
            max_amount = amount * 1.15
            query = query.gte("Amount", min_amount).lte("Amount", max_amount)

        # Execute query
        query = query.order("TxnDate", desc=True).limit(20)
        resp = query.execute()
        expenses = resp.data or []

        # Post-filter by category/account name if specified
        if category and expenses:
            category_lower = category.lower()
            filtered = []
            for exp in expenses:
                account_id = exp.get("account_id")
                if account_id:
                    account_name = accounts_map.get(account_id, "")
                    if category_lower in account_name.lower():
                        filtered.append(exp)
                # Also check LineDescription
                desc = exp.get("LineDescription", "") or ""
                if category_lower in desc.lower():
                    if exp not in filtered:
                        filtered.append(exp)
            expenses = filtered

        if not expenses:
            return {
                "text": f"No encontré gastos con los criterios: {criteria_desc}",
                "action": "no_results",
                "data": {"criteria": entities},
            }

        # Format results
        response = f"**Encontré {len(expenses)} gasto(s)** ({criteria_desc})\n\n"

        for i, exp in enumerate(expenses[:10], 1):  # Limit to 10 results
            amount_val = exp.get("Amount", 0)
            date = exp.get("TxnDate", "")[:10] if exp.get("TxnDate") else "Sin fecha"
            vendor = vendors_map.get(exp.get("vendor_id"), "Sin vendor")
            project = projects_map.get(exp.get("project"), "Sin proyecto")
            account = accounts_map.get(exp.get("account_id"), "")
            desc = exp.get("LineDescription", "") or ""

            response += f"**{i}.** ${amount_val:,.2f} - {vendor}\n"
            response += f"   📅 {date} | 📁 {project}\n"
            if account:
                response += f"   📂 {account}\n"
            if desc:
                response += f"   📝 {desc[:50]}{'...' if len(desc) > 50 else ''}\n"
            response += "\n"

        if len(expenses) > 10:
            response += f"_...y {len(expenses) - 10} más_"

        return {
            "text": response,
            "action": "search_expenses_results",
            "data": {
                "criteria": entities,
                "count": len(expenses),
                "expenses": expenses[:10],  # Return first 10
            },
        }

    except Exception as e:
        logger.error(f"Error searching expenses: {e}")
        return {
            "text": f"Hubo un error al buscar gastos: {str(e)}",
            "action": "error",
            "error": str(e),
        }


# -----------------------------------------------------------------------------
# EXPENSE AUTHORIZATION REMINDER
# -----------------------------------------------------------------------------

async def handle_expense_reminder(
    request: dict,
    context: dict = None,
    db_client=None
) -> dict:
    """
    Handle requests to send reminders about pending expenses to authorizers.

    Triggered by messages like:
    - "Tenemos muchos gastos sin autorizar"
    - "Recuerdale a los autorizadores que hay gastos pendientes"
    - "Enviar recordatorio de gastos"

    Returns:
        Dict with response text and notification results
    """
    from api.services.firebase_notifications import notify_expense_authorizers, get_supabase

    message = request.get("raw_text", "")
    user_name = "Alguien"
    pending_count = 0
    project_name = None

    # Get user name from context
    if context:
        user_name = context.get("user_name", "Alguien")

    # Try to get pending expense count from database
    try:
        if db_client:
            supabase = db_client
        else:
            supabase = get_supabase()

        # Count pending expenses (those with auth_status = 'Pending' or similar)
        result = supabase.table("expenses") \
            .select("expense_id", count="exact") \
            .eq("auth_status", "Pending") \
            .execute()

        pending_count = result.count if hasattr(result, 'count') else len(result.data or [])

    except Exception as e:
        logger.warning(f"Could not get pending expense count: {e}")
        pending_count = 0

    # Send notifications
    try:
        result = await notify_expense_authorizers(
            sender_name=user_name,
            pending_count=pending_count,
            message=message,
            project_name=project_name
        )

        if result["success"]:
            notified_users = result.get("notified_users", [])
            user_list = ", ".join(notified_users[:5])
            if len(notified_users) > 5:
                user_list += f" y {len(notified_users) - 5} más"

            count_text = f"Hay **{pending_count} gastos** pendientes de autorización. " if pending_count > 0 else ""

            response = f"📬 ¡Recordatorio enviado!\n\n"
            response += count_text
            response += f"Notifiqué a **{result['notified_count']}** autorizador(es): {user_list}.\n\n"
            response += "Recibirán una notificación push en sus dispositivos."

            return {
                "text": response,
                "action": "expense_reminder_sent",
                "data": {
                    "notified_count": result["notified_count"],
                    "pending_expenses": pending_count,
                    "notified_users": notified_users,
                },
            }
        else:
            return {
                "text": "No pude enviar el recordatorio. No encontré autorizadores con notificaciones activas.",
                "action": "expense_reminder_failed",
                "data": result,
            }

    except Exception as e:
        logger.error(f"Error sending expense reminder: {e}")
        return {
            "text": f"Hubo un error al enviar el recordatorio: {str(e)}",
            "action": "error",
            "error": str(e),
        }
