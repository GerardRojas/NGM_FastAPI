# api/routers/pipeline.py
# Refactored to use Supabase REST client instead of asyncpg direct connection

from __future__ import annotations

from typing import Dict, Any, List, Optional
import traceback

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, field_validator
from api.supabase_client import supabase

router = APIRouter(prefix="/pipeline", tags=["pipeline"])


# ====== MODELOS ======

class TaskCreate(BaseModel):
    task_description: str
    company: str  # UUID de la empresa
    project: Optional[str] = None  # UUID del proyecto
    owner: str  # UUID del owner
    collaborator: Optional[str] = None  # UUID del colaborador
    type: str  # UUID del tipo de tarea
    department: str  # UUID del departamento
    due_date: Optional[str] = None  # Fecha YYYY-MM-DD
    deadline: Optional[str] = None  # Fecha YYYY-MM-DD
    status: str = "not started"  # Nombre del status

    @field_validator("task_description", "company", "owner", "type", "department")
    @classmethod
    def not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("Field cannot be empty")
        return v.strip()


class TaskUpdate(BaseModel):
    """Modelo para actualización parcial de tareas."""
    task_description: Optional[str] = None
    project: Optional[str] = None
    company: Optional[str] = None
    department: Optional[str] = None
    type: Optional[str] = None
    owner: Optional[str] = None
    collaborator: Optional[str] = None
    manager: Optional[str] = None
    due_date: Optional[str] = None
    start_date: Optional[str] = None
    deadline: Optional[str] = None
    time_start: Optional[str] = None
    time_finish: Optional[str] = None
    status: Optional[str] = None
    priority: Optional[str] = None


# ====== CATALOG ENDPOINTS ======

@router.get("/projects")
def get_pipeline_projects() -> Dict[str, Any]:
    """Devuelve lista de proyectos para dropdowns en Pipeline UI."""
    try:
        response = supabase.table("projects").select("project_id, project_name").order("project_name").execute()
        return {"data": response.data or []}
    except Exception as e:
        print(f"[PIPELINE] ERROR in GET /pipeline/projects: {repr(e)}")
        print(traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"DB error: {e}") from e


@router.get("/companies")
def get_pipeline_companies() -> Dict[str, Any]:
    """Devuelve lista de empresas para dropdowns en Pipeline UI."""
    try:
        response = supabase.table("companies").select("id, name").order("name").execute()
        return {"data": response.data or []}
    except Exception as e:
        print(f"[PIPELINE] ERROR in GET /pipeline/companies: {repr(e)}")
        print(traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"DB error: {e}") from e


@router.get("/task-departments")
def get_pipeline_task_departments() -> Dict[str, Any]:
    """Devuelve lista de departamentos para dropdowns en Pipeline UI."""
    try:
        response = supabase.table("task_departments").select("department_id, department_name").order("department_name").execute()
        return {"data": response.data or []}
    except Exception as e:
        print(f"[PIPELINE] ERROR in GET /pipeline/task-departments: {repr(e)}")
        print(traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"DB error: {e}") from e


@router.get("/task-types")
def get_pipeline_task_types() -> Dict[str, Any]:
    """Devuelve lista de tipos de tarea para dropdowns en Pipeline UI."""
    try:
        response = supabase.table("task_types").select("type_id, type_name").order("type_name").execute()
        return {"data": response.data or []}
    except Exception as e:
        print(f"[PIPELINE] ERROR in GET /pipeline/task-types: {repr(e)}")
        print(traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"DB error: {e}") from e


@router.get("/task-priorities")
def get_pipeline_task_priorities() -> Dict[str, Any]:
    """Devuelve lista de prioridades para dropdowns en Pipeline UI."""
    try:
        response = supabase.table("tasks_priority").select("priority_id, priority").order("priority").execute()
        return {"data": response.data or []}
    except Exception as e:
        print(f"[PIPELINE] ERROR in GET /pipeline/task-priorities: {repr(e)}")
        print(traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"DB error: {e}") from e


@router.get("/users")
def get_pipeline_users() -> Dict[str, Any]:
    """Devuelve lista de usuarios para dropdowns en Pipeline UI."""
    try:
        response = supabase.table("users").select("user_id, user_name").order("user_name").execute()
        return {"data": response.data or []}
    except Exception as e:
        print(f"[PIPELINE] ERROR in GET /pipeline/users: {repr(e)}")
        print(traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"DB error: {e}") from e


# ====== MAIN GROUPED ENDPOINT ======

