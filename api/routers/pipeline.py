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
    company: Optional[str] = None  # UUID de la empresa
    project: Optional[str] = None  # UUID del proyecto
    owner: Optional[str] = None  # UUID del owner
    collaborator: Optional[str] = None  # UUID del colaborador
    type: Optional[str] = None  # UUID del tipo de tarea
    department: Optional[str] = None  # UUID del departamento
    due_date: Optional[str] = None  # Fecha YYYY-MM-DD
    deadline: Optional[str] = None  # Fecha YYYY-MM-DD
    status: str = "not started"  # Nombre del status

    @field_validator("task_description")
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
    # Support both single value (backward compat) and array for collaborators
    collaborator: Optional[str] = None  # Single collaborator (legacy)
    collaborators: Optional[List[str]] = None  # Multiple collaborators (array of UUIDs)
    # Support both single value (backward compat) and array for managers
    manager: Optional[str] = None  # Single manager (legacy)
    managers: Optional[List[str]] = None  # Multiple managers (array of UUIDs)
    due_date: Optional[str] = None
    start_date: Optional[str] = None
    deadline: Optional[str] = None
    time_start: Optional[str] = None
    time_finish: Optional[str] = None
    status: Optional[str] = None
    priority: Optional[str] = None
    estimated_hours: Optional[float] = None  # Estimated duration in hours
    docs_link: Optional[str] = None  # URL to documentation
    result_link: Optional[str] = None  # URL to result/deliverable


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

        # Users (para owner, collaborator, manager) - incluye avatar_color y user_photo para avatares
        users_response = supabase.table("users").select("user_id, user_name, avatar_color, user_photo").execute()
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

            # Handle collaborators: prefer new array column, fallback to legacy single
            collaborators_ids = task.get("collaborators_ids") or []
            if not collaborators_ids:
                # Fallback to legacy single collaborator
                legacy_collab = task.get("Colaborators_id")
                if legacy_collab:
                    collaborators_ids = [legacy_collab]

            # Handle managers: prefer new array column, fallback to legacy single
            managers_ids = task.get("managers_ids") or []
            if not managers_ids:
                # Fallback to legacy single manager
                legacy_manager = task.get("manager")
                if legacy_manager:
                    managers_ids = [legacy_manager]

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
                # Objetos anidados para el frontend (incluyen avatar_color y photo para avatares)
                "owner": {
                    "id": owner_id,
                    "name": owner_data["user_name"] if owner_data else None,
                    "avatar_color": owner_data.get("avatar_color") if owner_data else None,
                    "photo": owner_data.get("user_photo") if owner_data else None,
                } if owner_id else None,
                # Build collaborators array from all IDs
                "collaborators": [
                    {
                        "id": cid,
                        "name": users_map.get(cid, {}).get("user_name") if users_map.get(cid) else None,
                        "avatar_color": users_map.get(cid, {}).get("avatar_color") if users_map.get(cid) else None,
                        "photo": users_map.get(cid, {}).get("user_photo") if users_map.get(cid) else None,
                    }
                    for cid in collaborators_ids if cid
                ],
                # Build managers array from all IDs
                "managers": [
                    {
                        "id": mid,
                        "name": users_map.get(mid, {}).get("user_name") if users_map.get(mid) else None,
                        "avatar_color": users_map.get(mid, {}).get("avatar_color") if users_map.get(mid) else None,
                        "photo": users_map.get(mid, {}).get("user_photo") if users_map.get(mid) else None,
                    }
                    for mid in managers_ids if mid
                ],
                # Legacy single manager (for backward compatibility)
                "manager": {
                    "id": managers_ids[0] if managers_ids else None,
                    "name": users_map.get(managers_ids[0], {}).get("user_name") if managers_ids and users_map.get(managers_ids[0]) else None,
                    "avatar_color": users_map.get(managers_ids[0], {}).get("avatar_color") if managers_ids and users_map.get(managers_ids[0]) else None,
                    "photo": users_map.get(managers_ids[0], {}).get("user_photo") if managers_ids and users_map.get(managers_ids[0]) else None,
                } if managers_ids else None,
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

        # Preparar datos para insertar (solo campos con valor)
        task_data = {"task_description": payload.task_description, "task_status": status_id}
        optional_mappings = {
            "company_management": payload.company,
            "project_id": payload.project,
            "Owner_id": payload.owner,
            "Colaborators_id": payload.collaborator,
            "task_type": payload.type,
            "task_department": payload.department,
            "due_date": payload.due_date,
            "deadline": payload.deadline,
        }
        for col, val in optional_mappings.items():
            if val is not None:
                task_data[col] = val

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
    "collaborator": "Colaborators_id",  # Legacy single value
    "collaborators": "collaborators_ids",  # New array column
    "manager": "manager",  # Legacy single value
    "managers": "managers_ids",  # New array column
    "due_date": "due_date",
    "start_date": "start_date",
    "deadline": "deadline",
    "time_start": "time_start",
    "time_finish": "time_finish",
    "status": "task_status",
    "priority": "task_priority",
    "estimated_hours": "estimated_hours",
    "docs_link": "docs_link",
    "result_link": "result_link",
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
                    import uuid
                    uuid.UUID(value)
                    update_data[column] = value
                except ValueError:
                    status_response = supabase.table("tasks_status").select("task_status_id").ilike("task_status", value).execute()
                    if not status_response.data:
                        raise HTTPException(status_code=400, detail=f"Invalid status: '{value}'")
                    update_data[column] = status_response.data[0]["task_status_id"]
            # Manejar priority (puede ser nombre o UUID)
            elif field == "priority" and value is not None:
                try:
                    import uuid
                    uuid.UUID(value)
                    update_data[column] = value
                except ValueError:
                    priority_response = supabase.table("tasks_priority").select("priority_id").ilike("priority", value).execute()
                    if not priority_response.data:
                        raise HTTPException(status_code=400, detail=f"Invalid priority: '{value}'")
                    update_data[column] = priority_response.data[0]["priority_id"]
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


# ====== MY TASKS ENDPOINT (for Dashboard) ======

@router.get("/tasks/my-tasks/{user_id}")
def get_my_tasks(user_id: str) -> Dict[str, Any]:
    """
    Devuelve las tareas asignadas a un usuario para mostrar en su Dashboard.

    Incluye tareas donde el usuario es:
    - Owner (Owner_id)
    - Collaborator (collaborators_ids array o Colaborators_id legacy)
    - Manager (managers_ids array o manager legacy)

    Solo devuelve tareas que NO estan en status "Done".
    Incluye informacion del proyecto y prioridad.
    """
    print(f"[PIPELINE] GET /pipeline/tasks/my-tasks/{user_id}")

    try:
        # 1. Obtener el status_id de "Done" para excluirlo
        done_status_response = supabase.table("tasks_status").select(
            "task_status_id"
        ).ilike("task_status", "done").execute()

        done_status_id = None
        if done_status_response.data:
            done_status_id = done_status_response.data[0]["task_status_id"]

        # 2. Obtener tareas del usuario desde multiples roles
        # Supabase no soporta OR queries directamente, hacemos queries separadas

        all_tasks = []
        seen_task_ids = set()

        # 2a. Tareas como Owner
        owner_query = supabase.table("tasks").select("*").eq("Owner_id", user_id)
        if done_status_id:
            owner_query = owner_query.neq("task_status", done_status_id)
        owner_response = owner_query.execute()
        for task in (owner_response.data or []):
            task_id = task.get("task_id")
            if task_id and task_id not in seen_task_ids:
                task["_role"] = "owner"
                all_tasks.append(task)
                seen_task_ids.add(task_id)

        # 2b. Tareas como Collaborator (array field)
        collab_query = supabase.table("tasks").select("*").contains("collaborators_ids", [user_id])
        if done_status_id:
            collab_query = collab_query.neq("task_status", done_status_id)
        collab_response = collab_query.execute()
        for task in (collab_response.data or []):
            task_id = task.get("task_id")
            if task_id and task_id not in seen_task_ids:
                task["_role"] = "collaborator"
                all_tasks.append(task)
                seen_task_ids.add(task_id)

        # 2c. Tareas como Collaborator (legacy single field)
        collab_legacy_query = supabase.table("tasks").select("*").eq("Colaborators_id", user_id)
        if done_status_id:
            collab_legacy_query = collab_legacy_query.neq("task_status", done_status_id)
        collab_legacy_response = collab_legacy_query.execute()
        for task in (collab_legacy_response.data or []):
            task_id = task.get("task_id")
            if task_id and task_id not in seen_task_ids:
                task["_role"] = "collaborator"
                all_tasks.append(task)
                seen_task_ids.add(task_id)

        # 2d. Tareas como Manager (array field)
        manager_query = supabase.table("tasks").select("*").contains("managers_ids", [user_id])
        if done_status_id:
            manager_query = manager_query.neq("task_status", done_status_id)
        manager_response = manager_query.execute()
        for task in (manager_response.data or []):
            task_id = task.get("task_id")
            if task_id and task_id not in seen_task_ids:
                task["_role"] = "manager"
                all_tasks.append(task)
                seen_task_ids.add(task_id)

        # 2e. Tareas como Manager (legacy single field)
        manager_legacy_query = supabase.table("tasks").select("*").eq("manager", user_id)
        if done_status_id:
            manager_legacy_query = manager_legacy_query.neq("task_status", done_status_id)
        manager_legacy_response = manager_legacy_query.execute()
        for task in (manager_legacy_response.data or []):
            task_id = task.get("task_id")
            if task_id and task_id not in seen_task_ids:
                task["_role"] = "manager"
                all_tasks.append(task)
                seen_task_ids.add(task_id)

        # Ordenar por fecha de creacion (mas recientes primero)
        all_tasks.sort(key=lambda t: t.get("created_at") or "", reverse=True)
        tasks = all_tasks

        print(f"[PIPELINE] Found {len(tasks)} tasks for user {user_id} (owner + collaborator + manager)")

        if not tasks:
            return {"tasks": []}

        # 3. Obtener datos relacionados para enriquecer las tareas
        # Projects
        project_ids = list(set(t.get("project_id") for t in tasks if t.get("project_id")))
        projects_map = {}
        if project_ids:
            projects_response = supabase.table("projects").select(
                "project_id, project_name"
            ).in_("project_id", project_ids).execute()
            projects_map = {p["project_id"]: p for p in (projects_response.data or [])}

        # Priorities
        priority_ids = list(set(t.get("task_priority") for t in tasks if t.get("task_priority")))
        priorities_map = {}
        if priority_ids:
            priorities_response = supabase.table("tasks_priority").select(
                "priority_id, priority"
            ).in_("priority_id", priority_ids).execute()
            priorities_map = {p["priority_id"]: p for p in (priorities_response.data or [])}

        # Statuses
        status_ids = list(set(t.get("task_status") for t in tasks if t.get("task_status")))
        statuses_map = {}
        if status_ids:
            statuses_response = supabase.table("tasks_status").select(
                "task_status_id, task_status"
            ).in_("task_status_id", status_ids).execute()
            statuses_map = {s["task_status_id"]: s for s in (statuses_response.data or [])}

        # 4. Enriquecer las tareas
        enriched_tasks = []
        for task in tasks:
            project_id = task.get("project_id")
            priority_id = task.get("task_priority")
            status_id = task.get("task_status")

            project_info = projects_map.get(project_id, {})
            priority_info = priorities_map.get(priority_id, {})
            status_info = statuses_map.get(status_id, {})

            enriched_tasks.append({
                "task_id": task.get("task_id"),
                "task_description": task.get("task_description"),
                "project_id": project_id,
                "project_name": project_info.get("project_name"),
                "priority_id": priority_id,
                "priority_name": priority_info.get("priority"),
                "status_id": status_id,
                "status_name": status_info.get("task_status"),
                "due_date": task.get("due_date"),
                "deadline": task.get("deadline"),
                "time_start": task.get("time_start"),
                "time_finish": task.get("time_finish"),
                "task_notes": task.get("task_notes"),
                "created_at": task.get("created_at"),
                "role": task.get("_role", "owner"),  # owner, collaborator, or manager
            })

        return {"tasks": enriched_tasks}

    except Exception as e:
        print(f"[PIPELINE] ERROR in GET /pipeline/tasks/my-tasks/{user_id}: {repr(e)}")
        print(traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"DB error: {e}") from e


@router.post("/tasks/{task_id}/start")
def start_task(task_id: str) -> Dict[str, Any]:
    """
    Inicia una tarea: cambia el status a "Working on It" y registra time_start.

    Returns:
        - task: La tarea actualizada
        - status_changed: True si el status cambió
    """
    print(f"[PIPELINE] POST /pipeline/tasks/{task_id}/start")

    try:
        # 1. Verificar que la tarea existe
        existing = supabase.table("tasks").select("task_id, task_status, time_start").eq(
            "task_id", task_id
        ).execute()

        if not existing.data:
            raise HTTPException(status_code=404, detail="Task not found")

        task = existing.data[0]

        # 2. Obtener el status_id de "Working on It"
        working_status_response = supabase.table("tasks_status").select(
            "task_status_id"
        ).ilike("task_status", "working on it").execute()

        if not working_status_response.data:
            raise HTTPException(status_code=500, detail="Status 'Working on It' not found")

        working_status_id = working_status_response.data[0]["task_status_id"]

        # 3. Actualizar la tarea
        from datetime import datetime
        now = datetime.utcnow().isoformat()

        old_status = task.get("task_status")

        update_data = {
            "task_status": working_status_id,
            "time_start": now,
            "workflow_state": "active",
        }

        # Solo actualizar start_date si no tiene uno
        if not task.get("start_date"):
            update_data["start_date"] = datetime.utcnow().date().isoformat()

        response = supabase.table("tasks").update(update_data).eq(
            "task_id", task_id
        ).execute()

        if not response.data:
            raise HTTPException(status_code=500, detail="Failed to update task")

        updated_task = response.data[0]

        # Log the workflow event
        try:
            _log_workflow_event(
                task_id=task_id,
                event_type="started",
                old_status=old_status,
                new_status=working_status_id,
                metadata={"time_start": now}
            )
        except Exception as log_error:
            print(f"[PIPELINE] Warning: Could not log start event: {log_error}")

        return {
            "success": True,
            "task": updated_task,
            "status_changed": old_status != working_status_id,
            "new_status": "Working on It"
        }

    except HTTPException:
        raise
    except Exception as e:
        print(f"[PIPELINE] ERROR in POST /pipeline/tasks/{task_id}/start: {repr(e)}")
        print(traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"DB error: {e}") from e


class SendToReviewRequest(BaseModel):
    notes: Optional[str] = None  # Optional notes from the user
    result_link: Optional[str] = None  # Link to deliverable/result
    attachments: Optional[List[str]] = None  # Additional file links
    performed_by: Optional[str] = None  # UUID of owner submitting


