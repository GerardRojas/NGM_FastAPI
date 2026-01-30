"""
===============================================================================
 Copilot Handler for Arturito
===============================================================================
 Handles page-specific commands like filtering, sorting, searching.
 Acts as a copilot to control the current page UI.
===============================================================================
"""

import logging
import re
import os
import json
from typing import Optional
from datetime import datetime, timedelta
from openai import OpenAI

from ..ngm_knowledge import (
    COPILOT_ACTIONS,
    find_copilot_action,
    extract_copilot_params,
)
from ..failed_commands_logger import log_failed_command

logger = logging.getLogger(__name__)


# -----------------------------------------------------------------------------
# GPT FALLBACK FOR COMMAND INTERPRETATION
# -----------------------------------------------------------------------------

def interpret_command_with_gpt(
    raw_text: str,
    current_page: str,
    page_config: dict,
    entities: dict = None
) -> Optional[dict]:
    """
    Use GPT to interpret ambiguous copilot commands when exact keyword matching fails.

    Args:
        raw_text: User's original message
        current_page: Current page URL (e.g., 'expenses.html')
        page_config: Configuration for the current page from COPILOT_ACTIONS
        entities: Optional entities already extracted from NLU

    Returns:
        Dict with command and params, or None if GPT can't interpret
    """
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        logger.warning("OpenAI API key not configured, cannot use GPT fallback")
        return None

    page_name = page_config.get("page_name", current_page)
    actions = page_config.get("actions", {})

    # Build a description of available actions for this page
    actions_description = []
    for action_id, action in actions.items():
        action_desc = f"- {action['command']}: {action['description']}"
        if action.get("params"):
            action_desc += f" (params: {', '.join(action['params'])})"
        if action.get("examples"):
            action_desc += f"\n  Ejemplos: {', '.join(action['examples'][:2])}"
        actions_description.append(action_desc)

    actions_text = "\n".join(actions_description)

    # Build context from entities if available
    context_text = ""
    if entities:
        context_text = f"\nContexto detectado: {json.dumps(entities, ensure_ascii=False)}"

    prompt = f"""Eres un intérprete de comandos para la página "{page_name}" de NGM Hub.

El usuario dice: "{raw_text}"{context_text}

Acciones disponibles en esta página:
{actions_text}

TAREA: Interpreta qué comando quiere ejecutar el usuario y extrae los parámetros.

REGLAS:
- Si el comando es claro, devuelve el nombre del comando y sus parámetros
- Si el usuario habla en español o inglés, interpreta de manera flexible
- Normaliza los valores:
  * "pendiente", "pending", "sin autorizar" → "pending"
  * "autorizado", "authorized" → "authorized"
  * "todos", "all" → "all"
  * Para proyectos/vendors/usuarios, usa el nombre exacto que mencione
- Si no estás seguro, devuelve null

Responde SOLO con JSON válido (sin markdown):
{{
  "command": "commandName",
  "params": {{
    "param1": "value1",
    "param2": "value2"
  }},
  "confidence": 0.85
}}

Si no puedes interpretar el comando, responde:
{{
  "command": null,
  "confidence": 0.0
}}"""

    try:
        client = OpenAI(api_key=api_key)
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "Eres un intérprete preciso de comandos. Respondes SOLO con JSON válido."},
                {"role": "user", "content": prompt}
            ],
            temperature=0,
            max_tokens=300
        )

        raw_response = response.choices[0].message.content.strip()

        # Clean markdown fences if present
        cleaned = raw_response
        if cleaned.startswith("```"):
            cleaned = re.sub(r'^```(?:json)?\s*', '', cleaned)
            cleaned = re.sub(r'\s*```$', '', cleaned)

        # Parse JSON
        parsed = json.loads(cleaned)

        command = parsed.get("command")
        if not command or command == "null":
            return None

        confidence = parsed.get("confidence", 0.7)
        if confidence < 0.5:
            return None

        return {
            "command": command,
            "params": parsed.get("params", {}),
            "confidence": confidence,
            "source": "gpt"
        }

    except json.JSONDecodeError as e:
        logger.error(f"GPT returned invalid JSON: {raw_response}", exc_info=e)
        return None
    except Exception as e:
        logger.error(f"Error calling GPT for command interpretation: {e}", exc_info=e)
        return None


