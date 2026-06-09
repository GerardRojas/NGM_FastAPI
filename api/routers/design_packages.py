"""
Design Packages — curated sets of design products (one per slot) that can be
switched onto an estimate. Slots reuse material_classes (materials.class_id).
See estimator-database/DESIGN_PACKAGES_PLAN.md.
"""
from fastapi import APIRouter, HTTPException, Depends, Query
from api.auth import require_internal
from pydantic import BaseModel
from typing import Optional, List
from api.supabase_client import supabase

router = APIRouter(dependencies=[Depends(require_internal)], prefix="/design-packages", tags=["design-packages"])


class PackageCreate(BaseModel):
    name: str
    company_id: Optional[str] = None
    is_default: Optional[bool] = False


class PackageUpdate(BaseModel):
    name: Optional[str] = None
    is_default: Optional[bool] = None


class ItemsSet(BaseModel):
    """Replace a package's full product list (one material per slot is enforced
    in the UI; the backend just stores the set)."""
    material_ids: List[str]


@router.get("")
async def list_packages(company_id: Optional[str] = Query(None)):
    """List packages for a workspace (plus shared/null-company ones), with the
    product count per package."""
    try:
        query = supabase.table("design_packages").select("*")
        if company_id:
            query = query.or_(f"company_id.eq.{company_id},company_id.is.null")
        rows = query.order("name").execute().data or []
        # Attach item counts (one extra query, fine for the small package set).
        counts = {}
        items = supabase.table("design_package_items").select("package_id").execute().data or []
        for it in items:
            pid = it.get("package_id")
            counts[pid] = counts.get(pid, 0) + 1
        for r in rows:
            r["item_count"] = counts.get(r.get("id"), 0)
        return {"data": rows}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error listing design packages: {str(e)}")


@router.post("")
async def create_package(pkg: PackageCreate):
    try:
        resp = supabase.table("design_packages").insert({
            "name": pkg.name,
            "company_id": pkg.company_id,
            "is_default": bool(pkg.is_default),
        }).execute()
        return {"data": resp.data[0] if resp.data else None}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error creating design package: {str(e)}")


@router.patch("/{package_id}")
async def update_package(package_id: str, pkg: PackageUpdate):
    try:
        update = {}
        if pkg.name is not None:
            update["name"] = pkg.name
        if pkg.is_default is not None:
            update["is_default"] = bool(pkg.is_default)
        if not update:
            return {"data": None}
        resp = supabase.table("design_packages").update(update).eq("id", package_id).execute()
        return {"data": resp.data[0] if resp.data else None}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error updating design package: {str(e)}")


@router.delete("/{package_id}")
async def delete_package(package_id: str):
    try:
        supabase.table("design_packages").delete().eq("id", package_id).execute()
        return {"success": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error deleting design package: {str(e)}")


@router.get("/{package_id}/items")
async def get_package_items(package_id: str):
    """The package's products with the fields the builder + the switch need
    (slot via class_id/class_name, image/brand/sku/model, price)."""
    try:
        items = (
            supabase.table("design_package_items")
            .select("material_id")
            .eq("package_id", package_id)
            .execute()
            .data
            or []
        )
        ids = [it.get("material_id") for it in items if it.get("material_id")]
        if not ids:
            return {"data": []}
        mats = (
            supabase.table("materials")
            .select('"ID","Short Description","Image","Brand","SKU","Model",price_numeric,class_id,design_switch_key,is_design_element,material_classes(name),units(unit_name)')
            .in_('"ID"', ids)
            .execute()
            .data
            or []
        )
        out = []
        for m in mats:
            out.append({
                "id": m.get("ID"),
                "short_description": m.get("Short Description"),
                "image": m.get("Image"),
                "brand": m.get("Brand"),
                "sku": m.get("SKU"),
                "model": m.get("Model"),
                "price_numeric": m.get("price_numeric"),
                "class_id": m.get("class_id"),
                "class_name": m.get("material_classes", {}).get("name") if m.get("material_classes") else None,
                "design_switch_key": m.get("design_switch_key"),
                "unit_name": m.get("units", {}).get("unit_name") if m.get("units") else None,
                "is_design_element": bool(m.get("is_design_element")),
            })
        return {"data": out}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching package items: {str(e)}")


@router.put("/{package_id}/items")
async def set_package_items(package_id: str, body: ItemsSet):
    """Replace the package's products with the given set (idempotent)."""
    try:
        supabase.table("design_package_items").delete().eq("package_id", package_id).execute()
        unique = []
        seen = set()
        for mid in body.material_ids:
            m = str(mid).strip()
            if m and m not in seen:
                seen.add(m)
                unique.append(m)
        if unique:
            supabase.table("design_package_items").insert(
                [{"package_id": package_id, "material_id": m} for m in unique]
            ).execute()
        return {"success": True, "count": len(unique)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error setting package items: {str(e)}")
