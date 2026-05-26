"""
Router para gestión de permisos basados en roles
"""
from fastapi import APIRouter, HTTPException
from typing import List, Dict, Any, Optional
from pydantic import BaseModel
import logging

from api.supabase_client import supabase

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/permissions", tags=["permissions"])


def _build_user_menu(rol_id):
    """Build the React menu[] from role_permissions.menu_item_id -> menu_items -> menu_categories.
    Defensive: returns [] if the menu tables/column don't exist yet, so the vanilla hub
    and any pre-migration DB keep working untouched."""
    if not rol_id:
        return []
    try:
        rp = (supabase.table("role_permissions")
              .select("menu_item_id, module_name, can_view, can_edit, can_delete")
              .eq("rol_id", rol_id).execute().data) or []
        item_ids = [p["menu_item_id"] for p in rp if p.get("menu_item_id")]
        if not item_ids:
            return []
        items = (supabase.table("menu_items")
                 .select("id, slug, item_name, icon_type, icon_text, category_id, item_order:order")
                 .in_("id", item_ids).execute().data) or []
        items_by_id = {i["id"]: i for i in items}
        cat_ids = list({i.get("category_id") for i in items if i.get("category_id")})
        cats = {}
        if cat_ids:
            cd = (supabase.table("menu_categories")
                  .select("id, name, category_order:order")
                  .in_("id", cat_ids).execute().data) or []
            cats = {c["id"]: c for c in cd}
        menu = []
        for p in rp:
            mi = items_by_id.get(p.get("menu_item_id"))
            if not mi:
                continue
            cat = cats.get(mi.get("category_id")) or {}
            slug = (mi.get("slug") or "").strip("/")
            menu.append({
                "menu_item_id": mi["id"],
                "slug": slug,
                "item_name": mi.get("item_name"),
                "icon_type": mi.get("icon_type"),
                "icon_text": mi.get("icon_text"),
                "category_id": mi.get("category_id"),
                "category_name": cat.get("name"),
                "category_order": cat.get("category_order"),
                "item_order": mi.get("item_order"),
                "module_key": slug,
                "module_name": mi.get("item_name") or p.get("module_name"),
                "module_url": slug,
                "can_view": p.get("can_view", False),
                "can_edit": p.get("can_edit", False),
                "can_delete": p.get("can_delete", False),
            })
        menu.sort(key=lambda m: ((m.get("category_order") or 0), (m.get("item_order") or 0)))
        return menu
    except Exception as e:
        logger.warning("[PERMISSIONS] menu build skipped (menu tables/column missing?): %s", e)
        return []


# ========================================
# Endpoints
# ========================================

