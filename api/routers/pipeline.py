# api/routers/pipeline.py

from __future__ import annotations

from typing import Dict, Any, List, Optional, Set
import os
import datetime as dt
import uuid
import traceback

import asyncpg
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, field_validator

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
    project: Optional[str] = None  # UUID del proyecto (puede ser null para limpiar)
    company: Optional[str] = None  # UUID de la empresa
    department: Optional[str] = None  # UUID del departamento
    type: Optional[str] = None  # UUID del tipo de tarea
    owner: Optional[str] = None  # UUID del owner
    collaborator: Optional[str] = None  # UUID del colaborador (puede ser null)
    manager: Optional[str] = None  # UUID del manager (puede ser null)
    due_date: Optional[str] = None  # Fecha YYYY-MM-DD (puede ser null)
    start_date: Optional[str] = None  # Fecha YYYY-MM-DD (puede ser null)
    deadline: Optional[str] = None  # Fecha YYYY-MM-DD (puede ser null)
    time_start: Optional[str] = None  # Hora HH:MM (puede ser null)
    time_finish: Optional[str] = None  # Hora HH:MM (puede ser null)
    status: Optional[str] = None  # Nombre del status o UUID
    priority: Optional[str] = None  # UUID de prioridad

SUPABASE_DB_URL = os.getenv("SUPABASE_DB_URL")
if not SUPABASE_DB_URL:
    raise RuntimeError("Falta la variable de entorno SUPABASE_DB_URL.")

_pool: Optional[asyncpg.Pool] = None


async def get_pool() -> asyncpg.Pool:
    """Crea (lazy) y devuelve el pool de conexiones a Supabase."""
    global _pool

    if _pool is None:
        dsn = SUPABASE_DB_URL.replace("postgres://", "postgresql://", 1)

        _pool = await asyncpg.create_pool(
            dsn,
            ssl="require",
            timeout=10,
            statement_cache_size=0,
            min_size=1,
            max_size=5,
        )


    return _pool

def _to_jsonable(value: Any) -> Any:
    """Convierte tipos comunes de Postgres a tipos JSON-friendly."""
    if value is None:
        return None
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, (dt.datetime, dt.date, dt.time)):
        # FastAPI / ORJSON manejan datetime, pero lo dejamos intacto.
        # Si prefieres ISO explícito, cambia a: return value.isoformat()
        return value
    if isinstance(value, dt.timedelta):
        return value.total_seconds()
    return value


async def _get_tasks_columns(conn: asyncpg.Connection) -> List[str]:
    """Obtiene la lista real de columnas en public.tasks (orden por ordinal)."""
    sql_cols = """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'tasks'
        ORDER BY ordinal_position;
    """
    rows = await conn.fetch(sql_cols)
    return [r["column_name"] for r in rows]


@router.get("/projects")
async def get_pipeline_projects() -> Dict[str, Any]:
    """
    Devuelve lista de proyectos para dropdowns en Pipeline UI.
    """
    pool = await get_pool()
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT project_id, project_name
                FROM projects
                ORDER BY project_name;
                """
            )

            projects = [
                {
                    "project_id": _to_jsonable(r["project_id"]),
                    "project_name": r["project_name"],
                }
                for r in rows
            ]

            return {"data": projects}

    except Exception as e:
        print("ERROR in GET /pipeline/projects:", repr(e))
        print(traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"DB error: {e}") from e


@router.get("/companies")
async def get_pipeline_companies() -> Dict[str, Any]:
    """
    Devuelve lista de empresas para dropdowns en Pipeline UI.
    """
    pool = await get_pool()
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, name
                FROM companies
                ORDER BY name;
                """
            )

            companies = [
                {
                    "id": _to_jsonable(r["id"]),
                    "name": r["name"],
                }
                for r in rows
            ]

            return {"data": companies}

    except Exception as e:
        print("ERROR in GET /pipeline/companies:", repr(e))
        print(traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"DB error: {e}") from e


