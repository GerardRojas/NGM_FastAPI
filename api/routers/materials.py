"""
Router para gestion de Materials (Base de datos de materiales para estimator)
"""
import logging
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from typing import Optional, List
from supabase import create_client, Client
import os

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/materials", tags=["materials"])

# Inicializar cliente de Supabase
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    raise RuntimeError("Missing SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY in environment")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)


# ========================================
# Modelos Pydantic
# ========================================

class MaterialCreate(BaseModel):
    ID: str  # codigo del material
    short_description: Optional[str] = None
    full_description: Optional[str] = None
    brand: Optional[str] = None
    sku: Optional[str] = None
    price_numeric: Optional[float] = None
    image: Optional[str] = None
    design_package_option: Optional[str] = None
    link: Optional[str] = None
    # FKs
    vendor_id: Optional[str] = None
    category_id: Optional[str] = None
    class_id: Optional[str] = None
    unit_id: Optional[str] = None


class MaterialUpdate(BaseModel):
    short_description: Optional[str] = None
    full_description: Optional[str] = None
    brand: Optional[str] = None
    sku: Optional[str] = None
    price_numeric: Optional[float] = None
    image: Optional[str] = None
    design_package_option: Optional[str] = None
    link: Optional[str] = None
    vendor_id: Optional[str] = None
    category_id: Optional[str] = None
    class_id: Optional[str] = None
    unit_id: Optional[str] = None


class MaterialResponse(BaseModel):
    ID: str
    short_description: Optional[str] = None
    full_description: Optional[str] = None
    brand: Optional[str] = None
    sku: Optional[str] = None
    price_numeric: Optional[float] = None
    image: Optional[str] = None
    vendor_id: Optional[str] = None
    category_id: Optional[str] = None
    class_id: Optional[str] = None
    unit_id: Optional[str] = None
    # Joined data
    vendor_name: Optional[str] = None
    category_name: Optional[str] = None
    class_name: Optional[str] = None
    unit_name: Optional[str] = None


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
                "price_numeric": m.get("price_numeric"),
                "price": m.get("Price"),
                "image": m.get("Image"),
                "link": m.get("Link"),
                "design_package_option": m.get("Design Package Option"),
                "vendor_id": m.get("vendor_id"),
                "category_id": m.get("category_id"),
                "class_id": m.get("class_id"),
                "unit_id": m.get("unit_id"),
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
            "price_numeric": m.get("price_numeric"),
            "price": m.get("Price"),
            "image": m.get("Image"),
            "link": m.get("Link"),
            "design_package_option": m.get("Design Package Option"),
            "vendor_id": m.get("vendor_id"),
            "category_id": m.get("category_id"),
            "class_id": m.get("class_id"),
            "unit_id": m.get("unit_id"),
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
            "price_numeric": material.price_numeric,
            "Image": material.image,
            "Design Package Option": material.design_package_option,
            "Link": material.link,
            "vendor_id": material.vendor_id,
            "category_id": material.category_id,
            "class_id": material.class_id,
            "unit_id": material.unit_id,
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
        if material.price_numeric is not None:
            update_data["price_numeric"] = material.price_numeric
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
                "price_numeric": m.price_numeric,
                "Image": m.image,
                "Design Package Option": m.design_package_option,
                "Link": m.link,
                "vendor_id": m.vendor_id,
                "category_id": m.category_id,
                "class_id": m.class_id,
                "unit_id": m.unit_id,
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
