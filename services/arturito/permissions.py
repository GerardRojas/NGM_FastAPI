# services/arturito/permissions.py
# ================================
# Sistema de Permisos de Arturito
# ================================
# Configuración centralizada de qué acciones puede ejecutar Arturito
# Incluye permisos basados en roles de usuario
# Permissions persist to agent_config table via arturito_perm_<INTENT> keys

import os
import json
from typing import Dict, Any, List, Optional, Tuple
import logging

logger = logging.getLogger(__name__)

# Prefix used for storing Arturito permissions in agent_config table
_PERM_KEY_PREFIX = "arturito_perm_"


# ================================
# Role-Based Access Control (RBAC)
# ================================
# Define qué roles pueden ejecutar qué acciones
# y a qué equipo delegar si el usuario no tiene permiso

# Equipos y sus roles asociados
TEAMS = {
    "bookkeeping": {
        "name": "Bookkeeping Team",
        "roles": ["Bookkeeper", "Accounting Manager"],
        "description": "Equipo de contabilidad",
    },
    "management": {
        "name": "Management Team",
        "roles": ["CEO", "COO", "KD COO"],
        "description": "Equipo directivo",
    },
    "coordination": {
        "name": "Coordination Team",
        "roles": ["General Coordinator", "Project Coordinator"],
        "description": "Equipo de coordinación",
    },
    "estimating": {
        "name": "Estimating Team",
        "roles": ["Estimator"],
        "description": "Equipo de estimaciones",
    },
    "design": {
        "name": "Design Team",
        "roles": ["Architect"],
        "description": "Equipo de diseño/arquitectura",
    },
    "finance": {
        "name": "Finance Team",
        "roles": ["Financial Analyst"],
        "description": "Equipo de finanzas",
    },
}

# Mapeo de intents a equipos que pueden ejecutarlos
# Si el rol del usuario no está en la lista, se sugiere delegar al equipo responsable
INTENT_ROLE_PERMISSIONS: Dict[str, Dict[str, Any]] = {
    # Expenses - Solo bookkeeping y management
    "SEARCH_EXPENSES": {
        "allowed_roles": ["CEO", "COO", "KD COO", "Bookkeeper", "Accounting Manager", "Financial Analyst", "General Coordinator", "Project Coordinator"],
        "responsible_team": "bookkeeping",
        "action_description": "buscar gastos",
    },
    "EXPENSE_REMINDER": {
        "allowed_roles": ["CEO", "COO", "KD COO", "Bookkeeper", "Accounting Manager", "General Coordinator", "Project Coordinator"],
        "responsible_team": "bookkeeping",
        "action_description": "enviar recordatorios de gastos",
    },

    # Vendors - Bookkeeping y management
    "LIST_VENDORS": {
        "allowed_roles": ["CEO", "COO", "KD COO", "Bookkeeper", "Accounting Manager", "General Coordinator", "Project Coordinator", "Estimator", "Financial Analyst"],
        "responsible_team": "bookkeeping",
        "action_description": "ver vendors",
    },
    "CREATE_VENDOR": {
        "allowed_roles": ["CEO", "COO", "KD COO", "Bookkeeper", "Accounting Manager"],
        "responsible_team": "bookkeeping",
        "action_description": "crear vendors",
    },

    # Projects - Coordination y management
    "LIST_PROJECTS": {
        "allowed_roles": ["CEO", "COO", "KD COO", "Bookkeeper", "Accounting Manager", "General Coordinator", "Project Coordinator", "Estimator", "Financial Analyst", "Architect"],
        "responsible_team": "coordination",
        "action_description": "ver proyectos",
    },
    "CREATE_PROJECT": {
        "allowed_roles": ["CEO", "COO", "KD COO", "General Coordinator", "Project Coordinator"],
        "responsible_team": "coordination",
        "action_description": "crear proyectos",
    },

    # BVA/Reporting - Todos pueden ver, pero algunos roles limitados
    "BUDGET_VS_ACTUALS": {
        "allowed_roles": ["CEO", "COO", "KD COO", "Bookkeeper", "Accounting Manager", "General Coordinator", "Project Coordinator", "Estimator", "Financial Analyst"],
        "responsible_team": "bookkeeping",
        "action_description": "generar reportes BVA",
    },
    "CONSULTA_ESPECIFICA": {
        "allowed_roles": ["CEO", "COO", "KD COO", "Bookkeeper", "Accounting Manager", "General Coordinator", "Project Coordinator", "Estimator", "Financial Analyst"],
        "responsible_team": "bookkeeping",
        "action_description": "consultar datos del BVA",
    },

    # SOW - Estimating y coordination
    "SCOPE_OF_WORK": {
        "allowed_roles": ["CEO", "COO", "KD COO", "General Coordinator", "Project Coordinator", "Estimator", "Architect"],
        "responsible_team": "estimating",
        "action_description": "consultar el Scope of Work",
    },
}


