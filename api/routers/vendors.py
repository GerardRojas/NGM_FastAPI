"""
Router para gestión de Vendors (Proveedores)
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
from supabase import create_client, Client
import os

router = APIRouter(prefix="/vendors", tags=["vendors"])

# Inicializar cliente de Supabase
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    raise RuntimeError("Missing SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY in environment")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)


# ========================================
# Modelos Pydantic
# ========================================

class VendorCreate(BaseModel):
    vendor_name: str


class VendorUpdate(BaseModel):
    vendor_name: Optional[str] = None


# ========================================
# Endpoints
# ========================================

@router.get("/")
async def list_vendors():
    """
    Lista todos los vendors ordenados por nombre
    """
    try:
        response = supabase.table("Vendors").select("*").order("vendor_name").execute()
        return {"data": response.data or []}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching vendors: {str(e)}")


@router.get("/{vendor_id}")
async def get_vendor(vendor_id: str):
    """
    Obtiene un vendor específico por ID
    """
    try:
        response = supabase.table("Vendors").select("*").eq("id", vendor_id).single().execute()

        if not response.data:
            raise HTTPException(status_code=404, detail="Vendor not found")

        return response.data
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching vendor: {str(e)}")


@router.post("/")
async def create_vendor(vendor: VendorCreate):
    """
    Crea un nuevo vendor
    """
    try:
        # Verificar que no exista un vendor con el mismo nombre
        existing = supabase.table("Vendors").select("id").eq("vendor_name", vendor.vendor_name).execute()

        if existing.data and len(existing.data) > 0:
            raise HTTPException(status_code=400, detail="Vendor with this name already exists")

        response = supabase.table("Vendors").insert({
            "vendor_name": vendor.vendor_name
        }).execute()

        return {"message": "Vendor created successfully", "data": response.data[0] if response.data else None}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error creating vendor: {str(e)}")


@router.patch("/{vendor_id}")
async def update_vendor(vendor_id: str, vendor: VendorUpdate):
    """
    Actualiza un vendor existente (actualización parcial)
    """
    try:
        # Verificar que el vendor exista
        existing = supabase.table("Vendors").select("id").eq("id", vendor_id).execute()

        if not existing.data:
            raise HTTPException(status_code=404, detail="Vendor not found")

        # Construir datos a actualizar
        update_data = {}
        if vendor.vendor_name is not None:
            # Verificar que el nuevo nombre no esté en uso por otro vendor
            name_check = supabase.table("Vendors").select("id").eq("vendor_name", vendor.vendor_name).neq("id", vendor_id).execute()
            if name_check.data and len(name_check.data) > 0:
                raise HTTPException(status_code=400, detail="Vendor name already in use")
            update_data["vendor_name"] = vendor.vendor_name

        if not update_data:
            raise HTTPException(status_code=400, detail="No fields to update")

        response = supabase.table("Vendors").update(update_data).eq("id", vendor_id).execute()

        return {"message": "Vendor updated successfully", "data": response.data[0] if response.data else None}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error updating vendor: {str(e)}")


@router.delete("/{vendor_id}")
async def delete_vendor(vendor_id: str):
    """
    Elimina un vendor
    NOTA: Esto fallará si hay gastos (expenses) asociados a este vendor debido a foreign key constraint
    """
    try:
        # Verificar que el vendor exista
        existing = supabase.table("Vendors").select("id").eq("id", vendor_id).execute()

        if not existing.data:
            raise HTTPException(status_code=404, detail="Vendor not found")

        # Verificar si hay expenses asociados
        expenses_check = supabase.table("expenses").select("expense_id").eq("vendor_id", vendor_id).limit(1).execute()

        if expenses_check.data and len(expenses_check.data) > 0:
            raise HTTPException(
                status_code=400,
                detail="Cannot delete vendor: there are expenses associated with this vendor"
            )

        response = supabase.table("Vendors").delete().eq("id", vendor_id).execute()

        return {"message": "Vendor deleted successfully"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error deleting vendor: {str(e)}")
