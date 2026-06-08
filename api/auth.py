# api/auth.py

import os
import logging
from fastapi import APIRouter, HTTPException, Header, Depends, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from postgrest.exceptions import APIError
from datetime import datetime, timedelta, timezone
import jwt

from utils.auth import hash_password, verify_password
from api.supabase_client import supabase
from api.rate_limit import limiter
from api.security_log import logger as security_logger, client_ip

logger = logging.getLogger(__name__)

JWT_SECRET = os.getenv("JWT_SECRET")
if not JWT_SECRET:
    # Fail closed: never run with a guessable/default signing secret. Tokens signed
    # with a known secret can be forged, which would defeat every auth check below.
    raise RuntimeError("Falta JWT_SECRET en el entorno")
JWT_ALG = "HS256"
JWT_EXPIRES_MIN = int(os.getenv("JWT_EXPIRES_MIN", "2880"))  # 2 días

router = APIRouter(prefix="/auth", tags=["auth"])


# ====== DEPENDENCY: Get current user from JWT ======

security = HTTPBearer()

def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)) -> dict:
    """
    Dependency to extract and verify JWT token from Authorization header.
    Returns user info from token payload.
    """
    token = credentials.credentials

    try:
        decoded = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALG])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token has expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")
    except Exception:
        raise HTTPException(status_code=401, detail="Could not validate credentials")

    user_id = decoded.get("sub")
    username = decoded.get("username")
    role = decoded.get("role")

    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid token payload")

    return {
        "user_id": user_id,
        "username": username,
        "role": role,
        "account_type": decoded.get("account_type") or "internal",
        "client_id": decoded.get("client_id"),
    }


# ====== DEPENDENCIES: account-type gates (client portal) ======

def require_internal(current_user: dict = Depends(get_current_user)) -> dict:
    """
    Guard for internal-only endpoints: rejects external client accounts.
    Defense-in-depth — clients should never reach the internal API surface.
    """
    if (current_user.get("account_type") or "internal") == "client":
        raise HTTPException(status_code=403, detail="Internal access only")
    return current_user


# Roles considered "leadership" for management-only endpoints (demo users, role/
# permission edits, company management, user creation). Keep in sync with the
# pattern in routers/demo_admin.py::_require_leadership.
LEADERSHIP_ROLES = {"ceo", "coo"}


def require_leadership(current_user: dict = Depends(get_current_user)) -> dict:
    """
    Guard for management-only endpoints: only CEO/COO may pass. Used for sensitive
    administrative surface (creating users, editing roles/permissions, company CRUD).
    """
    role = str(current_user.get("role") or "").strip().lower()
    if role not in LEADERSHIP_ROLES:
        raise HTTPException(status_code=403, detail="Only CEO/COO can perform this action.")
    return current_user


def get_current_client(current_user: dict = Depends(get_current_user)) -> dict:
    """
    Dependency for the /portal router: requires an external client account and
    exposes its client_id. The portal resolves all data scope from this — never
    from request parameters — so a client can only ever see their own data.
    """
    if (current_user.get("account_type") or "internal") != "client":
        raise HTTPException(status_code=403, detail="Client portal access only")
    client_id = current_user.get("client_id")
    if not client_id:
        raise HTTPException(status_code=403, detail="Client account is not linked to a client")
    return {
        "user_id": current_user.get("user_id"),
        "username": current_user.get("username"),
        "client_id": client_id,
    }


def demo_account_from_request(authorization: "str | None") -> bool:
    """
    Lightweight check (no Depends) for the global write-block middleware: returns
    True only when the Authorization header carries a valid demo-account token.
    Invalid/absent tokens return False so the endpoint's own auth still runs.
    """
    if not authorization or not authorization.lower().startswith("bearer "):
        return False
    token = authorization.split(" ", 1)[1].strip()
    try:
        decoded = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALG])
    except Exception:
        return False
    return (decoded.get("account_type") or "internal") == "demo"


# ====== MODELOS Pydantic ======

