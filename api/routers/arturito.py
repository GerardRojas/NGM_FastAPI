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
