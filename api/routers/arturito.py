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
    session_id: Optional[str] = None
    personality_level: Optional[int] = 3
    thread_id: Optional[str] = None  # Optional: client can provide existing thread


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
            "space_name": "NGM HUB Web",
            "space_id": session_id,
            "is_mention": True,
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