@router.post("/tasks/{task_id}/send-to-review")
def send_task_to_review(task_id: str, payload: SendToReviewRequest) -> Dict[str, Any]:
    """
    Envía una tarea a revisión:
    - Cambia el status a "Awaiting Approval"
    - Registra time_finish
    - Actualiza las notas si se proporcionan
    - Crea una tarea automática para los autorizadores

    Returns:
        - task: La tarea actualizada
        - reviewer_task_created: True si se creó tarea para el autorizador
    """
    print(f"[PIPELINE] POST /pipeline/tasks/{task_id}/send-to-review")

    try:
        from datetime import datetime

        # 1. Obtener la tarea actual con todos sus datos
        existing = supabase.table("tasks").select("*").eq(
            "task_id", task_id
        ).execute()

        if not existing.data:
            raise HTTPException(status_code=404, detail="Task not found")

        task = existing.data[0]

        # 2. Obtener el status_id de "Awaiting Approval"
        approval_status_response = supabase.table("tasks_status").select(
            "task_status_id"
        ).ilike("task_status", "awaiting approval").execute()

        if not approval_status_response.data:
            raise HTTPException(status_code=500, detail="Status 'Awaiting Approval' not found")

        approval_status_id = approval_status_response.data[0]["task_status_id"]

        old_status = task.get("task_status")

        # 3. Actualizar la tarea original
        now = datetime.utcnow().isoformat()

        update_data = {
            "task_status": approval_status_id,
            "time_finish": now,
            "workflow_state": "in_review",
        }

        # Agregar result_link si se proporciono
        if payload.result_link:
            update_data["result_link"] = payload.result_link

        # Agregar notas si se proporcionaron
        if payload.notes:
            existing_notes = task.get("task_notes") or ""
            if existing_notes:
                update_data["task_notes"] = f"{existing_notes}\n\n[Submission Notes] {payload.notes}"
            else:
                update_data["task_notes"] = f"[Submission Notes] {payload.notes}"

        response = supabase.table("tasks").update(update_data).eq(
            "task_id", task_id
        ).execute()

        if not response.data:
            raise HTTPException(status_code=500, detail="Failed to update task")

        updated_task = response.data[0]

        # 4. Crear tarea para el autorizador
        reviewer_task_created = False
        reviewer_task_id = None

        try:
            reviewer_task_id = _create_reviewer_task(task, payload.notes, task_id)
            if reviewer_task_id:
                reviewer_task_created = True
                # Link the review task to original task
                supabase.table("tasks").update({
                    "review_task_id": reviewer_task_id
                }).eq("task_id", task_id).execute()
        except Exception as e:
            print(f"[PIPELINE] Warning: Could not create reviewer task: {e}")

        # 5. Calcular tiempo trabajado
        time_start = task.get("time_start")
        elapsed_time = None
        elapsed_seconds = None
        if time_start:
            try:
                start_dt = datetime.fromisoformat(time_start.replace("Z", "+00:00"))
                end_dt = datetime.fromisoformat(now.replace("Z", "+00:00"))
                diff = end_dt - start_dt
                elapsed_seconds = diff.total_seconds()
                hours = elapsed_seconds / 3600
                elapsed_time = f"{hours:.2f} hours"
            except:
                pass

        # 6. Log the workflow event
        try:
            _log_workflow_event(
                task_id=task_id,
                event_type="submitted_for_review",
                performed_by=payload.performed_by,
                old_status=old_status,
                new_status=approval_status_id,
                related_task_id=reviewer_task_id,
                notes=payload.notes,
                attachments=payload.attachments,
                metadata={
                    "time_finish": now,
                    "elapsed_seconds": elapsed_seconds,
                    "result_link": payload.result_link
                }
            )
        except Exception as log_error:
            print(f"[PIPELINE] Warning: Could not log review submission: {log_error}")

        return {
            "success": True,
            "task": updated_task,
            "new_status": "Awaiting Approval",
            "reviewer_task_created": reviewer_task_created,
            "reviewer_task_id": reviewer_task_id,
            "elapsed_time": elapsed_time
        }

    except HTTPException:
        raise
    except Exception as e:
        print(f"[PIPELINE] ERROR in POST /pipeline/tasks/{task_id}/send-to-review: {repr(e)}")
        print(traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"DB error: {e}") from e


def _create_reviewer_task(original_task: dict, submission_notes: str, original_task_id: str = None) -> Optional[str]:
    """
    Crea una tarea para el autorizador basado en el proyecto de la tarea original.

    El autorizador se determina por:
    1. Managers asignados a la tarea (managers_ids o manager)
    2. Manager del proyecto
    3. Si no hay manager, busca usuarios con rol CEO/COO

    Args:
        original_task: Task data dictionary
        submission_notes: Notes from the owner
        original_task_id: UUID of the original task (for parent_task_id link)

    Returns:
        task_id del task creado, o None si no se pudo crear
    """
    project_id = original_task.get("project_id")
    task_description = original_task.get("task_description", "Task")
    owner_id = original_task.get("Owner_id")

    # Obtener información del owner
    owner_name = "Unknown"
    if owner_id:
        owner_response = supabase.table("users").select("user_name").eq(
            "user_id", owner_id
        ).execute()
        if owner_response.data:
            owner_name = owner_response.data[0].get("user_name", "Unknown")

    # Determinar el reviewer (autorizador)
    reviewer_id = None

    # Opcion 1: Managers asignados a la tarea
    task_managers_ids = original_task.get("managers_ids")
    task_manager = original_task.get("manager")
    if task_managers_ids and len(task_managers_ids) > 0:
        reviewer_id = task_managers_ids[0]
    elif task_manager:
        reviewer_id = task_manager

    # Opcion 2: Manager del proyecto
    if not reviewer_id and project_id:
        project_response = supabase.table("projects").select(
            "project_name, project_manager"
        ).eq("project_id", project_id).execute()

        if project_response.data:
            project_data = project_response.data[0]
            reviewer_id = project_data.get("project_manager")

    # Opcion 3: Si no hay manager, buscar CEO/COO
    if not reviewer_id:
        # Buscar roles de CEO/COO
        roles_response = supabase.table("roles").select("role_id").or_(
            "role_name.ilike.%CEO%,role_name.ilike.%COO%"
        ).execute()

        if roles_response.data:
            role_ids = [r["role_id"] for r in roles_response.data]
            # Buscar un usuario con ese rol
            users_response = supabase.table("users").select("user_id").in_(
                "role_id", role_ids
            ).limit(1).execute()

            if users_response.data:
                reviewer_id = users_response.data[0]["user_id"]

    if not reviewer_id:
        print("[PIPELINE] No reviewer found for task")
        return None

    # Obtener el status "Not Started"
    status_response = supabase.table("tasks_status").select(
        "task_status_id"
    ).ilike("task_status", "not started").execute()

    not_started_id = None
    if status_response.data:
        not_started_id = status_response.data[0]["task_status_id"]

    # Crear la tarea de revision
    result_link = original_task.get("result_link")
    docs_link = original_task.get("docs_link")

    review_task_data = {
        "task_description": f"Review: {task_description} (submitted by {owner_name})",
        "project_id": project_id,
        "company": original_task.get("company"),
        "Owner_id": reviewer_id,
        "task_status": not_started_id,
        "task_department": original_task.get("task_department"),
        "parent_task_id": original_task_id or original_task.get("task_id"),
        "workflow_state": "active",
        "task_notes": f"[AUTO-REVIEW] Task pending approval.\n\nOriginal task: {task_description}\nSubmitted by: {owner_name}\n{f'Result: {result_link}' if result_link else ''}\n{f'Docs: {docs_link}' if docs_link else ''}\n\n{f'Notes: {submission_notes}' if submission_notes else ''}",
        "result_link": result_link,
        "docs_link": docs_link,
    }

    response = supabase.table("tasks").insert(review_task_data).execute()

    if response.data:
        new_task_id = response.data[0].get("task_id")
        print(f"[PIPELINE] Created reviewer task: {new_task_id}")
        return new_task_id

    return None


# ====== AUTOMATIONS ENDPOINTS ======

class AutomationsRunRequest(BaseModel):
    automations: List[str]  # List of automation IDs to run


# Automation marker prefix - tasks created by automations will have this in task_notes
AUTOMATION_MARKER = "[AUTOMATED]"


@router.post("/automations/run")
def run_automations(payload: AutomationsRunRequest) -> Dict[str, Any]:
    """
    Ejecuta las automatizaciones seleccionadas y crea/actualiza tareas.

    Automatizaciones disponibles:
    - pending_expenses_auth: Crea tareas para proyectos con gastos pendientes de autorización
    - pending_invoices: Crea tareas para facturas pendientes por enviar
    - overdue_tasks: Crea alertas para tareas vencidas
    """
    print(f"[AUTOMATIONS] Running automations: {payload.automations}")

    tasks_created = 0
    tasks_updated = 0
    errors = []

    try:
        for automation_id in payload.automations:
            if automation_id == "pending_expenses_auth":
                created, updated = _run_pending_expenses_automation()
                tasks_created += created
                tasks_updated += updated
            elif automation_id == "pending_expenses_categorize":
                created, updated = _run_pending_expenses_categorize_automation()
                tasks_created += created
                tasks_updated += updated
            elif automation_id == "pending_health_check":
                created, updated = _run_pending_health_check_automation()
                tasks_created += created
                tasks_updated += updated
            elif automation_id == "pending_invoices":
                # TODO: Implementar logica para facturas pendientes
                print(f"[AUTOMATIONS] pending_invoices: Not implemented yet")
            elif automation_id == "overdue_tasks":
                # TODO: Implementar logica para tareas vencidas
                print(f"[AUTOMATIONS] overdue_tasks: Not implemented yet")
            else:
                errors.append(f"Unknown automation: {automation_id}")

        return {
            "success": True,
            "tasks_created": tasks_created,
            "tasks_updated": tasks_updated,
            "errors": errors if errors else None
        }

    except Exception as e:
        print(f"[AUTOMATIONS] ERROR: {repr(e)}")
        print(traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"Automation error: {e}") from e


# =============================================================================
# @process: COGS_Authorization
# @process_name: COGS Authorization Workflow
# @process_category: bookkeeping
# @process_trigger: scheduled
# @process_description: Automated workflow that creates tasks for authorizing pending COGS expenses by project
# @process_owner: Accounting Manager
#
# @step: 1
# @step_name: Query Pending Expenses
# @step_type: action
# @step_description: Fetch all expenses with auth_status=null or false from expenses_manual_COGS
# @step_connects_to: 2
#
# @step: 2
# @step_name: Group by Project
# @step_type: action
# @step_description: Aggregate expenses count and total amount per project
# @step_connects_to: 3
#
# @step: 3
# @step_name: Check Existing Tasks
# @step_type: condition
# @step_description: Verify if automation task already exists for each project
# @step_connects_to: 4, 5
#
# @step: 4
# @step_name: Create New Task
# @step_type: action
# @step_description: Create task in Bookkeeping department for authorization
# @step_connects_to: 6
#
# @step: 5
# @step_name: Update Existing Task
# @step_type: action
# @step_description: Update task notes with current pending count and amount
# @step_connects_to: 6
#
# @step: 6
# @step_name: Notify Manager
# @step_type: notification
# @step_description: Task appears in manager's workflow for review
# =============================================================================
def _run_pending_expenses_automation() -> tuple:
    """
    Automation: Pending Expenses Authorization

    Crea una tarea por cada proyecto que tenga gastos pendientes de autorizacion.
    Si ya existe una tarea automatizada para ese proyecto, la actualiza.

    La tarea se crea con:
    - task_department: Bookkeeping (para que aparezca en el flujo de autorizacion)
    - manager: Usuario con rol Accounting Manager o CEO/COO (quien autoriza)
    - Owner_id: NULL (coordinacion lo asigna despues)
    - task_status: Not Started

    Returns:
        tuple: (tasks_created, tasks_updated)
    """
    print("[AUTOMATIONS] Running pending_expenses_auth...")

    tasks_created = 0
    tasks_updated = 0
    automation_type = "pending_expenses_auth"

    try:
        # 1. Obtener gastos pendientes de autorizacion agrupados por proyecto
        expenses_response = supabase.table("expenses_manual_COGS").select(
            "expense_id, project, Amount"
        ).or_("auth_status.is.null,auth_status.eq.false").execute()

        expenses = expenses_response.data or []
        print(f"[AUTOMATIONS] Found {len(expenses)} pending expenses")

        if not expenses:
            return (0, 0)

        # Agrupar por proyecto
        by_project: Dict[str, Dict] = {}
        for exp in expenses:
            project_id = exp.get("project")
            if not project_id:
                continue

            if project_id not in by_project:
                by_project[project_id] = {"count": 0, "total": 0}

            by_project[project_id]["count"] += 1
            by_project[project_id]["total"] += float(exp.get("Amount") or 0)

        print(f"[AUTOMATIONS] Projects with pending expenses: {len(by_project)}")

        if not by_project:
            return (0, 0)

        # 2. Obtener nombres de proyectos
        project_ids = list(by_project.keys())
        projects_response = supabase.table("projects").select(
            "project_id, project_name, project_manager"
        ).in_("project_id", project_ids).execute()

        projects_map = {p["project_id"]: p for p in (projects_response.data or [])}

        # 3. Obtener el status_id de "not started"
        status_response = supabase.table("tasks_status").select(
            "task_status_id"
        ).ilike("task_status", "not started").execute()

        not_started_status_id = None
        if status_response.data:
            not_started_status_id = status_response.data[0]["task_status_id"]

        # 4. Obtener el department_id de "bookkeeping"
        bookkeeping_dept_id = None
        dept_response = supabase.table("task_departments").select(
            "department_id"
        ).ilike("department_name", "%bookkeeping%").execute()

        if dept_response.data:
            bookkeeping_dept_id = dept_response.data[0]["department_id"]
            print(f"[AUTOMATIONS] Found bookkeeping department: {bookkeeping_dept_id}")
        else:
            print("[AUTOMATIONS] WARNING: Bookkeeping department not found")

        # 5. Encontrar un manager para autorizacion (Accounting Manager o CEO/COO)
        expense_manager_id = None

        # Primero buscar Accounting Manager
        roles_response = supabase.table("rols").select("rol_id").ilike(
            "rol_name", "%accounting manager%"
        ).execute()

        if roles_response.data:
            role_ids = [r["rol_id"] for r in roles_response.data]
            users_response = supabase.table("users").select("user_id").in_(
                "user_rol", role_ids
            ).limit(1).execute()

            if users_response.data:
                expense_manager_id = users_response.data[0]["user_id"]
                print(f"[AUTOMATIONS] Found Accounting Manager: {expense_manager_id}")

        # Si no hay Accounting Manager, buscar CEO/COO
        if not expense_manager_id:
            roles_response = supabase.table("rols").select("rol_id").or_(
                "rol_name.ilike.%CEO%,rol_name.ilike.%COO%"
            ).execute()

            if roles_response.data:
                role_ids = [r["rol_id"] for r in roles_response.data]
                users_response = supabase.table("users").select("user_id").in_(
                    "user_rol", role_ids
                ).limit(1).execute()

                if users_response.data:
                    expense_manager_id = users_response.data[0]["user_id"]
                    print(f"[AUTOMATIONS] Found CEO/COO as manager: {expense_manager_id}")

        if not expense_manager_id:
            print("[AUTOMATIONS] WARNING: No manager found for expense authorization")

        # 6. Obtener configuracion de la automatizacion
        settings_response = supabase.table("automation_settings").select(
            "*"
        ).eq("automation_type", automation_type).execute()

        settings = settings_response.data[0] if settings_response.data else {}
        default_owner_id = settings.get("default_owner_id")
        # Use settings manager if found, otherwise fallback to expense_manager_id
        settings_manager_id = settings.get("default_manager_id") or expense_manager_id
        settings_department_id = settings.get("default_department_id") or bookkeeping_dept_id

        # 7. Buscar tareas automatizadas existentes para estos proyectos
        existing_tasks_response = supabase.table("tasks").select(
            "task_id, project_id, task_notes"
        ).eq("automation_type", automation_type).eq("is_automated", True).execute()

        existing_tasks_map = {
            t["project_id"]: t for t in (existing_tasks_response.data or [])
        }

        # 8. Crear o actualizar tareas
        for project_id, data in by_project.items():
            project_info = projects_map.get(project_id, {})
            project_name = project_info.get("project_name", "Unknown Project")

            count = data["count"]
            total = data["total"]

            task_description = f"Gastos pendientes por autorizar en {project_name}"
            task_notes = f"{AUTOMATION_MARKER}:pending_expenses_auth | {count} gastos | ${total:,.2f} total"

            # Verificar si hay override para este proyecto
            override_response = supabase.table("automation_owner_overrides").select(
                "owner_id, manager_id"
            ).eq("automation_type", automation_type).eq("project_id", project_id).execute()

            owner_id = default_owner_id
            manager_id = settings_manager_id
            if override_response.data:
                override = override_response.data[0]
                owner_id = override.get("owner_id") or default_owner_id
                manager_id = override.get("manager_id") or settings_manager_id

            if project_id in existing_tasks_map:
                # Actualizar tarea existente
                existing_task = existing_tasks_map[project_id]
                supabase.table("tasks").update({
                    "task_description": task_description,
                    "task_notes": task_notes,
                    "automation_metadata": {"count": count, "total": total},
                }).eq("task_id", existing_task["task_id"]).execute()

                tasks_updated += 1
                print(f"[AUTOMATIONS] Updated task for project: {project_name}")
            else:
                # Crear nueva tarea
                new_task_data = {
                    "task_description": task_description,
                    "task_notes": task_notes,
                    "project_id": project_id,
                    "Owner_id": owner_id,  # Puede ser None si no hay default
                    "task_status": not_started_status_id,
                    "task_department": settings_department_id,
                    "manager": manager_id,
                    "automation_type": automation_type,
                    "is_automated": True,
                    "automation_metadata": {"count": count, "total": total},
                }

                supabase.table("tasks").insert(new_task_data).execute()
                tasks_created += 1
                print(f"[AUTOMATIONS] Created task for project: {project_name}")

        # 6. Opcional: Limpiar tareas automatizadas de proyectos que ya no tienen gastos pendientes
        # (esto evita que queden tareas obsoletas)
        for existing_project_id, existing_task in existing_tasks_map.items():
            if existing_project_id not in by_project:
                # El proyecto ya no tiene gastos pendientes, eliminar la tarea
                supabase.table("tasks").delete().eq(
                    "task_id", existing_task["task_id"]
                ).execute()
                print(f"[AUTOMATIONS] Removed obsolete task for project: {existing_project_id}")

        return (tasks_created, tasks_updated)

    except Exception as e:
        print(f"[AUTOMATIONS] ERROR in pending_expenses_auth: {repr(e)}")
        print(traceback.format_exc())
        raise


