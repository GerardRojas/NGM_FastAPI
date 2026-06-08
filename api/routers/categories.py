"""
Router para Categories / Subcategories (categories re-arch).

La jerarquía contable nueva: Category (con un QBO cost_code) -> Subcategory. Las
subcategorías son type-agnósticas; los tags material/labor/external_service que
se muestran por subcategoría se derivan de account_category_map (overlay de los
accounts existentes). Gestión gráfica que reemplaza los ~200 accounts planos.
"""
from fastapi import APIRouter, HTTPException, Depends
from api.auth import require_internal
from pydantic import BaseModel
from typing import List, Optional
from api.supabase_client import supabase

# Single source of truth for the cost_type enum values defined in
# categories_rearch_phase1.sql. Used to validate user-supplied chip selections.
_COST_TYPES = {"material", "labor", "external_service", "change_order", "other_expenses"}


def _normalize_allowed_cost_types(value):
    """Validate + dedupe + sort the cost_type chips coming from the UI."""
    if value is None:
        return None
    if not isinstance(value, list):
        raise HTTPException(status_code=400, detail="allowed_cost_types must be an array")
    out = []
    seen = set()
    for v in value:
        if not isinstance(v, str):
            raise HTTPException(status_code=400, detail=f"allowed_cost_types contains a non-string: {v!r}")
        s = v.strip()
        if not s:
            continue
        if s not in _COST_TYPES:
            raise HTTPException(status_code=400, detail=f"Unknown cost_type: {s!r}")
        if s not in seen:
            seen.add(s)
            out.append(s)
    out.sort()
    return out

router = APIRouter(dependencies=[Depends(require_internal)], prefix="/categories", tags=["categories"])


class CategoryCreate(BaseModel):
    name: str
    cost_code_id: Optional[str] = None
    sort_order: Optional[int] = 0


class CategoryUpdate(BaseModel):
    name: Optional[str] = None
    cost_code_id: Optional[str] = None
    sort_order: Optional[int] = None


class SubcategoryCreate(BaseModel):
    name: str
    sort_order: Optional[int] = 0
    allowed_cost_types: Optional[List[str]] = None


class SubcategoryUpdate(BaseModel):
    name: Optional[str] = None
    category_id: Optional[str] = None
    sort_order: Optional[int] = None
    allowed_cost_types: Optional[List[str]] = None


def _all(table: str, cols: str = "*"):
    rows, start = [], 0
    while True:
        b = supabase.table(table).select(cols).range(start, start + 999).execute().data or []
        rows += b
        if len(b) < 1000:
            break
        start += 1000
    return rows


@router.get("/account-map")
async def account_map():
    """account_id -> {category, subcategory, cost_type} from the overlay. Lets the
    reports group expenses/budgets (keyed by account_id) by the new hierarchy."""
    try:
        subs = {s["id"]: s for s in _all("subcategories", "id, category_id, name")}
        cats = {c["id"]: c.get("name") for c in _all("categories", "id, name")}
        out = []
        for m in _all("account_category_map", "account_id, subcategory_id, cost_type"):
            sub = subs.get(m["subcategory_id"]) or {}
            out.append({
                "account_id": m["account_id"],
                "category_id": sub.get("category_id"),
                "category": cats.get(sub.get("category_id")),
                "subcategory_id": m["subcategory_id"],
                "subcategory": sub.get("name"),
                "cost_type": m["cost_type"],
            })
        return {"data": out}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching account map: {str(e)}")


@router.get("")
async def list_categories():
    """Categories con su cost_code y subcategorías anidadas (+ tags de cost_type)."""
    try:
        cats = _all("categories")
        subs = _all("subcategories")
        codes = {c["id"]: c for c in _all("qbo_cost_codes", "id, code, name")}
        # cost_type tags per subcategory, derived from the account overlay.
        tags: dict = {}
        for m in _all("account_category_map", "subcategory_id, cost_type"):
            tags.setdefault(m["subcategory_id"], set()).add(m["cost_type"])

        subs_by_cat: dict = {}
        for s in sorted(subs, key=lambda x: (x.get("sort_order") or 0, (x.get("name") or "").lower())):
            # Allowed (user-configured) drives the chip UI. The derived set
            # (what actually has mappings today) ships alongside so the
            # picker can detect orphan chips (allowed but no account yet).
            allowed = list(s.get("allowed_cost_types") or [])
            allowed.sort()
            subs_by_cat.setdefault(s["category_id"], []).append({
                "id": s["id"],
                "name": s.get("name"),
                "sort_order": s.get("sort_order") or 0,
                "cost_types": sorted(tags.get(s["id"], set())),
                "allowed_cost_types": allowed,
            })

        out = []
        for c in sorted(cats, key=lambda x: (x.get("sort_order") or 0, (x.get("name") or "").lower())):
            cc = codes.get(c.get("cost_code_id"))
            out.append({
                "id": c["id"],
                "name": c.get("name"),
                "cost_code_id": c.get("cost_code_id"),
                "cost_code": ({"id": cc["id"], "code": cc.get("code"), "name": cc.get("name")} if cc else None),
                "sort_order": c.get("sort_order") or 0,
                "subcategories": subs_by_cat.get(c["id"], []),
            })
        return {"data": out}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching categories: {str(e)}")


