# api/security_log.py
"""
Security event logging for the public-facing API: failed/successful logins,
permission denials, demo write-blocks and rate-limit hits. Emits structured
lines to stdout (captured by Render's log stream) through a dedicated logger so
these stay greppable and independent of the rest of the app's logging.

Also the single source of truth for resolving the real client IP, used both here
and by the rate limiter — Cloudflare-aware so it keeps working if Cloudflare is
ever put in front of the API.
"""

import logging
import sys

logger = logging.getLogger("ngm.security")
if not logger.handlers:
    _handler = logging.StreamHandler(sys.stdout)
    _handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s [security] %(message)s"))
    logger.addHandler(_handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False  # own handler; don't double-log via root


def client_ip(request) -> str:
    """
    Resolve the originating client IP, preferring the most trustworthy header:
      1. CF-Connecting-IP — set by Cloudflare to the true client (if proxied).
      2. First hop of X-Forwarded-For — set by Render's proxy.
      3. The socket peer as a last resort.
    """
    cf = request.headers.get("cf-connecting-ip")
    if cf:
        return cf.strip()
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    client = getattr(request, "client", None)
    return client.host if client else "unknown"
