# api/rate_limit.py
"""
Shared rate limiter for abuse-prone public endpoints (login brute force, public
form spam). Defined in its own module so both main.py and the routers can import
the same Limiter instance without circular imports.

Keyed by the real client IP: behind Render's proxy the socket peer is the proxy,
so the actual client address comes from the X-Forwarded-For header. Storage is
in-memory, which is correct for a single instance; if the API is ever scaled to
multiple instances, point `storage_uri` at a shared Redis so buckets are global.
"""

from slowapi import Limiter

from api.security_log import client_ip


# Key buckets by the real client IP (Cloudflare-aware; see security_log.client_ip)
limiter = Limiter(key_func=client_ip)