# -----------------------------------------------------------------------------
# COPILOT HANDLER
# -----------------------------------------------------------------------------

def handle_copilot(
    request: dict,
    context: dict = None
) -> dict:
    """
    Handle copilot commands for controlling the current page.

    Args:
        request: Dict with intent, entities, raw_text
        context: Dict with current_page, user info, etc.

    Returns:
        Dict with text response and copilot action to execute
    """
    raw_text = request.get("raw_text", "")
    entities = request.get("entities", {})
    current_page = context.get("current_page", "") if context else ""

    # Normalize page name
    if "/" in current_page:
        current_page = current_page.split("/")[-1]

    # Check if we have copilot actions for this page
    page_config = COPILOT_ACTIONS.get(current_page)

    if not page_config:
        return {
            "text": f"No tengo comandos copilot configurados para esta página ({current_page}). Puedo ayudarte con navegación o preguntas sobre NGM Hub.",
            "action": "copilot_not_available",
        }

    page_name = page_config.get("page_name", current_page)

    # Find matching copilot action
    action_match = find_copilot_action(raw_text, current_page)

    if not action_match:
        # Try to understand the command type from entities
        command_type = entities.get("command_type", "")
        if command_type:
            return handle_generic_command(command_type, raw_text, current_page, page_config)

        # GPT FALLBACK: Try to interpret the command with AI
        logger.info(f"No exact match found for '{raw_text}', trying GPT interpretation...")
        gpt_result = interpret_command_with_gpt(raw_text, current_page, page_config, entities)

        if gpt_result and gpt_result.get("command"):
            # GPT successfully interpreted the command!
            command = gpt_result["command"]
            params = gpt_result.get("params", {})
            confidence = gpt_result.get("confidence", 0.7)

            logger.info(f"GPT interpreted command: {command} with params: {params} (confidence: {confidence})")

            # Build response for GPT-interpreted command
            response_text = f"Entendido (con {int(confidence*100)}% confianza): ejecutando {command}"
            if params:
                param_str = ", ".join(f"{k}={v}" for k, v in params.items())
                response_text += f" ({param_str})"

            return {
                "text": response_text,
                "action": "copilot_execute",
                "data": {
                    "command": command,
                    "params": params,
                    "page": current_page,
                    "page_name": page_name,
                    "source": "gpt",
                    "confidence": confidence,
                },
            }

        # Even GPT couldn't help - LOG and show help message
        # Log this failure for analytics
        user_id = context.get("user", {}).get("user_id") if context else None
        supabase = context.get("supabase") if context else None

        if user_id and supabase:
            import asyncio
            asyncio.create_task(
                log_failed_command(
                    supabase=supabase,
                    user_id=user_id,
                    command_text=raw_text,
                    current_page=current_page,
                    intent_detected=request.get("intent"),
                    entities_detected=entities,
                    error_reason="gpt_failed" if gpt_result else "no_exact_match",
                    gpt_attempted=bool(gpt_result),
                    gpt_response=gpt_result if gpt_result else None,
                    gpt_confidence=gpt_result.get("confidence") if gpt_result else None,
                )
            )

        return {
            "text": f"No entendí el comando. En {page_name} puedo ayudarte con:\n"
                    f"- Filtrar (ej: 'mostrar solo pendientes')\n"
                    f"- Ordenar (ej: 'ordenar por fecha')\n"
                    f"- Buscar (ej: 'buscar X')\n"
                    f"- Limpiar filtros",
            "action": "copilot_help",
        }

    # Extract parameters from the message
    params = extract_copilot_params(raw_text, action_match)

    # Merge with entities from NLU (GPT may have extracted better params)
    if entities:
        # Map NLU entity names to action param names
        if "filter_target" in entities and "filter_value" in entities:
            # Convert filter_target to specific param name
            filter_target = entities["filter_target"]
            filter_value = entities["filter_value"]

            if filter_target == "auth_status":
                params["status"] = filter_value
            elif filter_target == "project":
                params["project_name"] = filter_value
            elif filter_target == "vendor":
                params["vendor_name"] = filter_value
            elif filter_target in ["assignee", "user"]:
                params["user_name"] = filter_value
            elif filter_target in ["priority", "status"]:
                params[filter_target] = filter_value

        if "sort_column" in entities:
            params["column"] = entities["sort_column"]
        if "sort_direction" in entities:
            params["direction"] = entities["sort_direction"]
        if "search_query" in entities:
            params["query"] = entities["search_query"]

    # Add any extracted named entities (project name, vendor name, etc.)
    params = extract_named_entities(raw_text, action_match, params)

    # Build response
    command = action_match["command"]
    description = action_match["description"]

    # Custom messages for special commands
    custom_messages = {
        "healthCheckDuplicateBills": (
            "Revisando integridad de bills... Voy a verificar si hay facturas con el "
            "mismo número pero asignadas a diferentes vendors. Si encuentro conflictos, "
            "los resaltaré en naranja en la tabla."
        ),
        "filterByDuplicates": (
            "Filtrando la tabla para mostrar solo los gastos que parecen duplicados. "
            "Estos son gastos con el mismo número de factura pero asignados a diferentes vendors."
        ),
    }

    if command in custom_messages:
        response_text = custom_messages[command]
    else:
        response_text = f"Ejecutando: {description}"
        if params:
            param_str = ", ".join(f"{k}={v}" for k, v in params.items())
            response_text += f" ({param_str})"

    return {
        "text": response_text,
        "action": "copilot_execute",
        "data": {
            "command": command,
            "params": params,
            "page": current_page,
            "page_name": page_name,
            "action_id": action_match["action_id"],
            "expects_result": command in custom_messages,  # Flag for frontend
        },
    }