def get_user_role_from_context(context: Dict[str, Any]) -> Optional[str]:
    """
    Extrae el rol del usuario del contexto.

    Args:
        context: Contexto con información del usuario

    Returns:
        Nombre del rol o None
    """
    if not context:
        return None

    # Intentar obtener rol de diferentes fuentes
    user_role = context.get("user_role")
    if user_role:
        return user_role

    # Intentar desde user object
    user = context.get("user", {})
    if isinstance(user, dict):
        return user.get("role") or user.get("user_role")

    return None


def is_role_allowed_for_intent(intent: str, user_role: Optional[str]) -> bool:
    """
    Verifica si un rol específico puede ejecutar un intent.

    Args:
        intent: Nombre del intent
        user_role: Rol del usuario

    Returns:
        True si el rol puede ejecutar el intent
    """
    # Si no hay configuración de rol para este intent, permitir
    role_config = INTENT_ROLE_PERMISSIONS.get(intent)
    if not role_config:
        return True

    # Si no tenemos rol del usuario, permitir (será validado por otros medios)
    if not user_role:
        return True

    # Verificar si el rol está en la lista de permitidos
    allowed_roles = role_config.get("allowed_roles", [])
    return user_role in allowed_roles


def get_delegation_suggestion(intent: str, user_role: Optional[str]) -> Optional[Dict[str, Any]]:
    """
    Si el usuario no tiene permiso para un intent, sugiere a qué equipo delegar.

    Args:
        intent: Nombre del intent
        user_role: Rol del usuario

    Returns:
        Dict con información de delegación o None si no aplica
    """
    role_config = INTENT_ROLE_PERMISSIONS.get(intent)
    if not role_config:
        return None

    # Si el rol está permitido, no hay necesidad de delegar
    if is_role_allowed_for_intent(intent, user_role):
        return None

    # Obtener equipo responsable
    team_key = role_config.get("responsible_team")
    if not team_key or team_key not in TEAMS:
        return None

    team = TEAMS[team_key]
    action_desc = role_config.get("action_description", intent.lower().replace("_", " "))

    return {
        "should_delegate": True,
        "team_key": team_key,
        "team_name": team["name"],
        "team_roles": team["roles"],
        "action_description": action_desc,
        "message": f"Eso parece ser una tarea para el **{team['name']}**. ¿Quieres que les deje un mensaje para solicitarla?",
    }


def check_role_permission(
    intent: str,
    context: Dict[str, Any]
) -> Tuple[bool, Optional[Dict[str, Any]]]:
    """
    Verifica permisos basados en rol y retorna sugerencia de delegación si aplica.

    Args:
        intent: Nombre del intent
        context: Contexto con información del usuario

    Returns:
        Tuple de (is_allowed, delegation_info)
        - is_allowed: True si el usuario puede ejecutar la acción
        - delegation_info: Info de delegación si no tiene permiso, None si sí tiene
    """
    user_role = get_user_role_from_context(context)

    if is_role_allowed_for_intent(intent, user_role):
        return (True, None)

    delegation = get_delegation_suggestion(intent, user_role)
    return (False, delegation)

# ================================
# Configuración de Permisos
# ================================
# Cada permiso tiene:
#   - enabled: Si la acción está habilitada
#   - description: Descripción de la acción
#   - risk_level: low, medium, high (para UI)