# =============================================================================
# @process: Expense_Categorization
# @process_name: Expense Categorization Workflow
# @process_category: bookkeeping
# @process_trigger: scheduled
# @process_description: Creates tasks for categorizing uncategorized expenses in projects
# @process_owner: Bookkeeper
#
# @step: 1
# @step_name: Query Uncategorized
# @step_type: action
# @step_description: Fetch expenses where expense_category is null or empty
# @step_connects_to: 2
#
# @step: 2
# @step_name: Group by Project
# @step_type: action
# @step_description: Aggregate uncategorized expenses count per project
# @step_connects_to: 3
#
# @step: 3
# @step_name: Check Task Exists
# @step_type: condition
# @step_description: Check if categorization task already exists for project
# @step_connects_to: 4, 5
#
# @step: 4
# @step_name: Create Task
# @step_type: action
# @step_description: Create new task for expense categorization
# @step_connects_to: 6
#
# @step: 5
# @step_name: Update Task
# @step_type: action
# @step_description: Update existing task with current count
# @step_connects_to: 6
#
# @step: 6
# @step_name: Assign to Bookkeeper
# @step_type: assignment
# @step_description: Task assigned to bookkeeper for category assignment
# =============================================================================
def _run_pending_expenses_categorize_automation() -> tuple:
    """
    Automation: Pending Expenses Categorization

    Crea una tarea por cada proyecto que tenga gastos sin categorizar.
    Los gastos sin categorizar son aquellos donde expense_category es NULL o vacio.

    Returns:
        tuple: (tasks_created, tasks_updated)
    """
    print("[AUTOMATIONS] Running pending_expenses_categorize...")

    tasks_created = 0
    tasks_updated = 0
    automation_type = "pending_expenses_categorize"

    try:
        # 1. Obtener gastos sin categorizar
        expenses_response = supabase.table("expenses_manual_COGS").select(
            "expense_id, project, Amount, expense_description"
        ).or_("expense_category.is.null,expense_category.eq.").execute()

        expenses = expenses_response.data or []
        print(f"[AUTOMATIONS] Found {len(expenses)} uncategorized expenses")

        if not expenses:
            return (0, 0)

        # Agrupar por proyecto
        by_project: Dict[str, Dict] = {}
        for exp in expenses:
            project_id = exp.get("project")
            if not project_id:
                continue

            if project_id not in by_project:
                by_project[project_id] = {"count": 0, "total": 0}

            by_project[project_id]["count"] += 1
            by_project[project_id]["total"] += float(exp.get("Amount") or 0)

        print(f"[AUTOMATIONS] Projects with uncategorized expenses: {len(by_project)}")

        if not by_project:
            return (0, 0)

        # 2. Obtener configuracion de la automatizacion
        settings_response = supabase.table("automation_settings").select(
            "*"
        ).eq("automation_type", automation_type).execute()

        settings = settings_response.data[0] if settings_response.data else {}
        default_owner_id = settings.get("default_owner_id")
        default_manager_id = settings.get("default_manager_id")
        default_department_id = settings.get("default_department_id")

        # 3. Obtener status "Not Started"
        status_response = supabase.table("tasks_status").select(
            "task_status_id"
        ).ilike("task_status", "not started").execute()

        not_started_status_id = None
        if status_response.data:
            not_started_status_id = status_response.data[0]["task_status_id"]

        # 4. Obtener nombres de proyectos
        project_ids = list(by_project.keys())
        projects_response = supabase.table("projects").select(
            "project_id, project_name"
        ).in_("project_id", project_ids).execute()

        projects_map = {p["project_id"]: p for p in (projects_response.data or [])}

        # 5. Buscar tareas automatizadas existentes
        existing_tasks_response = supabase.table("tasks").select(
            "task_id, project_id"
        ).eq("automation_type", automation_type).eq("is_automated", True).execute()

        existing_tasks_map = {
            t["project_id"]: t for t in (existing_tasks_response.data or [])
        }

        # 6. Crear o actualizar tareas
        for project_id, data in by_project.items():
            project_info = projects_map.get(project_id, {})
            project_name = project_info.get("project_name", "Unknown Project")

            count = data["count"]
            total = data["total"]

            task_description = f"Gastos pendientes por categorizar en {project_name}"
            task_notes = f"{AUTOMATION_MARKER}:pending_expenses_categorize | {count} gastos | ${total:,.2f} total"

            # Verificar si hay override para este proyecto
            override_response = supabase.table("automation_owner_overrides").select(
                "owner_id, manager_id"
            ).eq("automation_type", automation_type).eq("project_id", project_id).execute()

            owner_id = default_owner_id
            manager_id = default_manager_id
            if override_response.data:
                override = override_response.data[0]
                owner_id = override.get("owner_id") or default_owner_id
                manager_id = override.get("manager_id") or default_manager_id

            if project_id in existing_tasks_map:
                # Actualizar tarea existente
                existing_task = existing_tasks_map[project_id]
                supabase.table("tasks").update({
                    "task_description": task_description,
                    "task_notes": task_notes,
                    "automation_metadata": {"count": count, "total": total},
                }).eq("task_id", existing_task["task_id"]).execute()

                tasks_updated += 1
            else:
                # Crear nueva tarea
                new_task_data = {
                    "task_description": task_description,
                    "task_notes": task_notes,
                    "project_id": project_id,
                    "Owner_id": owner_id,
                    "manager": manager_id,
                    "task_department": default_department_id,
                    "task_status": not_started_status_id,
                    "automation_type": automation_type,
                    "is_automated": True,
                    "automation_metadata": {"count": count, "total": total},
                }

                supabase.table("tasks").insert(new_task_data).execute()
                tasks_created += 1

        # 7. Limpiar tareas obsoletas
        for existing_project_id, existing_task in existing_tasks_map.items():
            if existing_project_id not in by_project:
                supabase.table("tasks").delete().eq(
                    "task_id", existing_task["task_id"]
                ).execute()

        return (tasks_created, tasks_updated)

    except Exception as e:
        print(f"[AUTOMATIONS] ERROR in pending_expenses_categorize: {repr(e)}")
        print(traceback.format_exc())
        raise


# =============================================================================
# @process: Project_Health_Check
# @process_name: Project Health Check Workflow
# @process_category: coordination
# @process_trigger: scheduled
# @process_description: Automated health checks for active projects monitoring budget and expenses
# @process_owner: Project Manager
#
# @step: 1
# @step_name: Get Active Projects
# @step_type: action
# @step_description: Query all projects with is_active=true
# @step_connects_to: 2
#
# @step: 2
# @step_name: Calculate Expenses
# @step_type: action
# @step_description: Sum total expenses per project from COGS table
# @step_connects_to: 3
#
# @step: 3
# @step_name: Check Budget Status
# @step_type: condition
# @step_description: Evaluate if budget remaining is below 20% threshold
# @step_connects_to: 4, 5
#
# @step: 4
# @step_name: Create Alert Task
# @step_type: action
# @step_description: Create health check task for projects needing attention
# @step_connects_to: 6
#
# @step: 5
# @step_name: Skip Healthy
# @step_type: action
# @step_description: No action needed for healthy projects
#
# @step: 6
# @step_name: Notify PM
# @step_type: notification
# @step_description: Project manager notified of health check task
# =============================================================================
def _run_pending_health_check_automation() -> tuple:
    """
    Automation: Pending Health Check

    Crea tareas de health check para proyectos activos que no han tenido
    una revision de salud reciente. Verifica:
    - Proyectos activos sin tareas de health check en los ultimos 30 dias
    - Proyectos con presupuesto bajo (menos del 20% restante)
    - Proyectos con gastos excesivos

    Returns:
        tuple: (tasks_created, tasks_updated)
    """
    print("[AUTOMATIONS] Running pending_health_check...")

    tasks_created = 0
    tasks_updated = 0
    automation_type = "pending_health_check"

    try:
        from datetime import datetime, timedelta

        # 1. Obtener proyectos activos
        projects_response = supabase.table("projects").select(
            "project_id, project_name, budget, project_manager"
        ).eq("is_active", True).execute()

        projects = projects_response.data or []
        print(f"[AUTOMATIONS] Found {len(projects)} active projects")

        if not projects:
            return (0, 0)

        project_ids = [p["project_id"] for p in projects]
        projects_map = {p["project_id"]: p for p in projects}

        # 2. Calcular gastos totales por proyecto
        expenses_response = supabase.table("expenses_manual_COGS").select(
            "project, Amount"
        ).in_("project", project_ids).execute()

        expenses_by_project: Dict[str, float] = {}
        for exp in (expenses_response.data or []):
            pid = exp.get("project")
            if pid:
                expenses_by_project[pid] = expenses_by_project.get(pid, 0) + float(exp.get("Amount") or 0)

        # 3. Obtener configuracion de la automatizacion
        settings_response = supabase.table("automation_settings").select(
            "*"
        ).eq("automation_type", automation_type).execute()

        settings = settings_response.data[0] if settings_response.data else {}
        default_owner_id = settings.get("default_owner_id")
        default_manager_id = settings.get("default_manager_id")
        default_department_id = settings.get("default_department_id")

        # 4. Obtener status "Not Started"
        status_response = supabase.table("tasks_status").select(
            "task_status_id"
        ).ilike("task_status", "not started").execute()

        not_started_status_id = None
        if status_response.data:
            not_started_status_id = status_response.data[0]["task_status_id"]

        # 5. Verificar tareas de health check existentes
        existing_tasks_response = supabase.table("tasks").select(
            "task_id, project_id, created_at"
        ).eq("automation_type", automation_type).eq("is_automated", True).execute()

        # Mapear por proyecto y verificar si son recientes (ultimos 30 dias)
        thirty_days_ago = datetime.utcnow() - timedelta(days=30)
        recent_health_checks = set()
        existing_tasks_map = {}

        for task in (existing_tasks_response.data or []):
            project_id = task.get("project_id")
            existing_tasks_map[project_id] = task

            created_at = task.get("created_at")
            if created_at:
                try:
                    task_date = datetime.fromisoformat(created_at.replace("Z", "+00:00").replace("+00:00", ""))
                    if task_date > thirty_days_ago:
                        recent_health_checks.add(project_id)
                except:
                    pass

        # 6. Determinar proyectos que necesitan health check
        projects_needing_check: Dict[str, Dict] = {}

        for project in projects:
            project_id = project["project_id"]
            project_name = project["project_name"]
            budget = float(project.get("budget") or 0)
            total_spent = expenses_by_project.get(project_id, 0)

            # Saltar si ya tiene health check reciente
            if project_id in recent_health_checks:
                continue

            # Calcular porcentaje de presupuesto restante
            remaining_percent = 100
            if budget > 0:
                remaining_percent = ((budget - total_spent) / budget) * 100

            # Determinar razon del health check
            reasons = []
            if remaining_percent < 20:
                reasons.append(f"Budget bajo ({remaining_percent:.0f}% restante)")
            if total_spent > budget and budget > 0:
                reasons.append(f"Sobre presupuesto (${total_spent - budget:,.2f} excedido)")
            if project_id not in recent_health_checks:
                reasons.append("Sin revision reciente")

            if reasons:
                projects_needing_check[project_id] = {
                    "project_name": project_name,
                    "reasons": reasons,
                    "remaining_percent": remaining_percent,
                    "total_spent": total_spent,
                    "budget": budget,
                }

        print(f"[AUTOMATIONS] Projects needing health check: {len(projects_needing_check)}")

        # 7. Crear o actualizar tareas
        for project_id, data in projects_needing_check.items():
            project_name = data["project_name"]
            reasons = data["reasons"]

            task_description = f"Health Check requerido para {project_name}"
            task_notes = f"{AUTOMATION_MARKER}:pending_health_check | Razones: {', '.join(reasons)}"

            # Verificar override
            override_response = supabase.table("automation_owner_overrides").select(
                "owner_id, manager_id"
            ).eq("automation_type", automation_type).eq("project_id", project_id).execute()

            owner_id = default_owner_id
            manager_id = default_manager_id
            if override_response.data:
                override = override_response.data[0]
                owner_id = override.get("owner_id") or default_owner_id
                manager_id = override.get("manager_id") or default_manager_id

            if project_id in existing_tasks_map:
                # Actualizar tarea existente
                existing_task = existing_tasks_map[project_id]
                supabase.table("tasks").update({
                    "task_description": task_description,
                    "task_notes": task_notes,
                    "automation_metadata": {
                        "reasons": reasons,
                        "remaining_percent": data["remaining_percent"],
                        "total_spent": data["total_spent"],
                        "budget": data["budget"],
                    },
                }).eq("task_id", existing_task["task_id"]).execute()

                tasks_updated += 1
            else:
                # Crear nueva tarea
                new_task_data = {
                    "task_description": task_description,
                    "task_notes": task_notes,
                    "project_id": project_id,
                    "Owner_id": owner_id,
                    "manager": manager_id,
                    "task_department": default_department_id,
                    "task_status": not_started_status_id,
                    "automation_type": automation_type,
                    "is_automated": True,
                    "automation_metadata": {
                        "reasons": reasons,
                        "remaining_percent": data["remaining_percent"],
                        "total_spent": data["total_spent"],
                        "budget": data["budget"],
                    },
                }

                supabase.table("tasks").insert(new_task_data).execute()
                tasks_created += 1

        return (tasks_created, tasks_updated)

    except Exception as e:
        print(f"[AUTOMATIONS] ERROR in pending_health_check: {repr(e)}")
        print(traceback.format_exc())
        raise


