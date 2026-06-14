"""
Router para el directorio unificado de External Contacts (NGM Connect).

Unifica lo que antes estaba partido entre la pagina `clients` y la seccion
"external users" de Team Management. Un contacto tiene un `tier`:

  * 'team_member' — externo interconectado: usa el hub (modulos, permisos de rol,
    recibe tareas). Es una cuenta de login que vive en `users` (muy FK-eada); su
    entrada de directorio se enlaza via users.contact_id. Aca NO se crean/borran
    esos logins — eso sigue por Team Management; este router gestiona la ficha.
  * 'client' — externo recurrente/informativo: workspace read-only de NGM Connect
    con modulos curados. Es la ficha CRM (mayormente soft-refs).

Ver sql/create_external_contacts.sql.
"""
from fastapi import APIRouter, HTTPException, Query, Depends
from api.auth import require_internal
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from api.supabase_client import supabase

router = APIRouter(dependencies=[Depends(require_internal)], prefix="/external-contacts", tags=["external-contacts"])


# ========================================
# Modelos Pydantic
# ========================================

TIER_PATTERN = "^(team_member|client)$"


class ContactCreate(BaseModel):
    name: str = Field(..., min_length=1)
    tier: str = Field(default="client", pattern=TIER_PATTERN)
    category: Optional[str] = None
    contact_name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    address: Optional[str] = None
    company_id: Optional[str] = None
    status: Optional[str] = Field(default="Active", pattern="^(Active|Inactive)$")
    notes: Optional[str] = None


class ContactUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1)
    tier: Optional[str] = Field(default=None, pattern=TIER_PATTERN)
    category: Optional[str] = None
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
async def list_contacts(
    q: Optional[str] = Query(default=None, description="Search by name"),
    tier: Optional[str] = Query(default=None, description="Filter by tier (team_member|client)"),
    category: Optional[str] = Query(default=None, description="Filter by category"),
    company_id: Optional[str] = Query(default=None, description="Scope to a workspace (NULL company_id always included)"),
) -> List[Dict[str, Any]]:
    """Lista los contactos externos ordenados por nombre, con filtros opcionales."""
    try:
        qry = supabase.table("external_contacts").select("*")
        if tier:
            qry = qry.eq("tier", tier)
        if category:
            qry = qry.eq("category", category)
        if company_id:
            qry = qry.or_(f"company_id.eq.{company_id},company_id.is.null")
        if q:
            qry = qry.ilike("name", f"%{q}%")
        response = qry.order("name").execute()
        return response.data or []
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching contacts: {str(e)}")


@router.get("/{contact_id}")
async def get_contact(contact_id: str):
    """Obtiene un contacto especifico por ID."""
    try:
        response = supabase.table("external_contacts").select("*").eq("id", contact_id).single().execute()
        if not response.data:
            raise HTTPException(status_code=404, detail="Contact not found")
        return response.data
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching contact: {str(e)}")


@router.post("")
async def create_contact(contact: ContactCreate):
    """Crea un nuevo contacto externo (ficha de directorio)."""
    try:
        existing = (
            supabase.table("external_contacts")
            .select("id")
            .eq("name", contact.name)
            .eq("tier", contact.tier)
            .execute()
        )
        if existing.data and len(existing.data) > 0:
            raise HTTPException(status_code=400, detail="A contact with this name already exists in this tier")

        # Remover None para que Supabase use los defaults (status, timestamps).
        insert_data = {k: v for k, v in contact.model_dump().items() if v is not None}

        response = supabase.table("external_contacts").insert(insert_data).execute()
        created = response.data[0] if response.data else None
        return {"message": "Contact created successfully", "data": created}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error creating contact: {str(e)}")


@router.patch("/{contact_id}")
async def update_contact(contact_id: str, contact: ContactUpdate):
    """Actualiza un contacto existente (actualizacion parcial)."""
    try:
        existing = supabase.table("external_contacts").select("id").eq("id", contact_id).execute()
        if not existing.data:
            raise HTTPException(status_code=404, detail="Contact not found")

        update_data = contact.model_dump(exclude_unset=True)
        if not update_data:
            raise HTTPException(status_code=400, detail="No fields to update")

        # Si cambia el nombre, evitar choque con otro contacto del mismo tier.
        if update_data.get("name"):
            tier_for_check = update_data.get("tier")
            name_check = supabase.table("external_contacts").select("id, tier").eq("name", update_data["name"]).neq("id", contact_id).execute()
            for row in (name_check.data or []):
                if tier_for_check is None or row.get("tier") == tier_for_check:
                    raise HTTPException(status_code=400, detail="Contact name already in use in this tier")

        response = supabase.table("external_contacts").update(update_data).eq("id", contact_id).execute()
        return {"message": "Contact updated successfully", "data": response.data[0] if response.data else None}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error updating contact: {str(e)}")


@router.delete("/{contact_id}")
async def delete_contact(contact_id: str):
    """
    Elimina un contacto. Bloquea si todavia esta referenciado:
      * team_member con login enlazado -> gestionarlo desde Team Management.
      * client con proyectos asignados -> reasignar/quitar primero.
    """
    try:
        existing = supabase.table("external_contacts").select("id, tier").eq("id", contact_id).single().execute()
        if not existing.data:
            raise HTTPException(status_code=404, detail="Contact not found")
        tier = existing.data.get("tier")

        # team_member: no borrar la ficha si todavia hay una cuenta de login.
        if tier == "team_member":
            try:
                linked = supabase.table("users").select("user_id").eq("contact_id", contact_id).limit(1).execute()
                if linked.data:
                    raise HTTPException(
                        status_code=400,
                        detail="This contact still has a login account. Remove the external user in Team Management first.",
                    )
            except HTTPException:
                raise
            except Exception:
                pass

        # client: no borrar si todavia esta referenciado por proyectos.
        if tier == "client":
            try:
                linked = supabase.table("projects").select("project_id").eq("client_id", contact_id).limit(1).execute()
                if linked.data:
                    raise HTTPException(
                        status_code=400,
                        detail="This client still has projects assigned. Reassign or remove them first.",
                    )
            except HTTPException:
                raise
            except Exception:
                pass

        supabase.table("external_contacts").delete().eq("id", contact_id).execute()
        return {"message": "Contact deleted successfully"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error deleting contact: {str(e)}")
