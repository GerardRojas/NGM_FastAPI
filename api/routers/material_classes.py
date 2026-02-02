"""
Router para gestion de Material Classes
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
from supabase import create_client, Client
import os

router = APIRouter(prefix="/material-classes", tags=["material-classes"])

# Inicializar cliente de Supabase
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    raise RuntimeError("Missing SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY in environment")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)


# ========================================
# Modelos Pydantic
# ========================================

class ClassCreate(BaseModel):
    name: str
    description: Optional[str] = None
    sort_order: Optional[int] = 0


class ClassUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    sort_order: Optional[int] = None
    is_active: Optional[bool] = None


# ========================================
# Endpoints
# ========================================

@router.get("")
async def list_classes(include_inactive: bool = False):
    """
    Lista todas las clases de materiales.
    Por defecto solo muestra activas.
    """
    try:
        query = supabase.table("material_classes").select("*")

        if not include_inactive:
            query = query.eq("is_active", True)

        response = query.order("sort_order").order("name").execute()

        return {"data": response.data or []}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching classes: {str(e)}")


@router.get("/{class_id}")
async def get_class(class_id: str):
    """
    Obtiene una clase especifica por ID.
    """
    try:
        response = supabase.table("material_classes").select("*").eq("id", class_id).single().execute()

        if not response.data:
            raise HTTPException(status_code=404, detail="Class not found")

        return response.data
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching class: {str(e)}")


@router.post("")
async def create_class(material_class: ClassCreate):
    """
    Crea una nueva clase.
    """
    try:
        # Verificar que no exista una clase con el mismo nombre
        existing = supabase.table("material_classes").select("id").eq("name", material_class.name).execute()

        if existing.data and len(existing.data) > 0:
            raise HTTPException(status_code=400, detail="Class with this name already exists")

        insert_data = {
            "name": material_class.name,
            "description": material_class.description,
            "sort_order": material_class.sort_order or 0,
        }

        response = supabase.table("material_classes").insert(insert_data).execute()

        return {"message": "Class created successfully", "data": response.data[0] if response.data else None}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error creating class: {str(e)}")


@router.patch("/{class_id}")
async def update_class(class_id: str, material_class: ClassUpdate):
    """
    Actualiza una clase existente.
    """
    try:
        # Verificar que la clase exista
        existing = supabase.table("material_classes").select("id").eq("id", class_id).execute()

        if not existing.data:
            raise HTTPException(status_code=404, detail="Class not found")

        update_data = {}

        if material_class.name is not None:
            # Verificar que el nombre no este en uso
            name_check = supabase.table("material_classes").select("id").eq("name", material_class.name).neq("id", class_id).execute()
            if name_check.data and len(name_check.data) > 0:
                raise HTTPException(status_code=400, detail="Class name already in use")
            update_data["name"] = material_class.name

        if material_class.description is not None:
            update_data["description"] = material_class.description
        if material_class.sort_order is not None:
            update_data["sort_order"] = material_class.sort_order
        if material_class.is_active is not None:
            update_data["is_active"] = material_class.is_active

        if not update_data:
            raise HTTPException(status_code=400, detail="No fields to update")

        response = supabase.table("material_classes").update(update_data).eq("id", class_id).execute()

        return {"message": "Class updated successfully", "data": response.data[0] if response.data else None}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error updating class: {str(e)}")


@router.delete("/{class_id}")
async def delete_class(class_id: str):
    """
    Elimina (soft delete) una clase.
    """
    try:
        # Verificar que la clase exista
        existing = supabase.table("material_classes").select("id").eq("id", class_id).execute()

        if not existing.data:
            raise HTTPException(status_code=404, detail="Class not found")

        # Verificar si hay materiales usando esta clase
        materials_check = supabase.table("materials").select('"ID"').eq("class_id", class_id).limit(1).execute()

        if materials_check.data and len(materials_check.data) > 0:
            # Soft delete - solo desactivar
            supabase.table("material_classes").update({"is_active": False}).eq("id", class_id).execute()
            return {
                "message": "Class deactivated (has materials assigned)",
                "soft_delete": True
            }

        # Hard delete si no hay materiales
        supabase.table("material_classes").delete().eq("id", class_id).execute()

        return {"message": "Class deleted successfully"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error deleting class: {str(e)}")
