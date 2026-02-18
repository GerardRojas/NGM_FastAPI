"""
Router para gestión de permisos basados en roles
"""
from fastapi import APIRouter, HTTPException
from typing import List, Dict, Any
from pydantic import BaseModel

from api.supabase_client import supabase

router = APIRouter(prefix="/permissions", tags=["permissions"])


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
async def get_permissions_by_role(rol_id: str):
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
    Obtiene los permisos de un usuario basado en su rol.
    Single query via nested embed: users → rols → role_permissions.
    """
    try:
        response = supabase.table("users").select(
            "user_rol, rols!users_user_rol_fkey(rol_id, role_permissions(id, module_key, module_name, module_url, can_view, can_edit, can_delete))"
        ).eq("user_id", user_id).single().execute()

        if not response.data:
            raise HTTPException(status_code=404, detail="User not found")

        rol_id = response.data.get("user_rol")
        if not rol_id:
            return {"user_id": user_id, "rol_id": None, "permissions": []}

        rols_data = response.data.get("rols") or {}
        permissions = rols_data.get("role_permissions") or []

        return {
            "user_id": user_id,
            "rol_id": rol_id,
            "permissions": permissions
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
    Verifica si un usuario tiene un permiso específico para un módulo.
    Single query via nested embed: users → rols → role_permissions.

    Args:
        user_id: ID del usuario
        module_key: Identificador del módulo (e.g., 'expenses', 'projects')
        action: Tipo de permiso ('view', 'edit', 'delete')
    """
    try:
        response = supabase.table("users").select(
            "user_rol, rols!users_user_rol_fkey(rol_id, role_permissions(module_key, can_view, can_edit, can_delete))"
        ).eq("user_id", user_id).single().execute()

        if not response.data:
            raise HTTPException(status_code=404, detail="User not found")

        rol_id = response.data.get("user_rol")
        if not rol_id:
            return {"has_permission": False, "reason": "User has no role assigned"}

        # Filtrar el permiso específico del módulo solicitado
        rols_data = response.data.get("rols") or {}
        all_perms = rols_data.get("role_permissions") or []
        perm = next((p for p in all_perms if p.get("module_key") == module_key), None)

        if not perm:
            return {"has_permission": False, "reason": "No permission record found for this role and module"}

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
    rol_id: str
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
    Actualiza múltiples permisos en batch.

    Recibe una lista de updates y los aplica en un solo upsert a Supabase
    (N llamadas HTTP → 1).  Los roles CEO y COO están protegidos.
    """
    try:
        # Roles protegidos
        PROTECTED_ROLES = ['CEO', 'COO']

        protected_roles_response = supabase.table("rols").select("rol_id, rol_name").execute()
        protected_rol_ids = {
            r["rol_id"] for r in protected_roles_response.data
            if r["rol_name"] in PROTECTED_ROLES
        }

        # Separar protegidos de permitidos
        upsert_rows = []
        failed_updates = []
        protected_blocked = 0

        for update in data.updates:
            if update.rol_id in protected_rol_ids:
                protected_blocked += 1
                failed_updates.append({
                    "rol_id": update.rol_id,
                    "module_key": update.module_key,
                    "reason": "Protected role (CEO/COO) cannot be modified"
                })
                continue

            upsert_rows.append({
                "rol_id": update.rol_id,
                "module_key": update.module_key,
                "module_name": update.module_key.replace("_", " ").title(),
                "module_url": f"{update.module_key}.html",
                "can_view": update.can_view,
                "can_edit": update.can_edit,
                "can_delete": update.can_delete,
            })

        # Single upsert for all allowed rows
        successful_updates = 0
        if upsert_rows:
            try:
                supabase.table("role_permissions").upsert(
                    upsert_rows,
                    on_conflict="rol_id,module_key"
                ).execute()
                successful_updates = len(upsert_rows)
            except Exception as e:
                failed_updates.append({
                    "rol_id": "batch",
                    "module_key": "*",
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
