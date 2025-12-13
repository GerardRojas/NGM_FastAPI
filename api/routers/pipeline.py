# api/routers/pipeline.py

from __future__ import annotations

from typing import Dict, Any, List, Optional, Set
import os
import datetime as dt
import uuid

import asyncpg
from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/pipeline", tags=["pipeline"])

SUPABASE_DB_URL = os.getenv("SUPABASE_DB_URL")
if not SUPABASE_DB_URL:
    raise RuntimeError("Falta la variable de entorno SUPABASE_DB_URL.")

_pool: Optional[asyncpg.Pool] = None


async def get_pool() -> asyncpg.Pool:
    """Crea (lazy) y devuelve el pool de conexiones a Supabase."""
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(SUPABASE_DB_URL)
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
        raise HTTPException(status_code=500, detail=f"DB error: {e}") from e

    # Para evitar colisiones o meter basura: sólo incluimos columnas reales de tasks
    tasks_cols_set: Set[str] = set(tasks_columns)

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
            "manager_obj": manager_obj,
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
