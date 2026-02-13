# api/services/gpt_client.py
# ============================================================================
# Centralized GPT Client - Unified interface for OpenAI model tiers
# ============================================================================
# Two tiers:
#   mini  -> gpt-5-mini  ($0.25/$2.00 per 1M tokens) via responses API
#   heavy -> gpt-5.2     ($1.75/$14.00 per 1M tokens) via chat.completions
#
# Usage:
#   from api.services.gpt_client import gpt
#
#   # Sync
#   result = gpt.mini("You classify expenses.", "Drywall 50 sheets")
#   result = gpt.heavy("You extract OCR data.", user_content, json_mode=True)
#
#   # Async
#   result = await gpt.mini_async("You route requests.", "@Andrew receipt...")
#   result = await gpt.heavy_async("You reconcile.", text, temperature=0.1)
#
#   # Fallback (mini first, heavy if confidence < threshold)
#   result = await gpt.with_fallback_async("Route this.", input_text, min_confidence=0.9)
# ============================================================================

import os
import json
import logging
import time
from typing import Optional, Union

from openai import OpenAI, AsyncOpenAI

logger = logging.getLogger(__name__)

# ── Model constants ──────────────────────────────────────────────
MINI_MODEL = "gpt-5-mini"
HEAVY_MODEL = "gpt-5.2"

# ── Singleton clients ────────────────────────────────────────────
_sync_client: Optional[OpenAI] = None
_async_client: Optional[AsyncOpenAI] = None


def _get_sync() -> OpenAI:
    global _sync_client
    if _sync_client is None:
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY not configured")
        _sync_client = OpenAI(api_key=api_key, timeout=30.0)
    return _sync_client


def _get_async() -> AsyncOpenAI:
    global _async_client
    if _async_client is None:
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY not configured")
        _async_client = AsyncOpenAI(api_key=api_key, timeout=30.0)
    return _async_client


# ── Mini tier (gpt-5-mini via responses API) ─────────────────────

def mini(instructions: str, input: str, json_mode: bool = False,
         max_tokens: int = 400, timeout: Optional[float] = None) -> Optional[str]:
    """Sync call to gpt-5-mini via responses API.

    Args:
        instructions: System-level instructions (like system prompt).
        input: User message content.
        json_mode: If True, appends JSON-only instruction.
        max_tokens: Max output tokens.
        timeout: Override default 30s timeout (e.g. 90 for large OCR prompts).

    Returns:
        Response text or None on failure.
    """
    t0 = time.monotonic()
    try:
        client = _get_sync()
        inst = instructions
        if json_mode:
            inst += "\n\nReturn ONLY valid JSON. No markdown, no explanation, no code fences."
        kwargs = {
            "model": MINI_MODEL,
            "instructions": inst,
            "input": input,
            "max_output_tokens": max_tokens,
        }
        if timeout:
            kwargs["timeout"] = timeout
        r = client.responses.create(**kwargs)
        text = r.output_text if hasattr(r, "output_text") else ""
        result = text.strip() if text and text.strip() else None
        ms = int((time.monotonic() - t0) * 1000)
        logger.info("[GPT:mini] %s %dms %s", MINI_MODEL, ms, "OK" if result else "EMPTY")
        return result
    except Exception as e:
        ms = int((time.monotonic() - t0) * 1000)
        logger.warning("[GPT:mini] %s %dms FAIL: %s", MINI_MODEL, ms, e)
        return None


async def mini_async(instructions: str, input: str, json_mode: bool = False,
                     max_tokens: int = 400,
                     timeout: Optional[float] = None) -> Optional[str]:
    """Async call to gpt-5-mini via responses API."""
    t0 = time.monotonic()
    try:
        client = _get_async()
        inst = instructions
        if json_mode:
            inst += "\n\nReturn ONLY valid JSON. No markdown, no explanation, no code fences."
        kwargs = {
            "model": MINI_MODEL,
            "instructions": inst,
            "input": input,
            "max_output_tokens": max_tokens,
        }
        if timeout:
            kwargs["timeout"] = timeout
        r = await client.responses.create(**kwargs)
        text = r.output_text if hasattr(r, "output_text") else ""
        result = text.strip() if text and text.strip() else None
        ms = int((time.monotonic() - t0) * 1000)
        logger.info("[GPT:mini_async] %s %dms %s", MINI_MODEL, ms, "OK" if result else "EMPTY")
        return result
    except Exception as e:
        ms = int((time.monotonic() - t0) * 1000)
        logger.warning("[GPT:mini_async] %s %dms FAIL: %s", MINI_MODEL, ms, e)
        return None


# ── Heavy tier (gpt-5.2 via chat.completions) ────────────────────

