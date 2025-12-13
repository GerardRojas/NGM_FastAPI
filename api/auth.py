# api/auth.py

import os
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from supabase import create_client, Client
from postgrest.exceptions import APIError

from utils.auth import hash_password, verify_password

router = APIRouter(prefix="/auth", tags=["auth"])

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    raise RuntimeError("SUPABASE_URL o SUPABASE_KEY no están definidos en el .env")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)


# ====== MODELOS Pydantic ======

class LoginRequest(BaseModel):
    username: str
    password: str


class CreateUserRequest(BaseModel):
    username: str
    password: str
    user_rol: str | int           # FK a tabla rols (id o clave)
    user_seniority: str | None = None  # opcional


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
        print("APIError en /auth/create_user:", repr(e))
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        print("Error inesperado en /auth/create_user:", repr(e))
        raise HTTPException(status_code=500, detail="Error creating user")

    user = result.data[0] if isinstance(result.data, list) else result.data
    if not user:
        raise HTTPException(status_code=500, detail="User not returned after creation")

    return {
        "message": "User created",
        "user": {
            "id": user.get("user_id"),                 # <- tu PK real
            "username": user.get("user_name"),
            "role": user.get("user_rol"),
            "seniority": user.get("user_seniority"),
        },
    }


# ====== ENDPOINT: Login ======

@router.post("/login")
def login(payload: LoginRequest):
    # 1) Buscar usuario por user_name
    try:
        result = (
            supabase.table("users")
            .select("*")
            .eq("user_name", payload.username)
            .single()
            .execute()
        )
    except APIError as e:
        print("APIError en /auth/login:", repr(e))
        raise HTTPException(status_code=401, detail="Invalid username or password")
    except Exception as e:
        print("Error inesperado en /auth/login:", repr(e))
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

    # DEBUG seguro (sin revelar password)
    pw = payload.password or ""
    try:
        print("[LOGIN] username:", payload.username)
        print("[LOGIN] password chars:", len(pw))
        print("[LOGIN] password bytes:", len(pw.encode("utf-8")))
        print("[LOGIN] hash prefix:", str(hashed)[:4])
        print("[LOGIN] hash length:", len(str(hashed)))
    except Exception:
        # no queremos que logs rompan login
        pass

    # 2) Verificar password (nunca 500 por bcrypt)
    try:
        ok = verify_password(pw, hashed)
    except ValueError as e:
        # bcrypt lanza ValueError si "secret" >72 bytes, hash inválido, etc.
        print("bcrypt ValueError during login:", repr(e))
        raise HTTPException(status_code=401, detail="Invalid username or password")
    except Exception as e:
        # Cualquier otra excepción inesperada
        print("Unexpected error during password verify:", repr(e))
        raise HTTPException(status_code=401, detail="Invalid username or password")

    if not ok:
        raise HTTPException(status_code=401, detail="Invalid username or password")

    # 3) Resolver FK de rol a nombre legible
    role_id = user.get("user_rol")              # UUID (FK)
    seniority_id = user.get("user_seniority")  # crudo por ahora

    role_name = None
    if role_id:
        try:
            role_res = (
                supabase.table("rols")
                .select("rol_name")
                .eq("rol_id", role_id)
                .single()
                .execute()
            )
            if role_res.data:
                role_name = role_res.data.get("rol_name")
        except Exception as e:
            print("Error obteniendo rol en /auth/login:", repr(e))
            role_name = None

    # 4) Seniority legible (placeholder)
    seniority_name = None

    # 5) Respuesta OK (sin mezclar UUID con label)
    return {
        "message": "Login ok",
        "user": {
            "id": user.get("user_id"),
            "username": user.get("user_name"),

            # IDs crudos (Fks)
            "role_id": role_id,
            "seniority_id": seniority_id,

            # Labels legibles
            "role": role_name,           # <-- solo string o None
            "seniority": seniority_name, # <-- por ahora None
        },
    }
