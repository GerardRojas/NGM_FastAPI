# services/arturito/nlu.py
# ================================
# Natural Language Understanding para Arturito
# ================================
# Migrado desde Interpretar.gs

import re
import json
import os
from typing import Dict, Any, Optional
from openai import OpenAI
from .persona import get_persona_prompt

# ================================
# Tipos de Intent soportados
# ================================

VALID_INTENTS = [
    "BUDGET_VS_ACTUALS",    # Reporte BVA global
    "CONSULTA_ESPECIFICA",  # Consulta sobre categoría específica del BVA
    "SCOPE_OF_WORK",        # Consultas sobre SOW
    "INFO",                 # Información del sistema / ayuda
    "SET_PERSONALITY",      # Cambiar nivel de sarcasmo
    "SMALL_TALK",           # Conversación general
    "GREETING",             # Saludos
    "UNKNOWN",              # No clasificado
]


# ================================
# Interpretación Local (regex rápido)
# ================================

def interpret_local(text: str) -> Optional[Dict[str, Any]]:
    """
    Intenta clasificar el mensaje con reglas locales antes de llamar a GPT.
    Retorna None si no hay match → se delega a GPT.
    """
    t = text.strip().lower()

    # Sarcasmo / Personalidad: "sarcasmo 3", "personalidad 5"
    match_sar = re.search(r'\b(sarcasmo|personalidad|personality)\s*(\d)\b', t)
    if match_sar:
        level = int(match_sar.group(2))
        return {
            "intent": "SET_PERSONALITY",
            "entities": {"level": min(5, max(1, level))},
            "confidence": 1.0,
            "source": "local"
        }

    # BVA directo: "bva del rio", "budgetvsactuals arthur neal"
    match_bva = re.match(r'^(bva|budgetvsactuals)\s+(.+)$', t)
    if match_bva:
        return {
            "intent": "BUDGET_VS_ACTUALS",
            "entities": {"project": match_bva.group(2).strip()},
            "confidence": 1.0,
            "source": "local"
        }

    # Ayuda / Help
    if re.search(r'\b(ayuda|help|qué puedes hacer|what can you do)\b', t):
        return {
            "intent": "INFO",
            "entities": {},
            "confidence": 0.95,
            "source": "local"
        }

    # Saludos simples
    if re.match(r'^(hola|hi|hello|hey|buenos días|buenas tardes|good morning)[\s!.,]*$', t):
        return {
            "intent": "GREETING",
            "entities": {},
            "confidence": 0.9,
            "source": "local"
        }

    # Identidad
    if re.search(r'\b(quién eres|quien eres|who are you|qué eres)\b', t):
        return {
            "intent": "INFO",
            "entities": {"topic": "identity"},
            "confidence": 0.95,
            "source": "local"
        }

    return None


# ================================
# Interpretación con GPT
# ================================

NLU_SYSTEM_PROMPT = """Eres un PARSER ESTRICTO. Clasifica el mensaje del usuario en UNO de estos intents:

1) BUDGET_VS_ACTUALS
   - Consultas GENERALES del proyecto: total budget, total actuals, balance global.
   - Se activa cuando NO hay trade/tema específico o piden "Budget vs Actuals", "global", "resumen".
   - Extrae: 'project' si aparece.

2) CONSULTA_ESPECIFICA
   - Pregunta sobre un grupo/categoría/cuenta ESPECÍFICA del BVA (HVAC, framing, windows, plumbing, etc.).
   - Mide métricas: budget, actuals, gastado, disponible, diferencia.
   - Extrae: 'topic' (trade), 'project' (si aparece).
   - REGLA: Si aparece trade + budget/actuals/gastado → SIEMPRE es CONSULTA_ESPECIFICA.

3) SCOPE_OF_WORK
   - Consultas sobre el alcance de obra (SOW).
   - Señales: incluye/excluye, NIC/by owner, qué contempla, qué dice el SOW.
   - Extrae: 'project', 'question'.

4) INFO
   - Preguntas sobre funciones o capacidades del sistema.
   - Ayuda, quién eres, qué puedes hacer.

5) SMALL_TALK
   - Conversación general o dudas técnicas simples.
   - Saludos, chistes, preguntas no relacionadas con proyectos.

REGLAS DE RESPUESTA:
- Devuelve SOLO JSON válido, sin markdown ni explicaciones.
- Si el proyecto no se menciona claramente, usa null.
- Formato exacto:
{
  "intent": "INTENT_NAME",
  "entities": { "project": "...", "topic": "...", "question": "..." },
  "confidence": 0.85
}
"""


def interpret_with_gpt(text: str, context: Dict[str, Any] = None) -> Dict[str, Any]:
    """
    Usa OpenAI para clasificar el intent cuando las reglas locales no matchean.
    """
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return {
            "intent": "UNKNOWN",
            "entities": {},
            "confidence": 0.0,
            "error": "OPENAI_API_KEY not configured"
        }

    client = OpenAI(api_key=api_key)

    # Agregar contexto del espacio si existe
    context_info = ""
    if context:
        if context.get("space_name"):
            context_info = f"\n\nContexto: El usuario está en el espacio '{context['space_name']}'"

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",  # Modelo rápido y económico para NLU
            messages=[
                {"role": "system", "content": NLU_SYSTEM_PROMPT},
                {"role": "user", "content": f"Mensaje del usuario: {text}{context_info}"}
            ],
            temperature=0,
            max_tokens=200
        )

        raw_response = response.choices[0].message.content.strip()

        # Limpiar respuesta (quitar markdown fences si los hay)
        cleaned = raw_response
        if cleaned.startswith("```"):
            cleaned = re.sub(r'^```(?:json)?\s*', '', cleaned)
            cleaned = re.sub(r'\s*```$', '', cleaned)

        # Parsear JSON
        parsed = json.loads(cleaned)

        return {
            "intent": parsed.get("intent", "UNKNOWN").upper(),
            "entities": parsed.get("entities", {}),
            "confidence": parsed.get("confidence", 0.7),
            "source": "gpt"
        }

    except json.JSONDecodeError as e:
        return {
            "intent": "SMALL_TALK",
            "entities": {},
            "confidence": 0.5,
            "source": "gpt",
            "parse_error": str(e)
        }
    except Exception as e:
        return {
            "intent": "UNKNOWN",
            "entities": {},
            "confidence": 0.0,
            "error": str(e)
        }


# ================================
# Pipeline Principal de Interpretación
# ================================

def interpret_message(text: str, context: Dict[str, Any] = None) -> Dict[str, Any]:
    """
    Pipeline completo de interpretación:
    1. Intenta reglas locales (rápido, sin API)
    2. Si no hay match, usa GPT
    3. Normaliza y retorna resultado estructurado
    """
    if not text or not text.strip():
        return {
            "intent": "UNKNOWN",
            "entities": {},
            "confidence": 0.0,
            "raw_text": ""
        }

    clean_text = text.strip()

    # 1. Intentar interpretación local
    local_result = interpret_local(clean_text)
    if local_result:
        local_result["raw_text"] = clean_text
        return local_result

    # 2. Delegar a GPT
    gpt_result = interpret_with_gpt(clean_text, context)
    gpt_result["raw_text"] = clean_text

    # 3. Inferir proyecto desde contexto si no se detectó
    if not gpt_result.get("entities", {}).get("project"):
        if context and context.get("space_name"):
            # Solo usar el nombre del espacio si parece un proyecto
            space_name = context["space_name"]
            if space_name and space_name.lower() not in ["default", "general", "random"]:
                gpt_result.setdefault("entities", {})["project"] = space_name
                gpt_result["project_inferred"] = True

    return gpt_result
