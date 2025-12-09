# api/auth.py
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
import secrets

router = APIRouter(
    prefix="/auth",
    tags=["auth"],
)


# ---------- MODELOS ----------

class LoginInput(BaseModel):
    username: str
    password: str


class UserPublic(BaseModel):
    username: str
    role: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserPublic


# ---------- "BASE DE DATOS" DUMMY ----------
# Luego esto lo conectamos a Supabase / tabla users

USERS_DB = {
    # username: datos
    "chief": {
        "username": "chief",
        "password": "1234",   # de momento plano, luego lo hasheamos
        "role": "admin",
    },
    "coord": {
        "username": "coord",
        "password": "1234",
        "role": "coordinator",
    },
    "viewer": {
        "username": "viewer",
        "password": "1234",
        "role": "viewer",
    },
}


def validar_credenciales(username: str, password: str):
    user = USERS_DB.get(username)
    if not user:
        return None
    if user["password"] != password:
        return None
    return user


# ---------- ENDPOINT LOGIN ----------

@router.post("/login", response_model=LoginResponse)
async def login(payload: LoginInput):
    """
    Login básico para NGM HUB.
    Recibe {username, password} y responde con:
    {
      "access_token": "...",
      "token_type": "bearer",
      "user": {
        "username": "...",
        "role": "admin" | "coordinator" | "viewer"
      }
    }
    """
    user = validar_credenciales(payload.username, payload.password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
        )

    # Token simple por ahora, luego lo cambiamos a JWT si quieres
    token = secrets.token_urlsafe(32)

    return LoginResponse(
        access_token=token,
        user=UserPublic(username=user["username"], role=user["role"]),
    )
