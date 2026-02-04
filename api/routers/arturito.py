# api/routers/arturito.py
# ================================
# ARTURITO - NGM Chat Bot Backend
# ================================
# Entry point para mensajes desde Google Chat y NGM HUB Web.
# Usa OpenAI Assistants API para memoria contextual eficiente.

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional, Dict, Any, List
from datetime import datetime
import re
import json
import time

# Importar el engine de Arturito
from services.arturito import (
    interpret_message,
    route,
    route_slash_command,
    set_personality_level,
)
from services.arturito.handlers.info_handler import get_system_status
from services.arturito.assistants import (
    send_message_and_get_response,
    clear_thread,
    get_thread_id,
)
from services.arturito.failed_commands_logger import (
    get_failed_commands,
    get_failed_commands_stats,
)

router = APIRouter(prefix="/arturito", tags=["arturito"])


# ================================
# MODELS
# ================================

class ChatMessage(BaseModel):
    """Mensaje entrante desde Google Chat"""
    text: str
    user_name: Optional[str] = None
    user_email: Optional[str] = None
    space_name: Optional[str] = None
    space_id: Optional[str] = None
    thread_id: Optional[str] = None
    is_mention: Optional[bool] = False


class SlashCommand(BaseModel):
    """Slash command desde Google Chat"""
    command: str
    args: Optional[str] = None
    user_name: Optional[str] = None
    user_email: Optional[str] = None
    space_name: Optional[str] = None
    space_id: Optional[str] = None


class BotResponse(BaseModel):
    """Respuesta del bot"""
    text: str
    action: Optional[str] = None
    data: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    card: Optional[Dict[str, Any]] = None
    thread_id: Optional[str] = None  # Thread ID for Assistants API


class WebChatMessage(BaseModel):
    """Mensaje desde la interfaz web de NGM HUB"""
    text: str
    user_name: Optional[str] = None
    user_email: Optional[str] = None
    user_role: Optional[str] = None  # Rol del usuario para control de permisos
    session_id: Optional[str] = None
    personality_level: Optional[int] = 3
    thread_id: Optional[str] = None  # Optional: client can provide existing thread
    current_page: Optional[str] = None  # Page user is on (e.g., "expenses.html") for copilot context


# ================================
# ENDPOINTS
# ================================

@router.post("/message", response_model=BotResponse)
async def receive_message(message: ChatMessage):
    """
    Endpoint principal que recibe mensajes desde Google Chat.
    """
    try:
        text = message.text.strip()

        if not text:
            return BotResponse(
                text="No recibí ningún mensaje. ¿En qué puedo ayudarte?",
                action="empty_message"
            )

        context = {
            "user_name": message.user_name,
            "user_email": message.user_email,
            "space_name": message.space_name,
            "space_id": message.space_id or message.space_name or "default",
            "thread_id": message.thread_id,
            "is_mention": message.is_mention,
        }

        # Detectar slash commands
        slash_match = re.match(r'^/(\w+)\s*(.*)?$', text)
        if slash_match:
            command = slash_match.group(1)
            args = (slash_match.group(2) or "").strip()
            result = route_slash_command(command, args, context)
            return BotResponse(
                text=result.get("text", ""),
                action=result.get("action"),
                data=result.get("data"),
                card=result.get("card")
            )

        # Limpiar mención del bot
        clean_text = re.sub(r'^@?\s*arturito[,:]?\s*', '', text, flags=re.IGNORECASE).strip()
        if not clean_text:
            clean_text = text

        # Interpretar y rutear
        intent_result = interpret_message(clean_text, context)
        response = route(intent_result, context)

        return BotResponse(
            text=response.get("text", ""),
            action=response.get("action"),
            data=response.get("data"),
            card=response.get("card"),
            error=response.get("error")
        )

    except Exception as e:
        return BotResponse(
            text="⚠️ Ocurrió un error procesando tu mensaje.",
            action="error",
            error=str(e)
        )


@router.post("/slash", response_model=BotResponse)
async def receive_slash_command(command: SlashCommand):
    """Endpoint para slash commands de Google Chat"""
    try:
        context = {
            "user_name": command.user_name,
            "user_email": command.user_email,
            "space_name": command.space_name,
            "space_id": command.space_id or command.space_name or "default",
        }

        result = route_slash_command(command.command, command.args or "", context)

        return BotResponse(
            text=result.get("text", ""),
            action=result.get("action"),
            data=result.get("data"),
            card=result.get("card")
        )

    except Exception as e:
        return BotResponse(
            text=f"⚠️ Error ejecutando /{command.command}",
            action="error",
            error=str(e)
        )


