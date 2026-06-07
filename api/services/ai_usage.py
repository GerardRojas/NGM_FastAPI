"""
AI usage ledger — token + estimated cost logging for every OpenAI call.

Writes one row per call into the `ai_usage` table (fire-and-forget, never
raises, never blocks the AI call). Read back by api/routers/ai_usage.py for the
IT > "AI Usage" page.

Two ways to attribute a call to a feature/company:
  1) Explicit kwargs on log_ai_usage(...).
  2) A contextvar set by the request/flow via ai_context(...) / set_ai_context(...)
     or the @ai_feature("...") decorator. gpt_client reads it at log time.

PRICING is a STATIC estimate (USD per 1M tokens, input/output). Keep it in sync
with the real OpenAI pricing; cost_usd is an estimate, not the billed amount.
"""

import asyncio
import contextvars
import functools
import logging
from contextlib import contextmanager
from typing import Optional

from api.supabase_client import supabase

logger = logging.getLogger(__name__)

# USD per 1,000,000 tokens: (input, output). Mirror gpt_client header comments.
PRICING = {
    "gpt-5-mini": (0.25, 2.00),
    "gpt-5.2": (1.75, 14.00),
    "gpt-4o-mini": (0.15, 0.60),
}

# Per-request attribution context (feature / company_id / user_id).
_ctx: contextvars.ContextVar[Optional[dict]] = contextvars.ContextVar("ai_usage_ctx", default=None)

# Keep references to fire-and-forget background logging tasks so they aren't GC'd.
_bg_tasks: set = set()


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Estimated USD cost for a call from the static PRICING table."""
    price_in, price_out = PRICING.get(model, (0.0, 0.0))
    cost = (input_tokens or 0) * price_in / 1_000_000 + (output_tokens or 0) * price_out / 1_000_000
    return round(cost, 6)


def set_ai_context(feature: Optional[str] = None, company_id: Optional[str] = None,
                   user_id: Optional[str] = None) -> None:
    """Set the attribution context for the current task/thread."""
    prev = _ctx.get() or {}
    _ctx.set({
        "feature": feature if feature is not None else prev.get("feature"),
        "company_id": company_id if company_id is not None else prev.get("company_id"),
        "user_id": user_id if user_id is not None else prev.get("user_id"),
    })


def clear_ai_context() -> None:
    _ctx.set(None)


@contextmanager
def ai_context(feature: Optional[str] = None, company_id: Optional[str] = None,
               user_id: Optional[str] = None):
    """Scoped attribution context; inherits unset fields from any parent and
    restores the previous context on exit (safe to nest)."""
    prev = _ctx.get()
    base = prev or {}
    token = _ctx.set({
        "feature": feature if feature is not None else base.get("feature"),
        "company_id": company_id if company_id is not None else base.get("company_id"),
        "user_id": user_id if user_id is not None else base.get("user_id"),
    })
    try:
        yield
    finally:
        _ctx.reset(token)


def ai_feature(feature: str):
    """Decorator: tag every AI call made inside `fn` with `feature`."""
    def deco(fn):
        if asyncio.iscoroutinefunction(fn):
            @functools.wraps(fn)
            async def aw(*args, **kwargs):
                with ai_context(feature=feature):
                    return await fn(*args, **kwargs)
            return aw

        @functools.wraps(fn)
        def sw(*args, **kwargs):
            with ai_context(feature=feature):
                return fn(*args, **kwargs)
        return sw
    return deco


def log_ai_usage(model: str, input_tokens: int = 0, output_tokens: int = 0,
                 latency_ms: Optional[int] = None, success: bool = True,
                 feature: Optional[str] = None, company_id: Optional[str] = None,
                 user_id: Optional[str] = None, source: Optional[str] = None) -> None:
    """Insert one ai_usage row. Fire-and-forget; never raises."""
    try:
        ctx = _ctx.get() or {}
        in_tok = int(input_tokens or 0)
        out_tok = int(output_tokens or 0)
        row = {
            "feature": feature or ctx.get("feature") or "unknown",
            "model": model,
            "input_tokens": in_tok,
            "output_tokens": out_tok,
            "total_tokens": in_tok + out_tok,
            "cost_usd": estimate_cost(model, in_tok, out_tok),
            "success": bool(success),
        }
        if latency_ms is not None:
            row["latency_ms"] = int(latency_ms)
        cid = company_id or ctx.get("company_id")
        uid = user_id or ctx.get("user_id")
        if cid:
            row["company_id"] = cid
        if uid:
            row["user_id"] = uid
        if source:
            row["source"] = source
        supabase.table("ai_usage").insert(row).execute()
    except Exception as exc:  # noqa: BLE001 — telemetry must never break the call
        logger.warning("[AI Usage] Failed to log: %s", exc)


def log_ai_usage_bg(**kwargs) -> None:
    """Non-blocking variant for async/user-facing paths. Resolves the
    attribution context immediately, then offloads the insert to a thread."""
    ctx = _ctx.get() or {}
    kwargs.setdefault("feature", ctx.get("feature"))
    kwargs.setdefault("company_id", ctx.get("company_id"))
    kwargs.setdefault("user_id", ctx.get("user_id"))
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        log_ai_usage(**kwargs)
        return
    task = loop.create_task(asyncio.to_thread(log_ai_usage, **kwargs))
    _bg_tasks.add(task)
    task.add_done_callback(_bg_tasks.discard)
