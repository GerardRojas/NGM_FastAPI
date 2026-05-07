"""
Router para gestión de permisos basados en roles
"""
import logging
from fastapi import APIRouter, HTTPException, Depends
from typing import List, Dict, Any
from pydantic import BaseModel

from api.auth import get_current_user
from api.supabase_client import supabase

router = APIRouter(prefix="/permissions", tags=["permissions"])
logger = logging.getLogger(__name__)
ROLES_MANAGEMENT_MODULE_KEY = "roles-management"
LEGACY_ROLES_MODULE_KEY = "roles"


# ========================================
# Helpers internos
# ========================================
def _load_target_user_role(user_id: str) -> Dict[str, Any]:
    response = supabase.table("users").select(
        "user_rol, rols!users_user_rol_fkey(rol_id, rol_name)"
    ).eq("user_id", user_id).single().execute()

    if not response.data:
        raise HTTPException(status_code=404, detail="User not found")

    rols_data = response.data.get("rols") or {}
    return {
        "rol_id": response.data.get("user_rol"),
        "rol_name": rols_data.get("rol_name"),
    }


def _normalize_slug(slug: Any) -> str | None:
    if slug is None:
        return None
    value = str(slug).strip().strip("/")
    return value or None


def _resolve_page_url(slug: Any, module_url: Any, module_key: Any) -> str | None:
    normalized_slug = _normalize_slug(slug)
    if normalized_slug:
        return normalized_slug
    if module_url is not None and str(module_url).strip():
        return str(module_url).strip()
    if module_key is not None and str(module_key).strip():
        return str(module_key).strip()
    return None


