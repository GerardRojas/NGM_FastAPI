import os
import httpx
from supabase import create_client, Client, ClientOptions

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
    raise RuntimeError("Faltan SUPABASE_URL o SUPABASE_SERVICE_ROLE_KEY en el entorno")

# Shared httpx client with tuned pooling for concurrent requests.
# NOTE:
# - We intentionally keep HTTP/1.1 (http2=False) because some upstream
#   disconnects may surface as RemoteProtocolError with HTTP/2 streams.
# - Transport retries help with transient connect-level failures.
_transport = httpx.HTTPTransport(retries=2)
_http_client = httpx.Client(
    transport=_transport,
    limits=httpx.Limits(
        max_connections=100,
        max_keepalive_connections=40,
        keepalive_expiry=10,
    ),
    timeout=httpx.Timeout(120.0),
    http2=False,
    follow_redirects=True,
)

supabase: Client = create_client(
    SUPABASE_URL,
    SUPABASE_SERVICE_ROLE_KEY,
    options=ClientOptions(httpx_client=_http_client),
)
