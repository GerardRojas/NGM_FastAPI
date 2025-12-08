from fastapi import APIRouter, HTTPException
from api.supabase_client import supabase

router = APIRouter(prefix="/projects", tags=["Projects"])


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
    """
    try:
        # En la tabla companies la PK se llama 'id', no 'company_id'
        companies_resp = (
            supabase
            .table("companies")
            .select("id, name")
            .order("name")
            .execute()
        )

        status_resp = (
            supabase
            .table("project_status")
            .select("status_id, status")
            .order("status")
            .execute()
        )

        raw_companies = companies_resp.data or []
        raw_statuses = status_resp.data or []

        # Normalizamos para que el front siempre vea company_id + name
        companies = [
            {
                "company_id": c.get("id"),
                "name": c.get("name"),
            }
            for c in raw_companies
        ]

        statuses = raw_statuses  # ya vienen como status_id + status

        return {
            "companies": companies,
            "statuses": statuses,
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