def _normalize_menu_label(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip().lower()


def _extract_menu_item_name(menu_item: Dict[str, Any]) -> str | None:
    return menu_item.get("item_name") or menu_item.get("name") or menu_item.get("title")


def _is_roles_management_menu_item(menu_item: Dict[str, Any]) -> bool:
    slug = _normalize_slug(menu_item.get("slug"))
    name = _normalize_menu_label(_extract_menu_item_name(menu_item))
    return (
        slug in {ROLES_MANAGEMENT_MODULE_KEY, LEGACY_ROLES_MODULE_KEY}
        or name in {"roles management", "manage roles", ROLES_MANAGEMENT_MODULE_KEY, LEGACY_ROLES_MODULE_KEY}
    )


def _load_menu_items_map(menu_item_ids: List[Any]) -> Dict[Any, Dict[str, Any]]:
    if not menu_item_ids:
        return {}

    try:
        menu_items_response = supabase.table("menu_items").select(
            "id, slug, item_name, icon_type, icon_text, category_id, item_order:order"
        ).in_("id", menu_item_ids).execute()
    except Exception:
        menu_items_response = supabase.table("menu_items").select(
            "id, slug, icon_type, icon_text, category_id, item_order:order"
        ).in_("id", menu_item_ids).execute()

    menu_items = menu_items_response.data or []
    return {item.get("id"): item for item in menu_items}


def _load_all_menu_items() -> List[Dict[str, Any]]:
    try:
        response = supabase.table("menu_items").select(
            "id, slug, item_name, icon_type, icon_text, category_id"
        ).execute()
    except Exception:
        response = supabase.table("menu_items").select(
            "id, slug, icon_type, icon_text, category_id"
        ).execute()
    return response.data or []


def _has_roles_management_permission_for_user(user_context: Dict[str, Any], action: str) -> bool:
    rol_id = user_context.get("user_rol")
    if not rol_id:
        return False

    action_column = {
        "view": "can_view",
        "edit": "can_edit",
        "delete": "can_delete",
    }.get(action, "can_view")

    perms_response = supabase.table("role_permissions").select(
        f"id, menu_item_id, {action_column}"
    ).eq("rol_id", rol_id).eq(action_column, True).execute()
    perms = perms_response.data or []
    if not perms:
        return False

    menu_item_ids = list({p.get("menu_item_id") for p in perms if p.get("menu_item_id") is not None})
    if not menu_item_ids:
        return False

    menu_items_map = _load_menu_items_map(menu_item_ids)
    for menu_item_id in menu_item_ids:
        if _is_roles_management_menu_item(menu_items_map.get(menu_item_id) or {}):
            return True

    return False


def require_roles_management_permission(action: str = "view"):
    """
    Deprecated: role-based guards are temporarily disabled.
    Keep for backward-compat but only validates JWT/authentication.
    """
    def _dependency(current_user: dict = Depends(get_current_user)) -> dict:
        return current_user
    return _dependency


def _load_role_permissions_menu_payload(rol_id: str, rol_name: str | None) -> Dict[str, Any]:
    rp_response = supabase.table("role_permissions").select(
        "id, menu_item_id, can_view, can_edit, can_delete"
    ).eq("rol_id", rol_id).execute()
    permissions = rp_response.data or []

    menu_item_ids = list({p.get("menu_item_id") for p in permissions if p.get("menu_item_id") is not None})
    menu_items_map: Dict[Any, Dict[str, Any]] = {}
    categories_map: Dict[Any, Dict[str, Any]] = {}

    if menu_item_ids:
        menu_items_map = _load_menu_items_map(menu_item_ids)
        menu_items = list(menu_items_map.values())
    else:
        menu_items = []

    category_ids = list({m.get("category_id") for m in menu_items if m.get("category_id") is not None})
    if category_ids:
        categories_response = supabase.table("menu_categories").select(
            "id, name, category_order:order"
        ).in_("id", category_ids).execute()
        categories = categories_response.data or []
        categories_map = {c.get("id"): c for c in categories}

    rows: List[Dict[str, Any]] = []
    menu: List[Dict[str, Any]] = []
    normalized_permissions: List[Dict[str, Any]] = []
    permissions_by_module: Dict[str, Dict[str, Any]] = {}

    for perm in permissions:
        menu_item_id = perm.get("menu_item_id")
        menu_item = menu_items_map.get(menu_item_id) or {}
        category_id = menu_item.get("category_id")
        category = categories_map.get(category_id) or {}

        slug = _normalize_slug(menu_item.get("slug"))
        item_name = _extract_menu_item_name(menu_item) or slug
        page_url = slug

        row = {
            "rol_id": rol_id,
            "rol_name": rol_name,
            "permission_id": perm.get("id"),
            "module_key": slug,
            "module_name": item_name,
            "module_url": page_url,
            "page_url": page_url,
            "legacy_module_url": None,
            "can_view": perm.get("can_view", False),
            "can_edit": perm.get("can_edit", False),
            "can_delete": perm.get("can_delete", False),
            "menu_item_id": menu_item_id,
            "slug": slug,
            "icon_type": menu_item.get("icon_type"),
            "icon_text": menu_item.get("icon_text"),
            "category_id": category_id,
            "category_name": category.get("name"),
            "category_order": category.get("category_order"),
            "item_order": menu_item.get("item_order"),
        }
        rows.append(row)
        normalized_permissions.append({
            "id": perm.get("id"),
            "module_key": slug,
            "module_name": item_name,
            "module_url": page_url,
            "page_url": page_url,
            "legacy_module_url": None,
            "can_view": perm.get("can_view", False),
            "can_edit": perm.get("can_edit", False),
            "can_delete": perm.get("can_delete", False),
            "menu_item_id": menu_item_id,
            "slug": slug,
        })

        slug_key = slug
        if slug_key:
            permissions_by_module[slug_key] = row

        if menu_item_id and perm.get("can_view", False):
            menu.append({
                "menu_item_id": menu_item_id,
                "slug": slug,
                "icon_type": menu_item.get("icon_type"),
                "icon_text": menu_item.get("icon_text"),
                "category_id": category_id,
                "category_name": category.get("name"),
                "category_order": category.get("category_order"),
                "item_order": menu_item.get("item_order"),
                "module_key": slug,
                "module_name": item_name,
                "module_url": page_url,
                "page_url": page_url,
                "legacy_module_url": None,
                "can_view": perm.get("can_view", False),
                "can_edit": perm.get("can_edit", False),
                "can_delete": perm.get("can_delete", False),
            })

    menu_by_id = {}
    for item in menu:
        menu_by_id[item["menu_item_id"]] = item
    deduped_menu = list(menu_by_id.values())

    def _safe_order(value: Any) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return 10**9

    deduped_menu.sort(
        key=lambda x: (
            _safe_order(x.get("category_order")),
            _safe_order(x.get("item_order")),
            (x.get("category_name") or ""),
            (x.get("slug") or ""),
        )
    )

    rows.sort(
        key=lambda x: (
            _safe_order(x.get("category_order")),
            _safe_order(x.get("item_order")),
            (x.get("category_name") or ""),
            (x.get("slug") or ""),
        )
    )

    return {
        "permissions": normalized_permissions,
        "menu": deduped_menu,
        "rows": rows,
        "permissions_by_module": permissions_by_module,
    }


# ========================================
# Endpoints
# ========================================

@router.get("/roles")
async def list_all_roles(
    current_user: dict = Depends(get_current_user),
):
    """
    Lista todos los roles disponibles
    """
    try:
        roles_response = supabase.table("rols").select("rol_id, rol_name").order("rol_name").execute()
        roles = roles_response.data or []

        if not roles:
            return {"data": []}

        role_ids = [r.get("rol_id") for r in roles if r.get("rol_id") is not None]
        counts_by_role: Dict[Any, Dict[str, int]] = {}

        if role_ids:
            perms_response = supabase.table("role_permissions").select(
                "rol_id, can_view, can_edit, can_delete"
            ).in_("rol_id", role_ids).execute()
            permissions = perms_response.data or []

            for perm in permissions:
                role_id = perm.get("rol_id")
                if role_id is None:
                    continue
                counts = counts_by_role.setdefault(
                    role_id,
                    {
                        "modules_can_view_count": 0,
                        "modules_can_edit_count": 0,
                        "modules_can_delete_count": 0,
                    },
                )

                if perm.get("can_view"):
                    counts["modules_can_view_count"] += 1
                if perm.get("can_edit"):
                    counts["modules_can_edit_count"] += 1
                if perm.get("can_delete"):
                    counts["modules_can_delete_count"] += 1

        data = []
        for role in roles:
            role_id = role.get("rol_id")
            counts = counts_by_role.get(
                role_id,
                {
                    "modules_can_view_count": 0,
                    "modules_can_edit_count": 0,
                    "modules_can_delete_count": 0,
                },
            )
            data.append({
                "rol_id": role_id,
                "rol_name": role.get("rol_name"),
                **counts,
            })

        return {"data": data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching roles: {str(e)}")


@router.get("/role/{rol_id}")
async def get_permissions_by_role(
    rol_id: str,
    current_user: dict = Depends(get_current_user),
):
    """
    Obtiene todos los permisos para un rol específico
    """
    try:
        # Catalogo de modulos: menu_items + menu_categories
        menu_items = _load_all_menu_items()
        category_ids = list({
            m.get("category_id")
            for m in menu_items
            if m.get("category_id") is not None
        })
        categories_map: Dict[Any, Dict[str, Any]] = {}
        if category_ids:
            categories_response = supabase.table("menu_categories").select(
                "id, name, category_order:order"
            ).in_("id", category_ids).execute()
            categories = categories_response.data or []
            categories_map = {c.get("id"): c for c in categories}

        # Permisos existentes del rol (si no hay fila, can_* = false)
        rp_resp = (
            supabase.table("role_permissions")
            .select("id, rol_id, menu_item_id, can_view, can_edit, can_delete")
            .eq("rol_id", rol_id)
            .execute()
        )
        role_perms = rp_resp.data or []
        perms_by_menu_item_id: Dict[Any, Dict[str, Any]] = {
            p.get("menu_item_id"): p
            for p in role_perms
            if p.get("menu_item_id") is not None
        }

        # Nombre del rol
        rol_name = None
        try:
            r_resp = supabase.table("rols").select("rol_name").eq("rol_id", rol_id).single().execute()
            rol_name = (r_resp.data or {}).get("rol_name")
        except Exception:
            rol_name = None

        rows: List[Dict[str, Any]] = []
        for mi in menu_items:
            menu_item_id = mi.get("id")
            slug = _normalize_slug(mi.get("slug"))
            if not slug or menu_item_id is None:
                continue

            perm = perms_by_menu_item_id.get(menu_item_id) or {}
            can_view = bool(perm.get("can_view", False))
            can_edit = bool(perm.get("can_edit", False))
            can_delete = bool(perm.get("can_delete", False))
            has_permission = bool(can_view or can_edit or can_delete)

            category_id = mi.get("category_id")
            category = categories_map.get(category_id) or {}

            rows.append({
                "category_id": category_id,
                "category_name": category.get("name"),
                "category_order": category.get("category_order"),
                "menu_item_id": menu_item_id,
                "item_name": _extract_menu_item_name(mi) or slug,
                "slug": slug,
                "icon_type": mi.get("icon_type"),
                "icon_text": mi.get("icon_text"),
                "item_order": mi.get("item_order"),
                "permission_id": perm.get("id"),
                "rol_id": rol_id,
                "rol_name": rol_name,
                "can_view": can_view,
                "can_edit": can_edit,
                "can_delete": can_delete,
                "has_permission": has_permission,
                # Backward-compat aliases
                "module_key": slug,
                "module_name": _extract_menu_item_name(mi) or slug,
                "module_url": slug,
                "page_url": slug,
                "legacy_module_url": None,
            })

        def _safe_order(value: Any) -> int:
            try:
                return int(value)
            except (TypeError, ValueError):
                return 10**9

        rows.sort(
            key=lambda x: (
                _safe_order(x.get("category_order")),
                _safe_order(x.get("item_order")),
                (x.get("category_name") or ""),
                (x.get("slug") or ""),
            )
        )

        return {
            "rol_id": rol_id,
            "rol_name": rol_name,
            "data": rows,
            # Backward-compat key (frontend may still read "permissions")
            "permissions": rows,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching permissions: {str(e)}")


@router.get("/user/{user_id}")
async def get_permissions_by_user(
    user_id: str,
    current_user: dict = Depends(get_current_user),
):
    """
    Obtiene los permisos de un usuario basado en su rol, incluyendo
    metadata de menú (menu_items + menu_categories) para cache de sesión.
    """
    if str(current_user.get("user_id")) != str(user_id):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    try:
        role_ctx = _load_target_user_role(user_id)
        rol_id = role_ctx.get("rol_id")
        rol_name = role_ctx.get("rol_name")

        if not rol_id:
            return {
                "user_id": user_id,
                "rol_id": None,
                "rol_name": None,
                "permissions": [],
                "menu": [],
                "rows": [],
            }

        role_payload = _load_role_permissions_menu_payload(rol_id=rol_id, rol_name=rol_name)

        return {
            "user_id": user_id,
            "rol_id": rol_id,
            "rol_name": rol_name,
            "permissions": role_payload.get("permissions", []),
            "menu": role_payload.get("menu", []),
            "rows": role_payload.get("rows", []),
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching user permissions: {str(e)}")


@router.get("/modules")
async def list_all_modules(
    current_user: dict = Depends(get_current_user),
):
    """
    Consulta unificada de roles + permisos + menú + categorías.
    """
    try:
        roles_response = supabase.table("rols").select("rol_id, rol_name").execute()
        roles = roles_response.data or []
        if not roles:
            return {"data": []}
        roles_by_id = {r.get("rol_id"): r.get("rol_name") for r in roles}

        perms_response = supabase.table("role_permissions").select(
            "id, rol_id, menu_item_id, can_view, can_edit, can_delete"
        ).execute()
        permissions = perms_response.data or []

        all_menu_items = _load_all_menu_items()
        all_menu_by_id = {m.get("id"): m for m in all_menu_items if m.get("id") is not None}
        category_ids = list({
            m.get("category_id")
            for m in all_menu_items
            if m.get("category_id") is not None
        })
        categories_map: Dict[Any, Dict[str, Any]] = {}
        if category_ids:
            categories_response = supabase.table("menu_categories").select("id, name").in_("id", category_ids).execute()
            categories = categories_response.data or []
            categories_map = {c.get("id"): c for c in categories}

        modules_catalog: Dict[str, Dict[str, Any]] = {}
        for menu_item in all_menu_items:
            slug = _normalize_slug(menu_item.get("slug"))
            if not slug:
                continue
            modules_catalog[slug] = {
                "module_key": slug,
                "module_name": _extract_menu_item_name(menu_item) or slug,
                "module_url": slug,
                "menu_item": menu_item,
            }

        permissions_by_role_module: Dict[tuple, Dict[str, Any]] = {}
        for perm in permissions:
            menu_item = all_menu_by_id.get(perm.get("menu_item_id")) if perm.get("menu_item_id") is not None else None
            resolved_key = _normalize_slug(menu_item.get("slug")) if menu_item else None
            if not resolved_key:
                continue

            permissions_by_role_module[(perm.get("rol_id"), resolved_key)] = {
                **perm,
                "resolved_key": resolved_key,
                "menu_item": menu_item,
            }

        rows: List[Dict[str, Any]] = []
        for rol_id, rol_name in roles_by_id.items():
            for module_key, module_info in modules_catalog.items():
                perm = permissions_by_role_module.get((rol_id, module_key))
                menu_item = (perm or {}).get("menu_item") or module_info.get("menu_item") or {}
                category = categories_map.get(menu_item.get("category_id")) or {}
                page_url = _resolve_page_url(
                    slug=_normalize_slug(menu_item.get("slug")),
                    module_url=module_info.get("module_url"),
                    module_key=module_key,
                )
                rows.append({
                    "rol_id": rol_id,
                    "rol_name": rol_name,
                    "permission_id": (perm or {}).get("id"),
                    "module_key": module_key,
                    "module_name": module_info.get("module_name") or module_key,
                    "item_name": _extract_menu_item_name(menu_item),
                    "module_url": page_url,
                    "can_view": bool((perm or {}).get("can_view", False)),
                    "can_edit": bool((perm or {}).get("can_edit", False)),
                    "can_delete": bool((perm or {}).get("can_delete", False)),
                    "menu_item_id": menu_item.get("id"),
                    "slug": _normalize_slug(menu_item.get("slug")) or module_key,
                    "icon_type": menu_item.get("icon_type"),
                    "icon_text": menu_item.get("icon_text"),
                    "category_id": category.get("id"),
                    "category_name": category.get("name"),
                })

        rows.sort(
            key=lambda x: (
                (x.get("rol_name") or ""),
                0 if x.get("category_name") is None else 1,
                (x.get("category_name") or ""),
                (x.get("slug") or ""),
            )
        )
        return {"data": rows}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching modules: {str(e)}")


@router.get("/check")
async def check_permission(
    user_id: str,
    slug: str | None = None,
    action: str = "view",
    current_user: dict = Depends(get_current_user),
):
    """
    Verifica si un usuario tiene un permiso específico para un módulo.
    Permission check by slug via menu_items + role_permissions.menu_item_id.

    Args:
        user_id: ID del usuario
        slug: Identificador del módulo (source of truth)
        action: Tipo de permiso ('view', 'edit', 'delete')
    """
    lookup_slug = _normalize_slug(slug)
    if not lookup_slug:
        raise HTTPException(status_code=400, detail="Missing module slug")

    if str(current_user.get("user_id")) != str(user_id):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    try:
        def _log_and_return(payload: Dict[str, Any]) -> Dict[str, Any]:
            logger.info("[permissions/check] response=%s", payload)
            return payload

        role_ctx = _load_target_user_role(user_id)
        rol_id = role_ctx.get("rol_id")
        rol_name = role_ctx.get("rol_name")
        if not rol_id:
            return _log_and_return({
                "has_permission": False,
                "reason": "User has no role assigned",
                "user_id": user_id,
                "rol_id": None,
                "rol_name": None,
                "slug": lookup_slug,
                "action": action,
                "permissions": None,
            })

        menu_item_resp = (
            supabase.table("menu_items")
            .select("id, slug, item_name, icon_type, icon_text, category_id")
            .eq("slug", lookup_slug)
            .execute()
        )
        if not menu_item_resp.data:
            return _log_and_return({
                "has_permission": False,
                "reason": "Module slug is not registered in menu_items",
                "user_id": user_id,
                "rol_id": rol_id,
                "rol_name": rol_name,
                "slug": lookup_slug,
                "action": action,
                "permissions": None,
            })
        menu_item = menu_item_resp.data[0]
        menu_item_id = menu_item.get("id")

        perm_resp = (
            supabase.table("role_permissions")
            .select("id, can_view, can_edit, can_delete, menu_item_id")
            .eq("rol_id", rol_id)
            .eq("menu_item_id", menu_item_id)
            .execute()
        )
        perm = perm_resp.data[0] if perm_resp.data else None

        if not perm:
            return _log_and_return({
                "has_permission": False,
                "reason": "No permission record found for this role and module",
                "user_id": user_id,
                "rol_id": rol_id,
                "rol_name": rol_name,
                "slug": lookup_slug,
                "action": action,
                "permissions": None,
            })

        action_map = {
            "view": perm.get("can_view", False),
            "edit": perm.get("can_edit", False),
            "delete": perm.get("can_delete", False),
        }
        has_permission = bool(action_map.get(action, False))

        normalized_perm = {
            "id": perm.get("id"),
            "slug": lookup_slug,
            "module_name": _extract_menu_item_name(menu_item) or lookup_slug,
            "module_url": lookup_slug,
            "page_url": lookup_slug,
            "legacy_module_url": None,
            "can_view": perm.get("can_view", False),
            "can_edit": perm.get("can_edit", False),
            "can_delete": perm.get("can_delete", False),
            "menu_item_id": menu_item_id,
            "icon_type": menu_item.get("icon_type"),
            "icon_text": menu_item.get("icon_text"),
            "category_id": menu_item.get("category_id"),
        }

        return _log_and_return({
            "has_permission": has_permission,
            "reason": None,
            "user_id": user_id,
            "rol_id": rol_id,
            "rol_name": rol_name,
            "slug": lookup_slug,
            "action": action,
            "permissions": normalized_perm,
        })
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error checking permission: {str(e)}")


# ========================================
# Modelos Pydantic para Batch Update
# ========================================

class PermissionUpdate(BaseModel):
    rol_id: str
    slug: str | None = None
    menu_item_id: str | None = None
    can_view: bool
    can_edit: bool
    can_delete: bool


class BatchPermissionUpdate(BaseModel):
    updates: List[PermissionUpdate]


# ========================================
# Batch Update Endpoint
# ========================================

@router.post("/batch-update")
async def batch_update_permissions(
    data: BatchPermissionUpdate,
    current_user: dict = Depends(get_current_user),
):
    """
    Actualiza múltiples permisos en batch.

    Recibe una lista de updates y decide por registro si corresponde
    crear (no existe) o editar (ya existe). Los roles CEO y COO están protegidos.
    """
    try:
        # Roles protegidos
        PROTECTED_ROLES = ['CEO', 'COO']

        protected_roles_response = supabase.table("rols").select("rol_id, rol_name").execute()
        protected_rol_ids = {
            r["rol_id"] for r in protected_roles_response.data
            if r["rol_name"] in PROTECTED_ROLES
        }

        # Catálogo de menú para resolver nombre/url oficial de módulo
        all_menu_items = _load_all_menu_items()
        menu_by_id = {m.get("id"): m for m in all_menu_items if m.get("id") is not None}
        menu_by_slug = {
            _normalize_slug(m.get("slug")): m
            for m in all_menu_items
            if _normalize_slug(m.get("slug"))
        }

        # Separar protegidos de permitidos
        requested_rows = []
        failed_updates = []
        protected_blocked = 0

        for update in data.updates:
            if update.rol_id in protected_rol_ids:
                protected_blocked += 1
                failed_updates.append({
                    "rol_id": update.rol_id,
                    "slug": update.slug,
                    "reason": "Protected role (CEO/COO) cannot be modified"
                })
                continue

            selected_menu_item = None
            if update.menu_item_id:
                selected_menu_item = menu_by_id.get(update.menu_item_id)

            raw_slug = _normalize_slug(update.slug)

            if selected_menu_item:
                menu_slug = selected_menu_item.get("slug")
                menu_slug = _normalize_slug(menu_slug)
                resolved_slug = raw_slug or menu_slug
                menu_item_id = selected_menu_item.get("id")
            else:
                resolved_slug = raw_slug
                selected_menu_item = menu_by_slug.get(resolved_slug) if resolved_slug else None
                menu_item_id = selected_menu_item.get("id") if selected_menu_item else None

            if not resolved_slug:
                failed_updates.append({
                    "rol_id": update.rol_id,
                    "slug": update.slug,
                    "reason": "Missing module reference (slug or menu_item_id)",
                })
                continue

            if not menu_item_id:
                failed_updates.append({
                    "rol_id": update.rol_id,
                    "slug": resolved_slug,
                    "reason": "Module slug not found in menu_items",
                })
                continue

            requested_rows.append({
                "rol_id": update.rol_id,
                "menu_item_id": menu_item_id,
                "can_view": update.can_view,
                "can_edit": update.can_edit,
                "can_delete": update.can_delete,
                "slug": resolved_slug,
            })

        # Deduplicar por (rol_id, menu_item_id): el último valor recibido gana.
        deduped_rows: Dict[tuple, Dict[str, Any]] = {}
        for row in requested_rows:
            deduped_rows[(row["rol_id"], row["menu_item_id"])] = row

        rows_to_process = list(deduped_rows.values())

        # Cargar registros existentes para decidir create vs update.
        existing_by_key: Dict[tuple, Dict[str, Any]] = {}
        role_ids = list({row["rol_id"] for row in rows_to_process})
        menu_item_ids = list({row["menu_item_id"] for row in rows_to_process})
        if role_ids and menu_item_ids:
            existing_response = (
                supabase.table("role_permissions")
                .select("id, rol_id, menu_item_id, can_view, can_edit, can_delete")
                .in_("rol_id", role_ids)
                .in_("menu_item_id", menu_item_ids)
                .execute()
            )
            for existing in existing_response.data or []:
                key = (existing.get("rol_id"), existing.get("menu_item_id"))
                if key[0] is not None and key[1] is not None:
                    existing_by_key[key] = existing

        insert_rows: List[Dict[str, Any]] = []
        update_rows: List[Dict[str, Any]] = []
        no_op_rows = 0

        for row in rows_to_process:
            key = (row["rol_id"], row["menu_item_id"])
            existing = existing_by_key.get(key)
            requested_has_true = bool(row["can_view"] or row["can_edit"] or row["can_delete"])

            if not existing:
                insert_rows.append({
                    "rol_id": row["rol_id"],
                    "menu_item_id": row["menu_item_id"],
                    "can_view": row["can_view"],
                    "can_edit": row["can_edit"],
                    "can_delete": row["can_delete"],
                })
                continue

            existing_has_true = bool(
                existing.get("can_view") or existing.get("can_edit") or existing.get("can_delete")
            )
            has_changes = any(
                bool(existing.get(flag, False)) != bool(row[flag])
                for flag in ("can_view", "can_edit", "can_delete")
            )

            # Edita registros existentes (caso normal cuando ya hay al menos un permiso en true)
            # y también cuando cambia un registro que estaba totalmente en false.
            if existing_has_true or requested_has_true or has_changes:
                update_rows.append({
                    "id": existing.get("id"),
                    "rol_id": row["rol_id"],
                    "menu_item_id": row["menu_item_id"],
                    "can_view": row["can_view"],
                    "can_edit": row["can_edit"],
                    "can_delete": row["can_delete"],
                })
            else:
                no_op_rows += 1

        # Ejecutar creación y edición en batch.
        successful_updates = 0
        if insert_rows:
            try:
                supabase.table("role_permissions").insert(insert_rows).execute()
                successful_updates += len(insert_rows)
            except Exception as e:
                failed_updates.append({
                    "rol_id": "batch_insert",
                    "slug": "*",
                    "reason": str(e)
                })

        if update_rows:
            try:
                supabase.table("role_permissions").upsert(
                    update_rows,
                    on_conflict="id"
                ).execute()
                successful_updates += len(update_rows)
            except Exception as e:
                failed_updates.append({
                    "rol_id": "batch_update",
                    "slug": "*",
                    "reason": str(e)
                })

        return {
            "message": "Batch update completed",
            "successful_updates": successful_updates,
            "created_records": len(insert_rows),
            "updated_records": len(update_rows),
            "no_op_records": no_op_rows,
            "failed_updates": len(failed_updates),
            "protected_blocked": protected_blocked,
            "details": failed_updates if failed_updates else None
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error in batch update: {str(e)}")