# ====== MY WORK / WORKLOAD ENDPOINTS ======

class WorkloadSettings(BaseModel):
    """Settings for workload calculation."""
    hours_per_day: float = 8.0
    days_per_week: int = 6


@router.get("/my-work/{user_id}")
def get_my_work_data(user_id: str, hours_per_day: float = 8.0, days_per_week: int = 6) -> Dict[str, Any]:
    """
    Devuelve las tareas del usuario para la página My Work con cálculo de workload.

    Solo incluye tareas con status "Not Started" o "Working on It".
    Calcula:
    - Carga de trabajo total (horas asignadas)
    - Capacidad disponible basada en hours_per_day y days_per_week
    - Indicadores de sobrecarga/subcarga

    Args:
        user_id: UUID del usuario
        hours_per_day: Horas de trabajo por día (default 8)
        days_per_week: Días de trabajo por semana (default 6)
    """
    print(f"[MY-WORK] GET /pipeline/my-work/{user_id}")

    try:
        from datetime import datetime, timedelta

        # 1. Obtener status IDs para filtrar
        statuses_response = supabase.table("tasks_status").select(
            "task_status_id, task_status"
        ).execute()

        status_map = {s["task_status"].lower(): s["task_status_id"] for s in (statuses_response.data or [])}
        status_name_map = {s["task_status_id"]: s["task_status"] for s in (statuses_response.data or [])}

        not_started_id = status_map.get("not started")
        working_id = status_map.get("working on it")

        valid_status_ids = [s for s in [not_started_id, working_id] if s]

        if not valid_status_ids:
            return {"tasks": [], "workload": {}}

        # 2. Obtener tareas del usuario con status válidos
        query = supabase.table("tasks").select("*").eq("Owner_id", user_id)
        query = query.in_("task_status", valid_status_ids)
        tasks_response = query.order("deadline", desc=False).execute()
        tasks = tasks_response.data or []

        print(f"[MY-WORK] Found {len(tasks)} tasks for user")

        if not tasks:
            return {
                "tasks": [],
                "workload": {
                    "total_hours": 0,
                    "capacity_hours_week": hours_per_day * days_per_week,
                    "utilization_percent": 0,
                    "status": "underloaded",
                    "overdue_count": 0,
                    "due_soon_count": 0,
                }
            }

        # 3. Obtener datos relacionados
        project_ids = list(set(t.get("project_id") for t in tasks if t.get("project_id")))
        projects_map = {}
        if project_ids:
            projects_response = supabase.table("projects").select(
                "project_id, project_name"
            ).in_("project_id", project_ids).execute()
            projects_map = {p["project_id"]: p for p in (projects_response.data or [])}

        priority_ids = list(set(t.get("task_priority") for t in tasks if t.get("task_priority")))
        priorities_map = {}
        if priority_ids:
            priorities_response = supabase.table("tasks_priority").select(
                "priority_id, priority"
            ).in_("priority_id", priority_ids).execute()
            priorities_map = {p["priority_id"]: p for p in (priorities_response.data or [])}

        # Task types
        type_ids = list(set(t.get("task_type") for t in tasks if t.get("task_type")))
        types_map = {}
        if type_ids:
            types_response = supabase.table("task_types").select(
                "type_id, type_name"
            ).in_("type_id", type_ids).execute()
            types_map = {t["type_id"]: t for t in (types_response.data or [])}

        # 4. Calcular workload
        now = datetime.utcnow()
        today = now.date()
        week_from_now = today + timedelta(days=7)

        total_estimated_hours = 0
        overdue_count = 0
        due_soon_count = 0

        enriched_tasks = []
        for task in tasks:
            project_id = task.get("project_id")
            priority_id = task.get("task_priority")
            status_id = task.get("task_status")
            type_id = task.get("task_type")

            project_info = projects_map.get(project_id, {})
            priority_info = priorities_map.get(priority_id, {})
            type_info = types_map.get(type_id, {})

            # Check if task is automated (has [AUTOMATED] in notes or specific type)
            task_notes = task.get("task_notes") or ""
            type_name = type_info.get("type_name", "")
            is_automated = "[AUTOMATED]" in task_notes or type_name.lower() == "automated"

            # Duración estimada (default 2 horas si no está especificada)
            duration = task.get("estimated_hours") or 2.0
            total_estimated_hours += duration

            # Verificar fechas
            deadline_str = task.get("deadline") or task.get("due_date")
            is_overdue = False
            is_due_soon = False

            if deadline_str:
                try:
                    deadline_date = datetime.fromisoformat(deadline_str.replace("Z", "")).date()
                    if deadline_date < today:
                        is_overdue = True
                        overdue_count += 1
                    elif deadline_date <= week_from_now:
                        is_due_soon = True
                        due_soon_count += 1
                except:
                    pass

            enriched_tasks.append({
                "task_id": task.get("task_id"),
                "task_description": task.get("task_description"),
                "project_id": project_id,
                "project_name": project_info.get("project_name"),
                "priority_id": priority_id,
                "priority_name": priority_info.get("priority"),
                "status_id": status_id,
                "status_name": status_name_map.get(status_id),
                "type_id": type_id,
                "type_name": type_name,
                "is_automated": is_automated,
                "due_date": task.get("due_date"),
                "deadline": task.get("deadline"),
                "estimated_hours": duration,
                "time_start": task.get("time_start"),
                "is_overdue": is_overdue,
                "is_due_soon": is_due_soon,
                "created_at": task.get("created_at"),
                # Position data for canvas (stored or default)
                "canvas_x": task.get("canvas_x"),
                "canvas_y": task.get("canvas_y"),
            })

        # 5. Calcular métricas de workload
        capacity_hours_week = hours_per_day * days_per_week
        utilization_percent = (total_estimated_hours / capacity_hours_week * 100) if capacity_hours_week > 0 else 0

        # Determinar status de carga
        if utilization_percent > 120:
            workload_status = "critical"  # Sobrecargado severamente
        elif utilization_percent > 100:
            workload_status = "overloaded"  # Sobrecargado
        elif utilization_percent > 80:
            workload_status = "optimal"  # Carga óptima
        elif utilization_percent > 50:
            workload_status = "normal"  # Normal
        else:
            workload_status = "underloaded"  # Subcargado

        # 6. Generar lista de tipos únicos para filtros
        unique_types = {}
        automated_count = 0
        for t in enriched_tasks:
            type_id = t.get("type_id")
            type_name = t.get("type_name") or "Unknown"
            if type_id and type_id not in unique_types:
                unique_types[type_id] = type_name
            if t.get("is_automated"):
                automated_count += 1

        task_types = [{"id": k, "name": v} for k, v in unique_types.items()]
        task_types.sort(key=lambda x: x["name"])

        return {
            "tasks": enriched_tasks,
            "task_types": task_types,
            "workload": {
                "total_hours": round(total_estimated_hours, 1),
                "capacity_hours_week": capacity_hours_week,
                "utilization_percent": round(utilization_percent, 1),
                "status": workload_status,
                "overdue_count": overdue_count,
                "due_soon_count": due_soon_count,
                "automated_count": automated_count,
                "hours_per_day": hours_per_day,
                "days_per_week": days_per_week,
            }
        }

    except Exception as e:
        print(f"[MY-WORK] ERROR: {repr(e)}")
        print(traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"Error: {e}") from e


@router.get("/my-work/team-overview")
def get_team_workload_overview(hours_per_day: float = 8.0, days_per_week: int = 6) -> Dict[str, Any]:
    """
    Devuelve resumen de carga de trabajo de todo el equipo.
    Solo accesible por Coordination/Management roles.

    Returns:
        Lista de usuarios con su carga de trabajo actual.
    """
    print("[MY-WORK] GET /pipeline/my-work/team-overview")

    try:
        from datetime import datetime, timedelta

        # 1. Obtener todos los usuarios activos
        users_response = supabase.table("users").select(
            "user_id, user_name, avatar_color, user_photo"
        ).execute()
        users = users_response.data or []

        if not users:
            return {"team": []}

        # 2. Obtener status válidos
        statuses_response = supabase.table("tasks_status").select(
            "task_status_id, task_status"
        ).execute()
        status_map = {s["task_status"].lower(): s["task_status_id"] for s in (statuses_response.data or [])}

        not_started_id = status_map.get("not started")
        working_id = status_map.get("working on it")
        valid_status_ids = [s for s in [not_started_id, working_id] if s]

        if not valid_status_ids:
            return {"team": [{"user_id": u["user_id"], "user_name": u["user_name"], "tasks_count": 0, "total_hours": 0, "status": "underloaded"} for u in users]}

        # 3. Obtener todas las tareas activas
        tasks_response = supabase.table("tasks").select(
            "task_id, Owner_id, estimated_hours, deadline, due_date"
        ).in_("task_status", valid_status_ids).execute()
        tasks = tasks_response.data or []

        # 4. Agrupar por usuario
        now = datetime.utcnow()
        today = now.date()
        capacity_hours_week = hours_per_day * days_per_week

        user_workload: Dict[str, Dict] = {u["user_id"]: {
            "user_id": u["user_id"],
            "user_name": u["user_name"],
            "avatar_color": u.get("avatar_color"),
            "photo": u.get("user_photo"),
            "tasks_count": 0,
            "total_hours": 0,
            "overdue_count": 0,
        } for u in users}

        for task in tasks:
            owner_id = task.get("Owner_id")
            if owner_id not in user_workload:
                continue

            duration = task.get("estimated_hours") or 2.0
            user_workload[owner_id]["tasks_count"] += 1
            user_workload[owner_id]["total_hours"] += duration

            # Check overdue
            deadline_str = task.get("deadline") or task.get("due_date")
            if deadline_str:
                try:
                    deadline_date = datetime.fromisoformat(deadline_str.replace("Z", "")).date()
                    if deadline_date < today:
                        user_workload[owner_id]["overdue_count"] += 1
                except:
                    pass

        # 5. Calcular status para cada usuario
        team_data = []
        for user_id, data in user_workload.items():
            utilization = (data["total_hours"] / capacity_hours_week * 100) if capacity_hours_week > 0 else 0

            if utilization > 120:
                status = "critical"
            elif utilization > 100:
                status = "overloaded"
            elif utilization > 80:
                status = "optimal"
            elif utilization > 50:
                status = "normal"
            else:
                status = "underloaded"

            team_data.append({
                **data,
                "total_hours": round(data["total_hours"], 1),
                "utilization_percent": round(utilization, 1),
                "status": status,
            })

        # Ordenar por utilización (más cargados primero)
        team_data.sort(key=lambda x: x["utilization_percent"], reverse=True)

        return {
            "team": team_data,
            "settings": {
                "hours_per_day": hours_per_day,
                "days_per_week": days_per_week,
                "capacity_hours_week": capacity_hours_week,
            }
        }

    except Exception as e:
        print(f"[MY-WORK] ERROR in team-overview: {repr(e)}")
        print(traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"Error: {e}") from e


@router.get("/automations/status")
def get_automations_status() -> Dict[str, Any]:
    """
    Devuelve el estado actual de las automatizaciones.

    Returns:
        - pending_expenses_auth: Conteo de proyectos con gastos pendientes
        - pending_invoices: Conteo de facturas pendientes
        - overdue_tasks: Conteo de tareas vencidas
    """
    try:
        # Pending expenses count
        expenses_response = supabase.table("expenses_manual_COGS").select(
            "project", count="exact"
        ).or_("auth_status.is.null,auth_status.eq.false").execute()

        pending_expenses_count = expenses_response.count if expenses_response.count else 0

        # Get unique projects with pending expenses
        expenses_data = supabase.table("expenses_manual_COGS").select(
            "project"
        ).or_("auth_status.is.null,auth_status.eq.false").execute()

        unique_projects = set(
            e.get("project") for e in (expenses_data.data or []) if e.get("project")
        )

        return {
            "pending_expenses_auth": {
                "expenses_count": pending_expenses_count,
                "projects_count": len(unique_projects),
            },
            "pending_invoices": {
                "count": 0,  # TODO: Implementar
            },
            "overdue_tasks": {
                "count": 0,  # TODO: Implementar
            }
        }

    except Exception as e:
        print(f"[AUTOMATIONS] ERROR in GET /automations/status: {repr(e)}")
        print(traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"Error: {e}") from e


