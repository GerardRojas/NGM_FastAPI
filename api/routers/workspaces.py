"""
Router para Workspaces (NGM Connect, Pillar B — capa de agrupacion aditiva).

Un workspace es una agrupacion NOMBRADA de miembros (external_contacts) +
proyectos. Se puede crear vacio y llenar de a poco. Los grants de modulos siguen
viviendo en project_client_access / project_user_access (este router NO los toca);
aca solo se gestiona el "quien" + "que proyectos" del workspace.

Ver sql/create_workspaces.sql.
"""
from fastapi import APIRouter, HTTPException, Depends
from api.auth import require_internal
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from api.supabase_client import supabase

router = APIRouter(dependencies=[Depends(require_internal)], prefix="/workspaces", tags=["workspaces"])


# ========================================
# Modelos
# ========================================

class WorkspaceCreate(BaseModel):
    name: str = Field(..., min_length=1)
    company_id: Optional[str] = None


class WorkspaceUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1)
    company_id: Optional[str] = None


class MemberAdd(BaseModel):
    external_type: str = Field(..., pattern="^(client|user)$")
    external_id: str = Field(..., min_length=1)


class ProjectAdd(BaseModel):
    project_id: str = Field(..., min_length=1)


# ========================================
# Helpers
# ========================================

def _counts_by_workspace(table: str) -> Dict[str, int]:
    """workspace_id -> row count for a child table (members/projects)."""
    out: Dict[str, int] = {}
    try:
        rows = supabase.table(table).select("workspace_id").execute().data or []
        for r in rows:
            wid = r.get("workspace_id")
            if wid:
                out[wid] = out.get(wid, 0) + 1
    except Exception:
        pass
    return out


# ========================================
# Endpoints — workspace CRUD
# ========================================

@router.get("")
async def list_workspaces() -> List[Dict[str, Any]]:
    """Lista todos los workspaces con conteo de miembros y proyectos."""
    try:
        rows = supabase.table("workspaces").select("*").order("name").execute().data or []
        members = _counts_by_workspace("workspace_members")
        projects = _counts_by_workspace("workspace_projects")
        for w in rows:
            w["member_count"] = members.get(w["id"], 0)
            w["project_count"] = projects.get(w["id"], 0)
        return rows
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching workspaces: {str(e)}")


@router.post("")
async def create_workspace(payload: WorkspaceCreate):
    """Crea un workspace (puede quedar vacio)."""
    try:
        insert_data = {k: v for k, v in payload.model_dump().items() if v is not None}
        res = supabase.table("workspaces").insert(insert_data).execute()
        created = res.data[0] if res.data else None
        if created is not None:
            created["member_count"] = 0
            created["project_count"] = 0
        return {"message": "Workspace created", "data": created}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error creating workspace: {str(e)}")


@router.get("/{workspace_id}")
async def get_workspace(workspace_id: str):
    """Detalle: workspace + miembros (con nombre/tier) + proyectos (con nombre)."""
    try:
        ws = supabase.table("workspaces").select("*").eq("id", workspace_id).single().execute()
        if not ws.data:
            raise HTTPException(status_code=404, detail="Workspace not found")

        members = supabase.table("workspace_members").select("*").eq("workspace_id", workspace_id).execute().data or []
        projects = supabase.table("workspace_projects").select("*").eq("workspace_id", workspace_id).execute().data or []

        # Resolve member names from the unified directory.
        member_ids = [m["external_id"] for m in members if m.get("external_id")]
        contact_by_id: Dict[str, Dict[str, Any]] = {}
        if member_ids:
            contacts = supabase.table("external_contacts").select("id, name, tier, category, email").in_("id", member_ids).execute().data or []
            contact_by_id = {c["id"]: c for c in contacts}
        for m in members:
            c = contact_by_id.get(m.get("external_id")) or {}
            m["name"] = c.get("name")
            m["tier"] = c.get("tier")
            m["category"] = c.get("category")
            m["email"] = c.get("email")

        # Resolve project names.
        project_ids = [p["project_id"] for p in projects if p.get("project_id")]
        name_by_project: Dict[str, str] = {}
        if project_ids:
            prows = supabase.table("projects").select("project_id, project_name").in_("project_id", project_ids).execute().data or []
            name_by_project = {p["project_id"]: p.get("project_name") for p in prows}
        for p in projects:
            p["project_name"] = name_by_project.get(p.get("project_id"))

        result = dict(ws.data)
        result["members"] = members
        result["projects"] = projects
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching workspace: {str(e)}")


