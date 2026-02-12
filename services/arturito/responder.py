# services/arturito/responder.py
# ================================
# Generador de Respuestas de Arturito
# ================================
# Migrado desde Responder.gs y HandleFreeTalk.gs

import os
from typing import Dict, Any, Optional
from openai import OpenAI
from .persona import get_persona_prompt


def generate_small_talk_response(
    user_text: str,
    space_id: str = "default"
) -> str:
    """
    Genera una respuesta conversacional usando GPT con la personalidad actual.
    Usado para SMALL_TALK y respuestas no estructuradas.
    """
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return "⚠️ OpenAI no está configurado. No puedo responder en este momento."

    client = OpenAI(api_key=api_key)

    # Obtener el persona prompt con la personalidad actual
    system_prompt = get_persona_prompt(space_id)

    try:
        response = client.chat.completions.create(
            model="gpt-5-mini",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_text}
            ],
            temperature=0.7,
            max_tokens=500
        )

        return response.choices[0].message.content.strip()

    except Exception as e:
        return f"⚠️ Error generando respuesta: {str(e)}"


def generate_contextual_response(
    user_text: str,
    context_data: str,
    space_id: str = "default"
) -> str:
    """
    Genera una respuesta basada en datos de contexto específicos.
    Útil para CONSULTA_ESPECIFICA donde tenemos datos del BVA.
    """
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return "⚠️ OpenAI no está configurado."

    client = OpenAI(api_key=api_key)

    system_prompt = get_persona_prompt(space_id)

    # Agregar contexto de datos
    augmented_prompt = f"""{system_prompt}

DATOS DE CONTEXTO:
{context_data}

Responde la pregunta del usuario basándote en estos datos.
Si los datos no contienen la información solicitada, indica que no tienes esa información.
"""

    try:
        response = client.chat.completions.create(
            model="gpt-5.1",  # Medium tier - data analysis
            messages=[
                {"role": "system", "content": augmented_prompt},
                {"role": "user", "content": user_text}
            ],
            temperature=0.3,
            max_tokens=800
        )

        return response.choices[0].message.content.strip()

    except Exception as e:
        return f"⚠️ Error generando respuesta: {str(e)}"


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
