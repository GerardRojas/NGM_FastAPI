"""
Router para gestión de permisos basados en roles
"""
from fastapi import APIRouter, HTTPException, Depends
from typing import List, Dict, Any
from pydantic import BaseModel

from api.auth import get_current_user
from api.supabase_client import supabase

router = APIRouter(prefix="/permissions", tags=["permissions"])
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
    def _dependency(current_user: dict = Depends(get_current_user)) -> dict:
        if not _has_roles_management_permission_for_user(current_user, action):
            raise HTTPException(
                status_code=403,
                detail=f"User does not have {action} permission for Roles Management",
            )
        return current_user

    return _dependency


def _load_role_permissions_menu_payload(rol_id: str, rol_name: str | None) -> Dict[str, Any]:
    rp_response = supabase.table("role_permissions").select(
        "id, module_key, module_name, module_url, can_view, can_edit, can_delete, menu_item_id"
    ).eq("rol_id", rol_id).execute()
    permissions = rp_response.data or []

    menu_item_ids = list({p.get("menu_item_id") for p in permissions if p.get("menu_item_id") is not None})
    menu_items_map: Dict[Any, Dict[str, Any]] = {}
    categories_map: Dict[Any, Dict[str, Any]] = {}

    if menu_item_ids:
        menu_items_map = _load_menu_items_map(menu_item_ids)
        menu_items = list(menu_items_map.values())

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
        item_name = _extract_menu_item_name(menu_item) or perm.get("module_name")
        legacy_module_url = perm.get("module_url")
        page_url = _resolve_page_url(slug=slug, module_url=legacy_module_url, module_key=perm.get("module_key"))

        row = {
            "rol_id": rol_id,
            "rol_name": rol_name,
            "permission_id": perm.get("id"),
            "module_key": perm.get("module_key"),
            "module_name": item_name,
            "module_url": page_url,
            "page_url": page_url,
            "legacy_module_url": legacy_module_url,
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
            "module_key": perm.get("module_key"),
            "module_name": item_name,
            "module_url": page_url,
            "page_url": page_url,
            "legacy_module_url": legacy_module_url,
            "can_view": perm.get("can_view", False),
            "can_edit": perm.get("can_edit", False),
            "can_delete": perm.get("can_delete", False),
            "menu_item_id": menu_item_id,
            "slug": slug,
        })

        module_key = perm.get("module_key")
        slug_key = slug
        if module_key:
            permissions_by_module[module_key] = row
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
                "module_key": perm.get("module_key"),
                "module_name": item_name,
                "module_url": page_url,
                "page_url": page_url,
                "legacy_module_url": legacy_module_url,
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
    current_user: dict = Depends(require_roles_management_permission("view")),
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
    current_user: dict = Depends(require_roles_management_permission("view")),
):
    """
    Obtiene todos los permisos para un rol específico
    """
    try:
        response = supabase.table("role_permissions").select(
            "id, module_key, module_name, module_url, can_view, can_edit, can_delete, menu_item_id"
        ).eq("rol_id", rol_id).execute()

        permissions = response.data or []
        menu_item_ids = list({p.get("menu_item_id") for p in permissions if p.get("menu_item_id") is not None})
        menu_items_map = _load_menu_items_map(menu_item_ids)

        normalized = []
        for perm in permissions:
            menu_item = menu_items_map.get(perm.get("menu_item_id")) or {}
            slug = _normalize_slug(menu_item.get("slug"))
            item_name = _extract_menu_item_name(menu_item) or perm.get("module_name")
            legacy_module_url = perm.get("module_url")
            page_url = _resolve_page_url(slug=slug, module_url=legacy_module_url, module_key=perm.get("module_key"))
            normalized.append({
                **perm,
                "module_name": item_name,
                "module_url": page_url,
                "page_url": page_url,
                "legacy_module_url": legacy_module_url,
                "slug": slug,
            })

        return {"rol_id": rol_id, "permissions": normalized}
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
        perms = current_user.get("permissions") or []
        has_team_view = any(
            p.get("module_key") == "team" and p.get("can_view", False)
            for p in perms
        )
        has_roles_view = _has_roles_management_permission_for_user(current_user, "view")
        if not has_team_view and not has_roles_view:
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
    current_user: dict = Depends(require_roles_management_permission("view")),
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
            "id, rol_id, module_key, module_name, module_url, can_view, can_edit, can_delete, menu_item_id"
        ).execute()
        permissions = perms_response.data or []
        if not permissions:
            return {"data": []}

        menu_item_ids = list({i.get("menu_item_id") for i in permissions if i.get("menu_item_id") is not None})
        menu_items_map = _load_menu_items_map(menu_item_ids)
        category_ids = list({
            m.get("category_id")
            for m in menu_items_map.values()
            if m.get("category_id") is not None
        })
        categories_map: Dict[Any, Dict[str, Any]] = {}
        if category_ids:
            categories_response = supabase.table("menu_categories").select("id, name").in_("id", category_ids).execute()
            categories = categories_response.data or []
            categories_map = {c.get("id"): c for c in categories}

        rows: List[Dict[str, Any]] = []
        for perm in permissions:
            rol_id = perm.get("rol_id")
            rol_name = roles_by_id.get(rol_id)
            menu_item = menu_items_map.get(perm.get("menu_item_id")) or {}
            category = categories_map.get(menu_item.get("category_id")) or {}
            slug = _normalize_slug(menu_item.get("slug"))
            item_name = _extract_menu_item_name(menu_item) or perm.get("module_name")
            page_url = _resolve_page_url(
                slug=slug,
                module_url=perm.get("module_url"),
                module_key=perm.get("module_key"),
            )
            rows.append({
                "rol_id": rol_id,
                "rol_name": rol_name,
                "permission_id": perm.get("id"),
                "module_key": perm.get("module_key"),
                "module_name": item_name,
                "module_url": page_url,
                "can_view": perm.get("can_view", False),
                "can_edit": perm.get("can_edit", False),
                "can_delete": perm.get("can_delete", False),
                "menu_item_id": menu_item.get("id"),
                "slug": slug,
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
    module_key: str,
    action: str = "view",
    current_user: dict = Depends(get_current_user),
):
    """
    Verifica si un usuario tiene un permiso específico para un módulo.
    Single query via nested embed: users → rols → role_permissions.

    Args:
        user_id: ID del usuario
        module_key: Identificador del módulo (e.g., 'expenses', 'projects')
        action: Tipo de permiso ('view', 'edit', 'delete')
    """
    if str(current_user.get("user_id")) != str(user_id):
        has_roles_view = _has_roles_management_permission_for_user(current_user, "view")
        if not has_roles_view:
            raise HTTPException(status_code=403, detail="Insufficient permissions")

    try:
        role_ctx = _load_target_user_role(user_id)
        rol_id = role_ctx.get("rol_id")
        rol_name = role_ctx.get("rol_name")
        if not rol_id:
            return {
                "has_permission": False,
                "reason": "User has no role assigned",
                "user_id": user_id,
                "rol_id": None,
                "rol_name": None,
                "module_key": module_key,
                "action": action,
                "permissions": None,
            }

        role_payload = _load_role_permissions_menu_payload(rol_id=rol_id, rol_name=rol_name)
        perm = (role_payload.get("permissions_by_module") or {}).get(module_key)

        if not perm:
            return {
                "has_permission": False,
                "reason": "No permission record found for this role and module",
                "user_id": user_id,
                "rol_id": rol_id,
                "rol_name": rol_name,
                "module_key": module_key,
                "action": action,
                "permissions": None,
            }

        action_map = {
            "view": perm.get("can_view", False),
            "edit": perm.get("can_edit", False),
            "delete": perm.get("can_delete", False),
        }
        has_permission = bool(action_map.get(action, False))

        return {
            "has_permission": has_permission,
            "reason": None,
            "user_id": user_id,
            "rol_id": rol_id,
            "rol_name": rol_name,
            "module_key": module_key,
            "action": action,
            "permissions": perm,
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
    module_key: str | None = None
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
    current_user: dict = Depends(require_roles_management_permission("edit")),
):
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

        # Catálogo de menú para resolver nombre/url oficial de módulo
        all_menu_items = _load_all_menu_items()
        menu_by_id = {m.get("id"): m for m in all_menu_items if m.get("id") is not None}
        menu_by_slug = {
            _normalize_slug(m.get("slug")): m
            for m in all_menu_items
            if _normalize_slug(m.get("slug"))
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

            selected_menu_item = None
            if update.menu_item_id:
                selected_menu_item = menu_by_id.get(update.menu_item_id)

            normalized_candidate = _normalize_slug(update.module_key) if update.module_key else None
            if selected_menu_item is None and normalized_candidate:
                selected_menu_item = menu_by_slug.get(normalized_candidate)
                if selected_menu_item is None and normalized_candidate.endswith(".html"):
                    selected_menu_item = menu_by_slug.get(normalized_candidate[:-5])

            if selected_menu_item:
                module_key = _normalize_slug(selected_menu_item.get("slug")) or normalized_candidate
                module_name = _extract_menu_item_name(selected_menu_item) or (module_key or "")
                module_url = _normalize_slug(selected_menu_item.get("slug")) or (module_key or "")
                menu_item_id = selected_menu_item.get("id")
            else:
                module_key = normalized_candidate
                module_name = (
                    update.module_key.replace("_", " ").replace("-", " ").title()
                    if update.module_key else ""
                )
                module_url = normalized_candidate
                menu_item_id = update.menu_item_id

            if not module_key:
                failed_updates.append({
                    "rol_id": update.rol_id,
                    "module_key": update.module_key,
                    "reason": "Missing module reference (module_key or menu_item_id)",
                })
                continue

            upsert_rows.append({
                "rol_id": update.rol_id,
                "module_key": module_key,
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