@router.patch("/{workspace_id}")
async def update_workspace(workspace_id: str, payload: WorkspaceUpdate):
    """Renombra / reasigna company del workspace."""
    try:
        existing = supabase.table("workspaces").select("id").eq("id", workspace_id).execute()
        if not existing.data:
            raise HTTPException(status_code=404, detail="Workspace not found")
        update_data = payload.model_dump(exclude_unset=True)
        if not update_data:
            raise HTTPException(status_code=400, detail="No fields to update")
        res = supabase.table("workspaces").update(update_data).eq("id", workspace_id).execute()
        return {"message": "Workspace updated", "data": res.data[0] if res.data else None}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error updating workspace: {str(e)}")


@router.delete("/{workspace_id}")
async def delete_workspace(workspace_id: str):
    """Elimina el workspace (members/projects caen por ON DELETE CASCADE).
    NO toca los grants de modulos en las tablas de acceso."""
    try:
        existing = supabase.table("workspaces").select("id").eq("id", workspace_id).execute()
        if not existing.data:
            raise HTTPException(status_code=404, detail="Workspace not found")
        supabase.table("workspaces").delete().eq("id", workspace_id).execute()
        return {"message": "Workspace deleted"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error deleting workspace: {str(e)}")


# ========================================
# Endpoints — members
# ========================================

@router.post("/{workspace_id}/members")
async def add_member(workspace_id: str, payload: MemberAdd):
    """Agrega un miembro (client o user externo) al workspace. Idempotente."""
    try:
        existing = (
            supabase.table("workspace_members")
            .select("id")
            .eq("workspace_id", workspace_id)
            .eq("external_type", payload.external_type)
            .eq("external_id", payload.external_id)
            .execute()
        )
        if existing.data:
            return {"message": "Member already in workspace"}
        supabase.table("workspace_members").insert({
            "workspace_id": workspace_id,
            "external_type": payload.external_type,
            "external_id": payload.external_id,
        }).execute()
        return {"message": "Member added"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error adding member: {str(e)}")


@router.delete("/{workspace_id}/members/{external_type}/{external_id}")
async def remove_member(workspace_id: str, external_type: str, external_id: str):
    """Quita un miembro del workspace (no toca sus grants de acceso)."""
    try:
        supabase.table("workspace_members").delete() \
            .eq("workspace_id", workspace_id) \
            .eq("external_type", external_type) \
            .eq("external_id", external_id) \
            .execute()
        return {"message": "Member removed"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error removing member: {str(e)}")


# ========================================
# Endpoints — projects
# ========================================

@router.post("/{workspace_id}/projects")
async def add_project(workspace_id: str, payload: ProjectAdd):
    """Adjunta un proyecto al workspace. Idempotente."""
    try:
        existing = (
            supabase.table("workspace_projects")
            .select("id")
            .eq("workspace_id", workspace_id)
            .eq("project_id", payload.project_id)
            .execute()
        )
        if existing.data:
            return {"message": "Project already in workspace"}
        supabase.table("workspace_projects").insert({
            "workspace_id": workspace_id,
            "project_id": payload.project_id,
        }).execute()
        return {"message": "Project added"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error adding project: {str(e)}")


@router.delete("/{workspace_id}/projects/{project_id}")
async def remove_project(workspace_id: str, project_id: str):
    """Quita un proyecto del workspace (no toca los grants de acceso)."""
    try:
        supabase.table("workspace_projects").delete() \
            .eq("workspace_id", workspace_id) \
            .eq("project_id", project_id) \
            .execute()
        return {"message": "Project removed"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error removing project: {str(e)}")