def handle_generic_command(
    command_type: str,
    raw_text: str,
    current_page: str,
    page_config: dict
) -> dict:
    """
    Handle generic command types when specific action not found.
    """
    page_name = page_config.get("page_name", current_page)
    actions = page_config.get("actions", {})

    if command_type == "filter":
        # Find filter-related actions
        filter_actions = [a for a_id, a in actions.items() if "filter" in a_id.lower()]
        if filter_actions:
            examples = []
            for action in filter_actions[:3]:
                if action.get("examples"):
                    examples.append(action["examples"][0])

            return {
                "text": f"Para filtrar en {page_name}, puedo ayudarte con:\n" +
                        "\n".join(f"- {ex}" for ex in examples),
                "action": "copilot_help",
            }

    elif command_type == "sort":
        if "sort_by_column" in actions:
            return {
                "text": f"Para ordenar en {page_name}, dime por qué columna y dirección.\n"
                        f"Ejemplo: 'ordenar por fecha más reciente'",
                "action": "copilot_help",
            }

    elif command_type == "search":
        # Extract search query
        search_match = re.search(r'(buscar?|search|encontrar?|find)\s+(.+)', raw_text.lower())
        if search_match:
            query = search_match.group(2).strip()
            return {
                "text": f"Buscando '{query}'...",
                "action": "copilot_execute",
                "data": {
                    "command": "searchText",
                    "params": {"query": query},
                    "page": current_page,
                    "page_name": page_name,
                },
            }

    elif command_type == "clear_filters":
        return {
            "text": "Limpiando todos los filtros...",
            "action": "copilot_execute",
            "data": {
                "command": "clearFilters",
                "params": {},
                "page": current_page,
                "page_name": page_name,
            },
        }

    elif command_type == "expand":
        return {
            "text": "Expandiendo todo...",
            "action": "copilot_execute",
            "data": {
                "command": "expandAll",
                "params": {},
                "page": current_page,
                "page_name": page_name,
            },
        }

    elif command_type == "collapse":
        return {
            "text": "Colapsando todo...",
            "action": "copilot_execute",
            "data": {
                "command": "collapseAll",
                "params": {},
                "page": current_page,
                "page_name": page_name,
            },
        }

    return {
        "text": f"No pude interpretar el comando '{command_type}' para {page_name}.",
        "action": "copilot_error",
    }