@router.get("/grouped")
def get_pipeline_grouped() -> Dict[str, Any]:
    """
    Devuelve las tareas agrupadas por task_status.
    Usa múltiples queries al cliente Supabase para obtener los datos relacionados.
    """
    print("[PIPELINE] GET /pipeline/grouped called")

    try:
        # 1. Obtener todos los statuses
        print("[PIPELINE] Fetching statuses...")
        statuses_response = supabase.table("tasks_status").select("task_status_id, task_status").order("task_status").execute()
        statuses = statuses_response.data or []
        print(f"[PIPELINE] Found {len(statuses)} statuses")

        # 2. Obtener todas las tareas
        print("[PIPELINE] Fetching tasks...")
        tasks_response = supabase.table("tasks").select("*").order("created_at", desc=True).execute()
        tasks = tasks_response.data or []
        print(f"[PIPELINE] Found {len(tasks)} tasks")

        # 3. Obtener datos relacionados para enriquecer las tareas
        print("[PIPELINE] Fetching related data...")

        # Users (para owner, collaborator, manager)
        users_response = supabase.table("users").select("user_id, user_name").execute()
        users_map = {u["user_id"]: u for u in (users_response.data or [])}

        # Projects
        projects_response = supabase.table("projects").select("project_id, project_name").execute()
        projects_map = {p["project_id"]: p for p in (projects_response.data or [])}

        # Companies
        companies_response = supabase.table("companies").select("id, name").execute()
        companies_map = {c["id"]: c for c in (companies_response.data or [])}

        # Priorities
        priorities_response = supabase.table("tasks_priority").select("priority_id, priority").execute()
        priorities_map = {p["priority_id"]: p for p in (priorities_response.data or [])}

        # Completed statuses
        completed_response = supabase.table("task_completed_status").select("completed_status_id, completed_status").execute()
        completed_map = {c["completed_status_id"]: c for c in (completed_response.data or [])}

        # Status map for names
        status_map = {s["task_status_id"]: s["task_status"] for s in statuses}

        print("[PIPELINE] Processing tasks...")

        # 4. Agrupar tareas por status
        groups_map: Dict[str, Dict[str, Any]] = {}

        for task in tasks:
            status_id = task.get("task_status")
            status_name = status_map.get(status_id, "(no status)")

            group_key = str(status_id) if status_id else status_name

            if group_key not in groups_map:
                groups_map[group_key] = {
                    "status_id": status_id,
                    "status_name": status_name,
                    "tasks": [],
                }

            # Enriquecer la tarea con datos relacionados
            owner_id = task.get("Owner_id")
            owner_data = users_map.get(owner_id) if owner_id else None

            collab_id = task.get("Colaborators_id")
            collab_data = users_map.get(collab_id) if collab_id else None

            manager_id = task.get("manager")
            manager_data = users_map.get(manager_id) if manager_id else None

            project_id = task.get("project_id")
            project_data = projects_map.get(project_id) if project_id else None

            company_id = task.get("company_management")
            company_data = companies_map.get(company_id) if company_id else None

            priority_id = task.get("task_priority")
            priority_data = priorities_map.get(priority_id) if priority_id else None

            finished_id = task.get("task_finished_status")
            finished_data = completed_map.get(finished_id) if finished_id else None

            # Construir objeto de tarea enriquecido
            enriched_task = {
                **task,
                # Nombres útiles
                "project_name": project_data["project_name"] if project_data else None,
                "company_name": company_data["name"] if company_data else None,
                "status_name": status_name,
                "priority_name": priority_data["priority"] if priority_data else None,
                "finished_status_name": finished_data["completed_status"] if finished_data else None,
                # Objetos anidados para el frontend
                "owner": {
                    "id": owner_id,
                    "name": owner_data["user_name"] if owner_data else None,
                } if owner_id else None,
                "collaborators": [{
                    "id": collab_id,
                    "name": collab_data["user_name"] if collab_data else None,
                }] if collab_id else [],
                "manager": {
                    "id": manager_id,
                    "name": manager_data["user_name"] if manager_data else None,
                } if manager_id else None,
                "priority": {
                    "priority_id": priority_id,
                    "priority_name": priority_data["priority"] if priority_data else None,
                } if priority_id else None,
                "finished_status": {
                    "completed_status_id": finished_id,
                    "completed_status_name": finished_data["completed_status"] if finished_data else None,
                } if finished_id else None,
            }

            groups_map[group_key]["tasks"].append(enriched_task)

        # 5. Construir lista final con todos los statuses (incluso vacíos)
        groups: List[Dict[str, Any]] = []

        for status in statuses:
            status_id = status["task_status_id"]
            status_name = status["task_status"]
            group_key = str(status_id)

            if group_key in groups_map:
                groups.append(groups_map.pop(group_key))
            else:
                groups.append({
                    "status_id": status_id,
                    "status_name": status_name,
                    "tasks": [],
                })

        # Añadir grupos remanentes (tareas sin status válido)
        for remaining_group in groups_map.values():
            groups.append(remaining_group)

        total_tasks = sum(len(g.get("tasks", [])) for g in groups)
        print(f"[PIPELINE] Returning {len(groups)} groups with {total_tasks} total tasks")

        return {"groups": groups}

    except Exception as e:
        print(f"[PIPELINE] ERROR in GET /pipeline/grouped: {repr(e)}")
        print(f"[PIPELINE] Traceback: {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"DB error: {e}") from e


# ====== TASK CRUD ENDPOINTS ======

@router.post("/tasks", status_code=201)
def create_task(payload: TaskCreate) -> Dict[str, Any]:
    """Crea una nueva tarea en el pipeline."""
    try:
        # Buscar el status_id basado en el nombre
        status_response = supabase.table("tasks_status").select("task_status_id").ilike("task_status", payload.status).execute()

        if not status_response.data:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid status: '{payload.status}'. Status not found."
            )

        status_id = status_response.data[0]["task_status_id"]

        # Preparar datos para insertar
        task_data = {
            "task_description": payload.task_description,
            "company_management": payload.company,
            "project_id": payload.project,
            "Owner_id": payload.owner,
            "Colaborators_id": payload.collaborator,
            "task_type": payload.type,
            "task_department": payload.department,
            "due_date": payload.due_date,
            "deadline": payload.deadline,
            "task_status": status_id,
        }

        # Insertar tarea
        response = supabase.table("tasks").insert(task_data).execute()

        if not response.data:
            raise HTTPException(status_code=500, detail="Failed to create task")

        return {
            "message": "Task created successfully",
            "task": response.data[0],
        }

    except HTTPException:
        raise
    except Exception as e:
        print(f"[PIPELINE] ERROR in POST /pipeline/tasks: {repr(e)}")
        print(traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"DB error: {e}") from e


