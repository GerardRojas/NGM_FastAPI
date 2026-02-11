# api/routers/team.py
from __future__ import annotations

from fastapi import APIRouter, Query, HTTPException
from typing import Optional, List, Dict, Any

from pydantic import BaseModel, Field
from passlib.context import CryptContext

from api.supabase_client import supabase

router = APIRouter(prefix="/team", tags=["team"])

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

SELECT_CLAUSE = """
  user_id,
  created_at,
  user_name,
  user_photo,
  avatar_color,
  is_external,
  user_phone_number,
  user_birthday,
  user_address,
  user_contract_url,
  user_description,
  user_position,

  rols!users_user_rol_fkey(rol_id, rol_name),
  users_seniority!users_user_seniority_fkey(id, user_seniority_name),
  users_status!users_user_status_fkey(id, user_status_name)
"""


def normalize_user_row(r: Dict[str, Any]) -> Dict[str, Any]:
    role = r.get("rols")
    sen = r.get("users_seniority")
    st = r.get("users_status")

    return {
        "user_id": r.get("user_id"),
        "created_at": r.get("created_at"),
        "user_name": r.get("user_name"),
        "user_photo": r.get("user_photo"),
        "avatar_color": r.get("avatar_color"),
        "is_external": r.get("is_external", False),
        "user_phone_number": r.get("user_phone_number"),
        "user_birthday": r.get("user_birthday"),
        "user_address": r.get("user_address"),
        "user_contract_url": r.get("user_contract_url"),
        "user_description": r.get("user_description"),
        "user_position": r.get("user_position"),
        "role": None if not role else {"id": role.get("rol_id"), "name": role.get("rol_name")},
        "seniority": None if not sen else {"id": sen.get("id"), "name": sen.get("user_seniority_name")},
        "status": None if not st else {"id": st.get("id"), "name": st.get("user_status_name")},
    }


def fetch_user_by_id(user_id: str) -> Dict[str, Any]:
    try:
        res = supabase.table("users").select(SELECT_CLAUSE).eq("user_id", user_id).limit(1).execute()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Supabase query failed: {e}")

    if not res.data:
        raise HTTPException(status_code=404, detail="User not found")

    return normalize_user_row(res.data[0])


class UserCreate(BaseModel):
    user_name: str = Field(..., min_length=1)

    role_id: Optional[str] = None
    seniority_id: Optional[str] = None
    status_id: Optional[str] = None

    user_photo: Optional[str] = None
    avatar_color: Optional[int] = Field(default=None, ge=0, le=360)
    is_external: Optional[bool] = False

    user_phone_number: Optional[str] = None
    user_birthday: Optional[str] = None  # "YYYY-MM-DD"
    user_address: Optional[str] = None
    user_contract_url: Optional[str] = None
    user_description: Optional[str] = None
    user_position: Optional[str] = None

    password: Optional[str] = None  # plaintext opcional (se hashea)


class UserUpdate(BaseModel):
    user_name: Optional[str] = Field(default=None, min_length=1)

    role_id: Optional[str] = None
    seniority_id: Optional[str] = None
    status_id: Optional[str] = None

    user_photo: Optional[str] = None
    avatar_color: Optional[int] = Field(default=None, ge=0, le=360)
    is_external: Optional[bool] = None

    user_phone_number: Optional[str] = None
    user_birthday: Optional[str] = None  # "YYYY-MM-DD"
    user_address: Optional[str] = None
    user_contract_url: Optional[str] = None
    user_description: Optional[str] = None
    user_position: Optional[str] = None

    password: Optional[str] = None  # plaintext opcional (se hashea)


class RoleCreate(BaseModel):
    rol_name: str = Field(..., min_length=1)


class RoleUpdate(BaseModel):
    rol_name: str = Field(..., min_length=1)


@router.get("/meta")
def team_meta() -> Dict[str, Any]:
    """
    Para poblar dropdowns en el frontend.
    """
    try:
        roles = supabase.table("rols").select("rol_id, rol_name").order("rol_name").execute().data or []
        seniorities = (
            supabase.table("users_seniority").select("id, user_seniority_name").order("user_seniority_name").execute().data
            or []
        )
        statuses = (
            supabase.table("users_status").select("id, user_status_name").order("user_status_name").execute().data
            or []
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Supabase meta query failed: {e}")

    return {
        "roles": [{"id": r["rol_id"], "name": r["rol_name"]} for r in roles],
        "seniorities": [{"id": s["id"], "name": s["user_seniority_name"]} for s in seniorities],
        "statuses": [{"id": s["id"], "name": s["user_status_name"]} for s in statuses],
    }


@router.get("/rols")
def list_roles() -> List[Dict[str, Any]]:
    """Lista roles para administración."""
    try:
        res = supabase.table("rols").select("rol_id, rol_name").order("rol_name").execute()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Supabase roles query failed: {e}")

    roles = res.data or []
    return [{"id": r["rol_id"], "name": r["rol_name"]} for r in roles]


@router.post("/rols")
def create_role(payload: RoleCreate) -> Dict[str, Any]:
    """Crea un nuevo rol."""
    name = payload.rol_name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="rol_name is required")

    try:
        ins = supabase.table("rols").insert({"rol_name": name}).execute()
    except Exception as e:
        msg = str(e)
        if "duplicate" in msg.lower() or "unique" in msg.lower():
            raise HTTPException(status_code=409, detail="Role already exists")
        raise HTTPException(status_code=500, detail=f"Supabase insert role failed: {e}")

    if not ins.data:
        raise HTTPException(status_code=500, detail="Insert role succeeded but returned no data")

    r = ins.data[0]
    return {"id": r.get("rol_id"), "name": r.get("rol_name")}


