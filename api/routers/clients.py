"""
Router para gestion de Clients (Clientes) — CRUD de la ficha de cliente.

Hasta ahora los clientes eran read-only (se leian via /projects/meta). Este
router agrega create/read/update/delete sobre la tabla `clients`, que ahora
incluye los campos de perfil (ver sql/add_client_profile_fields.sql).
"""
from fastapi import APIRouter, HTTPException, Query, Depends
from api.auth import require_internal
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from api.supabase_client import supabase

router = APIRouter(dependencies=[Depends(require_internal)], prefix="/clients", tags=["clients"])


# ========================================
# Modelos Pydantic
# ========================================

class ClientCreate(BaseModel):
    client_name: str = Field(..., min_length=1)
    contact_name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    address: Optional[str] = None
    company_id: Optional[str] = None
    status: Optional[str] = Field(default="Active", pattern="^(Active|Inactive)$")
    notes: Optional[str] = None


class ClientUpdate(BaseModel):
    client_name: Optional[str] = Field(default=None, min_length=1)
    contact_name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    address: Optional[str] = None
    company_id: Optional[str] = None
    status: Optional[str] = Field(default=None, pattern="^(Active|Inactive)$")
    notes: Optional[str] = None


# ========================================
# Endpoints
# ========================================

@router.get("")
async def list_clients(
    q: Optional[str] = Query(default=None, description="Search by client name"),
    company_id: Optional[str] = Query(default=None, description="Scope to a workspace (clients with NULL company_id are always included)"),
) -> List[Dict[str, Any]]:
    """
    Lista los clientes ordenados por nombre. Si se pasa company_id, se filtra al
    workspace activo (incluyendo siempre los clientes compartidos / sin company).
    """
    try:
        qry = supabase.table("clients").select("*")
        if company_id:
            qry = qry.or_(f"company_id.eq.{company_id},company_id.is.null")
        if q:
            qry = qry.ilike("client_name", f"%{q}%")
        response = qry.order("client_name").execute()
        return response.data or []
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching clients: {str(e)}")


@router.get("/{client_id}")
async def get_client(client_id: str):
    """Obtiene un cliente especifico por ID."""
    try:
        response = supabase.table("clients").select("*").eq("client_id", client_id).single().execute()
        if not response.data:
            raise HTTPException(status_code=404, detail="Client not found")
        return response.data
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching client: {str(e)}")


@router.post("")
async def create_client(client: ClientCreate):
    """Crea un nuevo cliente."""
    try:
        existing = supabase.table("clients").select("client_id").eq("client_name", client.client_name).execute()
        if existing.data and len(existing.data) > 0:
            raise HTTPException(status_code=400, detail="A client with this name already exists")

        # Remover None para que Supabase use los defaults (status, timestamps).
        insert_data = {k: v for k, v in client.model_dump().items() if v is not None}

        response = supabase.table("clients").insert(insert_data).execute()
        created = response.data[0] if response.data else None
        return {"message": "Client created successfully", "data": created}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error creating client: {str(e)}")


@router.patch("/{client_id}")
async def update_client(client_id: str, client: ClientUpdate):
    """Actualiza un cliente existente (actualizacion parcial)."""
    try:
        existing = supabase.table("clients").select("client_id").eq("client_id", client_id).execute()
        if not existing.data:
            raise HTTPException(status_code=404, detail="Client not found")

        update_data = client.model_dump(exclude_unset=True)
        if not update_data:
            raise HTTPException(status_code=400, detail="No fields to update")

        # Si cambia el nombre, evitar choque con otro cliente.
        if update_data.get("client_name"):
            name_check = (
                supabase.table("clients")
                .select("client_id")
                .eq("client_name", update_data["client_name"])
                .neq("client_id", client_id)
                .execute()
            )
            if name_check.data and len(name_check.data) > 0:
                raise HTTPException(status_code=400, detail="Client name already in use")

        response = supabase.table("clients").update(update_data).eq("client_id", client_id).execute()
        return {"message": "Client updated successfully", "data": response.data[0] if response.data else None}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error updating client: {str(e)}")


@router.delete("/{client_id}")
async def delete_client(client_id: str):
    """Elimina un cliente. Bloquea si todavia tiene proyectos asociados."""
    try:
        existing = supabase.table("clients").select("client_id").eq("client_id", client_id).execute()
        if not existing.data:
            raise HTTPException(status_code=404, detail="Client not found")

        # No borrar un cliente que aun esta referenciado por proyectos.
        try:
            linked = supabase.table("projects").select("project_id").eq("client_id", client_id).limit(1).execute()
            if linked.data:
                raise HTTPException(
                    status_code=400,
                    detail="This client still has projects assigned. Reassign or remove them first.",
                )
        except HTTPException:
            raise
        except Exception:
            # projects.client_id may not exist in some envs — don't block on the guard.
            pass

        supabase.table("clients").delete().eq("client_id", client_id).execute()
        return {"message": "Client deleted successfully"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error deleting client: {str(e)}")
