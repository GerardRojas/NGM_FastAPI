"""
Router para gestion de Units (Unidades de medida)
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
from supabase import create_client, Client
import os

router = APIRouter(prefix="/units", tags=["units"])

# Inicializar cliente de Supabase
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    raise RuntimeError("Missing SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY in environment")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)


# ========================================
# Modelos Pydantic
# ========================================

class UnitCreate(BaseModel):
    unit_name: str


# ========================================
# Endpoints
# ========================================

@router.get("")
async def list_units():
    """
    Lista todas las unidades de medida.
    """
    try:
        response = supabase.table("units").select("*").order("unit_name").execute()

        return {"data": response.data or []}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching units: {str(e)}")


@router.get("/{unit_id}")
async def get_unit(unit_id: str):
    """
    Obtiene una unidad especifica por ID.
    """
    try:
        response = supabase.table("units").select("*").eq("id_unit", unit_id).single().execute()

        if not response.data:
            raise HTTPException(status_code=404, detail="Unit not found")

        return response.data
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching unit: {str(e)}")


@router.post("")
async def create_unit(unit: UnitCreate):
    """
    Crea una nueva unidad de medida.
    """
    try:
        # Verificar que no exista una unidad con el mismo nombre
        existing = supabase.table("units").select("id_unit").eq("unit_name", unit.unit_name).execute()

        if existing.data and len(existing.data) > 0:
            raise HTTPException(status_code=400, detail="Unit with this name already exists")

        response = supabase.table("units").insert({
            "unit_name": unit.unit_name
        }).execute()

        return {"message": "Unit created successfully", "data": response.data[0] if response.data else None}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error creating unit: {str(e)}")


@router.delete("/{unit_id}")
async def delete_unit(unit_id: str):
    """
    Elimina una unidad de medida.
    """
    try:
        # Verificar que la unidad exista
        existing = supabase.table("units").select("id_unit").eq("id_unit", unit_id).execute()

        if not existing.data:
            raise HTTPException(status_code=404, detail="Unit not found")

        # Verificar si hay materiales usando esta unidad
        materials_check = supabase.table("materials").select('"ID"').eq("unit_id", unit_id).limit(1).execute()

        if materials_check.data and len(materials_check.data) > 0:
            raise HTTPException(
                status_code=400,
                detail="Cannot delete unit: there are materials using this unit"
            )

        supabase.table("units").delete().eq("id_unit", unit_id).execute()

        return {"message": "Unit deleted successfully"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error deleting unit: {str(e)}")
