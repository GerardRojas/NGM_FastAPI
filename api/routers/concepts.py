"""
Router para gestion de Concepts (Conceptos compuestos de materiales)
"""
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from typing import Optional, List
from supabase import create_client, Client
import os

router = APIRouter(prefix="/concepts", tags=["concepts"])

# Inicializar cliente de Supabase
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    raise RuntimeError("Missing SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY in environment")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)


# ========================================
# Modelos Pydantic
# ========================================

class ConceptCreate(BaseModel):
    code: str
    short_description: Optional[str] = None
    full_description: Optional[str] = None
    category_id: Optional[str] = None
    subcategory_id: Optional[str] = None
    class_id: Optional[str] = None
    unit_id: Optional[str] = None
    base_cost: Optional[float] = 0
    labor_cost: Optional[float] = 0
    overhead_percentage: Optional[float] = 0
    waste_percent: Optional[float] = 0  # Waste percentage for materials
    image: Optional[str] = None
    notes: Optional[str] = None
    is_template: Optional[bool] = False
    builder: Optional[dict] = None  # Builder state JSON (inline items, labor, totals)


class ConceptUpdate(BaseModel):
    code: Optional[str] = None
    short_description: Optional[str] = None
    full_description: Optional[str] = None
    category_id: Optional[str] = None
    subcategory_id: Optional[str] = None
    class_id: Optional[str] = None
    unit_id: Optional[str] = None
    base_cost: Optional[float] = None
    labor_cost: Optional[float] = None
    overhead_percentage: Optional[float] = None
    waste_percent: Optional[float] = None
    calculated_cost: Optional[float] = None
    image: Optional[str] = None
    notes: Optional[str] = None
    is_active: Optional[bool] = None
    is_template: Optional[bool] = None
    builder: Optional[dict] = None


class ConceptMaterialAdd(BaseModel):
    material_id: str
    quantity: float = 1
    unit_id: Optional[str] = None
    unit_cost_override: Optional[float] = None
    notes: Optional[str] = None
    sort_order: Optional[int] = 0


class ConceptMaterialUpdate(BaseModel):
    quantity: Optional[float] = None
    unit_id: Optional[str] = None
    unit_cost_override: Optional[float] = None
    notes: Optional[str] = None
    sort_order: Optional[int] = None


# ========================================
# CONCEPTS CRUD
# ========================================

