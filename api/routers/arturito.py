# api/routers/arturito.py
# ================================
# ARTURITO - NGM Chat Bot Backend
# ================================
# Entry point para mensajes desde Google Chat.
# Delega la lógica al módulo services/arturito/

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional, Dict, Any
from datetime import datetime
import os
import re

# Importar el engine de Arturito
from services.arturito import (
    interpret_message,
    route,
    route_slash_command,
    get_persona_prompt,
    set_personality_level,
)
from services.arturito.handlers.info_handler import get_system_status

router = APIRouter(prefix="/arturito", tags=["arturito"])


# ================================
# MODELS
# ================================

class ChatMessage(BaseModel):
    """Mensaje entrante desde Google Chat"""
    text: str
    user_name: Optional[str] = None
    user_email: Optional[str] = None
    space_name: Optional[str] = None  # Nombre del chat/room
    space_id: Optional[str] = None    # ID único del espacio
    thread_id: Optional[str] = None   # Para mantener contexto de conversación
    is_mention: Optional[bool] = False  # Si el bot fue mencionado directamente


class SlashCommand(BaseModel):
    """Slash command desde Google Chat"""
    command: str  # Nombre del comando sin "/"
    args: Optional[str] = None
    user_name: Optional[str] = None
    user_email: Optional[str] = None
    space_name: Optional[str] = None
    space_id: Optional[str] = None


class BotResponse(BaseModel):
    """Respuesta del bot hacia Google Chat"""
    text: str
    action: Optional[str] = None
    data: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    # Para Cards de Google Chat (opcional)
    card: Optional[Dict[str, Any]] = None


# ================================
# ENDPOINTS
# ================================

@router.post("/message", response_model=BotResponse)
async def receive_message(message: ChatMessage):
    """
    Endpoint principal que recibe mensajes desde Google Chat.
    El Apps Script de Google Chat hará POST aquí.

    Flujo:
    1. Detecta si es slash command
    2. Interpreta el mensaje con NLU
    3. Rutea al handler apropiado
    4. Retorna respuesta formateada
    """
    try:
        text = message.text.strip()

        if not text:
            return BotResponse(
                text="No recibí ningún mensaje. ¿En qué puedo ayudarte?",
                action="empty_message"
            )

        # Construir contexto
        context = {
            "user_name": message.user_name,
            "user_email": message.user_email,
            "space_name": message.space_name,
            "space_id": message.space_id or message.space_name or "default",
            "thread_id": message.thread_id,
            "is_mention": message.is_mention,
        }

        # 1. Detectar slash commands (/comando args)
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

        # 2. Limpiar mención del bot si existe (@Arturito)
        clean_text = re.sub(r'^@?\s*arturito[,:]?\s*', '', text, flags=re.IGNORECASE).strip()
        if not clean_text:
            clean_text = text  # Si solo era la mención, usar texto original

        # 3. Interpretar mensaje con NLU
        intent_result = interpret_message(clean_text, context)

        # 4. Rutear al handler apropiado
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
    """
    Endpoint alternativo para slash commands.
    Google Chat puede enviar comandos directamente aquí si se configura así.
    """
    try:
        context = {
            "user_name": command.user_name,
            "user_email": command.user_email,
            "space_name": command.space_name,
            "space_id": command.space_id or command.space_name or "default",
        }

        result = route_slash_command(
            command.command,
            command.args or "",
            context
        )

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
        **status
    }


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
    Procesa eventos: ADDED_TO_SPACE, MESSAGE, etc.

    Este endpoint puede recibir directamente los eventos de Google Chat
    si configuras el bot para usar HTTP endpoint en lugar de Apps Script.
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

        # Evento: Bot agregado al espacio
        if event_type == "ADDED_TO_SPACE":
            return {
                "text": f"🤖 ¡Hola {user_name}! Soy Arturito y estoy listo para ayudar en {space_name}."
            }

        # Evento: Mensaje recibido
        if event_type == "MESSAGE":
            text = message.get("argumentText") or message.get("text") or ""

            # Detectar si fue mención
            annotations = message.get("annotations", [])
            is_mention = any(a.get("type") == "USER_MENTION" for a in annotations)

            # Construir mensaje y procesar
            chat_message = ChatMessage(
                text=text.strip(),
                user_name=user_name,
                user_email=user_email,
                space_name=space_name,
                space_id=space_id,
                is_mention=is_mention
            )

            response = await receive_message(chat_message)

            # Formatear respuesta para Google Chat
            result = {"text": response.text}

            # Si hay card, agregarla
            if response.card:
                result["cardsV2"] = [response.card]

            return result

        # Evento no manejado
        return {"text": ""}

    except Exception as e:
        return {"text": f"⚠️ Error: {str(e)}"}
