# api/services/agent_attention.py
# ============================================================================
# Agent Attention Sessions
# ============================================================================
# When a user @mentions an agent, an "attention session" is created.
# For the next N messages (or T minutes), follow-up messages from that
# same user in the same channel are automatically routed to the agent
# WITHOUT requiring another @mention.
#
# This makes agent conversations feel natural:
#
#   User: @Andrew check my pending receipts
#   Andrew: You have 3 pending: #456, #457, #458
#   User: Process the first one          ← routed automatically
#   Andrew: Processing #456...
#   User: What about the second?         ← still in session
#   Andrew: #457 is from Home Depot...
#
# Session lifecycle:
#   - Created: when user @mentions an agent
#   - Extended: each follow-up resets the inactivity timer
#   - Ends: timeout, max messages reached, or user @mentions different agent
#
# Design decisions:
#   - In-memory (dict): Sessions are short-lived, no need for DB persistence
#   - Per user+channel: Same user can have sessions in different channels
#   - Only sender's messages: Other users' messages don't trigger the session
#   - Agent responses don't count: Only user messages consume the session
# ============================================================================

import time
import logging
from typing import Dict, Optional
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# ── Configuration ────────────────────────────────────────────────────────────
SESSION_TTL_SECONDS = 300       # 5 minutes of inactivity
SESSION_MAX_FOLLOWUPS = 5       # Max follow-up messages before session expires
SESSION_MAX_ENTRIES = 200       # Cap to prevent unbounded memory growth

# Patterns that signal the user is done with the conversation.
# If a follow-up matches these, the session ends after this message
# (the message still gets routed so the agent can reply naturally).
_CLOSING_PATTERNS = {
    "ok", "okay", "thanks", "thank you", "got it", "perfect",
    "cool", "great", "noted", "understood", "all good", "thats all",
    "that's all", "nothing else", "nvm", "nevermind", "never mind",
}


# ── Session data ─────────────────────────────────────────────────────────────
@dataclass
class AttentionSession:
    user_id: str
    agent_name: str
    channel_key: str            # e.g. "project_general:uuid"
    project_id: Optional[str]
    channel_type: str
    channel_id: Optional[str]
    created_at: float = field(default_factory=time.time)
    last_activity: float = field(default_factory=time.time)
    remaining: int = SESSION_MAX_FOLLOWUPS
    total_routed: int = 0       # How many follow-ups were routed


# ── Session store ────────────────────────────────────────────────────────────
# Key: "{user_id}:{channel_key}"
_sessions: Dict[str, AttentionSession] = {}


def _session_key(user_id: str, channel_key: str) -> str:
    return f"{user_id}:{channel_key}"


def _build_channel_key(channel_type: str, project_id: Optional[str],
                       channel_id: Optional[str]) -> str:
    """Build channel key matching the DB generated column pattern."""
    if channel_id and channel_type in ("custom", "direct", "group"):
        return f"{channel_type}:{channel_id}"
    elif project_id:
        return f"{channel_type}:{project_id}"
    return f"{channel_type}:unknown"


# ── Public API ───────────────────────────────────────────────────────────────