@router.get("/health")
async def health_check():
    """Health check para verificar que el bot está activo"""
    status = get_system_status()
    return {
        "status": "online",
        "timestamp": datetime.utcnow().isoformat(),
        "assistants_api": True,
        **status
    }


# ================================
# WEB CHAT ENDPOINT (Assistants API)
# ================================

@router.post("/web-chat", response_model=BotResponse)
async def web_chat(message: WebChatMessage):
    """
    Endpoint para el chat web de NGM HUB usando OpenAI Assistants API.

    Ventajas sobre el método anterior:
    - No necesita enviar historial completo cada vez
    - OpenAI mantiene el contexto en el thread
    - Menor costo y latencia
    - Memoria ilimitada (no solo 10 mensajes)

    Request body:
    {
        "text": "¿Cuál es el estado del proyecto Del Rio?",
        "user_name": "Juan",
        "session_id": "web_123456",
        "personality_level": 3,
        "thread_id": "thread_abc123"  // opcional, para continuar conversación
    }

    Response incluye thread_id para que el cliente lo guarde y reutilice.
    """
    try:
        text = message.text.strip()

        if not text:
            return BotResponse(
                text="No recibí ningún mensaje. ¿En qué puedo ayudarte?",
                action="empty_message"
            )

        session_id = message.session_id or "web_default"
        context = {
            "user_name": message.user_name,
            "user_email": message.user_email,
            "user_role": message.user_role,  # Rol para control de permisos
            "space_name": "NGM HUB Web",
            "space_id": session_id,
            "is_mention": True,
            "current_page": message.current_page,  # Page context for copilot commands
        }

        # Establecer personalidad
        personality_level = message.personality_level or 3
        set_personality_level(personality_level, session_id)

        # Detectar slash commands
        slash_match = re.match(r'^/(\w+)\s*(.*)?$', text)
        if slash_match:
            command = slash_match.group(1)
            args = (slash_match.group(2) or "").strip()
            result = route_slash_command(command, args, context)
            return BotResponse(
                text=result.get("text", ""),
                action=result.get("action"),
                data=result.get("data"),
                thread_id=message.thread_id or get_thread_id(session_id)
            )

        # Interpretar mensaje
        intent_result = interpret_message(text, context)

        # Para intents conversacionales, usar Assistants API
        if intent_result.get("intent") in ["SMALL_TALK", "GREETING", "UNKNOWN"]:
            response_text, thread_id, error = send_message_and_get_response(
                session_id=session_id,
                message=text,
                personality_level=personality_level,
                user_name=message.user_name,
                thread_id=message.thread_id
            )

            return BotResponse(
                text=response_text,
                action="chat_response",
                thread_id=thread_id,
                error=error
            )

        # Para otros intents (BVA, SOW, etc.), rutear al handler
        response = route(intent_result, context)

        return BotResponse(
            text=response.get("text", ""),
            action=response.get("action"),
            data=response.get("data"),
            error=response.get("error"),
            thread_id=message.thread_id or get_thread_id(session_id)
        )

    except Exception as e:
        return BotResponse(
            text="⚠️ Ocurrió un error procesando tu mensaje. Por favor intenta de nuevo.",
            action="error",
            error=str(e)
        )


