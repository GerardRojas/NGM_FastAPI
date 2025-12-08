# api/routers/pipeline.py

from typing import Dict, Any, List
import os

import asyncpg
from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/pipeline", tags=["pipeline"])

SUPABASE_DB_URL = os.getenv("SUPABASE_DB_URL")
if not SUPABASE_DB_URL:
    raise RuntimeError("Falta la variable de entorno SUPABASE_DB_URL.")

_pool: asyncpg.Pool | None = None


async def get_pool() -> asyncpg.Pool:
    """Crea (lazy) y devuelve el pool de conexiones a Supabase."""
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(SUPABASE_DB_URL)
    return _pool


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
    sql = """
    SELECT
      t.task_id,
      t.created_at,
      t."Owner_id",
      t."Colaborators_id",
      t.task_description,
      t.task_notes,
      t.start_date,
      t.due_date,
      t.deadline,
      t.task_status,
      t.task_priority,
      t.task_finished_status,
      t.estimated_hours,
      t.project_id,
      t.manager,
      t.company_management,
      t.result_link,
      t.docs_link,

      -- Owners / users
      u_owner.user_name        AS owner_name,
      u_collab.user_name       AS collaborator_name,
      u_manager.user_name      AS manager_name,

      -- Projects / companies
      p.project_name           AS project_name,
      c.name                   AS company_name,

      -- Status / priority / finished
      ts.task_status           AS status_name,
      tp.priority              AS priority_name,
      tcs.completed_status     AS finished_status_name

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

    # 👉 NUEVO: obtener todos los posibles estados, aunque no tengan tareas
    sql_statuses = """
    SELECT
      task_status_id,
      task_status
    FROM tasks_status
    ORDER BY task_status;  -- ajusta el ORDER BY si tienes otra columna de orden
    """

    pool = await get_pool()
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(sql)
            status_rows = await conn.fetch(sql_statuses)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"DB error: {e}") from e

    # Agrupar por status_id
    groups_map: Dict[str, Dict[str, Any]] = {}

    for r in rows:
        status_id = r["task_status"]
        status_name = r["status_name"] or "(no status)"

        # clave de grupo (si no hay id, usamos el nombre)
        group_key = str(status_id) if status_id is not None else status_name

        if group_key not in groups_map:
            groups_map[group_key] = {
                "status_id": status_id,
                "status_name": status_name,
                "tasks": [],
            }

        # owner
        owner_obj = None
        if r["Owner_id"] is not None or r["owner_name"] is not None:
            owner_obj = {
                "id": str(r["Owner_id"]) if r["Owner_id"] is not None else None,
                "name": r["owner_name"] or (str(r["Owner_id"]) if r["Owner_id"] else None),
            }

        # collaborators: de momento solo 1 colaborador -> array de 1 para el front
        collaborators_list: List[Dict[str, Any]] = []
        if r["Colaborators_id"] is not None or r["collaborator_name"] is not None:
            collaborators_list.append(
                {
                    "id": str(r["Colaborators_id"])
                    if r["Colaborators_id"] is not None
                    else None,
                    "name": r["collaborator_name"]
                    or (str(r["Colaborators_id"]) if r["Colaborators_id"] else None),
                }
            )

        # manager
        manager_obj = None
        if r["manager"] is not None or r["manager_name"] is not None:
            manager_obj = {
                "id": str(r["manager"]) if r["manager"] is not None else None,
                "name": r["manager_name"] or (str(r["manager"]) if r["manager"] else None),
            }

        # priority
        priority_obj = None
        if r["task_priority"] is not None or r["priority_name"] is not None:
            priority_obj = {
                "priority_id": str(r["task_priority"])
                if r["task_priority"] is not None
                else None,
                "priority_name": r["priority_name"]
                or (str(r["task_priority"]) if r["task_priority"] else None),
            }

        # finished status
        finished_obj = None
        if r["task_finished_status"] is not None or r["finished_status_name"] is not None:
            finished_obj = {
                "completed_status_id": str(r["task_finished_status"])
                if r["task_finished_status"] is not None
                else None,
                "completed_status_name": r["finished_status_name"]
                or (
                    str(r["task_finished_status"])
                    if r["task_finished_status"]
                    else None
                ),
            }

        task_obj = {
            "task_id": str(r["task_id"]) if r["task_id"] is not None else None,
            "created_at": r["created_at"],
            "task_description": r["task_description"],
            "task_notes": r["task_notes"],
            "start_date": r["start_date"],
            "due_date": r["due_date"],
            "deadline": r["deadline"],
            "estimated_hours": float(r["estimated_hours"])
            if r["estimated_hours"] is not None
            else None,
            "project_id": str(r["project_id"]) if r["project_id"] is not None else None,
            "project_name": r["project_name"],
            "company_management": str(r["company_management"])
            if r["company_management"] is not None
            else None,
            "company_name": r["company_name"],
            "docs_link": r["docs_link"],
            "result_link": r["result_link"],

            # nested objects para el front
            "owner": owner_obj,
            "collaborators": collaborators_list,
            "manager_obj": manager_obj,
            "priority": priority_obj,
            "finished_status": finished_obj,
        }

        groups_map[group_key]["tasks"].append(task_obj)

    # 👉 NUEVO: construir la lista final de grupos usando TODOS los estados
    groups: List[Dict[str, Any]] = []

    for s in status_rows:
        status_id = s["task_status_id"]
        status_name = s["task_status"]
        group_key = str(status_id)

        if group_key in groups_map:
            # ya hay tareas en este status, reutilizamos el grupo
            groups.append(groups_map.pop(group_key))
        else:
            # no hay tareas, mandamos grupo vacío
            groups.append(
                {
                    "status_id": status_id,
                    "status_name": status_name,
                    "tasks": [],
                }
            )

    # Cualquier grupo remanente en groups_map (por ejemplo, tareas sin status_id)
    # lo agregamos al final
    for remaining_group in groups_map.values():
        groups.append(remaining_group)

    return {"groups": groups}