class LoginRequest(BaseModel):
    username: str
    password: str


class CreateUserRequest(BaseModel):
    username: str
    password: str
    user_rol: str | int           # FK a tabla rols (id o clave)
    user_seniority: str | None = None  # opcional


class AcceptInviteRequest(BaseModel):
    token: str
    password: str
    display_name: str | None = None


# ====== HELPERS ======

def make_access_token(
    user_id: str,
    username: str,
    role: str | None,
    account_type: str = "internal",
    client_id: str | None = None,
) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "username": username,
        "role": role,
        # External-client support: the /portal router resolves its entire data
        # scope from these two claims alone (never from request params).
        "account_type": account_type or "internal",
        "client_id": str(client_id) if client_id else None,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=JWT_EXPIRES_MIN)).timestamp()),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALG)


# ====== ENDPOINT: Crear usuario (para ti / admin) ======

@router.post("/create_user")
def create_user(payload: CreateUserRequest, current_user: dict = Depends(require_leadership)):
    hashed = hash_password(payload.password)

    data_to_insert = {
        "user_name": payload.username,
        "password_hash": hashed,
        "user_rol": payload.user_rol,
    }

    # Sólo incluimos seniority si viene
    if payload.user_seniority is not None:
        data_to_insert["user_seniority"] = payload.user_seniority

    try:
        result = (
            supabase.table("users")
            .insert(data_to_insert)
            .execute()
        )
    except APIError as e:
        logger.warning("APIError en /auth/create_user: %r", e)
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error("Error inesperado en /auth/create_user: %r", e)
        raise HTTPException(status_code=500, detail="Error creating user")

    user = result.data[0] if isinstance(result.data, list) else result.data
    if not user:
        raise HTTPException(status_code=500, detail="User not returned after creation")

    return {
        "message": "User created",
        "user": {
            "id": user.get("user_id"),
            "username": user.get("user_name"),
            "role": user.get("user_rol"),
            "seniority": user.get("user_seniority"),
        },
    }


# ====== ENDPOINT: Login ======

@router.post("/login")
@limiter.limit("10/minute")
def login(request: Request, payload: LoginRequest):
    ip = client_ip(request)
    # 1) Buscar usuario + rol embebido (1 sola query en vez de 2)
    try:
        result = (
            supabase.table("users")
            .select("*, rols!users_user_rol_fkey(rol_name)")
            .eq("user_name", payload.username)
            .single()
            .execute()
        )
    except APIError:
        security_logger.warning("login_failed user=%r ip=%s reason=user_not_found", payload.username, ip)
        raise HTTPException(status_code=401, detail="Invalid username or password")
    except Exception as e:
        logger.error("Error en /auth/login query: %r", e)
        raise HTTPException(status_code=500, detail="Error querying user store")

    user = result.data
    if not user:
        security_logger.warning("login_failed user=%r ip=%s reason=user_not_found", payload.username, ip)
        raise HTTPException(status_code=401, detail="Invalid username or password")

    hashed = user.get("password_hash")
    if not hashed:
        raise HTTPException(
            status_code=500,
            detail="User has no password configured (missing password_hash)",
        )

    # 2) Verificar password
    pw = payload.password or ""
    try:
        ok = verify_password(pw, hashed)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid username or password")
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid username or password")

    if not ok:
        security_logger.warning("login_failed user=%r ip=%s reason=bad_password", payload.username, ip)
        raise HTTPException(status_code=401, detail="Invalid username or password")

    # 3) Extraer rol del join embebido (sin segunda query)
    role_id = user.get("user_rol")
    seniority_id = user.get("user_seniority")

    rols_data = user.get("rols")
    role_name = rols_data.get("rol_name") if rols_data else None

    # 4) Generar token (funciona con o sin rol)
    access_token = make_access_token(
        user_id=str(user.get("user_id")),
        username=user.get("user_name"),
        role=role_name,
        account_type=user.get("account_type") or "internal",
        client_id=user.get("client_id"),
    )

    security_logger.info("login_ok user=%r ip=%s role=%s", user.get("user_name"), ip, role_name)

    # 5) Respuesta OK
    seniority_name = None

    return {
        "message": "Login ok",
        "access_token": access_token,
        "token_type": "bearer",
        "user": {
            # Frontend expects these exact field names
            "user_id": user.get("user_id"),
            "user_name": user.get("user_name"),

            # Legacy/compatibility names
            "id": user.get("user_id"),
            "username": user.get("user_name"),

            # IDs crudos (Fks)
            "role_id": role_id,
            "seniority_id": seniority_id,

            # Labels legibles
            "role": role_name,
            "seniority": seniority_name,
        },
    }


