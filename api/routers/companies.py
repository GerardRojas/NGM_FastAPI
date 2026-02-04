"""
Router para gestion de Companies (Empresas)
"""
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from supabase import create_client, Client
import os

router = APIRouter(prefix="/companies", tags=["companies"])

# Inicializar cliente de Supabase
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    raise RuntimeError("Missing SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY in environment")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)


# ========================================
# Modelos Pydantic
# ========================================

class CompanyCreate(BaseModel):
    name: str = Field(..., min_length=1)
    description: Optional[str] = None
    avatar_color: Optional[int] = Field(default=None, ge=0, le=360)
    phone: Optional[str] = None
    email: Optional[str] = None
    address: Optional[str] = None
    status: Optional[str] = Field(default="Active", pattern="^(Active|Inactive)$")


class CompanyUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1)
    description: Optional[str] = None
    avatar_color: Optional[int] = Field(default=None, ge=0, le=360)
    phone: Optional[str] = None
    email: Optional[str] = None
    address: Optional[str] = None
    status: Optional[str] = Field(default=None, pattern="^(Active|Inactive)$")


# ========================================
# Endpoints
# ========================================

@router.get("")
async def list_companies(
    q: Optional[str] = Query(default=None, description="Search by company name"),
) -> List[Dict[str, Any]]:
    """
    Lista todas las companies ordenadas por nombre, con busqueda opcional.
    """
    try:
        qry = supabase.table("companies").select("*")
        if q:
            qry = qry.ilike("name", f"%{q}%")
        response = qry.order("name").execute()
        return response.data or []
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching companies: {str(e)}")


@router.get("/{company_id}")
async def get_company(company_id: str):
    """
    Obtiene una company especifica por ID.
    """
    try:
        response = supabase.table("companies").select("*").eq("id", company_id).single().execute()

        if not response.data:
            raise HTTPException(status_code=404, detail="Company not found")

        return response.data
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching company: {str(e)}")


@router.post("")
async def create_company(company: CompanyCreate):
    """
    Crea una nueva company.
    """
    try:
        # Verificar que no exista una company con el mismo nombre
        existing = supabase.table("companies").select("id").eq("name", company.name).execute()

        if existing.data and len(existing.data) > 0:
            raise HTTPException(status_code=400, detail="Company with this name already exists")

        insert_data = company.model_dump()
        # Remover valores None para que Supabase use los defaults
        insert_data = {k: v for k, v in insert_data.items() if v is not None}

        response = supabase.table("companies").insert(insert_data).execute()

        return {"message": "Company created successfully", "data": response.data[0] if response.data else None}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error creating company: {str(e)}")


@router.patch("/{company_id}")
async def update_company(company_id: str, company: CompanyUpdate):
    """
    Actualiza una company existente (actualizacion parcial).
    """
    try:
        # Verificar que la company exista
        existing = supabase.table("companies").select("id").eq("id", company_id).execute()

        if not existing.data:
            raise HTTPException(status_code=404, detail="Company not found")

        # Construir datos a actualizar (solo campos enviados)
        update_data = company.model_dump(exclude_unset=True)

        if not update_data:
            raise HTTPException(status_code=400, detail="No fields to update")

        # Si se cambia el nombre, verificar que no exista otro con ese nombre
        if "name" in update_data and update_data["name"] is not None:
            name_check = supabase.table("companies").select("id").eq("name", update_data["name"]).neq("id", company_id).execute()
            if name_check.data and len(name_check.data) > 0:
                raise HTTPException(status_code=400, detail="Company name already in use")

        response = supabase.table("companies").update(update_data).eq("id", company_id).execute()

        return {"message": "Company updated successfully", "data": response.data[0] if response.data else None}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error updating company: {str(e)}")


@router.delete("/{company_id}")
async def delete_company(company_id: str):
    """
    Elimina una company.
    """
    try:
        # Verificar que la company exista
        existing = supabase.table("companies").select("id").eq("id", company_id).execute()

        if not existing.data:
            raise HTTPException(status_code=404, detail="Company not found")

        response = supabase.table("companies").delete().eq("id", company_id).execute()

        return {"message": "Company deleted successfully"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error deleting company: {str(e)}")
