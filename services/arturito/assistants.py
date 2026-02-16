# services/arturito/assistants.py
# ================================
# OpenAI Assistants API Integration
# ================================
# Manages Arturito assistant and conversation threads
# More efficient than sending full history each time

from typing import Dict, Optional, Tuple
from openai import OpenAI
import os
import time

from .persona import get_persona_prompt, get_personality_level, BOT_NAME

# ================================
# CONFIGURATION
# ================================

# Cache for assistant ID (created once, reused)
_assistant_cache: Dict[str, str] = {}  # personality_level -> assistant_id

# Cache for threads (session_id -> thread_id) — capped to prevent unbounded growth
_thread_cache: Dict[str, str] = {}
_THREAD_CACHE_MAX = 500

# Model to use
MODEL = "gpt-5-mini"


def _get_client() -> Optional[OpenAI]:
    """Get OpenAI client if API key is configured"""
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return None
    return OpenAI(api_key=api_key)


# ================================
# ASSISTANT MANAGEMENT
# ================================

def get_or_create_assistant(personality_level: int = 3) -> Tuple[Optional[str], Optional[str]]:
    """
    Get or create an assistant for the given personality level.
    Returns (assistant_id, error_message)
    """
    cache_key = f"arturito_v1_level_{personality_level}"

    # Check cache first
    if cache_key in _assistant_cache:
        return _assistant_cache[cache_key], None

    client = _get_client()
    if not client:
        return None, "OpenAI API key not configured"

    try:
        # Build instructions based on personality level
        instructions = get_persona_prompt(f"assistant_level_{personality_level}")

        # Try to find existing assistant by name
        assistants = client.beta.assistants.list(limit=100)
        for assistant in assistants.data:
            if assistant.name == f"{BOT_NAME} (Level {personality_level})":
                _assistant_cache[cache_key] = assistant.id
                return assistant.id, None

        # Create new assistant if not found
        assistant = client.beta.assistants.create(
            name=f"{BOT_NAME} (Level {personality_level})",
            instructions=instructions,
            model=MODEL,
        )

        _assistant_cache[cache_key] = assistant.id
        return assistant.id, None

    except Exception as e:
        return None, f"Error creating assistant: {str(e)}"


def update_assistant_instructions(assistant_id: str, personality_level: int) -> Optional[str]:
    """
    Update an assistant's instructions when personality changes.
    Returns error message or None on success.
    """
    client = _get_client()
    if not client:
        return "OpenAI API key not configured"

    try:
        instructions = get_persona_prompt(f"assistant_level_{personality_level}")
        client.beta.assistants.update(
            assistant_id,
            instructions=instructions
        )
        return None
    except Exception as e:
        return f"Error updating assistant: {str(e)}"


# ================================
# THREAD MANAGEMENT
# ================================

def get_or_create_thread(session_id: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Get or create a thread for the given session.
    Returns (thread_id, error_message)
    """
    # Check cache first
    if session_id in _thread_cache:
        return _thread_cache[session_id], None

    client = _get_client()
    if not client:
        return None, "OpenAI API key not configured"

    try:
        thread = client.beta.threads.create()
        # Cap cache size: evict oldest entries when limit reached
        if len(_thread_cache) >= _THREAD_CACHE_MAX:
            keys = list(_thread_cache.keys())
            for k in keys[: len(keys) // 2]:
                del _thread_cache[k]
        _thread_cache[session_id] = thread.id
        return thread.id, None
    except Exception as e:
        return None, f"Error creating thread: {str(e)}"


def get_thread_id(session_id: str) -> Optional[str]:
    """Get cached thread ID for a session"""
    return _thread_cache.get(session_id)


def set_thread_id(session_id: str, thread_id: str):
    """Store thread ID for a session (useful when client provides it)"""
    _thread_cache[session_id] = thread_id


def clear_thread(session_id: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Clear a thread and create a new one for the session.
    Returns (new_thread_id, error_message)
    """
    # Remove from cache
    if session_id in _thread_cache:
        del _thread_cache[session_id]

    # Create new thread
    return get_or_create_thread(session_id)


# ================================
# CHAT FUNCTIONS
# ================================

def send_message_and_get_response(
    session_id: str,
    message: str,
    personality_level: int = 3,
    user_name: Optional[str] = None,
    thread_id: Optional[str] = None
) -> Tuple[str, Optional[str], Optional[str]]:
    """
    Send a message to Arturito and get a response using Assistants API.

    Args:
        session_id: Unique session identifier
        message: The user's message
        personality_level: 1-5 personality level
        user_name: Optional user name to personalize responses
        thread_id: Optional existing thread ID (if client has it)

    Returns:
        Tuple of (response_text, thread_id, error_message)
    """
    client = _get_client()
    if not client:
        return "OpenAI no está configurado.", None, "API key not configured"

    try:
        # Get or create assistant
        assistant_id, error = get_or_create_assistant(personality_level)
        if error:
            return f"Error: {error}", None, error

        # Get or create thread
        if thread_id:
            # Client provided thread_id, use it
            set_thread_id(session_id, thread_id)
            current_thread_id = thread_id
        else:
            current_thread_id, error = get_or_create_thread(session_id)
            if error:
                return f"Error: {error}", None, error

        # Add user context to message if available
        full_message = message
        if user_name:
            full_message = f"[Usuario: {user_name}]\n\n{message}"

        # Add message to thread
        client.beta.threads.messages.create(
            thread_id=current_thread_id,
            role="user",
            content=full_message
        )

        # Run the assistant
        run = client.beta.threads.runs.create(
            thread_id=current_thread_id,
            assistant_id=assistant_id
        )

        # Wait for completion (with timeout)
        max_wait = 30  # seconds
        start_time = time.time()

        while run.status in ["queued", "in_progress"]:
            if time.time() - start_time > max_wait:
                return "La respuesta tardó demasiado. Intenta de nuevo.", current_thread_id, "Timeout"

            time.sleep(0.5)
            run = client.beta.threads.runs.retrieve(
                thread_id=current_thread_id,
                run_id=run.id
            )

        if run.status == "completed":
            # Get the latest message
            messages = client.beta.threads.messages.list(
                thread_id=current_thread_id,
                limit=1,
                order="desc"
            )

            if messages.data and messages.data[0].role == "assistant":
                content = messages.data[0].content[0]
                if hasattr(content, 'text'):
                    return content.text.value, current_thread_id, None

            return "No pude generar una respuesta.", current_thread_id, "No response"

        elif run.status == "failed":
            error_msg = run.last_error.message if run.last_error else "Unknown error"
            return f"Error: {error_msg}", current_thread_id, error_msg

        else:
            return f"Estado inesperado: {run.status}", current_thread_id, f"Unexpected status: {run.status}"

    except Exception as e:
        return f"Error: {str(e)}", None, str(e)


def get_thread_messages(thread_id: str, limit: int = 20) -> Tuple[list, Optional[str]]:
    """
    Get messages from a thread.
    Returns (messages_list, error_message)
    """
    client = _get_client()
    if not client:
        return [], "OpenAI API key not configured"

    try:
        messages = client.beta.threads.messages.list(
            thread_id=thread_id,
            limit=limit,
            order="asc"
        )

        result = []
        for msg in messages.data:
            content = msg.content[0].text.value if msg.content else ""
            result.append({
                "role": msg.role,
                "content": content,
                "created_at": msg.created_at
            })

        return result, None

    except Exception as e:
        return [], str(e)