@router.post("/clear-thread")
async def clear_conversation(session_id: str):
    """
    Limpia el thread de conversación y crea uno nuevo.
    Útil cuando el usuario quiere empezar de cero.
    """
    try:
        new_thread_id, error = clear_thread(session_id)

        if error:
            raise HTTPException(status_code=500, detail=error)

        return {
            "success": True,
            "message": "Conversación limpiada",
            "thread_id": new_thread_id
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/personality")
async def set_personality(level: int, space_id: str = "default"):
    """Endpoint para cambiar la personalidad del bot"""
    if level < 1 or level > 5:
        raise HTTPException(status_code=400, detail="Level must be between 1 and 5")

    result = set_personality_level(level, space_id)
    return result


@router.get("/personality/{space_id}")
async def get_personality(space_id: str = "default"):
    """Obtiene la configuración de personalidad actual"""
    from services.arturito.persona import get_personality_level, get_profile

    level = get_personality_level(space_id)
    profile = get_profile(level)

    return {
        "space_id": space_id,
        "level": level,
        "title": profile["title"],
        "emoji": profile["emoji"]
    }


# ================================
# WEBHOOK PARA GOOGLE CHAT
# ================================

@router.post("/webhook")
async def google_chat_webhook(payload: Dict[str, Any]):
    """
    Webhook compatible con el formato de Google Chat.
    """
    try:
        event_type = payload.get("type", "").upper()
        space = payload.get("space", {})
        message = payload.get("message", {})
        user = payload.get("user", {})

        space_name = space.get("displayName") or space.get("name") or "default"
        space_id = space.get("name") or space_name
        user_name = user.get("displayName") or user.get("name") or "Usuario"
        user_email = user.get("email")

        if event_type == "ADDED_TO_SPACE":
            return {
                "text": f"🤖 ¡Hola {user_name}! Soy Arturito y estoy listo para ayudar en {space_name}."
            }

        if event_type == "MESSAGE":
            text = message.get("argumentText") or message.get("text") or ""
            annotations = message.get("annotations", [])
            is_mention = any(a.get("type") == "USER_MENTION" for a in annotations)

            chat_message = ChatMessage(
                text=text.strip(),
                user_name=user_name,
                user_email=user_email,
                space_name=space_name,
                space_id=space_id,
                is_mention=is_mention
            )

            response = await receive_message(chat_message)
            result = {"text": response.text}

            if response.card:
                result["cardsV2"] = [response.card]

            return result

        return {"text": ""}

    except Exception as e:
        return {"text": f"⚠️ Error: {str(e)}"}


# ================================
# PERMISSIONS CONTROL PANEL
# ================================

@router.get("/permissions")
async def get_permissions():
    """
    Obtiene la configuración actual de permisos de Arturito.
    Útil para mostrar en un panel de control.
    """
    from services.arturito.permissions import get_all_permissions, get_permissions_by_category

    return {
        "permissions": get_all_permissions(),
        "by_category": get_permissions_by_category(),
    }


class PermissionUpdate(BaseModel):
    """Modelo para actualizar un permiso"""
    intent: str
    enabled: bool


@router.patch("/permissions")
async def update_permission(update: PermissionUpdate):
    """
    Actualiza un permiso específico de Arturito.

    Body:
    {
        "intent": "CREATE_VENDOR",
        "enabled": true
    }
    """
    from services.arturito.permissions import ARTURITO_PERMISSIONS

    intent = update.intent.upper()

    if intent not in ARTURITO_PERMISSIONS:
        raise HTTPException(
            status_code=404,
            detail=f"Permission '{intent}' not found"
        )

    # Actualizar el permiso en memoria
    ARTURITO_PERMISSIONS[intent]["enabled"] = update.enabled

    return {
        "success": True,
        "message": f"Permission '{intent}' updated",
        "intent": intent,
        "enabled": update.enabled,
    }


@router.post("/permissions/reset")
async def reset_permissions():
    """
    Resetea todos los permisos a sus valores por defecto.
    """
    from services.arturito.permissions import ARTURITO_PERMISSIONS

    # Valores por defecto
    defaults = {
        "LIST_PROJECTS": True,
        "LIST_VENDORS": True,
        "BUDGET_VS_ACTUALS": True,
        "CONSULTA_ESPECIFICA": True,
        "SCOPE_OF_WORK": True,
        "SEARCH_EXPENSES": True,
        "CREATE_VENDOR": True,
        "CREATE_PROJECT": True,
        "DELETE_VENDOR": False,
        "DELETE_PROJECT": False,
        "UPDATE_VENDOR": False,
        "UPDATE_PROJECT": False,
        "EXPENSE_REMINDER": True,
        "REPORT_BUG": True,
        "NGM_ACTION": True,
        "COPILOT": True,
    }

    for intent, enabled in defaults.items():
        if intent in ARTURITO_PERMISSIONS:
            ARTURITO_PERMISSIONS[intent]["enabled"] = enabled

    return {
        "success": True,
        "message": "Permissions reset to defaults",
    }


# ================================
# TASK DELEGATION
# ================================

class DelegationRequest(BaseModel):
    """Modelo para solicitar delegación de tarea a otro equipo"""
    team_key: str
    action_description: str
    original_request: Optional[str] = None
    user_name: Optional[str] = None
    user_email: Optional[str] = None
    session_id: Optional[str] = None


@router.post("/delegate-task")
async def delegate_task(request: DelegationRequest):
    """
    Envía una solicitud de tarea a otro equipo.
    Por ahora, simula el envío (en producción se integraría con
    sistema de mensajes, Pipeline, o notificaciones).
    """
    from services.arturito.permissions import TEAMS

    team = TEAMS.get(request.team_key)
    if not team:
        raise HTTPException(status_code=404, detail=f"Team '{request.team_key}' not found")

    team_name = team["name"]
    team_roles = team["roles"]

    # TODO: En producción, aquí se enviaría:
    # 1. Notificación push a usuarios con esos roles
    # 2. Crear tarea en Pipeline
    # 3. Enviar email al equipo
    # 4. Crear mensaje interno

    # Por ahora, retornamos un mensaje de confirmación
    return {
        "success": True,
        "text": f"He enviado tu solicitud al **{team_name}**.\n\nLes notifiqué que necesitas ayuda para: _{request.action_description}_\n\nTe avisarán cuando esté lista.",
        "team": {
            "key": request.team_key,
            "name": team_name,
            "roles": team_roles,
        },
        "request": {
            "action": request.action_description,
            "original_text": request.original_request,
            "requested_by": request.user_name,
        }
    }


# ================================
# FAILED COMMANDS ANALYTICS
# ================================

class FailedCommandsQuery(BaseModel):
    """Query parameters for failed commands"""
    page: int = 1
    page_size: int = 50
    current_page: Optional[str] = None
    error_reason: Optional[str] = None
    days_back: int = 30


@router.get("/failed-commands")
async def get_failed_commands_endpoint(
    page: int = 1,
    page_size: int = 50,
    current_page: Optional[str] = None,
    error_reason: Optional[str] = None,
    days_back: int = 30,
    user_id: Optional[str] = None  # Admin can filter by user
):
    """
    Get failed copilot commands for analytics.

    Query params:
    - page: Page number (default: 1)
    - page_size: Results per page (default: 50, max: 100)
    - current_page: Filter by page (e.g., 'expenses.html')
    - error_reason: Filter by error reason
    - days_back: How many days back to look (default: 30)
    - user_id: Filter by user (admin only, optional)

    Returns paginated list of failed commands with user info.
    """
    try:
        # TODO: Add auth check - verify user has permission to view failed commands
        # For now, user can only see their own failures unless they're admin

        # Get supabase client from app state or dependency injection
        # This is a placeholder - actual implementation will depend on your auth setup
        from api.db import get_supabase_client
        supabase = get_supabase_client()

        # Limit page size
        page_size = min(page_size, 100)

        result = await get_failed_commands(
            supabase=supabase,
            user_id=user_id,
            page=page,
            page_size=page_size,
            current_page=current_page,
            error_reason=error_reason,
            days_back=days_back
        )

        return result

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ================================
# INTENT INTERPRETER (GPT-first architecture)
# ================================

class IntentRequest(BaseModel):
    """Request for GPT intent interpretation"""
    text: str
    current_page: Optional[str] = "dashboard.html"


class IntentResponse(BaseModel):
    """Structured intent response from GPT"""
    type: str  # copilot, navigate, modal, report, chat
    action: Optional[str] = None
    params: Optional[Dict[str, Any]] = None


# --- Entity cache for interpret-intent (projects + vendors) ---

_entity_cache = {
    "projects": {"data": [], "ts": 0},
    "vendors": {"data": [], "ts": 0},
    "account_groups": {"data": [], "ts": 0},
}
_ENTITY_CACHE_TTL = 300  # 5 minutes


def _get_cached_entities():
    """Fetch and cache project/vendor/account-group names from Supabase with 5-min TTL."""
    now = time.time()
    p_cache = _entity_cache["projects"]
    v_cache = _entity_cache["vendors"]
    g_cache = _entity_cache["account_groups"]

    needs_refresh = (
        (now - p_cache["ts"] > _ENTITY_CACHE_TTL)
        or (now - v_cache["ts"] > _ENTITY_CACHE_TTL)
        or (now - g_cache["ts"] > _ENTITY_CACHE_TTL)
    )

    if needs_refresh:
        try:
            from api.supabase_client import supabase

            p_resp = supabase.table("projects").select("project_name").execute()
            p_cache["data"] = sorted(set(
                p.get("project_name") for p in (p_resp.data or []) if p.get("project_name")
            ))
            p_cache["ts"] = now

            v_resp = supabase.table("Vendors").select("vendor_name").execute()
            v_cache["data"] = sorted(set(
                v.get("vendor_name") for v in (v_resp.data or []) if v.get("vendor_name")
            ))
            v_cache["ts"] = now

            a_resp = supabase.table("accounts").select("AccountCategory").execute()
            g_cache["data"] = sorted(set(
                a.get("AccountCategory") for a in (a_resp.data or []) if a.get("AccountCategory")
            ))
            g_cache["ts"] = now
        except Exception as e:
            print(f"[ARTURITO] Entity cache refresh error: {e}")

    return p_cache["data"], v_cache["data"], g_cache["data"]


# --- Modular prompt blocks ---

_INTENT_BASE_PROMPT = """You classify user messages in a project management app called NGM Hub. The user is currently on page "{page}".
Return ONLY valid JSON. No explanation, no markdown.

Response format: {{"type":"<TYPE>","action":"<ACTION>","params":{{...}}}}
Omit the "params" key entirely when there are no parameters.

Types:
- "copilot" = control the current page (filter, sort, search, expand, etc.)
- "navigate" = go to another page
- "modal" = open a dialog/form
- "report" = generate a report (BVA, bug report)
- "chat" = general conversation, question, or help request

RULES:
- If the message looks like it controls the CURRENT page, prefer "copilot" over "chat".
- User may write in English or Spanish. Normalize all param values to English.
- When the user mentions a project or vendor/company name (even with typos, abbreviations, or partial names), match it to the closest entry from the KNOWN PROJECTS or KNOWN VENDORS lists below and use the EXACT name from the list.
- If no close match exists in the lists, preserve the user's original text.
"""

_INTENT_PAGE_PROMPTS = {
    "expenses.html": """
CURRENT PAGE: Expenses Engine (expense tracking table with filters)

COPILOT ACTIONS (type="copilot"):
- action="filter", params: {{"field":"<FIELD>","value":"<VAL>"}}
  Fields:
    vendor = company/supplier name (Home Depot, Lowe's, Wayfair, etc.)
    bill_id = invoice/bill number (numeric)
    account = expense category (Hauling And Dump, Materials, Labor, Equipment Rental, Permits, etc.)
    type = expense type
    payment = payment method. Normalize: efectivo/cash=Cash, tarjeta/card/credit=Card, cheque/check=Check, transferencia/transfer/wire=Transfer, zelle=Zelle, venmo=Venmo, paypal=PayPal
    auth = authorization status. Values: Pending, Authorized, Review
    date = date filter (YYYY-MM-DD format, or {{"start":"YYYY-MM-DD","end":"YYYY-MM-DD"}})
- action="search", params: {{"query":"<text>"}}
- action="clear_filters" (no params)
- action="summary" (no params)
- action="show_filters" (no params)
- action="expand_all" (no params)
- action="collapse_all" (no params)
- action="health_check" (no params)
- action="sort", params: {{"column":"<date|amount|vendor|bill>","direction":"<asc|desc>"}}

IMPORTANT CONTEXT:
- If user types just a company/store name (e.g. "home depot", "lowes", "wayfair"), it IS a vendor filter.
- If user types just a number with 3+ digits (e.g. "1439", "456"), it IS a bill_id filter.
- If user types a payment method word alone (e.g. "cheque", "cash", "tarjeta"), it IS a payment filter.
- "pendientes"/"pending" alone = auth filter Pending. "autorizados"/"authorized" alone = auth filter Authorized.
- "basura", "dump" = account filter for "Hauling And Dump". Use semantic understanding for account names.
""",

    "pipeline.html": """
CURRENT PAGE: Pipeline Manager (task/workflow management with filters)

COPILOT ACTIONS (type="copilot"):
- action="filter", params: {{"field":"<FIELD>","value":"<VAL>"}}
  Fields:
    status = task status. Values: not_started, in_progress, review, done
    assignee = person name. If user says "my tasks"/"mis tareas", use value "__CURRENT_USER__"
    priority = task priority. Values: high, medium, low
    project = project name
- action="search", params: {{"query":"<text>"}}
- action="clear_filters" (no params)
""",

    "projects.html": """
CURRENT PAGE: Projects (project list with search and filters)

COPILOT ACTIONS (type="copilot"):
- action="filter", params: {{"field":"status","value":"<active|completed|on_hold|cancelled>"}}
- action="search", params: {{"query":"<text>"}}
- action="clear_filters" (no params)
""",

    "team.html": """
CURRENT PAGE: Team Management (user list with role filters)

COPILOT ACTIONS (type="copilot"):
- action="filter", params: {{"field":"role","value":"<role name>"}}
- action="search", params: {{"query":"<text>"}}
- action="clear_filters" (no params)
""",

    "vendors.html": """
CURRENT PAGE: Vendors (vendor/supplier list)

COPILOT ACTIONS (type="copilot"):
- action="search", params: {{"query":"<text>"}}
""",
}

_INTENT_GLOBAL_PROMPT = """
NAVIGATION (type="navigate"):
- action="goto", params: {{"page":"<PAGE_NAME>"}}
  Page names: expenses, pipeline, projects, team, vendors, budgets, dashboard, messages, accounts, estimator, reporting, budget_monitor, company_expenses, arturito, settings
  Spanish aliases: gastos=expenses, tareas=pipeline, proyectos=projects, equipo=team, proveedores=vendors, presupuestos=budgets, cuentas=accounts, reportes=reporting, monitor=budget_monitor, configuracion=settings

MODALS (type="modal"):
- action="open", params: {{"id":"<MODAL_ID>"}}
  Modal IDs: add_expense, scan_receipt, new_task, add_project, add_user
  Triggers: "agregar gasto"=add_expense, "escanear recibo"/"scan receipt"=scan_receipt, "nueva tarea"/"new task"=new_task, "nuevo proyecto"=add_project, "agregar usuario"=add_user

REPORTS (type="report"):
- action="bva", params: {{"project":"<project name or null>"}}
  Full BVA report (PDF). Triggers: "bva", "budget vs actuals", "reporte bva", "genera bva de [project]"
- action="query", params: {{"project":"<project name or null>","category":"<budget category or group>"}}
  Specific budget question about an account or account group.
  Triggers: questions about how much budget is left/used for a category.
  Examples: "cuanto tengo para ventanas en Thrasher", "how much HVAC budget in Del Rio", "what did we spend on plumbing", "show me finishes for Willowbrook"
  Category can be an account name (Windows, HVAC, Plumbing) or a group name (Finishes, Site Work, MEP).
  Category should be in the user's language (Spanish or English). Project must be normalized to KNOWN PROJECTS.
- action="bug", params: {{"description":"<bug description>"}}
  Triggers: "reportar bug", "hay un error", "something is broken"

For anything else (questions, greetings, help, general conversation): type="chat"
"""


@router.post("/interpret-intent", response_model=IntentResponse)
async def interpret_intent(request: IntentRequest):
    """
    GPT-first intent interpreter. Classifies user messages based on
    the current page context and returns structured action JSON.
    """
    from openai import OpenAI
    import os

    try:
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            return IntentResponse(type="chat")

        client = OpenAI(api_key=api_key)

        # Normalize page name
        page = request.current_page or "dashboard.html"
        if "/" in page:
            page = page.split("/")[-1]

        # Assemble prompt: BASE + PAGE_BLOCK + GLOBAL
        system_prompt = _INTENT_BASE_PROMPT.format(page=page)

        page_block = _INTENT_PAGE_PROMPTS.get(page, "")
        if page_block:
            system_prompt += page_block

        system_prompt += _INTENT_GLOBAL_PROMPT

        # Inject known entity lists for fuzzy matching
        project_names, vendor_names, account_groups = _get_cached_entities()
        if project_names:
            system_prompt += f"\nKNOWN PROJECTS: {', '.join(project_names)}\n"
        if vendor_names:
            system_prompt += f"\nKNOWN VENDORS: {', '.join(vendor_names)}\n"
        if account_groups:
            system_prompt += f"\nACCOUNT GROUPS (budget categories): {', '.join(account_groups)}\n"

        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": request.text},
            ],
            temperature=0,
            max_tokens=200,
            response_format={"type": "json_object"},
        )

        raw = response.choices[0].message.content.strip()

        # Parse JSON
        parsed = json.loads(raw)

        return IntentResponse(
            type=parsed.get("type", "chat"),
            action=parsed.get("action"),
            params=parsed.get("params"),
        )

    except Exception as e:
        print(f"[ARTURITO] interpret-intent error: {e}")
        return IntentResponse(type="chat")


