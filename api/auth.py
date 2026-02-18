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


# ====== ENDPOINT: Crear usuario (para ti / admin) ======

@router.post("/create_user")
def create_user(payload: CreateUserRequest):
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
    try:
        decoded = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALG])
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid token")

    return {
        "user_name": decoded.get("username"),
        "user_role": decoded.get("role"),
        "user_id": decoded.get("sub"),
    }


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
    }