# ====== TASK ATTACHMENTS ======

TASK_ATTACHMENTS_BUCKET = "task-attachments"


def ensure_attachments_bucket():
    """Ensures the task-attachments bucket exists, creates if not."""
    try:
        supabase.storage.get_bucket(TASK_ATTACHMENTS_BUCKET)
        return True
    except Exception:
        try:
            supabase.storage.create_bucket(
                TASK_ATTACHMENTS_BUCKET,
                options={"public": True}
            )
            print(f"[PIPELINE] Created bucket: {TASK_ATTACHMENTS_BUCKET}")
            return True
        except Exception as e:
            print(f"[PIPELINE] Bucket creation note: {e}")
            return False


@router.get("/attachments/init")
def init_attachments_bucket():
    """
    Initialize the task-attachments bucket.
    Creates the bucket if it doesn't exist.
    """
    try:
        success = ensure_attachments_bucket()
        return {
            "success": success,
            "bucket": TASK_ATTACHMENTS_BUCKET,
            "message": "Bucket ready" if success else "Bucket may already exist"
        }
    except Exception as e:
        print(f"[PIPELINE] ERROR in GET /attachments/init: {repr(e)}")
        raise HTTPException(status_code=500, detail=f"Error: {e}") from e


@router.get("/attachments/bucket-info")
def get_attachments_bucket_info():
    """
    Get information about the task-attachments bucket.
    """
    try:
        bucket = supabase.storage.get_bucket(TASK_ATTACHMENTS_BUCKET)
        return {
            "success": True,
            "bucket": {
                "id": bucket.id if hasattr(bucket, 'id') else TASK_ATTACHMENTS_BUCKET,
                "name": bucket.name if hasattr(bucket, 'name') else TASK_ATTACHMENTS_BUCKET,
                "public": bucket.public if hasattr(bucket, 'public') else True,
            }
        }
    except Exception as e:
        # Bucket doesn't exist
        return {
            "success": False,
            "bucket": None,
            "message": "Bucket not found. Call /attachments/init to create it."
        }


# ====== AUTOMATION SETTINGS ENDPOINTS ======

class AutomationSettingsUpdate(BaseModel):
    """Model for updating automation settings."""
    is_enabled: Optional[bool] = None
    default_owner_id: Optional[str] = None
    default_manager_id: Optional[str] = None
    default_department_id: Optional[str] = None
    default_priority: Optional[int] = None
    config: Optional[Dict[str, Any]] = None


class AutomationOverrideCreate(BaseModel):
    """Model for creating/updating automation owner overrides."""
    automation_type: str
    project_id: Optional[str] = None  # NULL = all projects
    owner_id: Optional[str] = None
    manager_id: Optional[str] = None


@router.get("/automations/settings")
def get_automation_settings() -> Dict[str, Any]:
    """
    Get all automation settings with their current configuration.

    Returns list of automation types with:
    - is_enabled: Whether automation is active
    - default_owner: Default owner for tasks (with name)
    - default_manager: Default reviewer/manager (with name)
    - default_department: Department for tasks
    """
    print("[AUTOMATIONS] GET /automations/settings")

    try:
        # Get automation settings
        settings_response = supabase.table("automation_settings").select("*").execute()
        settings = settings_response.data or []

        # Get users for owner/manager names
        users_response = supabase.table("users").select(
            "user_id, user_name, avatar_color"
        ).execute()
        users_map = {u["user_id"]: u for u in (users_response.data or [])}

        # Get departments
        depts_response = supabase.table("task_departments").select(
            "department_id, department_name"
        ).execute()
        depts_map = {d["department_id"]: d for d in (depts_response.data or [])}

        # Enrich settings with related data
        enriched = []
        for s in settings:
            owner_id = s.get("default_owner_id")
            manager_id = s.get("default_manager_id")
            dept_id = s.get("default_department_id")

            owner_data = users_map.get(owner_id) if owner_id else None
            manager_data = users_map.get(manager_id) if manager_id else None
            dept_data = depts_map.get(dept_id) if dept_id else None

            enriched.append({
                **s,
                "default_owner": {
                    "id": owner_id,
                    "name": owner_data["user_name"] if owner_data else None,
                    "avatar_color": owner_data.get("avatar_color") if owner_data else None,
                } if owner_id else None,
                "default_manager": {
                    "id": manager_id,
                    "name": manager_data["user_name"] if manager_data else None,
                    "avatar_color": manager_data.get("avatar_color") if manager_data else None,
                } if manager_id else None,
                "default_department": {
                    "id": dept_id,
                    "name": dept_data["department_name"] if dept_data else None,
                } if dept_id else None,
            })

        return {"settings": enriched}

    except Exception as e:
        print(f"[AUTOMATIONS] ERROR in GET /automations/settings: {repr(e)}")
        print(traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"Error: {e}") from e


@router.put("/automations/settings/{automation_type}")
def update_automation_settings(
    automation_type: str,
    payload: AutomationSettingsUpdate
) -> Dict[str, Any]:
    """
    Update settings for a specific automation type.

    Updates any provided fields:
    - is_enabled: Enable/disable the automation
    - default_owner_id: Set default owner for created tasks
    - default_manager_id: Set default manager/reviewer
    - default_department_id: Set department for tasks
    - default_priority: Set priority level (1-5)
    - config: Additional JSON configuration
    """
    print(f"[AUTOMATIONS] PUT /automations/settings/{automation_type}")

    try:
        # Check if automation type exists
        existing = supabase.table("automation_settings").select(
            "setting_id"
        ).eq("automation_type", automation_type).execute()

        if not existing.data:
            raise HTTPException(
                status_code=404,
                detail=f"Automation type '{automation_type}' not found"
            )

        # Build update data
        update_data = payload.model_dump(exclude_unset=True)

        if not update_data:
            raise HTTPException(status_code=400, detail="No fields to update")

        # Update the settings
        response = supabase.table("automation_settings").update(
            update_data
        ).eq("automation_type", automation_type).execute()

        if not response.data:
            raise HTTPException(status_code=500, detail="Failed to update settings")

        return {
            "success": True,
            "message": f"Settings for '{automation_type}' updated",
            "settings": response.data[0]
        }

    except HTTPException:
        raise
    except Exception as e:
        print(f"[AUTOMATIONS] ERROR in PUT /automations/settings: {repr(e)}")
        print(traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"Error: {e}") from e


@router.get("/automations/overrides")
def get_automation_overrides(
    automation_type: Optional[str] = None,
    project_id: Optional[str] = None
) -> Dict[str, Any]:
    """
    Get automation owner overrides.

    Optional filters:
    - automation_type: Filter by automation type
    - project_id: Filter by project
    """
    print("[AUTOMATIONS] GET /automations/overrides")

    try:
        query = supabase.table("automation_owner_overrides").select("*")

        if automation_type:
            query = query.eq("automation_type", automation_type)
        if project_id:
            query = query.eq("project_id", project_id)

        response = query.execute()
        overrides = response.data or []

        # Enrich with user and project names
        if overrides:
            user_ids = list(set(
                o.get("owner_id") or o.get("manager_id")
                for o in overrides if o.get("owner_id") or o.get("manager_id")
            ))
            project_ids = list(set(o.get("project_id") for o in overrides if o.get("project_id")))

            users_map = {}
            if user_ids:
                users_response = supabase.table("users").select(
                    "user_id, user_name"
                ).in_("user_id", user_ids).execute()
                users_map = {u["user_id"]: u for u in (users_response.data or [])}

            projects_map = {}
            if project_ids:
                projects_response = supabase.table("projects").select(
                    "project_id, project_name"
                ).in_("project_id", project_ids).execute()
                projects_map = {p["project_id"]: p for p in (projects_response.data or [])}

            enriched = []
            for o in overrides:
                owner_id = o.get("owner_id")
                project_id = o.get("project_id")

                enriched.append({
                    **o,
                    "owner_name": users_map.get(owner_id, {}).get("user_name") if owner_id else None,
                    "project_name": projects_map.get(project_id, {}).get("project_name") if project_id else None,
                })

            return {"overrides": enriched}

        return {"overrides": overrides}

    except Exception as e:
        print(f"[AUTOMATIONS] ERROR in GET /automations/overrides: {repr(e)}")
        print(traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"Error: {e}") from e


@router.post("/automations/overrides")
def create_or_update_automation_override(payload: AutomationOverrideCreate) -> Dict[str, Any]:
    """
    Create or update an automation owner override.

    If an override for the automation_type + project_id combination exists, it updates.
    Otherwise, it creates a new override.
    """
    print(f"[AUTOMATIONS] POST /automations/overrides - {payload.automation_type}")

    try:
        # Check if override already exists
        query = supabase.table("automation_owner_overrides").select("override_id").eq(
            "automation_type", payload.automation_type
        )

        if payload.project_id:
            query = query.eq("project_id", payload.project_id)
        else:
            query = query.is_("project_id", "null")

        existing = query.execute()

        override_data = {
            "automation_type": payload.automation_type,
            "project_id": payload.project_id,
            "owner_id": payload.owner_id,
            "manager_id": payload.manager_id,
        }

        if existing.data:
            # Update existing
            override_id = existing.data[0]["override_id"]
            response = supabase.table("automation_owner_overrides").update(
                override_data
            ).eq("override_id", override_id).execute()

            return {
                "success": True,
                "action": "updated",
                "override": response.data[0] if response.data else None
            }
        else:
            # Create new
            response = supabase.table("automation_owner_overrides").insert(
                override_data
            ).execute()

            return {
                "success": True,
                "action": "created",
                "override": response.data[0] if response.data else None
            }

    except Exception as e:
        print(f"[AUTOMATIONS] ERROR in POST /automations/overrides: {repr(e)}")
        print(traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"Error: {e}") from e


@router.delete("/automations/overrides/{override_id}")
def delete_automation_override(override_id: str) -> Dict[str, Any]:
    """Delete an automation owner override."""
    print(f"[AUTOMATIONS] DELETE /automations/overrides/{override_id}")

    try:
        # Check if exists
        existing = supabase.table("automation_owner_overrides").select(
            "override_id"
        ).eq("override_id", override_id).execute()

        if not existing.data:
            raise HTTPException(status_code=404, detail="Override not found")

        # Delete
        supabase.table("automation_owner_overrides").delete().eq(
            "override_id", override_id
        ).execute()

        return {"success": True, "message": "Override deleted"}

    except HTTPException:
        raise
    except Exception as e:
        print(f"[AUTOMATIONS] ERROR in DELETE /automations/overrides: {repr(e)}")
        print(traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"Error: {e}") from e


# ====== AUTOMATION TASKS (CLUSTER VIEW) ======

@router.get("/automations/tasks")
def get_automation_tasks_grouped() -> Dict[str, Any]:
    """
    Get all automated tasks grouped by automation type (clusters).

    Returns data structure optimized for the Operation Manager UI:
    - Clusters grouped by automation_type
    - Each cluster contains tasks for different projects
    - Includes completion status and counts
    """
    print("[AUTOMATIONS] GET /automations/tasks")

    try:
        # Get tasks that are automated
        tasks_response = supabase.table("tasks").select("*").eq(
            "is_automated", True
        ).execute()
        tasks = tasks_response.data or []

        if not tasks:
            return {"clusters": []}

        # Get related data
        project_ids = list(set(t.get("project_id") for t in tasks if t.get("project_id")))
        owner_ids = list(set(t.get("Owner_id") for t in tasks if t.get("Owner_id")))
        status_ids = list(set(t.get("task_status") for t in tasks if t.get("task_status")))

        projects_map = {}
        if project_ids:
            projects_response = supabase.table("projects").select(
                "project_id, project_name"
            ).in_("project_id", project_ids).execute()
            projects_map = {p["project_id"]: p for p in (projects_response.data or [])}

        users_map = {}
        if owner_ids:
            users_response = supabase.table("users").select(
                "user_id, user_name, avatar_color"
            ).in_("user_id", owner_ids).execute()
            users_map = {u["user_id"]: u for u in (users_response.data or [])}

        statuses_map = {}
        if status_ids:
            statuses_response = supabase.table("tasks_status").select(
                "task_status_id, task_status"
            ).in_("task_status_id", status_ids).execute()
            statuses_map = {s["task_status_id"]: s for s in (statuses_response.data or [])}

        # Get automation settings for display names
        settings_response = supabase.table("automation_settings").select(
            "automation_type, display_name, is_enabled"
        ).execute()
        settings_map = {s["automation_type"]: s for s in (settings_response.data or [])}

        # Group tasks by automation_type
        clusters_map: Dict[str, Dict] = {}

        for task in tasks:
            automation_type = task.get("automation_type") or "unknown"

            if automation_type not in clusters_map:
                setting = settings_map.get(automation_type, {})
                clusters_map[automation_type] = {
                    "automation_type": automation_type,
                    "display_name": setting.get("display_name", automation_type),
                    "is_enabled": setting.get("is_enabled", False),
                    "tasks": [],
                    "total_count": 0,
                    "completed_count": 0,
                    "pending_count": 0,
                }

            # Enrich task
            project_id = task.get("project_id")
            owner_id = task.get("Owner_id")
            status_id = task.get("task_status")

            project_data = projects_map.get(project_id, {})
            owner_data = users_map.get(owner_id, {})
            status_data = statuses_map.get(status_id, {})
            status_name = status_data.get("task_status", "")

            is_completed = status_name.lower() in ["done", "completed", "closed"]

            clusters_map[automation_type]["tasks"].append({
                "task_id": task.get("task_id"),
                "task_description": task.get("task_description"),
                "project_id": project_id,
                "project_name": project_data.get("project_name"),
                "owner_id": owner_id,
                "owner_name": owner_data.get("user_name"),
                "owner_avatar_color": owner_data.get("avatar_color"),
                "status_id": status_id,
                "status_name": status_name,
                "is_completed": is_completed,
                "automation_metadata": task.get("automation_metadata"),
                "created_at": task.get("created_at"),
            })

            clusters_map[automation_type]["total_count"] += 1
            if is_completed:
                clusters_map[automation_type]["completed_count"] += 1
            else:
                clusters_map[automation_type]["pending_count"] += 1

        # Convert to list and sort
        clusters = list(clusters_map.values())
        clusters.sort(key=lambda c: c["display_name"])

        return {"clusters": clusters}

    except Exception as e:
        print(f"[AUTOMATIONS] ERROR in GET /automations/tasks: {repr(e)}")
        print(traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"Error: {e}") from e