# ================================
# EXPENSE FILTER INTERPRETATION (legacy, kept for backwards compatibility)
# ================================

class FilterInterpretRequest(BaseModel):
    """Request for GPT filter interpretation"""
    text: str


class FilterInterpretResponse(BaseModel):
    """Response from GPT filter interpretation"""
    action: Optional[str] = None  # 'clear_filters', 'filter_bill', 'filter_vendor', 'search', 'summary'
    value: Optional[str] = None
    message: Optional[str] = None
    understood: bool = False


@router.post("/interpret-filter", response_model=FilterInterpretResponse)
async def interpret_filter_command(request: FilterInterpretRequest):
    """
    Use GPT to interpret a natural language expense filter command.
    Returns the detected action and parameters.
    """
    from openai import OpenAI
    import os
    import json

    try:
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            return FilterInterpretResponse(understood=False)

        client = OpenAI(api_key=api_key)

        # System prompt for filter interpretation
        system_prompt = """You are a command interpreter for an expense management system.
Your job is to interpret natural language commands and return structured actions.

Available actions:
- clear_filters: Remove all active filters (e.g., "quita los filtros", "clear filters", "mostrar todo")
- filter_bill: Filter by bill/invoice number (e.g., "filtra el bill 1439", "muestra factura 234")
- filter_vendor: Filter by COMPANY/SUPPLIER NAME (e.g., "gastos de Home Depot", "filtra vendor Wayfair", "solo The Home Depot")
- filter_account: Filter by EXPENSE CATEGORY/TYPE (e.g., "gastos de hauling and dump", "muestra solo materials", "filtra labor costs", "solo hauling", "dump fees")
- filter_status: Filter by authorization status - value should be "pending", "auth", or "review" (e.g., "muestrame solo gastos autorizados", "gastos pendientes", "solo pending", "expenses in review", "authorized only")
- search: Search for text in expenses (e.g., "busca pintura", "search for materials")
- summary: Show expense summary (e.g., "cuantos gastos tengo", "resumen de gastos")
- list_accounts: List all available expense accounts/categories (e.g., "ver cuentas", "lista de cuentas", "que cuentas hay", "show accounts")

IMPORTANT DISTINCTION:
- filter_vendor = Company names (Home Depot, Wayfair, Lowe's, ABC Construction, etc.)
- filter_account = Expense categories/types (hauling and dump, materials, labor, equipment rental, permits, etc.)

CONFIRMATION RESPONSES:
When user confirms an account choice (after seeing options), interpret as filter_account:
- "Hauling And Dump" → filter_account with value "Hauling And Dump"
- "filtrar hauling and dump" → filter_account with value "hauling and dump"
- "la primera" / "opción 1" / "1" → (cannot handle numbers - return null to let widget handle)
- Just an account name without "filtrar" keyword → filter_account

Examples:
- "muestrame solo los gastos de hauling and dump" → filter_account (hauling and dump is a category, not a company)
- "gastos de Home Depot" → filter_vendor (Home Depot is a company)
- "solo materials" → filter_account (materials is a category)
- "filtra Wayfair" → filter_vendor (Wayfair is a company)
- "muestrame solo gastos autorizados" → filter_status with value "auth"
- "gastos pendientes" → filter_status with value "pending"
- "expenses in review" → filter_status with value "review"
- "Hauling And Dump" (after seeing options) → filter_account with value "Hauling And Dump"
- "filtrar equipment rental" → filter_account with value "equipment rental"

Respond ONLY with a JSON object:
{"action": "action_name", "value": "extracted_value_or_null", "message": "friendly_response_in_spanish"}

For filter_account, extract the account/category name from the command.
If you can't interpret the command as a filter action, return:
{"action": null, "value": null, "message": null}"""

        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": request.text}
            ],
            temperature=0,
            max_tokens=150
        )

        result_text = response.choices[0].message.content.strip()

        # Parse JSON response
        try:
            # Remove markdown code blocks if present
            if result_text.startswith("```"):
                result_text = result_text.split("```")[1]
                if result_text.startswith("json"):
                    result_text = result_text[4:]
                result_text = result_text.strip()

            result = json.loads(result_text)

            if result.get("action"):
                return FilterInterpretResponse(
                    action=result.get("action"),
                    value=result.get("value"),
                    message=result.get("message"),
                    understood=True
                )
        except json.JSONDecodeError:
            pass

        return FilterInterpretResponse(understood=False)

    except Exception as e:
        print(f"[ARTURITO] Filter interpretation error: {e}")
        return FilterInterpretResponse(understood=False)