@router.get("/task-departments")
async def get_pipeline_task_departments() -> Dict[str, Any]:
    """
    Devuelve lista de departamentos para dropdowns en Pipeline UI.
    """
    pool = await get_pool()
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT department_id, department_name
                FROM task_departments
                ORDER BY department_name;
                """
            )

            departments = [
                {
                    "department_id": _to_jsonable(r["department_id"]),
                    "department_name": r["department_name"],
                }
                for r in rows
            ]

            return {"data": departments}

    except Exception as e:
        print("ERROR in GET /pipeline/task-departments:", repr(e))
        print(traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"DB error: {e}") from e


@router.get("/task-types")
async def get_pipeline_task_types() -> Dict[str, Any]:
    """
    Devuelve lista de tipos de tarea para dropdowns en Pipeline UI.
    """
    pool = await get_pool()
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT type_id, type_name
                FROM task_types
                ORDER BY type_name;
                """
            )

            types = [
                {
                    "type_id": _to_jsonable(r["type_id"]),
                    "type_name": r["type_name"],
                }
                for r in rows
            ]

            return {"data": types}

    except Exception as e:
        print("ERROR in GET /pipeline/task-types:", repr(e))
        print(traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"DB error: {e}") from e


@router.get("/task-priorities")
async def get_pipeline_task_priorities() -> Dict[str, Any]:
    """
    Devuelve lista de prioridades para dropdowns en Pipeline UI.
    """
    pool = await get_pool()
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT priority_id, priority
                FROM tasks_priority
                ORDER BY priority;
                """
            )

            priorities = [
                {
                    "priority_id": _to_jsonable(r["priority_id"]),
                    "priority": r["priority"],
                }
                for r in rows
            ]

            return {"data": priorities}

    except Exception as e:
        print("ERROR in GET /pipeline/task-priorities:", repr(e))
        print(traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"DB error: {e}") from e


@router.get("/grouped")
async def get_pipeline_grouped() -> Dict[str, Any]:
    """
    Devuelve las tareas agrupadas por task_status, con JOINs a:
    - users (owner, collaborator, manager)
    - projects
    - companies
    - tasks_status
    - tasks_priority
    - task_completed_status

    Formato:
    {
      "groups": [
        {
          "status_id": "...uuid...",
          "status_name": "Working on it",
          "tasks": [ { ... }, ... ]
        },
        ...
      ]
    }
    """

    # Traemos todas las columnas de tasks (t.*) + alias útiles de joins
    sql = """
    SELECT
      t.*,

      -- Users
      u_owner.user_name   AS owner_name,
      u_collab.user_name  AS collaborator_name,
      u_manager.user_name AS manager_name,

      -- Projects / companies
      p.project_name      AS project_name,
      c.name              AS company_name,

      -- Status / priority / finished
      ts.task_status      AS status_name,
      tp.priority         AS priority_name,
      tcs.completed_status AS finished_status_name

    FROM tasks t
    LEFT JOIN users u_owner
      ON t."Owner_id" = u_owner.user_id
    LEFT JOIN users u_collab
      ON t."Colaborators_id" = u_collab.user_id
    LEFT JOIN users u_manager
      ON t.manager = u_manager.user_id
    LEFT JOIN projects p
      ON t.project_id = p.project_id
    LEFT JOIN companies c
      ON t.company_management = c.id
    LEFT JOIN tasks_status ts
      ON t.task_status = ts.task_status_id
    LEFT JOIN tasks_priority tp
      ON t.task_priority = tp.priority_id
    LEFT JOIN task_completed_status tcs
      ON t.task_finished_status = tcs.completed_status_id

    ORDER BY t.created_at DESC;
    """

    # Todos los posibles estados, aunque no tengan tareas
    sql_statuses = """
    SELECT
      task_status_id,
      task_status
    FROM tasks_status
    ORDER BY task_status;
    """

    pool = await get_pool()
    try:
        async with pool.acquire() as conn:
            # 👇 clave: saber exactamente qué columnas existen HOY en public.tasks
            tasks_columns: List[str] = await _get_tasks_columns(conn)

            rows = await conn.fetch(sql)
            status_rows = await conn.fetch(sql_statuses)

    except Exception as e:
        print("ERROR in GET /pipeline/grouped:", repr(e))
        print(traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"DB error: {e}") from e

    # Agrupar por status_id
    groups_map: Dict[str, Dict[str, Any]] = {}

    for r in rows:
        # status_id viene de la tabla tasks (t.task_status)
        status_id = r.get("task_status")
        status_name = r.get("status_name") or "(no status)"

        group_key = str(status_id) if status_id is not None else str(status_name)

        if group_key not in groups_map:
            groups_map[group_key] = {
                "status_id": _to_jsonable(status_id),
                "status_name": status_name,
                "tasks": [],
            }

        # ===== base task: TODAS las columnas reales de tasks =====
        base_task: Dict[str, Any] = {}
        for col in tasks_columns:
            # r[col] existe porque t.* devuelve todas las columnas
            base_task[col] = _to_jsonable(r.get(col))

        # ===== nested objects para el front (con fallback a ids) =====

        owner_obj = None
        owner_id = r.get("Owner_id")
        owner_name = r.get("owner_name")
        if owner_id is not None or owner_name is not None:
            owner_obj = {
                "id": _to_jsonable(owner_id),
                "name": owner_name or (_to_jsonable(owner_id) if owner_id else None),
            }

        collaborators_list: List[Dict[str, Any]] = []
        collab_id = r.get("Colaborators_id")
        collab_name = r.get("collaborator_name")
        if collab_id is not None or collab_name is not None:
            collaborators_list.append(
                {
                    "id": _to_jsonable(collab_id),
                    "name": collab_name or (_to_jsonable(collab_id) if collab_id else None),
                }
            )

        manager_obj = None
        manager_id = r.get("manager")
        manager_name = r.get("manager_name")
        if manager_id is not None or manager_name is not None:
            manager_obj = {
                "id": _to_jsonable(manager_id),
                "name": manager_name or (_to_jsonable(manager_id) if manager_id else None),
            }

        priority_obj = None
        priority_id = r.get("task_priority")
        priority_name = r.get("priority_name")
        if priority_id is not None or priority_name is not None:
            priority_obj = {
                "priority_id": _to_jsonable(priority_id),
                "priority_name": priority_name or (_to_jsonable(priority_id) if priority_id else None),
            }

        finished_obj = None
        finished_id = r.get("task_finished_status")
        finished_name = r.get("finished_status_name")
        if finished_id is not None or finished_name is not None:
            finished_obj = {
                "completed_status_id": _to_jsonable(finished_id),
                "completed_status_name": finished_name or (_to_jsonable(finished_id) if finished_id else None),
            }

        # ===== task final =====
        # base_task incluye TODO lo de tasks (incluyendo Owner_id, task_status, etc.)
        # aquí sólo añadimos campos derivados + nombres útiles de joins
        task_obj: Dict[str, Any] = {
            **base_task,

            # nombres útiles (joins)
            "project_name": r.get("project_name"),
            "company_name": r.get("company_name"),
            "status_name": r.get("status_name"),
            "priority_name": r.get("priority_name"),
            "finished_status_name": r.get("finished_status_name"),

            # nested para front
            "owner": owner_obj,
            "collaborators": collaborators_list,
            "manager": manager_obj,
            "priority": priority_obj,
            "finished_status": finished_obj,
        }

        groups_map[group_key]["tasks"].append(task_obj)

    # Construir la lista final de grupos usando TODOS los estados
    groups: List[Dict[str, Any]] = []

    for s in status_rows:
        status_id = s["task_status_id"]
        status_name = s["task_status"]
        group_key = str(status_id)

        if group_key in groups_map:
            groups.append(groups_map.pop(group_key))
        else:
            groups.append(
                {
                    "status_id": _to_jsonable(status_id),
                    "status_name": status_name,
                    "tasks": [],
                }
            )

    # Cualquier grupo remanente (por ejemplo, tareas sin status_id)
    for remaining_group in groups_map.values():
        groups.append(remaining_group)

    return {"groups": groups}


@router.post("/tasks", status_code=201)
async def create_task(payload: TaskCreate) -> Dict[str, Any]:
    """
    Crea una nueva tarea en el pipeline.

    Campos requeridos:
    - task_description: descripción de la tarea
    - company: UUID de la empresa
    - owner: UUID del owner
    - type: UUID del tipo de tarea
    - department: UUID del departamento

    Campos opcionales:
    - project: UUID del proyecto
    - collaborator: UUID del colaborador
    - due_date: fecha de vencimiento (YYYY-MM-DD)
    - deadline: fecha límite (YYYY-MM-DD)
    - status: estado inicial (default: "not started")
    """
    pool = await get_pool()

    try:
        async with pool.acquire() as conn:
            # Buscar el status_id basado en el nombre del status
            status_row = await conn.fetchrow(
                "SELECT task_status_id FROM tasks_status WHERE LOWER(task_status) = LOWER($1)",
                payload.status
            )

            if not status_row:
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid status: '{payload.status}'. Status not found in tasks_status table."
                )

            status_id = status_row["task_status_id"]

            # Validar que la empresa existe
            company_row = await conn.fetchrow(
                "SELECT id FROM companies WHERE id = $1",
                uuid.UUID(payload.company)
            )
            if not company_row:
                raise HTTPException(status_code=400, detail="Invalid company ID")

            # Validar que el owner existe
            owner_row = await conn.fetchrow(
                "SELECT user_id FROM users WHERE user_id = $1",
                uuid.UUID(payload.owner)
            )
            if not owner_row:
                raise HTTPException(status_code=400, detail="Invalid owner ID")

            # Validar project si se proporciona
            if payload.project:
                project_row = await conn.fetchrow(
                    "SELECT project_id FROM projects WHERE project_id = $1",
                    uuid.UUID(payload.project)
                )
                if not project_row:
                    raise HTTPException(status_code=400, detail="Invalid project ID")

            # Validar collaborator si se proporciona
            if payload.collaborator:
                collab_row = await conn.fetchrow(
                    "SELECT user_id FROM users WHERE user_id = $1",
                    uuid.UUID(payload.collaborator)
                )
                if not collab_row:
                    raise HTTPException(status_code=400, detail="Invalid collaborator ID")

            # Preparar fechas
            due_date = None
            if payload.due_date:
                try:
                    due_date = dt.datetime.strptime(payload.due_date, "%Y-%m-%d").date()
                except ValueError:
                    raise HTTPException(status_code=400, detail="Invalid due_date format. Use YYYY-MM-DD")

            deadline = None
            if payload.deadline:
                try:
                    deadline = dt.datetime.strptime(payload.deadline, "%Y-%m-%d").date()
                except ValueError:
                    raise HTTPException(status_code=400, detail="Invalid deadline format. Use YYYY-MM-DD")

            # Insertar la tarea
            insert_sql = """
                INSERT INTO tasks (
                    task_description,
                    company_management,
                    project_id,
                    "Owner_id",
                    "Colaborators_id",
                    task_type,
                    task_department,
                    due_date,
                    deadline,
                    task_status,
                    created_at
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, NOW())
                RETURNING *;
            """

            row = await conn.fetchrow(
                insert_sql,
                payload.task_description,
                uuid.UUID(payload.company),
                uuid.UUID(payload.project) if payload.project else None,
                uuid.UUID(payload.owner),
                uuid.UUID(payload.collaborator) if payload.collaborator else None,
                uuid.UUID(payload.type),
                uuid.UUID(payload.department),
                due_date,
                deadline,
                status_id,
            )

            # Convertir el resultado a diccionario JSON-friendly
            task_data = {key: _to_jsonable(value) for key, value in dict(row).items()}

            return {
                "message": "Task created successfully",
                "task": task_data,
            }

    except HTTPException:
        raise
    except Exception as e:
        print("ERROR in POST /pipeline/tasks:", repr(e))
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

# Campos que son UUIDs (foreign keys)
UUID_FIELDS = {
    "project", "company", "department", "type", "owner",
    "collaborator", "manager", "status", "priority"
}

# Campos de fecha
DATE_FIELDS = {"due_date", "start_date", "deadline"}

# Campos de hora
TIME_FIELDS = {"time_start", "time_finish"}


@router.patch("/tasks/{task_id}")
async def patch_task(task_id: str, payload: TaskUpdate) -> Dict[str, Any]:
    """
    Actualiza campos individuales de una tarea.

    Campos permitidos:
    - task_description: descripción de la tarea
    - project: UUID del proyecto (null para limpiar)
    - company: UUID de la empresa
    - department: UUID del departamento
    - type: UUID del tipo de tarea
    - owner: UUID del owner
    - collaborator: UUID del colaborador (null para limpiar)
    - manager: UUID del manager (null para limpiar)
    - due_date: fecha de vencimiento YYYY-MM-DD (null para limpiar)
    - start_date: fecha de inicio YYYY-MM-DD (null para limpiar)
    - deadline: fecha límite YYYY-MM-DD (null para limpiar)
    - time_start: hora de inicio HH:MM (null para limpiar)
    - time_finish: hora de fin HH:MM (null para limpiar)
    - status: nombre del status o UUID
    - priority: UUID de prioridad
    """
    pool = await get_pool()

    try:
        # Validar task_id como UUID
        try:
            task_uuid = uuid.UUID(task_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid task_id format")

        # Obtener solo los campos que fueron enviados (no None en el payload original)
        # Usamos model_dump con exclude_unset para obtener solo los campos enviados
        updates_raw = payload.model_dump(exclude_unset=True)

        if not updates_raw:
            raise HTTPException(status_code=400, detail="No fields to update")

        async with pool.acquire() as conn:
            # Verificar que la tarea existe
            existing = await conn.fetchrow(
                'SELECT task_id FROM tasks WHERE task_id = $1',
                task_uuid
            )
            if not existing:
                raise HTTPException(status_code=404, detail="Task not found")

            # Construir los valores a actualizar
            update_values: Dict[str, Any] = {}

            for field, value in updates_raw.items():
                column = FIELD_TO_COLUMN.get(field)
                if not column:
                    continue  # Campo no permitido, ignorar

                # Manejar status especial (puede ser nombre o UUID)
                if field == "status":
                    if value is None:
                        update_values[column] = None
                    else:
                        # Intentar primero como UUID
                        try:
                            update_values[column] = uuid.UUID(value)
                        except ValueError:
                            # Buscar por nombre
                            status_row = await conn.fetchrow(
                                "SELECT task_status_id FROM tasks_status WHERE LOWER(task_status) = LOWER($1)",
                                value
                            )
                            if not status_row:
                                raise HTTPException(
                                    status_code=400,
                                    detail=f"Invalid status: '{value}'"
                                )
                            update_values[column] = status_row["task_status_id"]

                # Manejar campos UUID
                elif field in UUID_FIELDS:
                    if value is None:
                        update_values[column] = None
                    else:
                        try:
                            update_values[column] = uuid.UUID(value)
                        except ValueError:
                            raise HTTPException(
                                status_code=400,
                                detail=f"Invalid UUID format for {field}"
                            )

                # Manejar campos de fecha
                elif field in DATE_FIELDS:
                    if value is None:
                        update_values[column] = None
                    else:
                        try:
                            update_values[column] = dt.datetime.strptime(value, "%Y-%m-%d").date()
                        except ValueError:
                            raise HTTPException(
                                status_code=400,
                                detail=f"Invalid date format for {field}. Use YYYY-MM-DD"
                            )

                # Manejar campos de hora
                elif field in TIME_FIELDS:
                    if value is None:
                        update_values[column] = None
                    else:
                        try:
                            update_values[column] = dt.datetime.strptime(value, "%H:%M").time()
                        except ValueError:
                            raise HTTPException(
                                status_code=400,
                                detail=f"Invalid time format for {field}. Use HH:MM"
                            )

                # Campos de texto
                else:
                    update_values[column] = value

            if not update_values:
                raise HTTPException(status_code=400, detail="No valid fields to update")

            # Construir la query de UPDATE dinámicamente
            set_clauses = []
            params = [task_uuid]  # $1 es task_id
            param_idx = 2

            for column, value in update_values.items():
                # Escapar columnas con mayúsculas
                if column in ("Owner_id", "Colaborators_id"):
                    set_clauses.append(f'"{column}" = ${param_idx}')
                else:
                    set_clauses.append(f"{column} = ${param_idx}")
                params.append(value)
                param_idx += 1

            update_sql = f"""
                UPDATE tasks
                SET {", ".join(set_clauses)}
                WHERE task_id = $1
                RETURNING *;
            """

            row = await conn.fetchrow(update_sql, *params)

            if not row:
                raise HTTPException(status_code=404, detail="Task not found after update")

            # Convertir el resultado a diccionario JSON-friendly
            task_data = {key: _to_jsonable(value) for key, value in dict(row).items()}

            return {
                "message": "Task updated successfully",
                "task": task_data,
            }

    except HTTPException:
        raise
    except Exception as e:
        print("ERROR in PATCH /pipeline/tasks/:id:", repr(e))
        print(traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"DB error: {e}") from e
