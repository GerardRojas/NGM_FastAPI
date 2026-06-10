"""
Demo Admin Router — CRUD over demo accounts for the IT > "Demo Manager" page.

A demo account is a real `users` row with account_type = 'demo'. Every demo user
is pinned to the single canonical "Demo" workspace (a companies row with
is_demo = true); users.company_id -> the Demo company, carried into the JWT. The
demo session is a SANDBOX: it sees only the Demo workspace's seeded data and its
writes PERSIST there (scoped by company_id/source_company), but a server-side
allowlist (api/main.py) keeps those writes off global/shared tables. The active
Demo workspace is also what turns on the guided-tour bubbles in the hub.

Each demo user owns a DEDICATED role ("Demo — <name>"), and which modules that
demo can see is just that role's role_permissions (can_view). Modules the demo
can't view still render in the sidebar greyed-out (see SidebarNav
getDemoLockedModules). All endpoints are CEO/COO only.
"""
from __future__ import annotations

import uuid
from datetime import date, timedelta
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from api.auth import get_current_user
from api.supabase_client import supabase
from utils.auth import hash_password

router = APIRouter(prefix="/demo-admin", tags=["demo-admin"])

DEMO_ACCOUNT_TYPE = "demo"
DEMO_ROLE_PREFIX = "Demo"  # dedicated roles are named "Demo — <user_name>"
DEMO_COMPANY_NAME = "Demo"  # the single shared sandbox workspace
_LEADERSHIP_ROLES = {"ceo", "coo"}

# Modules whose backend is company-scoped (data isolated by company_id /
# source_company), so they're SAFE to expose in a demo sandbox: a demo session
# only sees the Demo workspace's rows and its writes stay inside it. Everything
# else (global/shared taxonomy, org config, team tools) is marked unsafe so the
# Demo Manager can warn/disable it. Keep in sync with _DEMO_WRITE_ALLOWED_PREFIXES
# in api/main.py.
_DEMO_SCOPED_SLUGS = {
    "dashboard",
    "analytics",
    "projects",
    "expenses",
    "budgets",
    "budget-vs-actuals",
    "pnl-report",
    "reporting",
    "estimator",
    "vendors",
    "art",
}

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
            # Safe to grant in a demo sandbox (its data is company-scoped)?
            "scoped": slug.lower() in _DEMO_SCOPED_SLUGS,
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


# ---------------------------------------------------------------------------
# The shared "Demo" workspace (one canonical company every demo user is pinned to)
# ---------------------------------------------------------------------------
def _ensure_demo_company() -> str:
    """Return the id of the canonical Demo workspace, creating + seeding it on
    first use. Prefers an existing is_demo company; falls back to one named
    'Demo'; otherwise creates it (is_demo=true) and seeds starter data."""
    found = (supabase.table("companies").select("id")
             .eq("is_demo", True).limit(1).execute().data) or []
    if found:
        return found[0]["id"]

    by_name = (supabase.table("companies").select("id")
               .eq("name", DEMO_COMPANY_NAME).limit(1).execute().data) or []
    if by_name:
        cid = by_name[0]["id"]
        # Make sure the flag is set so the hub turns on the guided experience.
        supabase.table("companies").update({"is_demo": True}).eq("id", cid).execute()
        return cid

    created = (supabase.table("companies").insert({
        "name": DEMO_COMPANY_NAME,
        "description": "Sandbox workspace for product demos.",
        "is_demo": True,
        "status": "Active",
    }).execute().data) or []
    if not created:
        raise HTTPException(status_code=500, detail="Could not create the Demo workspace.")
    cid = created[0]["id"]
    _seed_demo_company_data(cid)
    return cid


# Starter dataset: a handful of projects, each with a few expenses. Kept small
# and FK-safe (only required columns) so it can't break on schema drift. Shared
# by all demo users; the Reset action wipes and re-applies it.
_DEMO_PROJECTS = [
    {"name": "Maple Ave Residence", "city": "Austin", "address": "1420 Maple Ave"},
    {"name": "Riverside Office Fit-Out", "city": "Denver", "address": "88 Riverside Dr"},
    {"name": "Oakwood Retail Remodel", "city": "Dallas", "address": "305 Oakwood Blvd"},
]
_DEMO_EXPENSES = [  # (project index, description, amount, days-ago)
    (0, "Framing lumber package", 8450.00, 40),
    (0, "Concrete — foundation pour", 12200.00, 35),
    (0, "Electrical rough-in", 6300.00, 20),
    (1, "HVAC units", 18750.00, 30),
    (1, "Drywall + finishing", 9100.00, 14),
    (2, "Storefront glazing", 15400.00, 25),
    (2, "Flooring — polished concrete", 7200.00, 10),
]


def _seed_demo_company_data(company_id: str) -> Dict[str, int]:
    """Insert starter projects + expenses for the Demo workspace (idempotent: a
    no-op if it already has projects). Returns inserted counts."""
    existing = (supabase.table("projects").select("project_id")
                .eq("source_company", company_id).limit(1).execute().data) or []
    if existing:
        return {"projects": 0, "expenses": 0}

    project_ids: List[str] = []
    for p in _DEMO_PROJECTS:
        pid = str(uuid.uuid4())
        supabase.table("projects").insert({
            "project_id": pid,
            "project_name": p["name"],
            "source_company": company_id,
            "city": p["city"],
            "address": p["address"],
        }).execute()
        project_ids.append(pid)

    exp_rows = []
    for proj_idx, desc, amount, days_ago in _DEMO_EXPENSES:
        exp_rows.append({
            "project": project_ids[proj_idx],
            "TxnDate": (date.today() - timedelta(days=days_ago)).isoformat(),
            "Amount": amount,
            "LineDescription": desc,
            "show_on_reports": True,
        })
    if exp_rows:
        supabase.table("expenses_manual_COGS").insert(exp_rows).execute()

    return {"projects": len(project_ids), "expenses": len(exp_rows)}


def _clear_demo_company_data(company_id: str) -> None:
    """Delete the Demo workspace's project-scoped data (expenses first, then
    projects) so a reset can re-seed from a clean slate."""
    projs = (supabase.table("projects").select("project_id")
             .eq("source_company", company_id).execute().data) or []
    pids = [p["project_id"] for p in projs]
    for pid in pids:
        supabase.table("expenses_manual_COGS").delete().eq("project", pid).execute()
    if pids:
        supabase.table("projects").delete().eq("source_company", company_id).execute()


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
        {"slug": c["slug"], "item_name": c["item_name"],
         "category_name": c["category_name"], "scoped": c["scoped"]}
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

    company_id = _ensure_demo_company()

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
            "company_id": company_id,
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
    # The shared Demo company is intentionally NOT deleted — other demo users may
    # still be pinned to it. Use the Reset action to clean its sandbox data.
    return {"ok": True}


@router.post("/workspace/reset")
async def reset_demo_workspace(current_user: dict = Depends(get_current_user)):
    """Wipe the Demo workspace's sandbox data (projects + expenses) and re-seed it
    from the starter dataset. Lets a cluttered demo be reset to a clean slate.
    The company row, demo users and roles are preserved."""
    _require_leadership(current_user)
    company_id = _ensure_demo_company()
    _clear_demo_company_data(company_id)
    counts = _seed_demo_company_data(company_id)
    return {"ok": True, "company_id": company_id, "seeded": counts}