@router.post("")
async def create_category(payload: CategoryCreate):
    try:
        name = (payload.name or "").strip()
        if not name:
            raise HTTPException(status_code=400, detail="name is required")
        if supabase.table("categories").select("id").eq("name", name).execute().data:
            raise HTTPException(status_code=400, detail=f"A category named '{name}' already exists")
        row = {"name": name, "cost_code_id": payload.cost_code_id, "sort_order": payload.sort_order or 0}
        res = supabase.table("categories").insert(row).execute()
        return {"message": "Category created", "data": (res.data or [{}])[0]}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error creating category: {str(e)}")


@router.patch("/{category_id}")
async def update_category(category_id: str, payload: CategoryUpdate):
    try:
        updates = payload.model_dump(exclude_unset=True)
        if "name" in updates:
            updates["name"] = (updates["name"] or "").strip()
            if not updates["name"]:
                raise HTTPException(status_code=400, detail="name cannot be empty")
        if not updates:
            raise HTTPException(status_code=400, detail="No fields to update")
        res = supabase.table("categories").update(updates).eq("id", category_id).execute()
        if not res.data:
            raise HTTPException(status_code=404, detail="Category not found")
        return {"message": "Category updated", "data": res.data[0]}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error updating category: {str(e)}")


@router.delete("/{category_id}")
async def delete_category(category_id: str):
    try:
        if supabase.table("subcategories").select("id").eq("category_id", category_id).limit(1).execute().data:
            raise HTTPException(status_code=400, detail="Category has subcategories — move or delete them first.")
        supabase.table("categories").delete().eq("id", category_id).execute()
        return {"message": "Category deleted"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error deleting category: {str(e)}")


@router.post("/{category_id}/subcategories")
async def create_subcategory(category_id: str, payload: SubcategoryCreate):
    try:
        name = (payload.name or "").strip()
        if not name:
            raise HTTPException(status_code=400, detail="name is required")
        dup = supabase.table("subcategories").select("id").eq("category_id", category_id).eq("name", name).execute()
        if dup.data:
            raise HTTPException(status_code=400, detail=f"'{name}' already exists in this category")
        row = {"category_id": category_id, "name": name, "sort_order": payload.sort_order or 0}
        allowed = _normalize_allowed_cost_types(payload.allowed_cost_types)
        if allowed is not None:
            row["allowed_cost_types"] = allowed
        res = supabase.table("subcategories").insert(row).execute()
        return {"message": "Subcategory created", "data": (res.data or [{}])[0]}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error creating subcategory: {str(e)}")


@router.patch("/subcategories/{subcategory_id}")
async def update_subcategory(subcategory_id: str, payload: SubcategoryUpdate):
    """Rename, reorder, move to another category, or set allowed cost_types."""
    try:
        updates = payload.model_dump(exclude_unset=True)
        if "name" in updates:
            updates["name"] = (updates["name"] or "").strip()
            if not updates["name"]:
                raise HTTPException(status_code=400, detail="name cannot be empty")
        if "allowed_cost_types" in updates:
            updates["allowed_cost_types"] = _normalize_allowed_cost_types(updates["allowed_cost_types"]) or []
        if not updates:
            raise HTTPException(status_code=400, detail="No fields to update")
        res = supabase.table("subcategories").update(updates).eq("id", subcategory_id).execute()
        if not res.data:
            raise HTTPException(status_code=404, detail="Subcategory not found")
        return {"message": "Subcategory updated", "data": res.data[0]}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error updating subcategory: {str(e)}")


@router.delete("/subcategories/{subcategory_id}")
async def delete_subcategory(subcategory_id: str):
    """Blocked if accounts still map to it (account_category_map FK RESTRICT)."""
    try:
        if supabase.table("account_category_map").select("account_id").eq("subcategory_id", subcategory_id).limit(1).execute().data:
            raise HTTPException(status_code=400, detail="Subcategory is in use by classified accounts — reassign them first.")
        supabase.table("subcategories").delete().eq("id", subcategory_id).execute()
        return {"message": "Subcategory deleted"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error deleting subcategory: {str(e)}")
