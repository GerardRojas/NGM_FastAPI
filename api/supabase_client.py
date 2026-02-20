import os
import httpx
from supabase import create_client, Client, ClientOptions

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
    raise RuntimeError("Faltan SUPABASE_URL o SUPABASE_SERVICE_ROLE_KEY en el entorno")

# Shared httpx client with higher keepalive to handle bulk concurrent requests.
# Default httpx limits: max_connections=100, max_keepalive=20.
# Under bulk operations (40+ concurrent PATCH), low keepalive forces too many
# fresh socket opens → Errno 11 "Resource temporarily unavailable".
_http_client = httpx.Client(
    limits=httpx.Limits(
        max_connections=100,
        max_keepalive_connections=40,
        keepalive_expiry=30,
    ),
    timeout=httpx.Timeout(120.0),
    http2=True,
    follow_redirects=True,
)

supabase: Client = create_client(
    SUPABASE_URL,
    SUPABASE_SERVICE_ROLE_KEY,
    options=ClientOptions(httpx_client=_http_client),
)