# ====== WORKLOAD SCHEDULING ENDPOINTS ======

class UserCapacityUpdate(BaseModel):
    """Model for updating user capacity settings."""
    hours_per_day: Optional[float] = None
    days_per_week: Optional[int] = None
    working_days: Optional[List[int]] = None  # [1,2,3,4,5] = Mon-Fri
    buffer_percent: Optional[int] = None


class ScheduleTaskRequest(BaseModel):
    """Model for scheduling a task."""
    task_id: str
    force_reschedule: bool = False


@router.get("/workload/user/{user_id}")
def get_user_workload(user_id: str) -> Dict[str, Any]:
    """
    Get detailed workload information for a user including:
    - Current task queue
    - Scheduled dates
    - Next available slot
    - Burnout risk indicators
    """
    print(f"[WORKLOAD] GET /workload/user/{user_id}")

    try:
        from datetime import datetime, timedelta

        # Get user info
        user_response = supabase.table("users").select(
            "user_id, user_name, avatar_color"
        ).eq("user_id", user_id).single().execute()

        if not user_response.data:
            raise HTTPException(status_code=404, detail="User not found")

        user = user_response.data

        # Get user capacity settings
        capacity_response = supabase.table("user_capacity_settings").select("*").eq(
            "user_id", user_id
        ).execute()

        capacity = capacity_response.data[0] if capacity_response.data else {
            "hours_per_day": 8.0,
            "days_per_week": 5,
            "working_days": [1, 2, 3, 4, 5],
            "buffer_percent": 20,
        }

        hours_per_day = capacity.get("hours_per_day", 8.0)
        days_per_week = capacity.get("days_per_week", 5)
        buffer_percent = capacity.get("buffer_percent", 20)
        effective_hours = hours_per_day * (100 - buffer_percent) / 100

        # Get active tasks for user
        statuses_response = supabase.table("tasks_status").select(
            "task_status_id, task_status"
        ).execute()
        status_map = {s["task_status"].lower(): s["task_status_id"] for s in (statuses_response.data or [])}
        status_name_map = {s["task_status_id"]: s["task_status"] for s in (statuses_response.data or [])}

        not_started_id = status_map.get("not started")
        working_id = status_map.get("working on it")
        valid_status_ids = [s for s in [not_started_id, working_id] if s]

        tasks_response = supabase.table("tasks").select(
            "task_id, task_description, project_id, estimated_hours, deadline, due_date, "
            "scheduled_start_date, scheduled_end_date, queue_position, auto_linked, "
            "blocked_by_task_id, task_status, task_priority, created_at"
        ).eq("Owner_id", user_id).in_("task_status", valid_status_ids).order(
            "queue_position", desc=False
        ).execute()
        tasks = tasks_response.data or []

        # Get related data
        project_ids = list(set(t.get("project_id") for t in tasks if t.get("project_id")))
        projects_map = {}
        if project_ids:
            projects_response = supabase.table("projects").select(
                "project_id, project_name"
            ).in_("project_id", project_ids).execute()
            projects_map = {p["project_id"]: p for p in (projects_response.data or [])}

        priority_ids = list(set(t.get("task_priority") for t in tasks if t.get("task_priority")))
        priorities_map = {}
        if priority_ids:
            priorities_response = supabase.table("tasks_priority").select(
                "priority_id, priority"
            ).in_("priority_id", priority_ids).execute()
            priorities_map = {p["priority_id"]: p for p in (priorities_response.data or [])}

        # Calculate workload metrics
        today = datetime.utcnow().date()
        total_hours = 0
        overdue_count = 0
        due_soon_count = 0
        current_task = None

        enriched_tasks = []
        for task in tasks:
            duration = task.get("estimated_hours") or 2.0
            total_hours += duration

            deadline_str = task.get("deadline") or task.get("due_date")
            is_overdue = False
            is_due_soon = False

            if deadline_str:
                try:
                    deadline_date = datetime.fromisoformat(deadline_str.replace("Z", "")).date()
                    if deadline_date < today:
                        is_overdue = True
                        overdue_count += 1
                    elif deadline_date <= today + timedelta(days=7):
                        is_due_soon = True
                        due_soon_count += 1
                except:
                    pass

            status_id = task.get("task_status")
            status_name = status_name_map.get(status_id, "")

            task_data = {
                "task_id": task.get("task_id"),
                "task_description": task.get("task_description"),
                "project_id": task.get("project_id"),
                "project_name": projects_map.get(task.get("project_id"), {}).get("project_name"),
                "estimated_hours": duration,
                "deadline": task.get("deadline"),
                "due_date": task.get("due_date"),
                "scheduled_start_date": task.get("scheduled_start_date"),
                "scheduled_end_date": task.get("scheduled_end_date"),
                "queue_position": task.get("queue_position"),
                "auto_linked": task.get("auto_linked", False),
                "blocked_by_task_id": task.get("blocked_by_task_id"),
                "status_name": status_name,
                "priority_name": priorities_map.get(task.get("task_priority"), {}).get("priority"),
                "is_overdue": is_overdue,
                "is_due_soon": is_due_soon,
            }

            if status_name.lower() == "working on it" and not current_task:
                current_task = task_data

            enriched_tasks.append(task_data)

        # Calculate capacity and utilization
        weekly_capacity = hours_per_day * days_per_week
        effective_weekly_capacity = weekly_capacity * (100 - buffer_percent) / 100
        utilization = (total_hours / effective_weekly_capacity * 100) if effective_weekly_capacity > 0 else 0

        # Determine workload status
        if utilization > 120:
            workload_status = "critical"
        elif utilization > 100:
            workload_status = "overloaded"
        elif utilization > 80:
            workload_status = "optimal"
        elif utilization > 50:
            workload_status = "normal"
        else:
            workload_status = "underloaded"

        # Calculate days to clear backlog
        days_to_clear = int(total_hours / effective_hours) if effective_hours > 0 else 0

        # Calculate next available date (accounting for weekends)
        working_days = capacity.get("working_days", [1, 2, 3, 4, 5])
        next_available = today
        days_remaining = days_to_clear

        while days_remaining > 0:
            next_available = next_available + timedelta(days=1)
            if next_available.weekday() + 1 in working_days:  # weekday() is 0=Mon, we use 1=Mon
                days_remaining -= 1

        # Calculate burnout risk (0-100)
        burnout_risk = min(100, int(
            (utilization / 100 * 40) +  # 40% weight on utilization
            (overdue_count * 10) +       # 10 points per overdue task
            (due_soon_count * 5)         # 5 points per task due soon
        ))

        return {
            "user": {
                "user_id": user.get("user_id"),
                "user_name": user.get("user_name"),
                "avatar_color": user.get("avatar_color"),
            },
            "capacity": {
                "hours_per_day": hours_per_day,
                "days_per_week": days_per_week,
                "working_days": working_days,
                "buffer_percent": buffer_percent,
                "weekly_capacity": weekly_capacity,
                "effective_weekly_capacity": round(effective_weekly_capacity, 1),
                "effective_hours_per_day": round(effective_hours, 1),
            },
            "workload": {
                "total_hours": round(total_hours, 1),
                "utilization_percent": round(utilization, 1),
                "status": workload_status,
                "overdue_count": overdue_count,
                "due_soon_count": due_soon_count,
                "days_to_clear_backlog": days_to_clear,
                "next_available_date": str(next_available),
                "burnout_risk": burnout_risk,
            },
            "current_task": current_task,
            "task_queue": enriched_tasks,
        }

    except HTTPException:
        raise
    except Exception as e:
        print(f"[WORKLOAD] ERROR: {repr(e)}")
        print(traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"Error: {e}") from e


@router.get("/workload/team")
def get_team_workload() -> Dict[str, Any]:
    """
    Get workload overview for all team members.
    Useful for coordination to identify bottlenecks and balance work.
    """
    print("[WORKLOAD] GET /workload/team")

    try:
        from datetime import datetime, timedelta

        # Get all users
        users_response = supabase.table("users").select(
            "user_id, user_name, avatar_color"
        ).execute()
        users = users_response.data or []

        if not users:
            return {"team": []}

        # Get all capacity settings
        capacity_response = supabase.table("user_capacity_settings").select("*").execute()
        capacity_map = {c["user_id"]: c for c in (capacity_response.data or [])}

        # Get valid status IDs
        statuses_response = supabase.table("tasks_status").select(
            "task_status_id, task_status"
        ).execute()
        status_map = {s["task_status"].lower(): s["task_status_id"] for s in (statuses_response.data or [])}

        not_started_id = status_map.get("not started")
        working_id = status_map.get("working on it")
        valid_status_ids = [s for s in [not_started_id, working_id] if s]

        # Get all active tasks
        tasks_response = supabase.table("tasks").select(
            "task_id, Owner_id, estimated_hours, deadline, due_date, task_status"
        ).in_("task_status", valid_status_ids).execute()
        tasks = tasks_response.data or []

        # Group tasks by owner
        tasks_by_user = {}
        for task in tasks:
            owner_id = task.get("Owner_id")
            if owner_id:
                if owner_id not in tasks_by_user:
                    tasks_by_user[owner_id] = []
                tasks_by_user[owner_id].append(task)

        today = datetime.utcnow().date()
        team_data = []

        for user in users:
            user_id = user.get("user_id")
            user_tasks = tasks_by_user.get(user_id, [])

            # Get capacity
            capacity = capacity_map.get(user_id, {})
            hours_per_day = capacity.get("hours_per_day", 8.0)
            days_per_week = capacity.get("days_per_week", 5)
            buffer_percent = capacity.get("buffer_percent", 20)

            weekly_capacity = hours_per_day * days_per_week
            effective_capacity = weekly_capacity * (100 - buffer_percent) / 100

            # Calculate metrics
            total_hours = sum(t.get("estimated_hours") or 2.0 for t in user_tasks)
            overdue_count = 0

            for task in user_tasks:
                deadline_str = task.get("deadline") or task.get("due_date")
                if deadline_str:
                    try:
                        deadline_date = datetime.fromisoformat(deadline_str.replace("Z", "")).date()
                        if deadline_date < today:
                            overdue_count += 1
                    except:
                        pass

            utilization = (total_hours / effective_capacity * 100) if effective_capacity > 0 else 0

            if utilization > 120:
                status = "critical"
            elif utilization > 100:
                status = "overloaded"
            elif utilization > 80:
                status = "optimal"
            elif utilization > 50:
                status = "normal"
            else:
                status = "underloaded"

            # Days to clear
            effective_hours_day = hours_per_day * (100 - buffer_percent) / 100
            days_to_clear = int(total_hours / effective_hours_day) if effective_hours_day > 0 else 0

            team_data.append({
                "user_id": user_id,
                "user_name": user.get("user_name"),
                "avatar_color": user.get("avatar_color"),
                "tasks_count": len(user_tasks),
                "total_hours": round(total_hours, 1),
                "weekly_capacity": round(effective_capacity, 1),
                "utilization_percent": round(utilization, 1),
                "status": status,
                "overdue_count": overdue_count,
                "days_to_clear": days_to_clear,
            })

        # Sort by utilization (most loaded first)
        team_data.sort(key=lambda x: x["utilization_percent"], reverse=True)

        return {"team": team_data}

    except Exception as e:
        print(f"[WORKLOAD] ERROR: {repr(e)}")
        print(traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"Error: {e}") from e


@router.put("/workload/capacity/{user_id}")
def update_user_capacity(user_id: str, data: UserCapacityUpdate) -> Dict[str, Any]:
    """
    Update user capacity settings.
    """
    print(f"[WORKLOAD] PUT /workload/capacity/{user_id}")

    try:
        # Check if settings exist
        existing = supabase.table("user_capacity_settings").select("setting_id").eq(
            "user_id", user_id
        ).execute()

        update_data = {}
        if data.hours_per_day is not None:
            update_data["hours_per_day"] = data.hours_per_day
        if data.days_per_week is not None:
            update_data["days_per_week"] = data.days_per_week
        if data.working_days is not None:
            update_data["working_days"] = data.working_days
        if data.buffer_percent is not None:
            update_data["buffer_percent"] = data.buffer_percent

        if not update_data:
            return {"success": True, "message": "No changes"}

        update_data["updated_at"] = "now()"

        if existing.data:
            # Update existing
            response = supabase.table("user_capacity_settings").update(
                update_data
            ).eq("user_id", user_id).execute()
        else:
            # Insert new
            update_data["user_id"] = user_id
            response = supabase.table("user_capacity_settings").insert(
                update_data
            ).execute()

        return {"success": True, "data": response.data[0] if response.data else None}

    except Exception as e:
        print(f"[WORKLOAD] ERROR: {repr(e)}")
        print(traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"Error: {e}") from e


