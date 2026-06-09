"""
Router para gestion de Materials (Base de datos de materiales para estimator)
"""
import logging
from fastapi import APIRouter, HTTPException, Query, Depends
from api.auth import require_internal
from pydantic import BaseModel
from typing import Optional, List
from api.supabase_client import supabase

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(require_internal)], prefix="/materials", tags=["materials"])


# ========================================
# Modelos Pydantic
# ========================================

class MaterialCreate(BaseModel):
    ID: str  # codigo del material
    short_description: Optional[str] = None
    full_description: Optional[str] = None
    brand: Optional[str] = None
    sku: Optional[str] = None
    model: Optional[str] = None
    price_numeric: Optional[float] = None
    image: Optional[str] = None
    design_package_option: Optional[str] = None
    link: Optional[str] = None
    # FKs
    vendor_id: Optional[str] = None
    category_id: Optional[str] = None
    class_id: Optional[str] = None
    unit_id: Optional[str] = None
    # Categories re-arch: material | labor | external_service
    cost_type: Optional[str] = "material"
    # Design element = a finish/selection (tile, WC, paint…) that appears on the
    # Design Take Off. Catalog-level flag; only meaningful for cost_type=material.
    is_design_element: Optional[bool] = False


class MaterialUpdate(BaseModel):
    short_description: Optional[str] = None
    full_description: Optional[str] = None
    brand: Optional[str] = None
    sku: Optional[str] = None
    model: Optional[str] = None
    price_numeric: Optional[float] = None
    image: Optional[str] = None
    design_package_option: Optional[str] = None
    link: Optional[str] = None
    vendor_id: Optional[str] = None
    category_id: Optional[str] = None
    class_id: Optional[str] = None
    unit_id: Optional[str] = None
    cost_type: Optional[str] = None
    is_design_element: Optional[bool] = None


class MaterialResponse(BaseModel):
    ID: str
    short_description: Optional[str] = None
    full_description: Optional[str] = None
    brand: Optional[str] = None
    sku: Optional[str] = None
    model: Optional[str] = None
    price_numeric: Optional[float] = None
    image: Optional[str] = None
    vendor_id: Optional[str] = None
    category_id: Optional[str] = None
    class_id: Optional[str] = None
    unit_id: Optional[str] = None
    cost_type: Optional[str] = None
    is_design_element: Optional[bool] = None
    # Joined data
    vendor_name: Optional[str] = None
    category_name: Optional[str] = None
    class_name: Optional[str] = None
    unit_name: Optional[str] = None


# ========================================
# Helpers
# ========================================

def _price_text(price_numeric):
    """materials.price_numeric is kept in sync by a BEFORE INSERT/UPDATE trigger
    that derives it from the text "Price" column. An insert that sends only
    price_numeric (with "Price" empty) gets clobbered to NULL by that trigger,
    so we populate "Price" and let the trigger compute the numeric value."""
    return None if price_numeric is None else f"{price_numeric}"


# ========================================
# Endpoints
# ========================================