def start_session(
    user_id: str,
    agent_name: str,
    channel_type: str,
    project_id: Optional[str] = None,
    channel_id: Optional[str] = None,
) -> AttentionSession:
    """
    Start or restart an attention session for a user+channel.
    Called when an @mention is detected.
    If a session already exists for a DIFFERENT agent, it's replaced.
    """
    ck = _build_channel_key(channel_type, project_id, channel_id)
    key = _session_key(user_id, ck)

    # Evict if at capacity
    if len(_sessions) >= SESSION_MAX_ENTRIES:
        _cleanup_expired()
        # Still over? Drop oldest half
        if len(_sessions) >= SESSION_MAX_ENTRIES:
            sorted_keys = sorted(_sessions, key=lambda k: _sessions[k].last_activity)
            for k in sorted_keys[:len(sorted_keys) // 2]:
                del _sessions[k]

    session = AttentionSession(
        user_id=user_id,
        agent_name=agent_name,
        channel_key=ck,
        project_id=project_id,
        channel_type=channel_type,
        channel_id=channel_id,
    )
    _sessions[key] = session
    logger.info("[Attention] Session started: %s -> @%s in %s (max %d follow-ups, %ds TTL)",
                user_id[:8], agent_name, ck, SESSION_MAX_FOLLOWUPS, SESSION_TTL_SECONDS)
    return session


def check_session(
    user_id: str,
    channel_type: str,
    project_id: Optional[str] = None,
    channel_id: Optional[str] = None,
) -> Optional[AttentionSession]:
    """
    Check if there's an active attention session for this user+channel.
    Returns the session if active, None otherwise.
    Does NOT consume the session (call consume_session after routing).
    """
    ck = _build_channel_key(channel_type, project_id, channel_id)
    key = _session_key(user_id, ck)
    session = _sessions.get(key)

    if not session:
        return None

    # Check expiry
    now = time.time()
    if now - session.last_activity > SESSION_TTL_SECONDS:
        logger.info("[Attention] Session expired (inactivity): %s -> @%s", user_id[:8], session.agent_name)
        del _sessions[key]
        return None

    if session.remaining <= 0:
        logger.info("[Attention] Session expired (max messages): %s -> @%s", user_id[:8], session.agent_name)
        del _sessions[key]
        return None

    return session


def consume_session(
    user_id: str,
    channel_type: str,
    content: str,
    project_id: Optional[str] = None,
    channel_id: Optional[str] = None,
) -> Optional[AttentionSession]:
    """
    Consume one follow-up from the session.
    Updates last_activity and decrements remaining count.
    If the message is a closing signal, marks session for expiry after this message.
    Returns the session (for routing), or None if no active session.
    """
    ck = _build_channel_key(channel_type, project_id, channel_id)
    key = _session_key(user_id, ck)
    session = _sessions.get(key)

    if not session:
        return None

    # Check expiry
    now = time.time()
    if now - session.last_activity > SESSION_TTL_SECONDS:
        del _sessions[key]
        return None

    if session.remaining <= 0:
        del _sessions[key]
        return None

    # Consume
    session.last_activity = now
    session.remaining -= 1
    session.total_routed += 1

    # Check for closing signal
    normalized = content.strip().lower().rstrip(".!?,")
    if normalized in _CLOSING_PATTERNS:
        # Let this message route (agent can say goodbye), then end session
        session.remaining = 0
        logger.info("[Attention] Closing signal detected: '%s' -> ending session after response",
                    content[:30])

    logger.info("[Attention] Follow-up consumed: %s -> @%s | remaining=%d | text='%s'",
                user_id[:8], session.agent_name, session.remaining, content[:50])

    return session


def end_session(
    user_id: str,
    channel_type: str,
    project_id: Optional[str] = None,
    channel_id: Optional[str] = None,
) -> bool:
    """Explicitly end a session. Returns True if a session was ended."""
    ck = _build_channel_key(channel_type, project_id, channel_id)
    key = _session_key(user_id, ck)
    if key in _sessions:
        agent = _sessions[key].agent_name
        del _sessions[key]
        logger.info("[Attention] Session ended explicitly: %s -> @%s", user_id[:8], agent)
        return True
    return False


def get_active_sessions_count() -> int:
    """Return count of active sessions (for debug endpoint)."""
    return len(_sessions)


# ── Cleanup ──────────────────────────────────────────────────────────────────

def _cleanup_expired():
    """Remove expired sessions. Called by memory management loop."""
    now = time.time()
    expired = [
        k for k, s in _sessions.items()
        if (now - s.last_activity > SESSION_TTL_SECONDS) or s.remaining <= 0
    ]
    for k in expired:
        del _sessions[k]
    if expired:
        logger.info("[Attention] Cleaned up %d expired sessions", len(expired))


def cleanup():
    """Public cleanup function for the memory management loop in main.py."""
    _cleanup_expired()
