"""
Router para gestión de permisos basados en roles
"""
from fastapi import APIRouter, HTTPException
from typing import List, Dict, Any
from pydantic import BaseModel
from supabase import create_client, Client
import os

router = APIRouter(prefix="/permissions", tags=["permissions"])

# Inicializar cliente de Supabase
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    raise RuntimeError("Missing SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY in environment")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)


# ========================================
# Endpoints
# ========================================

@router.get("/roles")
async def list_all_roles():
    """
    Lista todos los roles disponibles
    """
    try:
        response = supabase.table("rols").select("rol_id, rol_name").order("rol_name").execute()
        return {"data": response.data or []}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching roles: {str(e)}")


@router.get("/role/{rol_id}")
async def get_permissions_by_role(rol_id: int):
    """
    Obtiene todos los permisos para un rol específico
    """
    try:
        response = supabase.table("role_permissions").select(
            "id, module_key, module_name, module_url, can_view, can_edit, can_delete"
        ).eq("rol_id", rol_id).execute()

        return {"rol_id": rol_id, "permissions": response.data or []}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching permissions: {str(e)}")


@router.get("/user/{user_id}")
async def get_permissions_by_user(user_id: str):
    """
    Obtiene los permisos de un usuario basado en su rol
    """
    try:
        # Primero, obtener el rol del usuario
        user_response = supabase.table("users").select("user_rol").eq("user_id", user_id).single().execute()

        if not user_response.data:
            raise HTTPException(status_code=404, detail="User not found")

        rol_id = user_response.data.get("user_rol")

        if not rol_id:
            return {"user_id": user_id, "rol_id": None, "permissions": []}

        # Obtener permisos del rol
        permissions_response = supabase.table("role_permissions").select(
            "id, module_key, module_name, module_url, can_view, can_edit, can_delete"
        ).eq("rol_id", rol_id).execute()

        return {
            "user_id": user_id,
            "rol_id": rol_id,
            "permissions": permissions_response.data or []
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching user permissions: {str(e)}")


@router.get("/modules")
async def list_all_modules():
    """
    Lista todos los módulos únicos disponibles en el sistema
    """
    try:
        response = supabase.table("role_permissions").select(
            "module_key, module_name, module_url"
        ).execute()

        if not response.data:
            return {"data": []}

        # Eliminar duplicados basándose en module_key
        modules_dict = {}
        for item in response.data:
            key = item.get("module_key")
            if key and key not in modules_dict:
                modules_dict[key] = {
                    "module_key": key,
                    "module_name": item.get("module_name"),
                    "module_url": item.get("module_url")
                }

        modules = list(modules_dict.values())
        modules.sort(key=lambda x: x.get("module_name", ""))

        return {"data": modules}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching modules: {str(e)}")


@router.get("/check")
async def check_permission(user_id: str, module_key: str, action: str = "view"):
    """
    Verifica si un usuario tiene un permiso específico para un módulo

    Args:
        user_id: ID del usuario
        module_key: Identificador del módulo (e.g., 'expenses', 'projects')
        action: Tipo de permiso ('view', 'edit', 'delete')
    """
    try:
        # Obtener el rol del usuario
        user_response = supabase.table("users").select("user_rol").eq("user_id", user_id).single().execute()

        if not user_response.data:
            raise HTTPException(status_code=404, detail="User not found")

        rol_id = user_response.data.get("user_rol")

        if not rol_id:
            return {"has_permission": False, "reason": "User has no role assigned"}

        # Obtener el permiso específico
        permission_response = supabase.table("role_permissions").select(
            "can_view, can_edit, can_delete"
        ).eq("rol_id", rol_id).eq("module_key", module_key).execute()

        if not permission_response.data or len(permission_response.data) == 0:
            return {"has_permission": False, "reason": "No permission record found for this role and module"}

        perm = permission_response.data[0]

        # Verificar el permiso según la acción solicitada
        action_map = {
            "view": perm.get("can_view", False),
            "edit": perm.get("can_edit", False),
            "delete": perm.get("can_delete", False)
        }

        has_permission = action_map.get(action, False)

        return {
            "has_permission": has_permission,
            "user_id": user_id,
            "module_key": module_key,
            "action": action,
            "permissions": perm
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error checking permission: {str(e)}")


# ========================================
# Modelos Pydantic para Batch Update
# ========================================

class PermissionUpdate(BaseModel):
    rol_id: int
    module_key: str
    can_view: bool
    can_edit: bool
    can_delete: bool


class BatchPermissionUpdate(BaseModel):
    updates: List[PermissionUpdate]


# ========================================
# Batch Update Endpoint
# ========================================

@router.post("/batch-update")
async def batch_update_permissions(data: BatchPermissionUpdate):
    """
    Actualiza múltiples permisos en batch

    Recibe una lista de updates y actualiza o inserta cada uno.
    Los roles CEO y COO están protegidos y no se pueden modificar.
    """
    try:
        # Roles protegidos
        PROTECTED_ROLES = ['CEO', 'COO']

        # Obtener información de roles protegidos
        protected_roles_response = supabase.table("rols").select("rol_id, rol_name").execute()
        protected_rol_ids = [
            r["rol_id"] for r in protected_roles_response.data
            if r["rol_name"] in PROTECTED_ROLES
        ]

        successful_updates = 0
        failed_updates = []
        protected_blocked = 0

        for update in data.updates:
            # Verificar si es un rol protegido
            if update.rol_id in protected_rol_ids:
                protected_blocked += 1
                failed_updates.append({
                    "rol_id": update.rol_id,
                    "module_key": update.module_key,
                    "reason": "Protected role (CEO/COO) cannot be modified"
                })
                continue

            try:
                # Intentar actualizar o insertar el permiso
                upsert_data = {
                    "rol_id": update.rol_id,
                    "module_key": update.module_key,
                    "module_name": update.module_key.replace("_", " ").title(),  # Fallback
                    "module_url": f"{update.module_key}.html",  # Fallback
                    "can_view": update.can_view,
                    "can_edit": update.can_edit,
                    "can_delete": update.can_delete
                }

                # Usar upsert para insertar o actualizar
                response = supabase.table("role_permissions").upsert(
                    upsert_data,
                    on_conflict="rol_id,module_key"
                ).execute()

                successful_updates += 1

            except Exception as e:
                failed_updates.append({
                    "rol_id": update.rol_id,
                    "module_key": update.module_key,
                    "reason": str(e)
                })

        return {
            "message": "Batch update completed",
            "successful_updates": successful_updates,
            "failed_updates": len(failed_updates),
            "protected_blocked": protected_blocked,
            "details": failed_updates if failed_updates else None
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error in batch update: {str(e)}")
