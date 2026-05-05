# api/auth.py

import os
import logging
from fastapi import APIRouter, HTTPException, Header, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from postgrest.exceptions import APIError
from datetime import datetime, timedelta, timezone
import jwt

from utils.auth import hash_password, verify_password
from api.supabase_client import supabase

logger = logging.getLogger(__name__)

JWT_SECRET = os.getenv("JWT_SECRET", "CHANGE_ME")
JWT_ALG = "HS256"
JWT_EXPIRES_MIN = int(os.getenv("JWT_EXPIRES_MIN", "2880"))  # 2 días

router = APIRouter(prefix="/auth", tags=["auth"])


# ====== MODELOS Pydantic ======

class LoginRequest(BaseModel):
    username: str
    password: str


class CreateUserRequest(BaseModel):
    username: str
    password: str
    user_rol: str | int           # FK a tabla rols (id o clave)
    user_seniority: str | None = None  # opcional
    user_token: str               # token del usuario solicitante


# ====== HELPERS ======

def make_access_token(user_id: str, username: str, role: str | None) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "username": username,
        "role": role,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=JWT_EXPIRES_MIN)).timestamp()),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALG)


def decode_access_token(token: str) -> dict:
    """Decode and validate JWT token."""
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALG])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token has expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")
    except Exception:
        raise HTTPException(status_code=401, detail="Could not validate credentials")


def _action_allowed(perm: dict, action: str) -> bool:
    action_map = {
        "view": perm.get("can_view", False),
        "edit": perm.get("can_edit", False),
        "delete": perm.get("can_delete", False),
    }
    return bool(action_map.get(action, False))


def _load_active_user_with_permissions(user_id: str) -> dict:
    """Load active user and role permissions in a single query."""
    try:
        response = (
            supabase.table("users")
            .select(
                "user_id, user_name, user_rol, rols!users_user_rol_fkey(rol_name, role_permissions(module_key, can_view, can_edit, can_delete))"
            )
            .eq("user_id", user_id)
            .single()
            .execute()
        )
    except Exception as e:
        logger.error("Error loading active user context: %r", e)
        raise HTTPException(status_code=500, detail="Error validating requester session")

    user_data = response.data
    if not user_data:
        raise HTTPException(status_code=401, detail="Requester session is not active")

    rols_data = user_data.get("rols") or {}
    permissions = rols_data.get("role_permissions") or []

    return {
        "user_id": user_data.get("user_id"),
        "username": user_data.get("user_name"),
        "role": rols_data.get("rol_name"),
        "user_rol": user_data.get("user_rol"),
        "permissions": permissions,
    }


def _assert_module_permission(user_context: dict, module_key: str, action: str) -> None:
    perms = user_context.get("permissions") or []
    perm = next((p for p in perms if p.get("module_key") == module_key), None)
    if not perm or not _action_allowed(perm, action):
        raise HTTPException(
            status_code=403,
            detail=f"User does not have {action} permission for {module_key} module",
        )


def validate_permission_from_token(user_token: str, module_key: str, action: str = "view") -> dict:
    """
    Validate requester token, active session, and required module permission.
    Returns requester context on success.
    """
    decoded = decode_access_token(user_token)
    requester_id = decoded.get("sub")

    if not requester_id:
        raise HTTPException(status_code=401, detail="Invalid token payload")

    user_context = _load_active_user_with_permissions(requester_id)
    _assert_module_permission(user_context, module_key=module_key, action=action)
    return user_context


def require_module_permission(module_key: str, action: str = "view"):
    """Dependency factory for module permission checks."""
    def _dependency(current_user: dict = Depends(get_current_user)) -> dict:
        _assert_module_permission(current_user, module_key=module_key, action=action)
        return current_user
    return _dependency


# ====== ENDPOINT: Crear usuario (para ti / admin) ======

@router.post("/create_user")
def create_user(payload: CreateUserRequest):
    validate_permission_from_token(payload.user_token, module_key="team", action="edit")

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
def login(payload: LoginRequest):
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
        raise HTTPException(status_code=401, detail="Invalid username or password")
    except Exception as e:
        logger.error("Error en /auth/login query: %r", e)
        raise HTTPException(status_code=500, detail="Error querying user store")

    user = result.data
    if not user:
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
    )

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


@router.get("/me")
def me(authorization: str | None = Header(default=None)):
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")

    token = authorization.split(" ", 1)[1].strip()
    decoded = decode_access_token(token)
    user_id = decoded.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid token payload")

    user_context = _load_active_user_with_permissions(str(user_id))

    return {
        "user_name": user_context.get("username"),
        "user_role": user_context.get("role"),
        "user_id": user_context.get("user_id"),
    }


# ====== DEPENDENCY: Get current user from JWT ======

security = HTTPBearer()

def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)) -> dict:
    """
    Dependency to extract and verify JWT token from Authorization header.
    Returns user info from token payload.
    """
    token = credentials.credentials

    decoded = decode_access_token(token)

    user_id = decoded.get("sub")
    username = decoded.get("username")

    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid token payload")

    user_context = _load_active_user_with_permissions(str(user_id))

    if username and not user_context.get("username"):
        user_context["username"] = username

    return user_context