@router.post("/workload/schedule-task")
def schedule_task(data: ScheduleTaskRequest) -> Dict[str, Any]:
    """
    Schedule a task based on owner's workload.
    Creates auto-dependencies if owner is busy.
    """
    print(f"[WORKLOAD] POST /workload/schedule-task - task_id: {data.task_id}")

    try:
        from datetime import datetime, timedelta

        # Get the task
        task_response = supabase.table("tasks").select("*").eq(
            "task_id", data.task_id
        ).single().execute()

        if not task_response.data:
            raise HTTPException(status_code=404, detail="Task not found")

        task = task_response.data
        owner_id = task.get("Owner_id")

        if not owner_id:
            raise HTTPException(status_code=400, detail="Task has no owner assigned")

        # Get owner's capacity
        capacity_response = supabase.table("user_capacity_settings").select("*").eq(
            "user_id", owner_id
        ).execute()

        capacity = capacity_response.data[0] if capacity_response.data else {
            "hours_per_day": 8.0,
            "days_per_week": 5,
            "working_days": [1, 2, 3, 4, 5],
            "buffer_percent": 20,
        }

        hours_per_day = capacity.get("hours_per_day", 8.0)
        buffer_percent = capacity.get("buffer_percent", 20)
        working_days = capacity.get("working_days", [1, 2, 3, 4, 5])
        effective_hours = hours_per_day * (100 - buffer_percent) / 100

        estimated_hours = task.get("estimated_hours") or 2.0

        # Get valid status IDs
        statuses_response = supabase.table("tasks_status").select(
            "task_status_id, task_status"
        ).execute()
        status_map = {s["task_status"].lower(): s["task_status_id"] for s in (statuses_response.data or [])}

        not_started_id = status_map.get("not started")
        working_id = status_map.get("working on it")
        valid_status_ids = [s for s in [not_started_id, working_id] if s]

        # Get owner's current tasks (excluding this one)
        existing_tasks = supabase.table("tasks").select(
            "task_id, estimated_hours, scheduled_end_date, queue_position"
        ).eq("Owner_id", owner_id).in_(
            "task_status", valid_status_ids
        ).neq("task_id", data.task_id).order("queue_position", desc=False).execute()

        existing = existing_tasks.data or []

        # Calculate total pending hours
        total_pending_hours = sum(t.get("estimated_hours") or 2.0 for t in existing)

        # Find the last task in queue (potential blocker)
        blocking_task_id = None
        if existing:
            blocking_task_id = existing[-1].get("task_id")

        # Calculate start date (after current backlog clears)
        today = datetime.utcnow().date()
        days_to_clear = int(total_pending_hours / effective_hours) if effective_hours > 0 else 0

        start_date = today
        days_remaining = days_to_clear
        while days_remaining > 0:
            start_date = start_date + timedelta(days=1)
            # Convert Python weekday (0=Mon) to our format (1=Mon)
            if (start_date.weekday() + 1) in working_days:
                days_remaining -= 1

        # Calculate end date
        task_days = int(estimated_hours / effective_hours) if effective_hours > 0 else 1
        task_days = max(1, task_days)

        end_date = start_date
        days_remaining = task_days
        while days_remaining > 0:
            end_date = end_date + timedelta(days=1)
            if (end_date.weekday() + 1) in working_days:
                days_remaining -= 1

        # Get next queue position
        max_position = max((t.get("queue_position") or 0 for t in existing), default=0)

        # Update task with scheduled dates
        update_data = {
            "scheduled_start_date": str(start_date),
            "scheduled_end_date": str(end_date),
            "queue_position": max_position + 1,
            "scheduling_status": "scheduled",
        }

        dependency_created = False

        # If there's a blocking task, create auto-dependency
        if blocking_task_id:
            update_data["auto_linked"] = True
            update_data["blocked_by_task_id"] = blocking_task_id

            # Check if dependency already exists
            existing_dep = supabase.table("task_dependencies").select("dependency_id").eq(
                "predecessor_task_id", blocking_task_id
            ).eq("successor_task_id", data.task_id).execute()

            if not existing_dep.data:
                # Create dependency
                supabase.table("task_dependencies").insert({
                    "predecessor_task_id": blocking_task_id,
                    "successor_task_id": data.task_id,
                    "dependency_type": "finish_to_start",
                    "is_auto_generated": True,
                }).execute()
                dependency_created = True

        # Update the task
        supabase.table("tasks").update(update_data).eq("task_id", data.task_id).execute()

        return {
            "success": True,
            "task_id": data.task_id,
            "scheduled_start_date": str(start_date),
            "scheduled_end_date": str(end_date),
            "queue_position": max_position + 1,
            "blocking_task_id": blocking_task_id,
            "dependency_created": dependency_created,
            "total_pending_hours_before": round(total_pending_hours, 1),
            "days_until_start": (start_date - today).days,
        }

    except HTTPException:
        raise
    except Exception as e:
        print(f"[WORKLOAD] ERROR: {repr(e)}")
        print(traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"Error: {e}") from e


@router.post("/workload/recalculate/{user_id}")
def recalculate_user_schedule(user_id: str) -> Dict[str, Any]:
    """
    Recalculate all scheduled dates for a user's tasks.
    Called after task completion or priority changes.
    """
    print(f"[WORKLOAD] POST /workload/recalculate/{user_id}")

    try:
        from datetime import datetime, timedelta

        # Get user capacity
        capacity_response = supabase.table("user_capacity_settings").select("*").eq(
            "user_id", user_id
        ).execute()

        capacity = capacity_response.data[0] if capacity_response.data else {
            "hours_per_day": 8.0,
            "working_days": [1, 2, 3, 4, 5],
            "buffer_percent": 20,
        }

        hours_per_day = capacity.get("hours_per_day", 8.0)
        working_days = capacity.get("working_days", [1, 2, 3, 4, 5])
        buffer_percent = capacity.get("buffer_percent", 20)
        effective_hours = hours_per_day * (100 - buffer_percent) / 100

        # Get valid status IDs
        statuses_response = supabase.table("tasks_status").select(
            "task_status_id, task_status"
        ).execute()
        status_map = {s["task_status"].lower(): s["task_status_id"] for s in (statuses_response.data or [])}

        not_started_id = status_map.get("not started")
        working_id = status_map.get("working on it")
        valid_status_ids = [s for s in [not_started_id, working_id] if s]

        # Get tasks ordered by priority and deadline
        tasks_response = supabase.table("tasks").select(
            "task_id, estimated_hours, deadline, task_priority"
        ).eq("Owner_id", user_id).in_(
            "task_status", valid_status_ids
        ).order("deadline", desc=False).execute()

        tasks = tasks_response.data or []

        if not tasks:
            return {"success": True, "tasks_scheduled": 0}

        # Schedule tasks sequentially
        today = datetime.utcnow().date()
        current_date = today
        hours_remaining_today = effective_hours
        scheduled_count = 0
        previous_task_id = None

        for idx, task in enumerate(tasks):
            task_hours = task.get("estimated_hours") or 2.0

            # Find start date (must be a working day)
            while (current_date.weekday() + 1) not in working_days:
                current_date = current_date + timedelta(days=1)
                hours_remaining_today = effective_hours

            start_date = current_date

            # Calculate end date
            hours_needed = task_hours
            end_date = current_date

            while hours_needed > 0:
                if hours_needed <= hours_remaining_today:
                    hours_remaining_today -= hours_needed
                    hours_needed = 0
                else:
                    hours_needed -= hours_remaining_today
                    # Move to next working day
                    end_date = end_date + timedelta(days=1)
                    while (end_date.weekday() + 1) not in working_days:
                        end_date = end_date + timedelta(days=1)
                    hours_remaining_today = effective_hours

            # Update task
            update_data = {
                "scheduled_start_date": str(start_date),
                "scheduled_end_date": str(end_date),
                "queue_position": idx + 1,
                "scheduling_status": "scheduled",
            }

            # Link to previous task if exists
            if previous_task_id:
                update_data["blocked_by_task_id"] = previous_task_id
                update_data["auto_linked"] = True

            supabase.table("tasks").update(update_data).eq("task_id", task["task_id"]).execute()
            scheduled_count += 1

            # Update for next iteration
            current_date = end_date
            previous_task_id = task["task_id"]

        return {
            "success": True,
            "user_id": user_id,
            "tasks_scheduled": scheduled_count,
            "schedule_ends": str(current_date),
        }

    except Exception as e:
        print(f"[WORKLOAD] ERROR: {repr(e)}")
        print(traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"Error: {e}") from e


@router.get("/workload/next-available/{user_id}")
def get_next_available_slot(user_id: str, estimated_hours: float = 2.0) -> Dict[str, Any]:
    """
    Get the next available time slot for a user.
    Useful for showing when a new task could realistically start.
    """
    print(f"[WORKLOAD] GET /workload/next-available/{user_id}")

    try:
        from datetime import datetime, timedelta

        # Get user capacity
        capacity_response = supabase.table("user_capacity_settings").select("*").eq(
            "user_id", user_id
        ).execute()

        capacity = capacity_response.data[0] if capacity_response.data else {
            "hours_per_day": 8.0,
            "working_days": [1, 2, 3, 4, 5],
            "buffer_percent": 20,
        }

        hours_per_day = capacity.get("hours_per_day", 8.0)
        working_days = capacity.get("working_days", [1, 2, 3, 4, 5])
        buffer_percent = capacity.get("buffer_percent", 20)
        effective_hours = hours_per_day * (100 - buffer_percent) / 100

        # Get valid status IDs
        statuses_response = supabase.table("tasks_status").select(
            "task_status_id, task_status"
        ).execute()
        status_map = {s["task_status"].lower(): s["task_status_id"] for s in (statuses_response.data or [])}

        not_started_id = status_map.get("not started")
        working_id = status_map.get("working on it")
        valid_status_ids = [s for s in [not_started_id, working_id] if s]

        # Get pending hours
        tasks_response = supabase.table("tasks").select("estimated_hours").eq(
            "Owner_id", user_id
        ).in_("task_status", valid_status_ids).execute()

        total_pending = sum(t.get("estimated_hours") or 2.0 for t in (tasks_response.data or []))

        # Calculate available date
        today = datetime.utcnow().date()
        days_to_clear = int(total_pending / effective_hours) if effective_hours > 0 else 0

        available_date = today
        days_remaining = days_to_clear
        while days_remaining > 0:
            available_date = available_date + timedelta(days=1)
            if (available_date.weekday() + 1) in working_days:
                days_remaining -= 1

        # Calculate end date for new task
        task_days = int(estimated_hours / effective_hours) if effective_hours > 0 else 1
        task_days = max(1, task_days)

        end_date = available_date
        days_remaining = task_days
        while days_remaining > 0:
            end_date = end_date + timedelta(days=1)
            if (end_date.weekday() + 1) in working_days:
                days_remaining -= 1

        return {
            "user_id": user_id,
            "current_pending_hours": round(total_pending, 1),
            "estimated_hours_requested": estimated_hours,
            "available_start_date": str(available_date),
            "estimated_end_date": str(end_date),
            "days_until_available": (available_date - today).days,
            "effective_hours_per_day": round(effective_hours, 1),
        }

    except Exception as e:
        print(f"[WORKLOAD] ERROR in next-available: {repr(e)}")
        print(traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"Error: {e}") from e


# ====== TASK WORKFLOW ENDPOINTS ======

def _log_workflow_event(
    task_id: str,
    event_type: str,
    performed_by: Optional[str] = None,
    old_status: Optional[str] = None,
    new_status: Optional[str] = None,
    related_task_id: Optional[str] = None,
    notes: Optional[str] = None,
    attachments: Optional[List[str]] = None,
    metadata: Optional[dict] = None
) -> Optional[str]:
    """
    Helper to log workflow events to task_workflow_log table.

    event_type values:
    - 'started': Task work began
    - 'submitted_for_review': Owner sent task for approval
    - 'approved': Manager approved the task
    - 'rejected': Manager rejected with feedback
    - 'converted_to_coordination': Approved task became coordination task
    - 'status_changed': General status change
    - 'reassigned': Task was reassigned

    Returns: log_id if successful, None otherwise
    """
    try:
        log_data = {
            "task_id": task_id,
            "event_type": event_type,
        }

        if performed_by:
            log_data["performed_by"] = performed_by
        if old_status:
            log_data["old_status"] = old_status
        if new_status:
            log_data["new_status"] = new_status
        if related_task_id:
            log_data["related_task_id"] = related_task_id
        if notes:
            log_data["notes"] = notes
        if attachments:
            log_data["attachments"] = attachments
        if metadata:
            log_data["metadata"] = metadata

        response = supabase.table("task_workflow_log").insert(log_data).execute()

        if response.data:
            log_id = response.data[0].get("log_id")
            print(f"[WORKFLOW LOG] Created log {log_id} for task {task_id}: {event_type}")
            return log_id
        return None
    except Exception as e:
        print(f"[WORKFLOW LOG] Warning: Could not create log: {e}")
        return None


class TaskApproveRequest(BaseModel):
    reviewer_notes: Optional[str] = None
    performed_by: Optional[str] = None  # UUID of manager approving


class TaskRejectRequest(BaseModel):
    rejection_notes: str  # Required feedback for rejection
    attachments: Optional[List[str]] = None  # Reference files/links
    performed_by: Optional[str] = None  # UUID of manager rejecting


class SendToReviewRequestV2(BaseModel):
    notes: Optional[str] = None
    result_link: Optional[str] = None  # Link to deliverable
    attachments: Optional[List[str]] = None  # Additional files
    performed_by: Optional[str] = None  # UUID of owner submitting


@router.post("/tasks/{task_id}/approve")
def approve_task(task_id: str, payload: TaskApproveRequest) -> Dict[str, Any]:
    """
    Manager approves a task:
    - Changes original task status to "Good to Go"
    - Marks review task as completed
    - Logs the approval event
    - Updates workflow_state to 'completed'

    Returns:
        - task: The approved task
        - review_task_completed: True if review task was marked done
    """
    print(f"[WORKFLOW] POST /pipeline/tasks/{task_id}/approve")

    try:
        from datetime import datetime

        # 1. Get the task (could be the review task or original task)
        existing = supabase.table("tasks").select("*").eq("task_id", task_id).execute()

        if not existing.data:
            raise HTTPException(status_code=404, detail="Task not found")

        task = existing.data[0]
        original_task_id = task.get("parent_task_id") or task_id
        review_task_id = task_id if task.get("parent_task_id") else task.get("review_task_id")

        # 2. Get "Good to Go" status
        good_status_response = supabase.table("tasks_status").select(
            "task_status_id"
        ).ilike("task_status", "good to go").execute()

        if not good_status_response.data:
            raise HTTPException(status_code=500, detail="Status 'Good to Go' not found")

        good_status_id = good_status_response.data[0]["task_status_id"]

        # Get "Done" status for review task
        done_status_response = supabase.table("tasks_status").select(
            "task_status_id"
        ).or_("task_status.ilike.done,task_status.ilike.completed").execute()

        done_status_id = None
        if done_status_response.data:
            done_status_id = done_status_response.data[0]["task_status_id"]

        old_status = task.get("task_status")

        # 3. Update original task to "Good to Go"
        original_update = {
            "task_status": good_status_id,
            "workflow_state": "completed",
            "reviewer_notes": payload.reviewer_notes,
            "review_task_id": None,  # Clear the review link
        }

        original_response = supabase.table("tasks").update(original_update).eq(
            "task_id", original_task_id
        ).execute()

        if not original_response.data:
            raise HTTPException(status_code=500, detail="Failed to update original task")

        updated_task = original_response.data[0]

        # 4. Mark review task as done (if exists and different from original)
        review_task_completed = False
        if review_task_id and review_task_id != original_task_id and done_status_id:
            try:
                review_update = {
                    "task_status": done_status_id,
                    "workflow_state": "completed",
                }
                supabase.table("tasks").update(review_update).eq(
                    "task_id", review_task_id
                ).execute()
                review_task_completed = True
            except Exception as e:
                print(f"[WORKFLOW] Warning: Could not complete review task: {e}")

        # 5. Log the approval event
        _log_workflow_event(
            task_id=original_task_id,
            event_type="approved",
            performed_by=payload.performed_by,
            old_status=old_status,
            new_status=good_status_id,
            related_task_id=review_task_id if review_task_id != original_task_id else None,
            notes=payload.reviewer_notes,
            metadata={"approved_at": datetime.utcnow().isoformat()}
        )

        return {
            "success": True,
            "task": updated_task,
            "new_status": "Good to Go",
            "review_task_completed": review_task_completed,
            "message": "Task approved successfully"
        }

    except HTTPException:
        raise
    except Exception as e:
        print(f"[WORKFLOW] ERROR in POST /pipeline/tasks/{task_id}/approve: {repr(e)}")
        print(traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"DB error: {e}") from e