def heavy(system: str, user: Union[str, list], temperature: float = 0.1,
          max_tokens: int = 500, json_mode: bool = False,
          timeout: Optional[float] = None) -> Optional[str]:
    """Sync call to gpt-5.2 via chat.completions.

    Args:
        system: System prompt.
        user: User message (str for text, list for Vision content blocks).
        temperature: Sampling temperature (0.0-2.0).
        max_tokens: Max output tokens.
        json_mode: If True, uses response_format json_object.
        timeout: Override default 30s timeout (e.g. 120 for Vision OCR).

    Returns:
        Response text or None on failure.
    """
    t0 = time.monotonic()
    try:
        client = _get_sync()
        kwargs = {
            "model": HEAVY_MODEL,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temperature,
            "max_completion_tokens": max_tokens,
        }
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        if timeout:
            kwargs["timeout"] = timeout
        r = client.chat.completions.create(**kwargs)
        text = r.choices[0].message.content
        result = text.strip() if text and text.strip() else None
        ms = int((time.monotonic() - t0) * 1000)
        vision = isinstance(user, list)
        logger.info("[GPT:heavy] %s %dms %s%s", HEAVY_MODEL, ms,
                    "OK" if result else "EMPTY", " (vision)" if vision else "")
        return result
    except Exception as e:
        ms = int((time.monotonic() - t0) * 1000)
        logger.warning("[GPT:heavy] %s %dms FAIL: %s", HEAVY_MODEL, ms, e)
        return None


async def heavy_async(system: str, user: Union[str, list],
                      temperature: float = 0.1, max_tokens: int = 500,
                      json_mode: bool = False,
                      timeout: Optional[float] = None) -> Optional[str]:
    """Async call to gpt-5.2 via chat.completions."""
    t0 = time.monotonic()
    try:
        client = _get_async()
        kwargs = {
            "model": HEAVY_MODEL,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temperature,
            "max_completion_tokens": max_tokens,
        }
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        if timeout:
            kwargs["timeout"] = timeout
        r = await client.chat.completions.create(**kwargs)
        text = r.choices[0].message.content
        result = text.strip() if text and text.strip() else None
        ms = int((time.monotonic() - t0) * 1000)
        vision = isinstance(user, list)
        logger.info("[GPT:heavy_async] %s %dms %s%s", HEAVY_MODEL, ms,
                    "OK" if result else "EMPTY", " (vision)" if vision else "")
        return result
    except Exception as e:
        ms = int((time.monotonic() - t0) * 1000)
        logger.warning("[GPT:heavy_async] %s %dms FAIL: %s", HEAVY_MODEL, ms, e)
        return None


# ── Fallback pattern (mini -> heavy if low confidence) ───────────

def with_fallback(instructions: str, input: str, min_confidence: float = 0.9,
                  max_tokens: int = 400, temperature_heavy: float = 0.1) -> Optional[str]:
    """Sync: Try mini first; if confidence < threshold, retry with heavy.

    The JSON response MUST contain a 'confidence' field (0.0-1.0).
    """
    # Step 1: Try mini
    result_text = mini(instructions, input, json_mode=True, max_tokens=max_tokens)
    if result_text:
        try:
            parsed = json.loads(result_text)
            confidence = float(parsed.get("confidence", 0))
            if confidence >= min_confidence:
                logger.info("[GPT:fallback] mini confident (%.2f >= %.2f)", confidence, min_confidence)
                return result_text
            logger.info("[GPT:fallback] mini low confidence (%.2f < %.2f), escalating to heavy",
                        confidence, min_confidence)
        except (json.JSONDecodeError, ValueError, TypeError):
            logger.warning("[GPT:fallback] mini returned invalid JSON, escalating to heavy")

    # Step 2: Fallback to heavy
    system = instructions + "\n\nReturn ONLY valid JSON. No markdown, no explanation."
    result_text = heavy(system, input, temperature=temperature_heavy,
                        max_tokens=max_tokens, json_mode=True)
    if result_text:
        logger.info("[GPT:fallback] heavy responded")
    return result_text


async def with_fallback_async(instructions: str, input: str,
                              min_confidence: float = 0.9,
                              max_tokens: int = 400,
                              temperature_heavy: float = 0.1) -> Optional[str]:
    """Async: Try mini first; if confidence < threshold, retry with heavy."""
    # Step 1: Try mini
    result_text = await mini_async(instructions, input, json_mode=True, max_tokens=max_tokens)
    if result_text:
        try:
            parsed = json.loads(result_text)
            confidence = float(parsed.get("confidence", 0))
            if confidence >= min_confidence:
                logger.info("[GPT:fallback] mini confident (%.2f >= %.2f)", confidence, min_confidence)
                return result_text
            logger.info("[GPT:fallback] mini low confidence (%.2f < %.2f), escalating to heavy",
                        confidence, min_confidence)
        except (json.JSONDecodeError, ValueError, TypeError):
            logger.warning("[GPT:fallback] mini returned invalid JSON, escalating to heavy")

    # Step 2: Fallback to heavy
    system = instructions + "\n\nReturn ONLY valid JSON. No markdown, no explanation."
    result_text = await heavy_async(system, input, temperature=temperature_heavy,
                                    max_tokens=max_tokens, json_mode=True)
    if result_text:
        logger.info("[GPT:fallback] heavy responded")
    return result_text


# ── Convenience namespace ────────────────────────────────────────
# Allows: from api.services.gpt_client import gpt
#         gpt.mini(...), gpt.heavy(...), gpt.with_fallback_async(...)

class _GPTNamespace:
    mini = staticmethod(mini)
    mini_async = staticmethod(mini_async)
    heavy = staticmethod(heavy)
    heavy_async = staticmethod(heavy_async)
    with_fallback = staticmethod(with_fallback)
    with_fallback_async = staticmethod(with_fallback_async)
    MINI_MODEL = MINI_MODEL
    HEAVY_MODEL = HEAVY_MODEL

gpt = _GPTNamespace()