ARTURITO_PERMISSIONS: Dict[str, Dict[str, Any]] = {
    # ================================
    # READ Operations (generally safe)
    # ================================
    "LIST_PROJECTS": {
        "enabled": True,
        "description": "Listar proyectos del sistema",
        "risk_level": "low",
        "category": "read",
    },
    "LIST_VENDORS": {
        "enabled": True,
        "description": "Listar vendors/proveedores",
        "risk_level": "low",
        "category": "read",
    },
    "BUDGET_VS_ACTUALS": {
        "enabled": True,
        "description": "Consultar reportes Budget vs Actuals",
        "risk_level": "low",
        "category": "read",
    },
    "CONSULTA_ESPECIFICA": {
        "enabled": True,
        "description": "Consultar categorías específicas del BVA",
        "risk_level": "low",
        "category": "read",
    },
    "SCOPE_OF_WORK": {
        "enabled": True,
        "description": "Consultar Scope of Work",
        "risk_level": "low",
        "category": "read",
    },
    "SEARCH_EXPENSES": {
        "enabled": True,
        "description": "Buscar gastos por criterios (monto, vendor, categoría)",
        "risk_level": "low",
        "category": "read",
    },

    # ================================
    # CREATE Operations (medium risk)
    # ================================
    "CREATE_VENDOR": {
        "enabled": True,
        "description": "Crear nuevos vendors/proveedores",
        "risk_level": "medium",
        "category": "create",
    },
    "CREATE_PROJECT": {
        "enabled": True,
        "description": "Crear nuevos proyectos",
        "risk_level": "medium",
        "category": "create",
    },

    # ================================
    # DELETE Operations (high risk - DISABLED by default)
    # ================================
    "DELETE_VENDOR": {
        "enabled": False,
        "description": "Eliminar vendors (DESHABILITADO)",
        "risk_level": "high",
        "category": "delete",
    },
    "DELETE_PROJECT": {
        "enabled": False,
        "description": "Eliminar proyectos (DESHABILITADO)",
        "risk_level": "high",
        "category": "delete",
    },

    # ================================
    # UPDATE Operations (medium-high risk)
    # ================================
    "UPDATE_VENDOR": {
        "enabled": False,
        "description": "Modificar vendors existentes",
        "risk_level": "medium",
        "category": "update",
    },
    "UPDATE_PROJECT": {
        "enabled": False,
        "description": "Modificar proyectos existentes",
        "risk_level": "medium",
        "category": "update",
    },

    # ================================
    # NOTIFICATION Operations
    # ================================
    "EXPENSE_REMINDER": {
        "enabled": True,
        "description": "Enviar recordatorios de gastos pendientes",
        "risk_level": "medium",
        "category": "notification",
    },
    "REPORT_BUG": {
        "enabled": True,
        "description": "Reportar bugs al sistema",
        "risk_level": "low",
        "category": "notification",
    },

    # ================================
    # NAVIGATION Operations
    # ================================
    "NGM_ACTION": {
        "enabled": True,
        "description": "Navegar y abrir modales en NGM Hub",
        "risk_level": "low",
        "category": "navigation",
    },
    "COPILOT": {
        "enabled": True,
        "description": "Controlar filtros y UI de la página actual",
        "risk_level": "low",
        "category": "navigation",
    },
}


def is_action_permitted(intent: str) -> bool:
    """
    Verifica si una acción está permitida.

    Args:
        intent: Nombre del intent/acción

    Returns:
        True si la acción está permitida, False si no
    """
    permission = ARTURITO_PERMISSIONS.get(intent)

    if permission is None:
        # Si no hay configuración específica, permitir (para intents como GREETING, SMALL_TALK, etc.)
        return True

    return permission.get("enabled", False)


def get_permission_denial_message(intent: str) -> str:
    """
    Retorna un mensaje amigable cuando una acción no está permitida.

    Args:
        intent: Nombre del intent/acción denegada

    Returns:
        Mensaje explicativo
    """
    permission = ARTURITO_PERMISSIONS.get(intent)

    if permission is None:
        return "Esta acción no está configurada."

    action_name = permission.get("description", intent)
    risk_level = permission.get("risk_level", "unknown")

    if risk_level == "high":
        return f"🚫 No puedo ejecutar: **{action_name}**\n\nEsta es una operación de alto riesgo que está deshabilitada por seguridad. Contacta a un administrador si necesitas realizar esta acción."
    elif risk_level == "medium":
        return f"⚠️ No puedo ejecutar: **{action_name}**\n\nEsta operación está deshabilitada. Si necesitas habilitarla, contacta a un administrador."
    else:
        return f"Esta acción no está habilitada: {action_name}"


def get_all_permissions() -> List[Dict[str, Any]]:
    """
    Retorna la lista completa de permisos para mostrar en UI.

    Returns:
        Lista de permisos con su configuración
    """
    permissions = []
    for intent, config in ARTURITO_PERMISSIONS.items():
        permissions.append({
            "intent": intent,
            "enabled": config.get("enabled", False),
            "description": config.get("description", ""),
            "risk_level": config.get("risk_level", "unknown"),
            "category": config.get("category", "other"),
        })

    # Ordenar por categoría y luego por nombre
    permissions.sort(key=lambda x: (x["category"], x["intent"]))
    return permissions


