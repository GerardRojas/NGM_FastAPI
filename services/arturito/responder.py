# services/arturito/responder.py
# ================================
# Generador de Respuestas de Arturito
# ================================
# Migrado desde Responder.gs y HandleFreeTalk.gs

from typing import Dict, Any, Optional
from api.services.gpt_client import gpt
from .persona import get_persona_prompt


def generate_small_talk_response(
    user_text: str,
    space_id: str = "default"
) -> str:
    """
    Genera una respuesta conversacional usando GPT con la personalidad actual.
    Usado para SMALL_TALK y respuestas no estructuradas.
    """
    system_prompt = get_persona_prompt(space_id)
    result = gpt.mini(system_prompt, user_text, max_tokens=500)
    return result if result else "I'm having trouble responding right now. Please try again."


def generate_contextual_response(
    user_text: str,
    context_data: str,
    space_id: str = "default"
) -> str:
    """
    Genera una respuesta basada en datos de contexto específicos.
    Útil para CONSULTA_ESPECIFICA donde tenemos datos del BVA.
    """
    system_prompt = get_persona_prompt(space_id)
    augmented_prompt = f"""{system_prompt}

DATOS DE CONTEXTO:
{context_data}

Responde la pregunta del usuario basándote en estos datos.
Si los datos no contienen la información solicitada, indica que no tienes esa información.
"""
    result = gpt.mini(augmented_prompt, user_text, max_tokens=800)
    return result if result else "I couldn't process that request. Please try again."


def format_card_response(
    title: str,
    subtitle: str,
    url: Optional[str] = None,
    button_text: str = "Abrir"
) -> Dict[str, Any]:
    """
    Formatea una respuesta como Card de Google Chat (estructura compatible).

    Esta estructura será convertida al formato cardsV2 por el endpoint.
    """
    card = {
        "type": "card",
        "title": title,
        "subtitle": subtitle,
    }

    if url:
        card["button"] = {
            "text": button_text,
            "url": url
        }

    return card


def format_text_response(text: str, action: str = None) -> Dict[str, Any]:
    """
    Formatea una respuesta de texto simple.
    """
    response = {"text": text}
    if action:
        response["action"] = action
    return response