# Mapeo de campos UI → columnas de la tabla tasks
FIELD_TO_COLUMN = {
    "task_description": "task_description",
    "project": "project_id",
    "company": "company_management",
    "department": "task_department",
    "type": "task_type",
    "owner": "Owner_id",
    "collaborator": "Colaborators_id",
    "manager": "manager",
    "due_date": "due_date",
    "start_date": "start_date",
    "deadline": "deadline",
    "time_start": "time_start",
    "time_finish": "time_finish",
    "status": "task_status",
    "priority": "task_priority",
}


@router.patch("/tasks/{task_id}")
def patch_task(task_id: str, payload: TaskUpdate) -> Dict[str, Any]:
    """Actualiza campos individuales de una tarea."""
    try:
        # Verificar que la tarea existe
        existing = supabase.table("tasks").select("task_id").eq("task_id", task_id).execute()

        if not existing.data:
            raise HTTPException(status_code=404, detail="Task not found")

        # Obtener solo los campos enviados
        updates_raw = payload.model_dump(exclude_unset=True)

        if not updates_raw:
            raise HTTPException(status_code=400, detail="No fields to update")

        # Construir datos de actualización
        update_data: Dict[str, Any] = {}

        for field, value in updates_raw.items():
            column = FIELD_TO_COLUMN.get(field)
            if not column:
                continue

            # Manejar status especial (puede ser nombre o UUID)
            if field == "status" and value is not None:
                # Intentar buscar por nombre si no parece UUID
                try:
                    # Si es UUID válido, usarlo directamente
                    import uuid
                    uuid.UUID(value)
                    update_data[column] = value
                except ValueError:
                    # Buscar por nombre
                    status_response = supabase.table("tasks_status").select("task_status_id").ilike("task_status", value).execute()
                    if not status_response.data:
                        raise HTTPException(status_code=400, detail=f"Invalid status: '{value}'")
                    update_data[column] = status_response.data[0]["task_status_id"]
            else:
                update_data[column] = value

        if not update_data:
            raise HTTPException(status_code=400, detail="No valid fields to update")

        # Actualizar tarea
        response = supabase.table("tasks").update(update_data).eq("task_id", task_id).execute()

        if not response.data:
            raise HTTPException(status_code=404, detail="Task not found after update")

        return {
            "message": "Task updated successfully",
            "task": response.data[0],
        }

    except HTTPException:
        raise
    except Exception as e:
        print(f"[PIPELINE] ERROR in PATCH /pipeline/tasks/{task_id}: {repr(e)}")
        print(traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"DB error: {e}") from e


@router.delete("/tasks/{task_id}")
def delete_task(task_id: str) -> Dict[str, Any]:
    """Elimina una tarea del pipeline."""
    try:
        # Verificar que existe
        existing = supabase.table("tasks").select("task_id").eq("task_id", task_id).execute()

        if not existing.data:
            raise HTTPException(status_code=404, detail="Task not found")

        # Eliminar
        supabase.table("tasks").delete().eq("task_id", task_id).execute()

        return {"message": "Task deleted successfully"}

    except HTTPException:
        raise
    except Exception as e:
        print(f"[PIPELINE] ERROR in DELETE /pipeline/tasks/{task_id}: {repr(e)}")
        print(traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"DB error: {e}") from e