# ====== CLIENT PORTAL: magic-link invitation onboarding ======

def _decode_invite(token: str) -> dict:
    """Decode + validate a client_invite JWT and its DB row (status/expiry)."""
    try:
        decoded = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALG])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=400, detail="This invitation has expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=400, detail="Invalid invitation link")
    if decoded.get("type") != "client_invite":
        raise HTTPException(status_code=400, detail="Invalid invitation link")

    res = supabase.table("client_invites").select("*").eq("token", token).execute()
    if not res.data:
        raise HTTPException(status_code=404, detail="Invitation not found")
    invite = res.data[0]
    if invite["status"] == "accepted":
        raise HTTPException(status_code=400, detail="This invitation has already been used")
    if invite["status"] == "revoked":
        raise HTTPException(status_code=400, detail="This invitation has been revoked")
    return invite


@router.get("/invite/verify")
def verify_invite(token: str):
    """Public: validate an invite and return who it's for (powers the accept page)."""
    invite = _decode_invite(token)
    client_name = None
    try:
        c = supabase.table("clients").select("client_name").eq("client_id", invite["client_id"]).single().execute()
        client_name = (c.data or {}).get("client_name")
    except Exception:
        pass
    return {
        "valid": True,
        "email": invite["email"],
        "client_id": invite["client_id"],
        "client_name": client_name,
    }


@router.post("/invite/accept")
def accept_invite(payload: AcceptInviteRequest):
    """
    Public: accept a magic-link invitation. Provisions a client account
    (account_type='client', linked to the client) and auto-logs the user in.
    """
    invite = _decode_invite(payload.token)
    if len(payload.password or "") < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")

    email = invite["email"]
    # Don't hijack an existing account — the email/username must be free.
    existing = supabase.table("users").select("user_id").eq("user_name", email).execute()
    if existing.data:
        raise HTTPException(status_code=409, detail="An account already exists for this email")

    try:
        created = supabase.table("users").insert({
            "user_name": email,
            "password_hash": hash_password(payload.password),
            "account_type": "client",
            "client_id": invite["client_id"],
            "is_external": True,
        }).execute()
    except Exception as e:
        logger.error("Error provisioning client account: %r", e)
        raise HTTPException(status_code=500, detail="Could not create client account")

    user = created.data[0] if created.data else None
    if not user:
        raise HTTPException(status_code=500, detail="Account not returned after creation")

    supabase.table("client_invites").update({
        "status": "accepted",
        "accepted_at": datetime.now(timezone.utc).isoformat(),
    }).eq("id", invite["id"]).execute()

    access_token = make_access_token(
        user_id=str(user.get("user_id")),
        username=email,
        role=None,
        account_type="client",
        client_id=invite["client_id"],
    )
    return {
        "message": "Invitation accepted",
        "access_token": access_token,
        "token_type": "bearer",
        "user": {
            "user_id": user.get("user_id"),
            "user_name": email,
            "account_type": "client",
            "client_id": invite["client_id"],
        },
    }


@router.get("/me")
def me(authorization: str | None = Header(default=None)):
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")

    token = authorization.split(" ", 1)[1].strip()
    try:
        decoded = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALG])
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid token")

    return {
        "user_name": decoded.get("username"),
        "user_role": decoded.get("role"),
        "user_id": decoded.get("sub"),
    }