@router.get("")
async def list_concepts(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    search: Optional[str] = None,
    category_id: Optional[str] = None,
    class_id: Optional[str] = None,
    is_template: Optional[bool] = None,
    include_inactive: bool = False
):
    """
    Lista conceptos con paginacion y filtros.
    """
    try:
        offset = (page - 1) * page_size

        # Query con joins
        query = supabase.table("concepts").select(
            "*,"
            "material_categories!concepts_category_id_fkey(name),"
            "material_classes!concepts_class_id_fkey(name),"
            "units!concepts_unit_id_fkey(unit_name)",
            count="exact"
        )

        # Filtros
        if not include_inactive:
            query = query.eq("is_active", True)

        if search:
            query = query.or_(
                f"code.ilike.%{search}%,"
                f"short_description.ilike.%{search}%,"
                f"full_description.ilike.%{search}%"
            )

        if category_id:
            query = query.eq("category_id", category_id)

        if class_id:
            query = query.eq("class_id", class_id)

        if is_template is not None:
            query = query.eq("is_template", is_template)

        # Ordenar y paginar
        query = query.order("short_description").range(offset, offset + page_size - 1)

        response = query.execute()

        # Formatear respuesta
        concepts = []
        for c in response.data or []:
            # Contar materiales
            materials_count_resp = supabase.table("concept_materials").select("id", count="exact").eq("concept_id", c["id"]).execute()

            concept = {
                **c,
                "category_name": c.get("material_categories", {}).get("name") if c.get("material_categories") else None,
                "class_name": c.get("material_classes", {}).get("name") if c.get("material_classes") else None,
                "unit_name": c.get("units", {}).get("unit_name") if c.get("units") else None,
                "materials_count": materials_count_resp.count or 0
            }
            # Limpiar campos de join
            concept.pop("material_categories", None)
            concept.pop("material_classes", None)
            concept.pop("units", None)
            concepts.append(concept)

        total = response.count or 0
        total_pages = (total + page_size - 1) // page_size

        return {
            "data": concepts,
            "pagination": {
                "page": page,
                "page_size": page_size,
                "total": total,
                "total_pages": total_pages
            }
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching concepts: {str(e)}")


@router.get("/{concept_id}")
async def get_concept(concept_id: str):
    """
    Obtiene un concepto especifico con sus materiales.
    """
    try:
        # Obtener concepto
        response = supabase.table("concepts").select(
            "*,"
            "material_categories!concepts_category_id_fkey(name),"
            "material_classes!concepts_class_id_fkey(name),"
            "units!concepts_unit_id_fkey(unit_name)"
        ).eq("id", concept_id).single().execute()

        if not response.data:
            raise HTTPException(status_code=404, detail="Concept not found")

        c = response.data

        # Obtener materiales del concepto
        materials_resp = supabase.table("concept_materials").select(
            "*"
        ).eq("concept_id", concept_id).order("sort_order").execute()

        # Obtener info de cada material
        materials = []
        for cm in materials_resp.data or []:
            # Buscar info del material
            mat_resp = supabase.table("materials").select(
                '"ID", "Short Description", "Full Description", "Brand", "Image", "Price", price_numeric, "Unit"'
            ).eq('"ID"', cm["material_id"]).execute()

            mat_info = mat_resp.data[0] if mat_resp.data else {}

            materials.append({
                "id": cm["id"],
                "material_id": cm["material_id"],
                "quantity": cm["quantity"],
                "unit_id": cm["unit_id"],
                "unit_cost_override": cm["unit_cost_override"],
                "notes": cm["notes"],
                "sort_order": cm["sort_order"],
                # Info del material
                "material_name": mat_info.get("Short Description"),
                "material_full_description": mat_info.get("Full Description"),
                "material_brand": mat_info.get("Brand"),
                "material_image": mat_info.get("Image"),
                "material_price": mat_info.get("price_numeric") or mat_info.get("Price"),
                "material_unit": mat_info.get("Unit"),
                # Costo efectivo
                "effective_unit_cost": cm["unit_cost_override"] or mat_info.get("price_numeric") or 0,
                "line_total": (cm["quantity"] or 0) * (cm["unit_cost_override"] or mat_info.get("price_numeric") or 0)
            })

        # Calcular total de materiales
        total_materials_cost = sum(m["line_total"] for m in materials)

        return {
            "id": c["id"],
            "code": c["code"],
            "short_description": c["short_description"],
            "full_description": c["full_description"],
            "category_id": c["category_id"],
            "subcategory_id": c["subcategory_id"],
            "class_id": c["class_id"],
            "unit_id": c["unit_id"],
            "base_cost": c["base_cost"],
            "labor_cost": c["labor_cost"],
            "overhead_percentage": c["overhead_percentage"],
            "waste_percent": c.get("waste_percent", 0),
            "calculated_cost": c["calculated_cost"],
            "image": c["image"],
            "notes": c["notes"],
            "is_active": c["is_active"],
            "is_template": c["is_template"],
            "created_at": c["created_at"],
            "updated_at": c["updated_at"],
            # Builder state (inline items, labor, etc.)
            "builder": c.get("builder"),
            # Nombres de relaciones
            "category_name": c.get("material_categories", {}).get("name") if c.get("material_categories") else None,
            "class_name": c.get("material_classes", {}).get("name") if c.get("material_classes") else None,
            "unit_name": c.get("units", {}).get("unit_name") if c.get("units") else None,
            # Materiales
            "materials": materials,
            "materials_count": len(materials),
            "total_materials_cost": total_materials_cost
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching concept: {str(e)}")


@router.post("")
async def create_concept(concept: ConceptCreate):
    """
    Crea un nuevo concepto.
    """
    try:
        # Verificar que no exista un concepto con el mismo codigo
        existing = supabase.table("concepts").select("id").eq("code", concept.code).execute()

        if existing.data and len(existing.data) > 0:
            raise HTTPException(status_code=400, detail="Concept with this code already exists")

        insert_data = {
            "code": concept.code,
            "short_description": concept.short_description,
            "full_description": concept.full_description,
            "category_id": concept.category_id,
            "subcategory_id": concept.subcategory_id,
            "class_id": concept.class_id,
            "unit_id": concept.unit_id,
            "base_cost": concept.base_cost or 0,
            "labor_cost": concept.labor_cost or 0,
            "overhead_percentage": concept.overhead_percentage or 0,
            "waste_percent": concept.waste_percent or 0,
            "image": concept.image,
            "notes": concept.notes,
            "is_template": concept.is_template or False,
            "builder": concept.builder,
        }

        # Remover None values
        insert_data = {k: v for k, v in insert_data.items() if v is not None}

        response = supabase.table("concepts").insert(insert_data).execute()

        return {"message": "Concept created successfully", "data": response.data[0] if response.data else None}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error creating concept: {str(e)}")


@router.patch("/{concept_id}")
async def update_concept(concept_id: str, concept: ConceptUpdate):
    """
    Actualiza un concepto existente.
    """
    try:
        # Verificar que el concepto exista
        existing = supabase.table("concepts").select("id").eq("id", concept_id).execute()

        if not existing.data:
            raise HTTPException(status_code=404, detail="Concept not found")

        update_data = {}

        if concept.code is not None:
            # Verificar que el codigo no este en uso
            code_check = supabase.table("concepts").select("id").eq("code", concept.code).neq("id", concept_id).execute()
            if code_check.data and len(code_check.data) > 0:
                raise HTTPException(status_code=400, detail="Concept code already in use")
            update_data["code"] = concept.code

        if concept.short_description is not None:
            update_data["short_description"] = concept.short_description
        if concept.full_description is not None:
            update_data["full_description"] = concept.full_description
        if concept.category_id is not None:
            update_data["category_id"] = concept.category_id
        if concept.subcategory_id is not None:
            update_data["subcategory_id"] = concept.subcategory_id
        if concept.class_id is not None:
            update_data["class_id"] = concept.class_id
        if concept.unit_id is not None:
            update_data["unit_id"] = concept.unit_id
        if concept.base_cost is not None:
            update_data["base_cost"] = concept.base_cost
        if concept.labor_cost is not None:
            update_data["labor_cost"] = concept.labor_cost
        if concept.overhead_percentage is not None:
            update_data["overhead_percentage"] = concept.overhead_percentage
        if concept.waste_percent is not None:
            update_data["waste_percent"] = concept.waste_percent
        if concept.calculated_cost is not None:
            update_data["calculated_cost"] = concept.calculated_cost
        if concept.image is not None:
            update_data["image"] = concept.image
        if concept.notes is not None:
            update_data["notes"] = concept.notes
        if concept.is_active is not None:
            update_data["is_active"] = concept.is_active
        if concept.is_template is not None:
            update_data["is_template"] = concept.is_template
        if concept.builder is not None:
            update_data["builder"] = concept.builder

        if not update_data:
            raise HTTPException(status_code=400, detail="No fields to update")

        response = supabase.table("concepts").update(update_data).eq("id", concept_id).execute()

        return {"message": "Concept updated successfully", "data": response.data[0] if response.data else None}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error updating concept: {str(e)}")


@router.delete("/{concept_id}")
async def delete_concept(concept_id: str):
    """
    Elimina un concepto (soft delete si tiene materiales).
    """
    try:
        # Verificar que el concepto exista
        existing = supabase.table("concepts").select("id").eq("id", concept_id).execute()

        if not existing.data:
            raise HTTPException(status_code=404, detail="Concept not found")

        # Verificar si tiene materiales
        materials_check = supabase.table("concept_materials").select("id").eq("concept_id", concept_id).limit(1).execute()

        if materials_check.data and len(materials_check.data) > 0:
            # Soft delete
            supabase.table("concepts").update({"is_active": False}).eq("id", concept_id).execute()
            return {"message": "Concept deactivated (has materials)", "soft_delete": True}

        # Hard delete
        supabase.table("concepts").delete().eq("id", concept_id).execute()

        return {"message": "Concept deleted successfully"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error deleting concept: {str(e)}")


# ========================================
# CONCEPT MATERIALS (materiales dentro de un concepto)
# ========================================

@router.get("/{concept_id}/materials")
async def get_concept_materials(concept_id: str):
    """
    Lista los materiales de un concepto.
    """
    try:
        # Verificar que el concepto exista
        concept = supabase.table("concepts").select("id").eq("id", concept_id).execute()
        if not concept.data:
            raise HTTPException(status_code=404, detail="Concept not found")

        # Obtener materiales
        response = supabase.table("concept_materials").select("*").eq("concept_id", concept_id).order("sort_order").execute()

        materials = []
        total_cost = 0

        for cm in response.data or []:
            # Buscar info del material
            mat_resp = supabase.table("materials").select(
                '"ID", "Short Description", "Brand", "Image", price_numeric, "Unit"'
            ).eq('"ID"', cm["material_id"]).execute()

            mat_info = mat_resp.data[0] if mat_resp.data else {}
            effective_cost = cm["unit_cost_override"] or mat_info.get("price_numeric") or 0
            line_total = (cm["quantity"] or 0) * effective_cost

            materials.append({
                **cm,
                "material_name": mat_info.get("Short Description"),
                "material_brand": mat_info.get("Brand"),
                "material_image": mat_info.get("Image"),
                "material_price": mat_info.get("price_numeric"),
                "material_unit": mat_info.get("Unit"),
                "effective_unit_cost": effective_cost,
                "line_total": line_total
            })
            total_cost += line_total

        return {
            "data": materials,
            "total_cost": total_cost,
            "count": len(materials)
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching concept materials: {str(e)}")


@router.post("/{concept_id}/materials")
async def add_material_to_concept(concept_id: str, material: ConceptMaterialAdd):
    """
    Agrega un material a un concepto.
    """
    try:
        # Verificar que el concepto exista
        concept = supabase.table("concepts").select("id").eq("id", concept_id).execute()
        if not concept.data:
            raise HTTPException(status_code=404, detail="Concept not found")

        # Verificar que el material exista
        mat_check = supabase.table("materials").select('"ID"').eq('"ID"', material.material_id).execute()
        if not mat_check.data:
            raise HTTPException(status_code=404, detail="Material not found")

        # Verificar que no este duplicado
        existing = supabase.table("concept_materials").select("id").eq("concept_id", concept_id).eq("material_id", material.material_id).execute()
        if existing.data and len(existing.data) > 0:
            raise HTTPException(status_code=400, detail="Material already in concept")

        insert_data = {
            "concept_id": concept_id,
            "material_id": material.material_id,
            "quantity": material.quantity,
            "unit_id": material.unit_id,
            "unit_cost_override": material.unit_cost_override,
            "notes": material.notes,
            "sort_order": material.sort_order or 0,
        }

        response = supabase.table("concept_materials").insert(insert_data).execute()

        # Recalcular costo del concepto
        await recalculate_concept_cost(concept_id)

        return {"message": "Material added to concept", "data": response.data[0] if response.data else None}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error adding material: {str(e)}")


@router.patch("/{concept_id}/materials/{material_entry_id}")
async def update_concept_material(concept_id: str, material_entry_id: str, material: ConceptMaterialUpdate):
    """
    Actualiza un material en un concepto.
    """
    try:
        # Verificar que exista
        existing = supabase.table("concept_materials").select("id").eq("id", material_entry_id).eq("concept_id", concept_id).execute()
        if not existing.data:
            raise HTTPException(status_code=404, detail="Material entry not found")

        update_data = {}
        if material.quantity is not None:
            update_data["quantity"] = material.quantity
        if material.unit_id is not None:
            update_data["unit_id"] = material.unit_id
        if material.unit_cost_override is not None:
            update_data["unit_cost_override"] = material.unit_cost_override
        if material.notes is not None:
            update_data["notes"] = material.notes
        if material.sort_order is not None:
            update_data["sort_order"] = material.sort_order

        if not update_data:
            raise HTTPException(status_code=400, detail="No fields to update")

        response = supabase.table("concept_materials").update(update_data).eq("id", material_entry_id).execute()

        # Recalcular costo
        await recalculate_concept_cost(concept_id)

        return {"message": "Material updated", "data": response.data[0] if response.data else None}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error updating material: {str(e)}")


@router.delete("/{concept_id}/materials/{material_entry_id}")
async def remove_material_from_concept(concept_id: str, material_entry_id: str):
    """
    Elimina un material de un concepto.
    """
    try:
        # Verificar que exista
        existing = supabase.table("concept_materials").select("id").eq("id", material_entry_id).eq("concept_id", concept_id).execute()
        if not existing.data:
            raise HTTPException(status_code=404, detail="Material entry not found")

        supabase.table("concept_materials").delete().eq("id", material_entry_id).execute()

        # Recalcular costo
        await recalculate_concept_cost(concept_id)

        return {"message": "Material removed from concept"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error removing material: {str(e)}")


# ========================================
# Helper Functions
# ========================================

async def recalculate_concept_cost(concept_id: str):
    """
    Recalcula el costo total de materiales de un concepto.
    """
    try:
        # Obtener materiales
        materials_resp = supabase.table("concept_materials").select("material_id, quantity, unit_cost_override").eq("concept_id", concept_id).execute()

        total = 0
        for cm in materials_resp.data or []:
            if cm["unit_cost_override"]:
                unit_cost = cm["unit_cost_override"]
            else:
                mat_resp = supabase.table("materials").select("price_numeric").eq('"ID"', cm["material_id"]).execute()
                unit_cost = mat_resp.data[0].get("price_numeric", 0) if mat_resp.data else 0

            total += (cm["quantity"] or 0) * (unit_cost or 0)

        # Actualizar concepto
        supabase.table("concepts").update({"calculated_cost": total}).eq("id", concept_id).execute()

        return total
    except Exception as e:
        print(f"Error recalculating concept cost: {e}")
        return 0