async def semantic_account_search(query: str, accounts: list, limit: int = 5):
    """
    Use GPT to find semantically matching accounts.
    Handles synonyms and related concepts (e.g., "basura" → "Hauling And Dump").
    """
    try:
        # Create account list for GPT
        account_list = "\n".join([
            f"- {acc.get('Name')} (ID: {acc.get('account_id')})"
            for acc in accounts[:50]  # Limit to avoid token limits
        ])

        system_prompt = f"""You are an expense categorization assistant.
Your task is to find which expense account(s) best match a given search query.

Available accounts:
{account_list}

Return a JSON array of matching account IDs, ordered by relevance (most relevant first).
Consider synonyms and semantic relationships:
- "basura", "desechos", "desperdicios" → "Hauling And Dump"
- "materiales", "materials" → material-related accounts
- "mano de obra", "labor" → labor-related accounts
- etc.

If multiple accounts could match a general concept, include all relevant ones.

Response format:
{{"matches": ["account_id_1", "account_id_2", ...], "reasoning": "brief explanation"}}

If no semantic match is found, return: {{"matches": [], "reasoning": "no match"}}"""

        client = get_openai_client()
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"Find accounts matching: {query}"}
            ],
            temperature=0,
            max_tokens=200
        )

        result_text = response.choices[0].message.content.strip()

        # Parse JSON response
        if result_text.startswith("```"):
            result_text = result_text.split("```")[1]
            if result_text.startswith("json"):
                result_text = result_text[4:]
            result_text = result_text.strip()

        result = json.loads(result_text)
        matched_ids = result.get("matches", [])

        if matched_ids:
            # Build response with matched accounts
            matched_accounts = []
            for acc in accounts:
                if acc.get("account_id") in matched_ids:
                    matched_accounts.append({
                        "account_id": acc["account_id"],
                        "name": acc["Name"],
                        "account_num": acc.get("AcctNum"),
                        "full_name": acc.get("FullyQualifiedName"),
                        "score": 90,  # Semantic matches get high score
                        "semantic": True,
                        "reasoning": result.get("reasoning", "")
                    })

            # Sort by original order from GPT (relevance)
            ordered_matches = []
            for match_id in matched_ids:
                for acc in matched_accounts:
                    if acc["account_id"] == match_id:
                        ordered_matches.append(acc)
                        break

            return ordered_matches[:limit]

        return []

    except Exception as e:
        print(f"[ARTURITO] Semantic search error: {e}")
        return []


