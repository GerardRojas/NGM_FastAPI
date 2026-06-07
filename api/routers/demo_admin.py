"""
Demo Admin Router — CRUD over demo accounts for the IT > "Demo Manager" page.

A demo account is a real `users` row with account_type = 'demo' (so the JWT
carries account_type:'demo' and the React app's isDemoAccount() flips the whole
session read-only — writes blocked, fixtures served, realtime off). Each demo
user owns a DEDICATED role ("Demo — <name>"), and which modules that demo can
see is just that role's role_permissions (can_view). Modules the demo can't view
still render in the sidebar greyed-out (see SidebarNav getDemoLockedModules).

Visibility is the ONLY thing controlled per demo — demos are always read-only,
so can_edit/can_delete stay false. All endpoints are CEO/COO only.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from api.auth import get_current_user
from api.supabase_client import supabase
from utils.auth import hash_password

router = APIRouter(prefix="/demo-admin", tags=["demo-admin"])

DEMO_ACCOUNT_TYPE = "demo"
DEMO_ROLE_PREFIX = "Demo"  # dedicated roles are named "Demo — <user_name>"
_LEADERSHIP_ROLES = {"ceo", "coo"}

# Internal IT / admin tools never offered to (or shown greyed for) demo accounts:
# managing the org, roles, AI spend, or demos themselves makes no sense for a demo
# viewer. Excluded from the catalog → absent from the checklist AND never seeded
# (so they don't even appear greyed in a demo's sidebar). Slug variants included.
_DEMO_EXCLUDED_SLUGS = {
    "demo-manager", "demo_manager",
    "ai-usage", "ai_usage",
    "roles", "roles-management", "roles_management",
    "team", "team-management", "team_management",
}


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------
class DemoUserCreate(BaseModel):
    user_name: str = Field(..., min_length=1, max_length=80)
    password: str = Field(..., min_length=4, max_length=200)
    module_keys: List[str] = Field(default_factory=list)


class DemoUserUpdate(BaseModel):
    user_name: Optional[str] = Field(default=None, min_length=1, max_length=80)
    password: Optional[str] = Field(default=None, min_length=4, max_length=200)
    module_keys: Optional[List[str]] = None


# ---------------------------------------------------------------------------
# Guards / helpers
# ---------------------------------------------------------------------------
def _require_leadership(current_user: dict) -> None:
    """Demo accounts/credentials are sensitive, so management is CEO/COO only."""
    role = str(current_user.get("role") or "").strip().lower()
    if role not in _LEADERSHIP_ROLES:
        raise HTTPException(status_code=403, detail="Only CEO/COO can manage demo users.")


def _module_catalog() -> List[Dict[str, Any]]:
    """Every sidebar-eligible module (the toggle universe), grouped/sorted by
    category. Mirrors permissions.py::_build_user_menu, but role-independent."""
    items = (supabase.table("menu_items")
             .select("id, slug, item_name, category_id, item_order:order")
             .execute().data) or []
    cat_ids = list({i.get("category_id") for i in items if i.get("category_id")})
    cats: Dict[Any, Dict[str, Any]] = {}
    if cat_ids:
        cd = (supabase.table("menu_categories")
              .select("id, name, category_order:order")
              .in_("id", cat_ids).execute().data) or []
        cats = {c["id"]: c for c in cd}

    out: List[Dict[str, Any]] = []
    for i in items:
        slug = (i.get("slug") or "").strip("/")
        if not slug or slug.lower() in _DEMO_EXCLUDED_SLUGS:
            continue
        cat = cats.get(i.get("category_id")) or {}
        out.append({
            "slug": slug,
            "item_name": i.get("item_name") or slug,
            "category_name": cat.get("name") or "General",
            "category_order": cat.get("category_order") or 0,
            "item_order": i.get("item_order") or 0,
            "menu_item_id": i.get("id"),
        })
    out.sort(key=lambda m: (m["category_order"], m["item_order"], m["item_name"]))
    return out


def _seed_role_modules(rol_id: str, module_keys: List[str]) -> None:
    """Rewrite a role's permissions over the FULL module catalog: chosen slugs get
    can_view=true, every other module a can_view=false row (still linked to its
    menu_items row). Writing the false rows too — instead of just omitting them —
    is what lets the React sidebar render the non-granted modules greyed-out for
    demos (SidebarNav getDemoLockedModules keys off can_view=false menu rows).
    Demos are read-only, so can_edit/can_delete are always false."""
    catalog = _module_catalog()
    granted = {str(slug or "").strip() for slug in module_keys}

    # Wipe first so a re-save yields exactly this catalog snapshot (role is ours).
    supabase.table("role_permissions").delete().eq("rol_id", rol_id).execute()

    rows: List[Dict[str, Any]] = [
        {
            "rol_id": rol_id,
            "module_key": c["slug"],
            "module_name": c["item_name"],
            "module_url": c["slug"],
            "menu_item_id": c["menu_item_id"],
            "can_view": c["slug"] in granted,
            "can_edit": False,
            "can_delete": False,
        }
        for c in catalog
    ]
    if rows:
        supabase.table("role_permissions").insert(rows).execute()


def _demo_user_payload(user_row: Dict[str, Any]) -> Dict[str, Any]:
    """Shape a demo user for the UI: identity + its visible module slugs."""
    rol_id = user_row.get("user_rol")
    rol_name = None
    module_keys: List[str] = []
    if rol_id:
        r = (supabase.table("rols").select("rol_name")
             .eq("rol_id", rol_id).limit(1).execute().data) or []
        rol_name = r[0]["rol_name"] if r else None
        perms = (supabase.table("role_permissions")
                 .select("module_key, can_view")
                 .eq("rol_id", rol_id).execute().data) or []
        module_keys = [p["module_key"] for p in perms if p.get("can_view")]
    return {
        "user_id": user_row.get("user_id"),
        "user_name": user_row.get("user_name"),
        "rol_id": rol_id,
        "rol_name": rol_name,
        "module_keys": module_keys,
    }


def _unique_role_name(base_name: str) -> str:
    """`Demo — <name>`, suffixed with a counter if that role already exists."""
    candidate = f"{DEMO_ROLE_PREFIX} — {base_name}"
    existing = {
        str(r.get("rol_name") or "")
        for r in (supabase.table("rols").select("rol_name")
                  .ilike("rol_name", f"{DEMO_ROLE_PREFIX} — %").execute().data or [])
    }
    if candidate not in existing:
        return candidate
    n = 2
    while f"{candidate} ({n})" in existing:
        n += 1
    return f"{candidate} ({n})"


def _load_demo_user(user_id: str) -> Dict[str, Any]:
    rows = (supabase.table("users")
            .select("user_id, user_name, user_rol, account_type")
            .eq("user_id", user_id).limit(1).execute().data) or []
    if not rows:
        raise HTTPException(status_code=404, detail="Demo user not found.")
    u = rows[0]
    if (u.get("account_type") or "") != DEMO_ACCOUNT_TYPE:
        raise HTTPException(status_code=400, detail="That account is not a demo user.")
    return u


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@router.get("/modules")
async def list_modules(current_user: dict = Depends(get_current_user)):
    """The full catalog of modules a demo can be granted (for the checklist)."""
    _require_leadership(current_user)
    return {"data": [
        {"slug": c["slug"], "item_name": c["item_name"], "category_name": c["category_name"]}
        for c in _module_catalog()
    ]}


@router.get("/users")
async def list_demo_users(current_user: dict = Depends(get_current_user)):
    """Every demo account with its dedicated role and visible module slugs."""
    _require_leadership(current_user)
    rows = (supabase.table("users")
            .select("user_id, user_name, user_rol, account_type")
            .eq("account_type", DEMO_ACCOUNT_TYPE)
            .order("user_name").execute().data) or []
    return {"data": [_demo_user_payload(r) for r in rows]}


@router.post("/users")
async def create_demo_user(payload: DemoUserCreate,
                           current_user: dict = Depends(get_current_user)):
    """Create a demo account: dedicated role + read-only user + module grants."""
    _require_leadership(current_user)
    name = payload.user_name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="A username is required.")

    dup = (supabase.table("users").select("user_id")
           .eq("user_name", name).limit(1).execute().data) or []
    if dup:
        raise HTTPException(status_code=409, detail=f'A user named "{name}" already exists.')

    role_ins = (supabase.table("rols")
                .insert({"rol_name": _unique_role_name(name)}).execute().data) or []
    if not role_ins:
        raise HTTPException(status_code=500, detail="Could not create the demo role.")
    rol_id = role_ins[0]["rol_id"]

    try:
        user_ins = (supabase.table("users").insert({
            "user_name": name,
            "password_hash": hash_password(payload.password),
            "user_rol": rol_id,
            "account_type": DEMO_ACCOUNT_TYPE,
            "is_external": False,
        }).execute().data) or []
    except Exception as e:
        # Roll back the orphan role so a retry isn't blocked by a stale role.
        supabase.table("rols").delete().eq("rol_id", rol_id).execute()
        raise HTTPException(status_code=500, detail=f"Could not create demo user: {e}")

    if not user_ins:
        supabase.table("rols").delete().eq("rol_id", rol_id).execute()
        raise HTTPException(status_code=500, detail="Demo user insert returned no data.")

    user_id = user_ins[0]["user_id"]
    _seed_role_modules(rol_id, payload.module_keys)
    return _demo_user_payload({
        "user_id": user_id, "user_name": name, "user_rol": rol_id,
        "account_type": DEMO_ACCOUNT_TYPE,
    })


@router.put("/users/{user_id}")
async def update_demo_user(user_id: str, payload: DemoUserUpdate,
                           current_user: dict = Depends(get_current_user)):
    """Update a demo's name/password and/or which modules it sees."""
    _require_leadership(current_user)
    u = _load_demo_user(user_id)

    updates: Dict[str, Any] = {}
    if payload.user_name and payload.user_name.strip():
        new_name = payload.user_name.strip()
        if new_name != u.get("user_name"):
            dup = (supabase.table("users").select("user_id")
                   .eq("user_name", new_name).neq("user_id", user_id)
                   .limit(1).execute().data) or []
            if dup:
                raise HTTPException(status_code=409, detail=f'A user named "{new_name}" already exists.')
            updates["user_name"] = new_name
    if payload.password:
        updates["password_hash"] = hash_password(payload.password)
    if updates:
        supabase.table("users").update(updates).eq("user_id", user_id).execute()

    if payload.module_keys is not None and u.get("user_rol"):
        _seed_role_modules(u["user_rol"], payload.module_keys)

    return _demo_user_payload({
        "user_id": user_id,
        "user_name": updates.get("user_name", u.get("user_name")),
        "user_rol": u.get("user_rol"),
        "account_type": DEMO_ACCOUNT_TYPE,
    })


@router.delete("/users/{user_id}")
async def delete_demo_user(user_id: str,
                           current_user: dict = Depends(get_current_user)):
    """Remove a demo account and its dedicated role + permissions."""
    _require_leadership(current_user)
    u = _load_demo_user(user_id)
    rol_id = u.get("user_rol")

    supabase.table("users").delete().eq("user_id", user_id).execute()
    if rol_id:
        supabase.table("role_permissions").delete().eq("rol_id", rol_id).execute()
        # Only drop the role if it's a demo-dedicated one (never a shared role).
        r = (supabase.table("rols").select("rol_name")
             .eq("rol_id", rol_id).limit(1).execute().data) or []
        if r and str(r[0].get("rol_name") or "").startswith(DEMO_ROLE_PREFIX):
            supabase.table("rols").delete().eq("rol_id", rol_id).execute()
    return {"ok": True}