@router.patch("/rols/{rol_id}")
def update_role(rol_id: str, payload: RoleUpdate) -> Dict[str, Any]:
    """Renombra un rol existente."""
    name = payload.rol_name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="rol_name is required")

    try:
        upd = supabase.table("rols").update({"rol_name": name}).eq("rol_id", rol_id).execute()
    except Exception as e:
        msg = str(e)
        if "duplicate" in msg.lower() or "unique" in msg.lower():
            raise HTTPException(status_code=409, detail="Role already exists")
        raise HTTPException(status_code=500, detail=f"Supabase update role failed: {e}")

    if upd.data == []:
        raise HTTPException(status_code=404, detail="Role not found")

    r = upd.data[0]
    return {"id": r.get("rol_id"), "name": r.get("rol_name")}


@router.delete("/rols/{rol_id}")
def delete_role(rol_id: str) -> Dict[str, Any]:
    """Borra un rol. Si hay users apuntando a este rol, la FK puede impedir el borrado."""
    try:
        res = supabase.table("rols").delete().eq("rol_id", rol_id).execute()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Supabase delete role failed: {e}")

    if res.data == []:
        raise HTTPException(status_code=404, detail="Role not found")

    return {"ok": True, "deleted_role_id": rol_id}


@router.get("/users")
def list_team_users(
    q: Optional[str] = Query(default=None, description="Search by user_name"),
) -> List[Dict[str, Any]]:
    try:
        qry = supabase.table("users").select(SELECT_CLAUSE)

        if q:
            qry = qry.ilike("user_name", f"%{q}%")

        res = qry.order("user_name", desc=False).execute()

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Supabase query failed: {e}")

    if res.data is None:
        raise HTTPException(status_code=500, detail="Supabase returned no data")

    return [normalize_user_row(r) for r in (res.data or [])]


@router.post("/users")
def create_user(payload: UserCreate) -> Dict[str, Any]:
    data = payload.model_dump()

    insert_obj: Dict[str, Any] = {
        "user_name": data.get("user_name"),
        "user_photo": data.get("user_photo"),
        "avatar_color": data.get("avatar_color"),
        "is_external": data.get("is_external", False),
        "user_phone_number": data.get("user_phone_number"),
        "user_birthday": data.get("user_birthday"),
        "user_address": data.get("user_address"),
        "user_contract_url": data.get("user_contract_url"),
        "user_description": data.get("user_description"),
        "user_position": data.get("user_position"),
        "user_rol": data.get("role_id"),
        "user_seniority": data.get("seniority_id"),
        "user_status": data.get("status_id"),
    }

    # hash password if provided
    if data.get("password"):
        insert_obj["password_hash"] = pwd_context.hash(data["password"])

    # remove None (so DB defaults apply)
    insert_obj = {k: v for k, v in insert_obj.items() if v is not None}

    try:
        ins = supabase.table("users").insert(insert_obj).execute()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Supabase insert failed: {e}")

    if not ins.data:
        raise HTTPException(status_code=500, detail="Insert succeeded but returned no data")

    user_id = ins.data[0].get("user_id")
    return fetch_user_by_id(user_id)


@router.patch("/users/{user_id}")
def update_user(user_id: str, payload: UserUpdate) -> Dict[str, Any]:
    data = payload.model_dump(exclude_unset=True)

    update_obj: Dict[str, Any] = {}

    # fields
    for f in [
        "user_name",
        "user_photo",
        "avatar_color",
        "is_external",
        "user_phone_number",
        "user_birthday",
        "user_address",
        "user_contract_url",
        "user_description",
        "user_position",
    ]:
        if f in data:
            update_obj[f] = data[f]

    # fkeys
    if "role_id" in data:
        update_obj["user_rol"] = data["role_id"]
    if "seniority_id" in data:
        update_obj["user_seniority"] = data["seniority_id"]
    if "status_id" in data:
        update_obj["user_status"] = data["status_id"]

    # password
    if "password" in data and data["password"]:
        update_obj["password_hash"] = pwd_context.hash(data["password"])

    if not update_obj:
        return fetch_user_by_id(user_id)

    try:
        upd = supabase.table("users").update(update_obj).eq("user_id", user_id).execute()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Supabase update failed: {e}")

    # If user_id doesn't exist, PostgREST returns empty array
    if upd.data == []:
        raise HTTPException(status_code=404, detail="User not found")

    return fetch_user_by_id(user_id)


@router.delete("/users/{user_id}")
def delete_user(user_id: str) -> Dict[str, Any]:
    try:
        # delete returns deleted rows in data (often)
        res = supabase.table("users").delete().eq("user_id", user_id).execute()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Supabase delete failed: {e}")

    if res.data == []:
        raise HTTPException(status_code=404, detail="User not found")

    return {"ok": True, "deleted_user_id": user_id}