@router.get("/roles")
async def list_all_roles():
    """
    Lista todos los roles disponibles, con el conteo de modulos por permiso
    (can_view / can_edit / can_delete) que consume el hub de Roles Management.
    """
    try:
        roles = (supabase.table("rols")
                 .select("rol_id, rol_name").order("rol_name").execute().data) or []

        # Conteo por rol agregado desde role_permissions (1 query, no N).
        perms = (supabase.table("role_permissions")
                 .select("rol_id, can_view, can_edit, can_delete").execute().data) or []
        counts: Dict[str, Dict[str, int]] = {}
        for p in perms:
            rid = p.get("rol_id")
            if not rid:
                continue
            c = counts.setdefault(rid, {"view": 0, "edit": 0, "delete": 0})
            if p.get("can_view"):
                c["view"] += 1
            if p.get("can_edit"):
                c["edit"] += 1
            if p.get("can_delete"):
                c["delete"] += 1

        for r in roles:
            c = counts.get(r["rol_id"], {"view": 0, "edit": 0, "delete": 0})
            r["modules_can_view_count"] = c["view"]
            r["modules_can_edit_count"] = c["edit"]
            r["modules_can_delete_count"] = c["delete"]

        return {"data": roles}
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
            "user_rol, rols!users_user_rol_fkey(rol_id, rol_name, role_permissions(id, module_key, module_name, module_url, can_view, can_edit, can_delete))"
        ).eq("user_id", user_id).single().execute()

        if not response.data:
            raise HTTPException(status_code=404, detail="User not found")

        rol_id = response.data.get("user_rol")
        rols_data = response.data.get("rols") or {}
        rol_name = rols_data.get("rol_name")

        if not rol_id:
            return {"user_id": user_id, "rol_id": None, "rol_name": rol_name,
                    "permissions": [], "menu": [], "rows": []}

        permissions = rols_data.get("role_permissions") or []

        # permissions[] keeps its exact legacy shape (vanilla hub depends on module_key).
        # menu[] is built additively for the React hub; empty if the menu tables aren't present.
        return {
            "user_id": user_id,
            "rol_id": rol_id,
            "rol_name": rol_name,
            "permissions": permissions,
            "menu": _build_user_menu(rol_id),
            "rows": []
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
async def check_permission(user_id: str, module_key: str = None, slug: str = None, action: str = "view"):
    """
    Verifica si un usuario tiene un permiso específico para un módulo.
    Acepta `module_key` (hub vanilla) o `slug` (hub React) — ambos como la misma clave.

    Args:
        user_id: ID del usuario
        module_key / slug: Identificador del módulo (e.g., 'expenses', 'projects')
        action: Tipo de permiso ('view', 'edit', 'delete')
    """
    key = module_key or slug
    if not key:
        raise HTTPException(status_code=422, detail="module_key or slug is required")
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
        perm = next((p for p in all_perms if p.get("module_key") == key), None)

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
            "module_key": key,
            "slug": slug,
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
    menu_item_id: Optional[str] = None


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
        # Roles protegidos. rol_id es bigint en la DB pero llega como string en
        # el payload, así que comparamos como string (antes nunca matcheaba y la
        # protección de CEO/COO quedaba sin efecto).
        PROTECTED_ROLES = ['CEO', 'COO']

        protected_roles_response = supabase.table("rols").select("rol_id, rol_name").execute()
        protected_rol_ids = {
            str(r["rol_id"]) for r in (protected_roles_response.data or [])
            if r["rol_name"] in PROTECTED_ROLES
        }

        # Filas existentes de los roles tocados. batch-update SOLO debe cambiar
        # los flags can_*: nunca debe pisar el module_name/module_url curado ni,
        # sobre todo, el menu_item_id que enlaza la fila al sidebar.
        rol_ids = list({u.rol_id for u in data.updates})
        existing_by_key: Dict[Any, Dict[str, Any]] = {}
        if rol_ids:
            existing_rows = (supabase.table("role_permissions")
                             .select("rol_id, module_key, module_name, module_url, menu_item_id")
                             .in_("rol_id", rol_ids).execute().data) or []
            existing_by_key = {
                (str(r["rol_id"]), r["module_key"]): r for r in existing_rows
            }

        # Separar protegidos de permitidos
        upsert_rows = []
        failed_updates = []
        protected_blocked = 0

        for update in data.updates:
            if str(update.rol_id) in protected_rol_ids:
                protected_blocked += 1
                failed_updates.append({
                    "rol_id": update.rol_id,
                    "module_key": update.module_key,
                    "reason": "Protected role (CEO/COO) cannot be modified"
                })
                continue

            prior = existing_by_key.get((str(update.rol_id), update.module_key))
            if prior:
                # Preservar metadata y enlace de menú; sólo cambian los flags.
                menu_item_id = prior.get("menu_item_id")
                module_name = prior.get("module_name") or update.module_key.replace("_", " ").title()
                module_url = prior.get("module_url") or f"{update.module_key}.html"
            else:
                # Fila nueva: derivar metadata y resolver el menu_item_id por slug
                # para que el módulo pueda aparecer en el sidebar (no quede huérfano).
                menu_item_id = update.menu_item_id
                if not menu_item_id:
                    mi = (supabase.table("menu_items").select("id")
                          .eq("slug", update.module_key).limit(1).execute().data) or []
                    menu_item_id = mi[0]["id"] if mi else None
                module_name = update.module_key.replace("_", " ").title()
                module_url = f"{update.module_key}.html"

            # Todas las filas llevan las MISMAS claves: PostgREST arma el SET del
            # upsert con la unión de columnas, así que menu_item_id debe ir siempre
            # (con su valor previo) o se nullificaría el enlace de menú en el update.
            upsert_rows.append({
                "rol_id": update.rol_id,
                "module_key": update.module_key,
                "module_name": module_name,
                "module_url": module_url,
                "menu_item_id": menu_item_id,
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