@router.get("/search-accounts")
async def search_accounts(query: str, limit: int = 5):
    """
    Search for accounts using hybrid fuzzy + semantic matching.
    Returns best matches for natural language account queries.

    Args:
        query: Search term (e.g., "hauling", "basura", "desechos")
        limit: Maximum number of results (default: 5)

    Returns:
        List of matching accounts with similarity scores
    """
    try:
        # Get all accounts
        from api.supabase_client import supabase
        response = supabase.table("accounts").select(
            "account_id, Name, AcctNum, FullyQualifiedName"
        ).execute()

        accounts = response.data or []

        if not accounts:
            return {"matches": []}

        # Normalize query for better matching
        import re
        query_normalized = re.sub(r'\s+', ' ', query.lower().strip())

        # Phase 1: Fuzzy matching with improved scoring
        scored_accounts = []

        for account in accounts:
            name = account.get("Name", "")
            name_normalized = re.sub(r'\s+', ' ', name.lower().strip())
            full_name = account.get("FullyQualifiedName", "").lower()

            # Calculate similarity score
            score = 0

            # Exact match (normalized) gets highest score
            if query_normalized == name_normalized:
                score = 100
            elif query_normalized in name_normalized:
                score = 80
            elif query_normalized in full_name:
                score = 60
            else:
                # Word-based matching
                query_words = query_normalized.split()
                name_words = name_normalized.split()

                matching_words = sum(1 for qw in query_words if any(qw in nw for nw in name_words))
                if matching_words > 0:
                    score = (matching_words / len(query_words)) * 50

            if score > 0:
                scored_accounts.append({
                    "account_id": account["account_id"],
                    "name": account["Name"],
                    "account_num": account.get("AcctNum"),
                    "full_name": account.get("FullyQualifiedName"),
                    "score": round(score, 2)
                })

        # Sort by score descending
        scored_accounts.sort(key=lambda x: x["score"], reverse=True)

        # Phase 2: If no good match found (best score < 70), try GPT semantic search
        if not scored_accounts or scored_accounts[0]["score"] < 70:
            print(f"[ARTURITO] Fuzzy match weak (best: {scored_accounts[0]['score'] if scored_accounts else 0}), trying GPT semantic search...")

            semantic_matches = await semantic_account_search(query, accounts)
            if semantic_matches:
                print(f"[ARTURITO] GPT found {len(semantic_matches)} semantic matches")
                return {"matches": semantic_matches[:limit], "method": "semantic"}

        return {"matches": scored_accounts[:limit], "method": "fuzzy"}

    except Exception as e:
        print(f"[ARTURITO] Account search error: {e}")
        return {"matches": []}


@router.get("/failed-commands/stats")
async def get_failed_commands_stats_endpoint(
    days_back: int = 30,
    user_id: Optional[str] = None
):
    """
    Get aggregated statistics about failed commands.

    Query params:
    - days_back: How many days back to analyze (default: 30)
    - user_id: Filter by user (admin only, optional)

    Returns:
    - total_failures: Total number of failed commands
    - unique_commands: Number of unique command texts
    - gpt_attempt_rate: Percentage of failures where GPT was attempted
    - top_pages: Pages with most failures
    - top_errors: Most common error reasons
    - most_common_commands: Most frequently failed commands
    """
    try:
        # TODO: Add auth check - verify user has permission to view stats

        from api.db import get_supabase_client
        supabase = get_supabase_client()

        stats = await get_failed_commands_stats(
            supabase=supabase,
            user_id=user_id,
            days_back=days_back
        )

        return stats

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
