"""
Router para gestion de Percentage Items (items de porcentaje reutilizables)
Waste, overhead, profit margins, etc. para el Concept Builder
"""
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from typing import Optional
from supabase import create_client, Client
import os

router = APIRouter(prefix="/percentage-items", tags=["percentage-items"])

# Inicializar cliente de Supabase
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    raise RuntimeError("Missing SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY in environment")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)


# ========================================
# Modelos Pydantic
# ========================================

class PercentageItemCreate(BaseModel):
    code: str
    description: str
    applies_to: Optional[str] = "material"
    default_value: Optional[float] = 0
    is_standard: Optional[bool] = False
    sort_order: Optional[int] = 0


class PercentageItemUpdate(BaseModel):
    code: Optional[str] = None
    description: Optional[str] = None
    applies_to: Optional[str] = None
    default_value: Optional[float] = None
    is_standard: Optional[bool] = None
    is_active: Optional[bool] = None
    sort_order: Optional[int] = None


# ========================================
# CRUD
# ========================================

@router.get("")
async def list_percentage_items(
    is_standard: Optional[bool] = None,
    applies_to: Optional[str] = None,
    include_inactive: bool = False
):
    """
    Lista percentage items con filtros opcionales.
    """
    try:
        query = supabase.table("percentage_items").select("*", count="exact")

        if not include_inactive:
            query = query.eq("is_active", True)

        if is_standard is not None:
            query = query.eq("is_standard", is_standard)

        if applies_to:
            query = query.eq("applies_to", applies_to)

        query = query.order("sort_order").order("code")

        response = query.execute()

        return {
            "data": response.data or [],
            "total": response.count or 0
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching percentage items: {str(e)}")


@router.get("/standard")
async def list_standard_percentage_items():
    """
    Lista solo los items estandar (is_standard=true, is_active=true).
    Usado por el Concept Builder para precargar items automaticamente.
    """
    try:
        response = supabase.table("percentage_items").select("*") \
            .eq("is_standard", True) \
            .eq("is_active", True) \
            .order("sort_order") \
            .execute()

        return {
            "data": response.data or []
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching standard percentage items: {str(e)}")


@router.get("/{item_id}")
async def get_percentage_item(item_id: str):
    """
    Obtiene un percentage item especifico.
    """
    try:
        response = supabase.table("percentage_items").select("*").eq("id", item_id).single().execute()

        if not response.data:
            raise HTTPException(status_code=404, detail="Percentage item not found")

        return response.data
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching percentage item: {str(e)}")


@router.post("")
async def create_percentage_item(item: PercentageItemCreate):
    """
    Crea un nuevo percentage item.
    """
    try:
        # Validar applies_to
        if item.applies_to not in ("material", "labor", "total"):
            raise HTTPException(status_code=400, detail="applies_to must be 'material', 'labor', or 'total'")

        # Verificar codigo unico
        existing = supabase.table("percentage_items").select("id").eq("code", item.code).execute()
        if existing.data and len(existing.data) > 0:
            raise HTTPException(status_code=400, detail="Percentage item with this code already exists")

        insert_data = {
            "code": item.code,
            "description": item.description,
            "applies_to": item.applies_to,
            "default_value": item.default_value or 0,
            "is_standard": item.is_standard or False,
            "sort_order": item.sort_order or 0,
        }

        response = supabase.table("percentage_items").insert(insert_data).execute()

        return {"message": "Percentage item created", "data": response.data[0] if response.data else None}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error creating percentage item: {str(e)}")


@router.patch("/{item_id}")
async def update_percentage_item(item_id: str, item: PercentageItemUpdate):
    """
    Actualiza un percentage item.
    """
    try:
        existing = supabase.table("percentage_items").select("id").eq("id", item_id).execute()
        if not existing.data:
            raise HTTPException(status_code=404, detail="Percentage item not found")

        update_data = {}

        if item.code is not None:
            # Verificar unicidad
            code_check = supabase.table("percentage_items").select("id").eq("code", item.code).neq("id", item_id).execute()
            if code_check.data and len(code_check.data) > 0:
                raise HTTPException(status_code=400, detail="Code already in use")
            update_data["code"] = item.code

        if item.description is not None:
            update_data["description"] = item.description
        if item.applies_to is not None:
            if item.applies_to not in ("material", "labor", "total"):
                raise HTTPException(status_code=400, detail="applies_to must be 'material', 'labor', or 'total'")
            update_data["applies_to"] = item.applies_to
        if item.default_value is not None:
            update_data["default_value"] = item.default_value
        if item.is_standard is not None:
            update_data["is_standard"] = item.is_standard
        if item.is_active is not None:
            update_data["is_active"] = item.is_active
        if item.sort_order is not None:
            update_data["sort_order"] = item.sort_order

        if not update_data:
            raise HTTPException(status_code=400, detail="No fields to update")

        response = supabase.table("percentage_items").update(update_data).eq("id", item_id).execute()

        return {"message": "Percentage item updated", "data": response.data[0] if response.data else None}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error updating percentage item: {str(e)}")


@router.delete("/{item_id}")
async def delete_percentage_item(item_id: str):
    """
    Elimina un percentage item. Los standard items se desactivan en vez de eliminarse.
    """
    try:
        existing = supabase.table("percentage_items").select("id, is_standard").eq("id", item_id).execute()
        if not existing.data:
            raise HTTPException(status_code=404, detail="Percentage item not found")

        if existing.data[0].get("is_standard"):
            # Soft delete para standard items
            supabase.table("percentage_items").update({"is_active": False}).eq("id", item_id).execute()
            return {"message": "Standard item deactivated", "soft_delete": True}

        # Hard delete para non-standard
        supabase.table("percentage_items").delete().eq("id", item_id).execute()

        return {"message": "Percentage item deleted"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error deleting percentage item: {str(e)}")
