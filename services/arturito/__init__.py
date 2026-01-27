# services/arturito/__init__.py
# ================================
# ARTURITO - NGM Chat Bot Engine
# ================================
# Modular chatbot system with OpenAI Assistants API integration

from .router import route, route_slash_command, ROUTES
from .nlu import interpret_message
from .persona import get_persona_prompt, set_personality_level, get_personality_level
from .assistants import (
    send_message_and_get_response,
    get_or_create_assistant,
    get_or_create_thread,
    clear_thread,
    get_thread_id,
)

__all__ = [
    # Router
    "route",
    "route_slash_command",
    "ROUTES",
    # NLU
    "interpret_message",
    # Persona
    "get_persona_prompt",
    "set_personality_level",
    "get_personality_level",
    # Assistants API
    "send_message_and_get_response",
    "get_or_create_assistant",
    "get_or_create_thread",
    "clear_thread",
    "get_thread_id",
]