def extract_named_entities(raw_text: str, action: dict, params: dict) -> dict:
    """
    Extract named entities like project names, vendor names, user names from the message.
    """
    text_lower = raw_text.lower()

    # Project name extraction (simple heuristic)
    if "project_name" in action.get("params", []) and "project_name" not in params:
        # Look for "de/del/proyecto/project" followed by a name
        project_patterns = [
            r'(?:de|del|proyecto|project)\s+([A-Z][a-zA-Z\s]+?)(?:\s|$|,)',
            r'(?:gastos?|tareas?)\s+(?:de|del)\s+([A-Z][a-zA-Z\s]+?)(?:\s|$|,)',
        ]
        for pattern in project_patterns:
            match = re.search(pattern, raw_text)
            if match:
                params["project_name"] = match.group(1).strip()
                break

    # Vendor name extraction
    if "vendor_name" in action.get("params", []) and "vendor_name" not in params:
        vendor_patterns = [
            r'(?:vendor|proveedor)\s+([A-Z][a-zA-Z\s]+?)(?:\s|$|,)',
            r'(?:de)\s+([A-Z][a-zA-Z\s]+?)(?:\s|$|,)',
        ]
        for pattern in vendor_patterns:
            match = re.search(pattern, raw_text)
            if match:
                params["vendor_name"] = match.group(1).strip()
                break

    # User name extraction
    if "user_name" in action.get("params", []) and "user_name" not in params:
        # Check for "my tasks" / "mis tareas"
        if "mis tareas" in text_lower or "my tasks" in text_lower:
            params["user_name"] = "__CURRENT_USER__"
        else:
            user_patterns = [
                r'(?:de|a|asignadas?\s+a)\s+([A-Z][a-zA-Z]+)(?:\s|$|,)',
            ]
            for pattern in user_patterns:
                match = re.search(pattern, raw_text)
                if match:
                    params["user_name"] = match.group(1).strip()
                    break

    # Query extraction for search
    if "query" in action.get("params", []) and "query" not in params:
        search_patterns = [
            r'(?:buscar?|search|encontrar?|find)\s+(.+)',
        ]
        for pattern in search_patterns:
            match = re.search(pattern, text_lower)
            if match:
                params["query"] = match.group(1).strip()
                break

    # Date range extraction
    if "start_date" in action.get("params", []) or "end_date" in action.get("params", []):
        today = datetime.now()

        if "este mes" in text_lower or "this month" in text_lower:
            params["start_date"] = today.replace(day=1).strftime("%Y-%m-%d")
            params["end_date"] = today.strftime("%Y-%m-%d")
        elif "mes pasado" in text_lower or "last month" in text_lower:
            first_of_this_month = today.replace(day=1)
            last_month_end = first_of_this_month - timedelta(days=1)
            last_month_start = last_month_end.replace(day=1)
            params["start_date"] = last_month_start.strftime("%Y-%m-%d")
            params["end_date"] = last_month_end.strftime("%Y-%m-%d")
        elif "ultima semana" in text_lower or "last week" in text_lower:
            params["start_date"] = (today - timedelta(days=7)).strftime("%Y-%m-%d")
            params["end_date"] = today.strftime("%Y-%m-%d")

    return params
