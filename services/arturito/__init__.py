# services/arturito/__init__.py
# ================================
# ARTURITO - NGM Chat Bot Engine
# ================================
# Modular chatbot system migrated from Google Apps Script

from .router import route, route_slash_command, ROUTES
from .nlu import interpret_message
from .persona import get_persona_prompt, set_personality_level, get_personality_level

__all__ = [
    "route",
    "route_slash_command",
    "ROUTES",
    "interpret_message",
    "get_persona_prompt",
    "set_personality_level",
    "get_personality_level",
]
