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
    estimated_hours: Optional[float] = None  # Estimated duration in hours


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
                # Objetos anidados para el frontend (incluyen avatar_color y photo para avatares)
                "owner": {
                    "id": owner_id,
                    "name": owner_data["user_name"] if owner_data else None,
                    "avatar_color": owner_data.get("avatar_color") if owner_data else None,
                    "photo": owner_data.get("user_photo") if owner_data else None,
                } if owner_id else None,
                "collaborators": [{
                    "id": collab_id,
                    "name": collab_data["user_name"] if collab_data else None,
                    "avatar_color": collab_data.get("avatar_color") if collab_data else None,
                    "photo": collab_data.get("user_photo") if collab_data else None,
                }] if collab_id else [],
                "manager": {
                    "id": manager_id,
                    "name": manager_data["user_name"] if manager_data else None,
                    "avatar_color": manager_data.get("avatar_color") if manager_data else None,
                    "photo": manager_data.get("user_photo") if manager_data else None,
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
    "estimated_hours": "estimated_hours",
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


# ====== MY TASKS ENDPOINT (for Dashboard) ======

@router.get("/tasks/my-tasks/{user_id}")
def get_my_tasks(user_id: str) -> Dict[str, Any]:
    """
    Devuelve las tareas asignadas a un usuario para mostrar en su Dashboard.

    Solo devuelve tareas que NO están en status "Done".
    Incluye información del proyecto y prioridad.
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

        # 2. Obtener tareas del usuario (como owner)
        query = supabase.table("tasks").select("*").eq("Owner_id", user_id)

        # Excluir tareas completadas si encontramos el status
        if done_status_id:
            query = query.neq("task_status", done_status_id)

        # Ordenar por fecha de creación (más recientes primero)
        tasks_response = query.order("created_at", desc=True).execute()
        tasks = tasks_response.data or []

        print(f"[PIPELINE] Found {len(tasks)} tasks for user {user_id}")

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

        update_data = {
            "task_status": working_status_id,
            "time_start": now,
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

        return {
            "success": True,
            "task": updated_task,
            "status_changed": task.get("task_status") != working_status_id,
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

        # 3. Actualizar la tarea original
        now = datetime.utcnow().isoformat()

        update_data = {
            "task_status": approval_status_id,
            "time_finish": now,
        }

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
            reviewer_task_id = _create_reviewer_task(task, payload.notes)
            if reviewer_task_id:
                reviewer_task_created = True
        except Exception as e:
            print(f"[PIPELINE] Warning: Could not create reviewer task: {e}")

        # 5. Calcular tiempo trabajado
        time_start = task.get("time_start")
        elapsed_time = None
        if time_start:
            try:
                start_dt = datetime.fromisoformat(time_start.replace("Z", "+00:00"))
                end_dt = datetime.fromisoformat(now.replace("Z", "+00:00"))
                diff = end_dt - start_dt
                hours = diff.total_seconds() / 3600
                elapsed_time = f"{hours:.2f} hours"
            except:
                pass

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


def _create_reviewer_task(original_task: dict, submission_notes: str) -> Optional[str]:
    """
    Crea una tarea para el autorizador basado en el proyecto de la tarea original.

    El autorizador se determina por:
    1. El manager del proyecto
    2. Si no hay manager, busca usuarios con rol CEO/COO

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

    # Opción 1: Manager del proyecto
    if project_id:
        project_response = supabase.table("projects").select(
            "project_name, project_manager"
        ).eq("project_id", project_id).execute()

        if project_response.data:
            project_data = project_response.data[0]
            reviewer_id = project_data.get("project_manager")

    # Opción 2: Si no hay manager, buscar CEO/COO
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

    # Crear la tarea de revisión
    review_task_data = {
        "task_description": f"Review: {task_description} (submitted by {owner_name})",
        "project_id": project_id,
        "Owner_id": reviewer_id,
        "task_status": not_started_id,
        "task_notes": f"[AUTO-REVIEW] Task pending approval.\n\nOriginal task: {task_description}\nSubmitted by: {owner_name}\n\n{f'Notes: {submission_notes}' if submission_notes else ''}",
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
            elif automation_id == "pending_invoices":
                # TODO: Implementar lógica para facturas pendientes
                print(f"[AUTOMATIONS] pending_invoices: Not implemented yet")
            elif automation_id == "overdue_tasks":
                # TODO: Implementar lógica para tareas vencidas
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


def _run_pending_expenses_automation() -> tuple:
    """
    Automation: Pending Expenses Authorization

    Crea una tarea por cada proyecto que tenga gastos pendientes de autorización.
    Si ya existe una tarea automatizada para ese proyecto, la actualiza.

    Returns:
        tuple: (tasks_created, tasks_updated)
    """
    print("[AUTOMATIONS] Running pending_expenses_auth...")

    tasks_created = 0
    tasks_updated = 0

    try:
        # 1. Obtener gastos pendientes de autorización agrupados por proyecto
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

        # 4. Buscar tareas automatizadas existentes para estos proyectos
        existing_tasks_response = supabase.table("tasks").select(
            "task_id, project_id, task_notes"
        ).in_("project_id", project_ids).like(
            "task_notes", f"{AUTOMATION_MARKER}:pending_expenses_auth%"
        ).execute()

        existing_tasks_map = {
            t["project_id"]: t for t in (existing_tasks_response.data or [])
        }

        # 5. Crear o actualizar tareas
        for project_id, data in by_project.items():
            project_info = projects_map.get(project_id, {})
            project_name = project_info.get("project_name", "Unknown Project")
            project_manager = project_info.get("project_manager")

            count = data["count"]
            total = data["total"]

            task_description = f"Gastos pendientes por autorizar en {project_name}"
            task_notes = f"{AUTOMATION_MARKER}:pending_expenses_auth | {count} gastos | ${total:,.2f} total"

            if project_id in existing_tasks_map:
                # Actualizar tarea existente
                existing_task = existing_tasks_map[project_id]
                supabase.table("tasks").update({
                    "task_description": task_description,
                    "task_notes": task_notes,
                }).eq("task_id", existing_task["task_id"]).execute()

                tasks_updated += 1
                print(f"[AUTOMATIONS] Updated task for project: {project_name}")
            else:
                # Crear nueva tarea
                new_task_data = {
                    "task_description": task_description,
                    "task_notes": task_notes,
                    "project_id": project_id,
                    "Owner_id": project_manager,  # Asignar al project manager
                    "task_status": not_started_status_id,
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