@router.get("")
async def list_materials(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    search: Optional[str] = None,
    category_id: Optional[str] = None,
    class_id: Optional[str] = None,
    vendor_id: Optional[str] = None,
    unit_id: Optional[str] = None
):
    """
    Lista materiales con paginacion y filtros.
    Incluye datos de tablas relacionadas (vendor, category, class, unit).
    """
    try:
        # Calcular offset
        offset = (page - 1) * page_size

        # Query base con joins
        query = supabase.table("materials").select(
            "*,"
            "Vendors(vendor_name),"
            "material_categories(name),"
            "material_classes(name),"
            "units(unit_name)",
            count="exact"
        )

        # Aplicar filtros
        if search:
            query = query.or_(
                f'"Short Description".ilike.%{search}%,'
                f'"Full Description".ilike.%{search}%,'
                f'"ID".ilike.%{search}%,'
                f'"Brand".ilike.%{search}%,'
                f'"SKU".ilike.%{search}%'
            )

        if category_id:
            query = query.eq("category_id", category_id)

        if class_id:
            query = query.eq("class_id", class_id)

        if vendor_id:
            query = query.eq("vendor_id", vendor_id)

        if unit_id:
            query = query.eq("unit_id", unit_id)

        # Ordenar y paginar
        query = query.order('"Short Description"').range(offset, offset + page_size - 1)

        response = query.execute()

        # Formatear respuesta con nombres de relaciones
        materials = []
        for m in response.data or []:
            material = {
                "ID": m.get("ID"),
                "short_description": m.get("Short Description"),
                "full_description": m.get("Full Description"),
                "brand": m.get("Brand"),
                "sku": m.get("SKU"),
                "model": m.get("Model"),
                "price_numeric": m.get("price_numeric"),
                "price": m.get("Price"),
                "image": m.get("Image"),
                "link": m.get("Link"),
                "design_package_option": m.get("Design Package Option"),
                "vendor_id": m.get("vendor_id"),
                "category_id": m.get("category_id"),
                "class_id": m.get("class_id"),
                "unit_id": m.get("unit_id"),
                "cost_type": m.get("cost_type"),
                "is_design_element": m.get("is_design_element"),
                "updated_at": m.get("updated_at"),
                # Nombres de relaciones
                "vendor_name": m.get("Vendors", {}).get("vendor_name") if m.get("Vendors") else None,
                "category_name": m.get("material_categories", {}).get("name") if m.get("material_categories") else None,
                "class_name": m.get("material_classes", {}).get("name") if m.get("material_classes") else None,
                "unit_name": m.get("units", {}).get("unit_name") if m.get("units") else None,
            }
            materials.append(material)

        total = response.count or 0
        total_pages = (total + page_size - 1) // page_size

        return {
            "data": materials,
            "pagination": {
                "page": page,
                "page_size": page_size,
                "total": total,
                "total_pages": total_pages
            }
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching materials: {str(e)}")


@router.get("/by-ids")
async def get_materials_by_ids(ids: str = Query(..., description="Comma-separated material IDs")):
    """Resolve a set of materials by ID to the minimal product fields the Design
    Take Off selection schedule needs (image, brand, sku, model, unit,
    is_design_element). Declared BEFORE /{material_id} so the literal path wins.
    Returns { data: { <id>: {…} } } keyed by material ID for O(1) lookup."""
    try:
        id_list = [s.strip() for s in (ids or "").split(",") if s.strip()]
        if not id_list:
            return {"data": {}}
        resp = (
            supabase.table("materials")
            .select('"ID","Short Description","Image","Brand","SKU","Model",cost_type,is_design_element,units(unit_name)')
            .in_('"ID"', id_list)
            .execute()
        )
        out = {}
        for m in resp.data or []:
            mid = m.get("ID")
            if not mid:
                continue
            out[str(mid)] = {
                "id": str(mid),
                "short_description": m.get("Short Description"),
                "image": m.get("Image"),
                "brand": m.get("Brand"),
                "sku": m.get("SKU"),
                "model": m.get("Model"),
                "cost_type": m.get("cost_type"),
                "is_design_element": bool(m.get("is_design_element")),
                "unit_name": m.get("units", {}).get("unit_name") if m.get("units") else None,
            }
        return {"data": out}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching materials by ids: {str(e)}")


@router.get("/{material_id}")
async def get_material(material_id: str):
    """
    Obtiene un material especifico por ID con datos relacionados.
    """
    try:
        response = supabase.table("materials").select(
            "*,"
            "Vendors(vendor_name),"
            "material_categories(name),"
            "material_classes(name),"
            "units(unit_name)"
        ).eq('"ID"', material_id).single().execute()

        if not response.data:
            raise HTTPException(status_code=404, detail="Material not found")

        m = response.data
        return {
            "ID": m.get("ID"),
            "short_description": m.get("Short Description"),
            "full_description": m.get("Full Description"),
            "brand": m.get("Brand"),
            "sku": m.get("SKU"),
            "model": m.get("Model"),
            "price_numeric": m.get("price_numeric"),
            "price": m.get("Price"),
            "image": m.get("Image"),
            "link": m.get("Link"),
            "design_package_option": m.get("Design Package Option"),
            "vendor_id": m.get("vendor_id"),
            "category_id": m.get("category_id"),
            "class_id": m.get("class_id"),
            "unit_id": m.get("unit_id"),
            "cost_type": m.get("cost_type"),
            "is_design_element": m.get("is_design_element"),
            "updated_at": m.get("updated_at"),
            "vendor_name": m.get("Vendors", {}).get("vendor_name") if m.get("Vendors") else None,
            "category_name": m.get("material_categories", {}).get("name") if m.get("material_categories") else None,
            "class_name": m.get("material_classes", {}).get("name") if m.get("material_classes") else None,
            "unit_name": m.get("units", {}).get("unit_name") if m.get("units") else None,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching material: {str(e)}")


@router.post("")
async def create_material(material: MaterialCreate):
    """
    Crea un nuevo material.
    """
    try:
        # Verificar que no exista un material con el mismo ID
        existing = supabase.table("materials").select('"ID"').eq('"ID"', material.ID).execute()

        if existing.data and len(existing.data) > 0:
            raise HTTPException(status_code=400, detail="Material with this ID already exists")

        insert_data = {
            "ID": material.ID,
            "Short Description": material.short_description,
            "Full Description": material.full_description,
            "Brand": material.brand,
            "SKU": material.sku,
            "Model": material.model,
            # "Price" drives price_numeric via the sync trigger (see _price_text).
            "Price": _price_text(material.price_numeric),
            "price_numeric": material.price_numeric,
            "Image": material.image,
            "Design Package Option": material.design_package_option,
            "Link": material.link,
            "vendor_id": material.vendor_id,
            "category_id": material.category_id,
            "class_id": material.class_id,
            "unit_id": material.unit_id,
            "cost_type": material.cost_type or "material",
            "is_design_element": bool(material.is_design_element),
        }

        # Remover None values
        insert_data = {k: v for k, v in insert_data.items() if v is not None}

        response = supabase.table("materials").insert(insert_data).execute()

        return {"message": "Material created successfully", "data": response.data[0] if response.data else None}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error creating material: {str(e)}")


@router.patch("/{material_id}")
async def update_material(material_id: str, material: MaterialUpdate):
    """
    Actualiza un material existente (actualizacion parcial).
    """
    try:
        # Verificar que el material exista
        existing = supabase.table("materials").select('"ID"').eq('"ID"', material_id).execute()

        if not existing.data:
            raise HTTPException(status_code=404, detail="Material not found")

        # Construir datos a actualizar
        update_data = {}

        if material.short_description is not None:
            update_data["Short Description"] = material.short_description
        if material.full_description is not None:
            update_data["Full Description"] = material.full_description
        if material.brand is not None:
            update_data["Brand"] = material.brand
        if material.sku is not None:
            update_data["SKU"] = material.sku
        if material.model is not None:
            update_data["Model"] = material.model
        if material.price_numeric is not None:
            update_data["price_numeric"] = material.price_numeric
            # Keep the "Price" text column (the trigger's source) in sync so the
            # two never drift; the trigger re-derives the same numeric value.
            update_data["Price"] = _price_text(material.price_numeric)
        if material.image is not None:
            update_data["Image"] = material.image
        if material.design_package_option is not None:
            update_data["Design Package Option"] = material.design_package_option
        if material.link is not None:
            update_data["Link"] = material.link
        if material.vendor_id is not None:
            update_data["vendor_id"] = material.vendor_id
        if material.category_id is not None:
            update_data["category_id"] = material.category_id
        if material.class_id is not None:
            update_data["class_id"] = material.class_id
        if material.unit_id is not None:
            update_data["unit_id"] = material.unit_id
        if material.cost_type is not None:
            update_data["cost_type"] = material.cost_type
        if material.is_design_element is not None:
            update_data["is_design_element"] = bool(material.is_design_element)

        if not update_data:
            raise HTTPException(status_code=400, detail="No fields to update")

        response = supabase.table("materials").update(update_data).eq('"ID"', material_id).execute()

        return {"message": "Material updated successfully", "data": response.data[0] if response.data else None}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error updating material: {str(e)}")


@router.delete("/{material_id}")
async def delete_material(material_id: str):
    """
    Elimina un material.
    """
    try:
        # Verificar que el material exista
        existing = supabase.table("materials").select('"ID"').eq('"ID"', material_id).execute()

        if not existing.data:
            raise HTTPException(status_code=404, detail="Material not found")

        # Referential integrity check: ensure no concepts reference this material
        refs = supabase.table("concept_materials").select("id", count="exact").eq("material_id", material_id).execute()
        ref_count = refs.count or 0
        if ref_count > 0:
            raise HTTPException(
                status_code=409,
                detail=f"Material is referenced by {ref_count} concept(s). Remove material from concepts before deleting."
            )

        supabase.table("materials").delete().eq('"ID"', material_id).execute()

        return {"message": "Material deleted successfully"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error deleting material: {str(e)}")


# ========================================
# Bulk Operations
# ========================================

@router.post("/bulk")
async def bulk_create_materials(materials: List[MaterialCreate]):
    """
    Crea multiples materiales en una sola operacion.
    """
    try:
        insert_data = []
        for m in materials:
            data = {
                "ID": m.ID,
                "Short Description": m.short_description,
                "Full Description": m.full_description,
                "Brand": m.brand,
                "SKU": m.sku,
                "Model": m.model,
                "Price": _price_text(m.price_numeric),
                "price_numeric": m.price_numeric,
                "Image": m.image,
                "Design Package Option": m.design_package_option,
                "Link": m.link,
                "vendor_id": m.vendor_id,
                "category_id": m.category_id,
                "class_id": m.class_id,
                "unit_id": m.unit_id,
                "cost_type": m.cost_type or "material",
                "is_design_element": bool(m.is_design_element),
            }
            # Remover None values
            data = {k: v for k, v in data.items() if v is not None}
            insert_data.append(data)

        response = supabase.table("materials").upsert(insert_data).execute()

        return {
            "message": f"{len(insert_data)} materials created/updated successfully",
            "count": len(response.data) if response.data else 0
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error bulk creating materials: {str(e)}")