@router.post("/tasks/{task_id}/reject")
def reject_task(task_id: str, payload: TaskRejectRequest) -> Dict[str, Any]:
    """
    Manager rejects a task:
    - Returns original task to "Working on It" status
    - Increments rejection_count
    - Stores reviewer_notes with feedback
    - Marks review task as done (rejection processed)
    - Logs the rejection event

    Returns:
        - task: The rejected task (now back with owner)
        - rejection_count: Total times this task was rejected
    """
    print(f"[WORKFLOW] POST /pipeline/tasks/{task_id}/reject")

    try:
        from datetime import datetime

        # 1. Get the task
        existing = supabase.table("tasks").select("*").eq("task_id", task_id).execute()

        if not existing.data:
            raise HTTPException(status_code=404, detail="Task not found")

        task = existing.data[0]
        original_task_id = task.get("parent_task_id") or task_id
        review_task_id = task_id if task.get("parent_task_id") else task.get("review_task_id")

        # 2. Get original task data if we're on the review task
        if task.get("parent_task_id"):
            original_response = supabase.table("tasks").select("*").eq(
                "task_id", original_task_id
            ).execute()
            if original_response.data:
                original_task = original_response.data[0]
            else:
                original_task = task
        else:
            original_task = task

        # 3. Get "Working on It" status
        working_status_response = supabase.table("tasks_status").select(
            "task_status_id"
        ).ilike("task_status", "working on it").execute()

        if not working_status_response.data:
            raise HTTPException(status_code=500, detail="Status 'Working on It' not found")

        working_status_id = working_status_response.data[0]["task_status_id"]

        # Get "Done" status for review task
        done_status_response = supabase.table("tasks_status").select(
            "task_status_id"
        ).or_("task_status.ilike.done,task_status.ilike.completed").execute()

        done_status_id = None
        if done_status_response.data:
            done_status_id = done_status_response.data[0]["task_status_id"]

        old_status = original_task.get("task_status")
        current_rejection_count = original_task.get("rejection_count") or 0

        # 4. Update original task
        rejection_note_entry = f"[Rejection #{current_rejection_count + 1}] {payload.rejection_notes}"
        existing_notes = original_task.get("reviewer_notes") or ""
        combined_notes = f"{existing_notes}\n\n{rejection_note_entry}" if existing_notes else rejection_note_entry

        original_update = {
            "task_status": working_status_id,
            "workflow_state": "active",
            "reviewer_notes": combined_notes.strip(),
            "rejection_count": current_rejection_count + 1,
            "review_task_id": None,
            "time_start": datetime.utcnow().isoformat(),  # Reset timer
            "time_finish": None,
        }

        original_response = supabase.table("tasks").update(original_update).eq(
            "task_id", original_task_id
        ).execute()

        if not original_response.data:
            raise HTTPException(status_code=500, detail="Failed to update original task")

        updated_task = original_response.data[0]

        # 5. Mark review task as done (rejection processed)
        if review_task_id and review_task_id != original_task_id and done_status_id:
            try:
                review_update = {
                    "task_status": done_status_id,
                    "workflow_state": "completed",
                    "task_notes": f"{task.get('task_notes', '')}\n\n[REJECTED] Task returned to owner with feedback."
                }
                supabase.table("tasks").update(review_update).eq(
                    "task_id", review_task_id
                ).execute()
            except Exception as e:
                print(f"[WORKFLOW] Warning: Could not complete review task: {e}")

        # 6. Log the rejection event
        _log_workflow_event(
            task_id=original_task_id,
            event_type="rejected",
            performed_by=payload.performed_by,
            old_status=old_status,
            new_status=working_status_id,
            related_task_id=review_task_id if review_task_id != original_task_id else None,
            notes=payload.rejection_notes,
            attachments=payload.attachments,
            metadata={
                "rejected_at": datetime.utcnow().isoformat(),
                "rejection_count": current_rejection_count + 1
            }
        )

        return {
            "success": True,
            "task": updated_task,
            "new_status": "Working on It",
            "rejection_count": current_rejection_count + 1,
            "message": "Task returned to owner with feedback"
        }

    except HTTPException:
        raise
    except Exception as e:
        print(f"[WORKFLOW] ERROR in POST /pipeline/tasks/{task_id}/reject: {repr(e)}")
        print(traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"DB error: {e}") from e


@router.post("/tasks/{task_id}/convert-to-coordination")
def convert_to_coordination(task_id: str) -> Dict[str, Any]:
    """
    Convert an approved task (Good to Go) to a coordination task.
    Creates a new task in coordination with reference to original.

    Returns:
        - coordination_task: The new coordination task
        - original_task: The original task (marked as converted)
    """
    print(f"[WORKFLOW] POST /pipeline/tasks/{task_id}/convert-to-coordination")

    try:
        from datetime import datetime

        # 1. Get the original task
        existing = supabase.table("tasks").select("*").eq("task_id", task_id).execute()

        if not existing.data:
            raise HTTPException(status_code=404, detail="Task not found")

        task = existing.data[0]

        # 2. Verify task is in "Good to Go" status
        good_status_response = supabase.table("tasks_status").select(
            "task_status_id"
        ).ilike("task_status", "good to go").execute()

        if good_status_response.data:
            good_status_id = good_status_response.data[0]["task_status_id"]
            if task.get("task_status") != good_status_id:
                raise HTTPException(
                    status_code=400,
                    detail="Task must be in 'Good to Go' status to convert to coordination"
                )

        # 3. Get "Not Started" status for new coordination task
        not_started_response = supabase.table("tasks_status").select(
            "task_status_id"
        ).ilike("task_status", "not started").execute()

        not_started_id = None
        if not_started_response.data:
            not_started_id = not_started_response.data[0]["task_status_id"]

        # 4. Get Coordination department ID
        coord_dept_response = supabase.table("task_departments").select(
            "department_id"
        ).ilike("department", "%coordination%").execute()

        coord_dept_id = None
        if coord_dept_response.data:
            coord_dept_id = coord_dept_response.data[0]["department_id"]

        # 5. Create coordination task
        coordination_task_data = {
            "task_description": f"[COORD] {task.get('task_description', 'Task')}",
            "project_id": task.get("project_id"),
            "company": task.get("company"),
            "task_status": not_started_id,
            "task_department": coord_dept_id or task.get("task_department"),
            "is_coordination_task": True,
            "converted_from_task_id": task_id,
            "workflow_state": "active",
            "task_notes": f"[AUTO-CONVERTED] From approved task.\n\nOriginal: {task.get('task_description')}\nResult: {task.get('result_link', 'N/A')}\nDocs: {task.get('docs_link', 'N/A')}",
            "docs_link": task.get("docs_link"),
            "result_link": task.get("result_link"),
        }

        # Copy managers as they coordinate
        if task.get("managers_ids"):
            coordination_task_data["Owner_id"] = task.get("managers_ids")[0] if task.get("managers_ids") else None
        elif task.get("manager"):
            coordination_task_data["Owner_id"] = task.get("manager")

        coord_response = supabase.table("tasks").insert(coordination_task_data).execute()

        if not coord_response.data:
            raise HTTPException(status_code=500, detail="Failed to create coordination task")

        coordination_task = coord_response.data[0]

        # 6. Update original task as converted
        done_status_response = supabase.table("tasks_status").select(
            "task_status_id"
        ).or_("task_status.ilike.done,task_status.ilike.completed").execute()

        done_status_id = None
        if done_status_response.data:
            done_status_id = done_status_response.data[0]["task_status_id"]

        original_update = {
            "workflow_state": "converted",
        }
        if done_status_id:
            original_update["task_status"] = done_status_id

        supabase.table("tasks").update(original_update).eq("task_id", task_id).execute()

        # 7. Log the conversion
        _log_workflow_event(
            task_id=task_id,
            event_type="converted_to_coordination",
            related_task_id=coordination_task.get("task_id"),
            metadata={
                "converted_at": datetime.utcnow().isoformat(),
                "coordination_task_id": coordination_task.get("task_id")
            }
        )

        return {
            "success": True,
            "coordination_task": coordination_task,
            "original_task_id": task_id,
            "message": "Task converted to coordination successfully"
        }

    except HTTPException:
        raise
    except Exception as e:
        print(f"[WORKFLOW] ERROR in POST /pipeline/tasks/{task_id}/convert-to-coordination: {repr(e)}")
        print(traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"DB error: {e}") from e


@router.get("/tasks/{task_id}/workflow-history")
def get_task_workflow_history(task_id: str) -> Dict[str, Any]:
    """
    Get the complete workflow history/audit log for a task.

    Returns:
        - history: List of workflow events in chronological order
        - task: Current task data
    """
    print(f"[WORKFLOW] GET /pipeline/tasks/{task_id}/workflow-history")

    try:
        # 1. Get task info
        task_response = supabase.table("tasks").select("*").eq("task_id", task_id).execute()

        if not task_response.data:
            raise HTTPException(status_code=404, detail="Task not found")

        task = task_response.data[0]

        # 2. Get workflow logs for this task
        logs_response = supabase.table("task_workflow_log").select(
            "*, performed_by_user:users!task_workflow_log_performed_by_fkey(user_name)"
        ).eq("task_id", task_id).order("performed_at", desc=False).execute()

        history = []
        for log in (logs_response.data or []):
            entry = {
                "log_id": log.get("log_id"),
                "event_type": log.get("event_type"),
                "performed_at": log.get("performed_at"),
                "performed_by": log.get("performed_by"),
                "performed_by_name": log.get("performed_by_user", {}).get("user_name") if log.get("performed_by_user") else None,
                "notes": log.get("notes"),
                "attachments": log.get("attachments"),
                "old_status": log.get("old_status"),
                "new_status": log.get("new_status"),
                "related_task_id": log.get("related_task_id"),
                "metadata": log.get("metadata"),
            }
            history.append(entry)

        return {
            "success": True,
            "task_id": task_id,
            "task": task,
            "history": history,
            "total_events": len(history)
        }

    except HTTPException:
        raise
    except Exception as e:
        print(f"[WORKFLOW] ERROR in GET /pipeline/tasks/{task_id}/workflow-history: {repr(e)}")
        print(traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"DB error: {e}") from e


@router.get("/tasks/pending-reviews/{user_id}")
def get_pending_reviews(user_id: str) -> Dict[str, Any]:
    """
    Get all tasks pending review by a specific manager.

    This includes tasks where:
    - User is the manager and task is in "Awaiting Approval" status
    - User is in managers_ids array and task is in "Awaiting Approval"
    - Review tasks assigned to the user

    Returns:
        - tasks: List of tasks awaiting this user's review
    """
    print(f"[WORKFLOW] GET /pipeline/tasks/pending-reviews/{user_id}")

    try:
        # 1. Get "Awaiting Approval" status ID
        approval_status_response = supabase.table("tasks_status").select(
            "task_status_id"
        ).ilike("task_status", "awaiting approval").execute()

        if not approval_status_response.data:
            return {"success": True, "tasks": [], "total": 0}

        approval_status_id = approval_status_response.data[0]["task_status_id"]

        all_tasks = []
        seen_task_ids = set()

        # 2a. Tasks where user is single manager
        manager_query = supabase.table("tasks").select("*").eq(
            "manager", user_id
        ).eq("task_status", approval_status_id).execute()

        for task in (manager_query.data or []):
            if task.get("task_id") not in seen_task_ids:
                task["_review_role"] = "manager"
                all_tasks.append(task)
                seen_task_ids.add(task.get("task_id"))

        # 2b. Tasks where user is in managers_ids array
        managers_array_query = supabase.table("tasks").select("*").contains(
            "managers_ids", [user_id]
        ).eq("task_status", approval_status_id).execute()

        for task in (managers_array_query.data or []):
            if task.get("task_id") not in seen_task_ids:
                task["_review_role"] = "manager"
                all_tasks.append(task)
                seen_task_ids.add(task.get("task_id"))

        # 2c. Review tasks assigned to this user (created by workflow)
        try:
            review_query = supabase.table("tasks").select("*").eq(
                "Owner_id", user_id
            ).eq("task_status", approval_status_id).not_.is_("parent_task_id", "null").execute()

            for task in (review_query.data or []):
                if task.get("task_id") not in seen_task_ids:
                    task["_review_role"] = "reviewer"
                    all_tasks.append(task)
                    seen_task_ids.add(task.get("task_id"))
        except Exception as e:
            print(f"[WORKFLOW] Review query failed: {repr(e)}")

        # 3. Batch-load users and projects for enrichment
        owner_ids = list({t.get("Owner_id") for t in all_tasks if t.get("Owner_id")})
        project_ids = list({t.get("project_id") for t in all_tasks if t.get("project_id")})
        parent_ids = list({t.get("parent_task_id") for t in all_tasks if t.get("parent_task_id")})

        users_map = {}
        if owner_ids:
            try:
                users_resp = supabase.table("users").select("user_id, user_name").in_("user_id", owner_ids).execute()
                users_map = {u["user_id"]: u["user_name"] for u in (users_resp.data or [])}
            except Exception:
                pass

        projects_map = {}
        if project_ids:
            try:
                proj_resp = supabase.table("projects").select("project_id, project_name").in_("project_id", project_ids).execute()
                projects_map = {p["project_id"]: p["project_name"] for p in (proj_resp.data or [])}
            except Exception:
                pass

        parents_map = {}
        if parent_ids:
            try:
                parent_resp = supabase.table("tasks").select("*").in_("task_id", parent_ids).execute()
                parents_map = {t["task_id"]: t for t in (parent_resp.data or [])}
            except Exception:
                pass

        enriched_tasks = []
        for task in all_tasks:
            enriched_tasks.append({
                **task,
                "owner_name": users_map.get(task.get("Owner_id")),
                "project_name": projects_map.get(task.get("project_id")),
                "original_task": parents_map.get(task.get("parent_task_id")),
                "review_role": task.get("_review_role"),
            })

        return {
            "success": True,
            "tasks": enriched_tasks,
            "total": len(enriched_tasks)
        }

    except Exception as e:
        print(f"[WORKFLOW] ERROR in GET /pipeline/tasks/pending-reviews/{user_id}: {repr(e)}")
        print(traceback.format_exc())
        return {"success": False, "tasks": [], "total": 0}