def get_permissions_by_category() -> Dict[str, List[Dict[str, Any]]]:
    """
    Retorna los permisos agrupados por categoría.

    Returns:
        Dict con categorías como keys y lista de permisos como values
    """
    by_category: Dict[str, List[Dict[str, Any]]] = {}

    for intent, config in ARTURITO_PERMISSIONS.items():
        category = config.get("category", "other")
        if category not in by_category:
            by_category[category] = []

        by_category[category].append({
            "intent": intent,
            "enabled": config.get("enabled", False),
            "description": config.get("description", ""),
            "risk_level": config.get("risk_level", "unknown"),
        })

    return by_category


# ================================
# Database Persistence
# ================================
# Permissions are stored in the agent_config table with keys like
# "arturito_perm_LIST_PROJECTS" = "true" or "false".
# Defaults come from ARTURITO_PERMISSIONS dict above.

_db_loaded = False


def _get_supabase():
    """Get Supabase client for permission persistence."""
    try:
        from supabase import create_client
        url = os.getenv("SUPABASE_URL")
        key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
        if url and key:
            return create_client(url, key)
    except Exception as e:
        logger.warning(f"[Permissions] Supabase unavailable: {e}")
    return None


def load_permissions_from_db():
    """
    Load permission overrides from agent_config table.
    Merges DB values into the in-memory ARTURITO_PERMISSIONS dict.
    Only loads once per process (cached via _db_loaded flag).
    """
    global _db_loaded
    if _db_loaded:
        return

    sb = _get_supabase()
    if not sb:
        _db_loaded = True
        return

    try:
        result = sb.table("agent_config") \
            .select("key, value") \
            .like("key", f"{_PERM_KEY_PREFIX}%") \
            .execute()

        if result.data:
            for row in result.data:
                intent = row["key"].replace(_PERM_KEY_PREFIX, "")
                if intent in ARTURITO_PERMISSIONS:
                    ARTURITO_PERMISSIONS[intent]["enabled"] = row["value"].lower() == "true"

            logger.info(f"[Permissions] Loaded {len(result.data)} permission overrides from DB")
    except Exception as e:
        logger.warning(f"[Permissions] Could not load from DB (using defaults): {e}")

    _db_loaded = True


def save_permission_to_db(intent: str, enabled: bool) -> bool:
    """
    Persist a single permission change to agent_config table.
    Uses upsert pattern: SELECT then UPDATE or INSERT.

    Returns True on success.
    """
    sb = _get_supabase()
    if not sb:
        return False

    key = f"{_PERM_KEY_PREFIX}{intent}"
    value = "true" if enabled else "false"

    try:
        # Check if exists
        existing = sb.table("agent_config") \
            .select("key") \
            .eq("key", key) \
            .execute()

        if existing.data and len(existing.data) > 0:
            sb.table("agent_config").update({"value": value}).eq("key", key).execute()
        else:
            sb.table("agent_config").insert({
                "key": key,
                "value": value,
                "description": f"Arturito permission: {intent}",
            }).execute()

        # Also update in-memory
        if intent in ARTURITO_PERMISSIONS:
            ARTURITO_PERMISSIONS[intent]["enabled"] = enabled

        return True
    except Exception as e:
        logger.error(f"[Permissions] Save to DB failed for {intent}: {e}")
        return False


def reset_permissions_to_defaults() -> bool:
    """
    Reset all permissions to hardcoded defaults and persist to DB.
    """
    # Define defaults (same as initial ARTURITO_PERMISSIONS values)
    defaults = {
        "LIST_PROJECTS": True, "LIST_VENDORS": True,
        "BUDGET_VS_ACTUALS": True, "CONSULTA_ESPECIFICA": True,
        "SCOPE_OF_WORK": True, "SEARCH_EXPENSES": True,
        "CREATE_VENDOR": True, "CREATE_PROJECT": True,
        "DELETE_VENDOR": False, "DELETE_PROJECT": False,
        "UPDATE_VENDOR": False, "UPDATE_PROJECT": False,
        "EXPENSE_REMINDER": True, "REPORT_BUG": True,
        "NGM_ACTION": True, "COPILOT": True,
    }

    success = True
    for intent, enabled in defaults.items():
        if intent in ARTURITO_PERMISSIONS:
            ARTURITO_PERMISSIONS[intent]["enabled"] = enabled
        if not save_permission_to_db(intent, enabled):
            success = False

    return success


# Auto-load on module import
load_permissions_from_db()
