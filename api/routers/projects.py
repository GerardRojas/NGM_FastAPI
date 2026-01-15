from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from api.supabase_client import supabase
import uuid

router = APIRouter(prefix="/projects", tags=["Projects"])


# ====== MODELOS ======

class ProjectCreate(BaseModel):
    project_name: str
    source_company: str
    client: str | None = None
    address: str | None = None
    city: str | None = None
    status: str | None = None


class ProjectUpdate(BaseModel):
    project_name: str | None = None
    source_company: str | None = None
    city: str | None = None
    status: str | None = None


# ====== HELPERS ======

def extract_rel_value(row: dict, rel_name: str, field: str):
    """
    Extrae un valor desde una relación embebida de Supabase.
    Soporta tanto dict como list.
    """
    rel = row.get(rel_name)
    if rel is None:
        return None

    if isinstance(rel, list):
        if not rel:
            return None
        rel = rel[0]

    if isinstance(rel, dict):
        return rel.get(field)

    return None


# ====== ENDPOINTS ======

@router.post("/", status_code=201)
def create_project(payload: ProjectCreate):
    try:
        data = payload.dict()

        # ===== VALIDACIÓN DE FOREIGN KEYS =====
        # Validar source_company
        comp = supabase.table("companies").select("id").eq("id", data["source_company"]).single().execute()
        if not comp.data:
            raise HTTPException(status_code=400, detail="Invalid source_company")

        # Validar status
        if data["status"] is not None:
            status = supabase.table("project_status").select("status_id").eq("status_id", data["status"]).single().execute()
            if not status.data:
                raise HTTPException(status_code=400, detail="Invalid status")

        # Validar client
        if data["client"] is not None:
            client = supabase.table("clients").select("client_id").eq("client_id", data["client"]).single().execute()
            if not client.data:
                raise HTTPException(status_code=400, detail="Invalid client")

        # Generar un UUID para project_id
        data["project_id"] = str(uuid.uuid4())

        # ===== INSERCIÓN =====
        res = supabase.table("projects").insert(data).execute()

        return {
            "message": "Project created",
            "project": res.data[0],
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/")
def list_projects(limit: int = 100):
    """
    Devuelve lista de proyectos desde 'projects',
    incluyendo:
      - status_name desde project_status.status
      - company_name desde companies.name
      - client_name desde clients.client_name
    """
    try:
        resp = (
            supabase
            .table("projects")
            .select(
                """
                project_id,
                project_name,
                status,
                address,
                city,
                source_company,
                client,
                project_status(status),
                companies(name),
                clients(client_name)
                """
            )
            .limit(limit)
            .execute()
        )

        raw_projects = resp.data or []
        projects = []

        for row in raw_projects:
            row["status_name"] = extract_rel_value(row, "project_status", "status")
            row["company_name"] = extract_rel_value(row, "companies", "name")
            row["client_name"] = extract_rel_value(row, "clients", "client_name")

            row.pop("project_status", None)
            row.pop("companies", None)
            row.pop("clients", None)

            projects.append(row)

        return {"data": projects}

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error leyendo projects: {e}")


@router.get("/meta")
def get_projects_meta():
    """
    Devuelve catálogos básicos para la UI de Projects:
      - companies: company_id + name  (mapeado desde companies.id)
      - statuses: status_id + status
      - clients: client_id + client_name
    """
    try:
        # Companies (PK se llama 'id')
        companies_resp = (
            supabase
            .table("companies")
            .select("id, name")
            .order("name")
            .execute()
        )

        # Statuses
        status_resp = (
            supabase
            .table("project_status")
            .select("status_id, status")
            .order("status")
            .execute()
        )

        # Clients
        clients_resp = (
            supabase
            .table("clients")
            .select("client_id, client_name")
            .order("client_name")
            .execute()
        )

        raw_companies = companies_resp.data or []
        raw_statuses = status_resp.data or []
        raw_clients = clients_resp.data or []

        companies = [
            {
                "company_id": c.get("id"),
                "name": c.get("name"),
            }
            for c in raw_companies
        ]

        statuses = raw_statuses  # ya vienen como status_id + status
        clients = raw_clients    # ya vienen como client_id + client_name

        return {
            "companies": companies,
            "statuses": statuses,
            "clients": clients,
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error leyendo meta de projects: {e}")


@router.get("/{project_id}")
def get_project(project_id: str):
    """
    Devuelve un proyecto por ID (project_id),
    incluyendo:
      - status_name desde project_status.status
      - company_name desde companies.name
      - client_name desde clients.client_name
    """
    try:
        resp = (
            supabase
            .table("projects")
            .select(
                """
                project_id,
                project_name,
                status,
                address,
                city,
                source_company,
                client,
                project_status(status),
                companies(name),
                clients(client_name)
                """
            )
            .eq("project_id", project_id)
            .single()
            .execute()
        )

        if not resp.data:
            raise HTTPException(status_code=404, detail="Project not found")

        row = resp.data

        row["status_name"] = extract_rel_value(row, "project_status", "status")
        row["company_name"] = extract_rel_value(row, "companies", "name")
        row["client_name"] = extract_rel_value(row, "clients", "client_name")

        row.pop("project_status", None)
        row.pop("companies", None)
        row.pop("clients", None)

        return {"data": row}

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error leyendo project: {e}")


@router.delete("/{project_id}", status_code=200)
def delete_project(project_id: str):
    """
    Elimina un proyecto por su UUID.
    """
    try:
        # Verificar que el proyecto existe
        existing = (
            supabase
            .table("projects")
            .select("project_id")
            .eq("project_id", project_id)
            .single()
            .execute()
        )

        if not existing.data:
            raise HTTPException(status_code=404, detail="Project not found")

        # Eliminar el proyecto
        supabase.table("projects").delete().eq("project_id", project_id).execute()

        return {"message": "Project deleted successfully"}

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/{project_id}", status_code=200)
def update_project(project_id: str, payload: ProjectUpdate):
    """
    Actualiza un proyecto existente.
    Campos actualizables: project_name, source_company, city, status
    """
    try:
        # Verificar que el proyecto existe
        existing = (
            supabase
            .table("projects")
            .select("project_id")
            .eq("project_id", project_id)
            .single()
            .execute()
        )

        if not existing.data:
            raise HTTPException(status_code=404, detail="Project not found")

        # Filtrar solo los campos que no son None
        update_data = {k: v for k, v in payload.dict().items() if v is not None}

        if not update_data:
            raise HTTPException(status_code=400, detail="No fields to update")

        # Validar foreign keys si se proporcionan
        if "source_company" in update_data:
            comp = supabase.table("companies").select("id").eq("id", update_data["source_company"]).single().execute()
            if not comp.data:
                raise HTTPException(status_code=400, detail="Invalid source_company")

        if "status" in update_data:
            status = supabase.table("project_status").select("status_id").eq("status_id", update_data["status"]).single().execute()
            if not status.data:
                raise HTTPException(status_code=400, detail="Invalid status")

        # Actualizar el proyecto
        res = (
            supabase
            .table("projects")
            .update(update_data)
            .eq("project_id", project_id)
            .execute()
        )

        return {
            "message": "Project updated",
            "project": res.data[0],
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
