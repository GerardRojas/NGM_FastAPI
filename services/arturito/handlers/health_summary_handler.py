# services/arturito/handlers/health_summary_handler.py
# ====================================================
# Handler: Project Health Summary
# Returns a consolidated snapshot: budget, pending auth, tasks, receipts
# ====================================================

import logging
from typing import Dict, Any

logger = logging.getLogger(__name__)

from api.supabase_client import supabase

# Reuse project resolution helpers from BVA handler
from .bva_handler import (
    resolve_project,
    fetch_recent_projects,
    _gpt_ask_missing_entity,
)


def handle_project_health(
    request: Dict[str, Any],
    context: Dict[str, Any] = None,
) -> Dict[str, Any]:
    """
    Genera un resumen de salud del proyecto:
      - Budget total vs actuals (% usado)
      - Gastos pendientes de autorizar
      - Tareas abiertas por status
      - Recibos pendientes de procesar
    """
    entities = request.get("entities", {})
    ctx = context or {}

    # --- Resolve project ---
    project_input = entities.get("project")
    if project_input:
        project_input = str(project_input).strip()

    if not project_input:
        project_input = ctx.get("space_name", "")

    raw_text = request.get("raw_text", "")
    space_id = ctx.get("space_id", "default")

    if not project_input or project_input.lower() in (
        "default", "general", "random", "none", "ngm hub web",
    ):
        recent = fetch_recent_projects(limit=8)
        hint = ", ".join(p.get("project_name", "") for p in recent[:4]) if recent else ""
        data = None
        if recent:
            data = {
                "projects": [
                    {"id": p.get("project_id"), "name": p.get("project_name")}
                    for p in recent
                ]
            }
        text = _gpt_ask_missing_entity(
            raw_text, "project", hint, space_id, report_type="health summary",
        )
        result: Dict[str, Any] = {"ok": False, "text": text, "action": "ask_project"}
        if data:
            data["command"] = "health"
            result["data"] = data
        else:
            result["data"] = {"command": "health"}
        return result

    project = resolve_project(project_input)
    if not project:
        return {
            "ok": False,
            "text": f"No encontré el proyecto '{project_input}'. Verifica el nombre.",
            "action": "project_not_found",
        }

    project_id = project.get("project_id") or project.get("id")
    project_name = project.get("project_name") or project.get("name") or project_input

    # --- Fetch all metrics ---
    budget_info = _fetch_budget_info(project_id)
    pending_info = _fetch_pending_auth(project_id)
    tasks_info = _fetch_tasks_summary(project_id)
    receipts_info = _fetch_pending_receipts(project_id)

    # --- Format response ---
    response_text = _format_health_summary(
        project_name, budget_info, pending_info, tasks_info, receipts_info,
    )

    return {
        "ok": True,
        "text": response_text,
        "action": "project_health",
        "data": {
            "project_id": project_id,
            "project_name": project_name,
            "budget": budget_info,
            "pending_auth": pending_info,
            "tasks": tasks_info,
            "receipts": receipts_info,
        },
    }


# ---------------------------------------------------------------------------
# Data fetchers (lightweight — no pagination needed for aggregates)
# ---------------------------------------------------------------------------

def _sum_expenses(project_id: str, auth_status: bool) -> tuple[float, int]:
    """
    Paginated SUM of expenses for a project, filtered by auth_status.
    Rebuilds query each page to avoid reusing a consumed builder.
    Returns (total_amount, row_count).
    """
    total = 0.0
    count = 0
    page_size = 1000
    offset = 0
    while True:
        resp = (
            supabase.table("expenses_manual_COGS")
            .select("Amount, amount")
            .eq("project", project_id)
            .eq("auth_status", auth_status)
            .neq("status", "review")
            .range(offset, offset + page_size - 1)
            .execute()
        )
        batch = resp.data or []
        for r in batch:
            total += float(r.get("Amount") or r.get("amount") or 0)
        count += len(batch)
        if len(batch) < page_size:
            break
        offset += page_size
    return total, count


def _fetch_budget_info(project_id: str) -> Dict[str, Any]:
    """Budget total + actuals total for the project."""
    try:
        # Budget (few rows per project — no pagination needed)
        bud_resp = (
            supabase.table("budgets_qbo")
            .select("amount_sum")
            .eq("ngm_project_id", project_id)
            .eq("active", True)
            .execute()
        )
        total_budget = sum(
            float(r.get("amount_sum") or 0) for r in (bud_resp.data or [])
        )

        # Actuals (authorized only) — paginated to avoid 1000-row limit
        total_actuals, _ = _sum_expenses(project_id, auth_status=True)

        pct = (total_actuals / total_budget * 100) if total_budget else 0.0
        return {
            "total_budget": round(total_budget, 2),
            "total_actuals": round(total_actuals, 2),
            "remaining": round(total_budget - total_actuals, 2),
            "pct_used": round(pct, 1),
        }
    except Exception as e:
        logger.error("[HEALTH] Budget fetch error: %s", e)
        return {"total_budget": 0, "total_actuals": 0, "remaining": 0, "pct_used": 0}


def _fetch_pending_auth(project_id: str) -> Dict[str, Any]:
    """Count + total $ of expenses awaiting authorization."""
    try:
        total, count = _sum_expenses(project_id, auth_status=False)
        return {"count": count, "total": round(total, 2)}
    except Exception as e:
        logger.error("[HEALTH] Pending auth fetch error: %s", e)
        return {"count": 0, "total": 0}


def _fetch_tasks_summary(project_id: str) -> Dict[str, Any]:
    """Count tasks by status for the project."""
    try:
        # Status catalog
        st_resp = supabase.table("tasks_status").select("task_status_id, task_status").execute()
        status_map = {
            s["task_status_id"]: s["task_status"] for s in (st_resp.data or [])
        }

        # Tasks for this project (only need the status column)
        t_resp = (
            supabase.table("tasks")
            .select("task_status")
            .eq("project_id", project_id)
            .execute()
        )
        counts: Dict[str, int] = {}
        for t in (t_resp.data or []):
            name = status_map.get(t.get("task_status"), "other")
            counts[name] = counts.get(name, 0) + 1

        return {"total": sum(counts.values()), "by_status": counts}
    except Exception as e:
        logger.error("[HEALTH] Tasks fetch error: %s", e)
        return {"total": 0, "by_status": {}}


def _fetch_pending_receipts(project_id: str) -> Dict[str, Any]:
    """Count receipts not yet linked to an expense."""
    try:
        resp = (
            supabase.table("pending_receipts")
            .select("id")
            .eq("project_id", project_id)
            .is_("expense_id", "null")
            .not_.in_("status", ["rejected", "processing"])
            .execute()
        )
        return {"count": len(resp.data or [])}
    except Exception as e:
        logger.error("[HEALTH] Pending receipts fetch error: %s", e)
        return {"count": 0}


# ---------------------------------------------------------------------------
# Formatter
# ---------------------------------------------------------------------------

def _format_health_summary(
    project_name: str,
    budget: Dict[str, Any],
    pending: Dict[str, Any],
    tasks: Dict[str, Any],
    receipts: Dict[str, Any],
) -> str:
    def fmt(amount: float) -> str:
        return f"${abs(amount):,.2f}"

    lines = [f"Salud del Proyecto: {project_name}", ""]

    # Budget
    lines.append("BUDGET")
    if budget["total_budget"]:
        lines.append(f"  Total budget:   {fmt(budget['total_budget'])}")
        lines.append(f"  Total gastado:  {fmt(budget['total_actuals'])}  ({budget['pct_used']}%)")
        remaining = budget["remaining"]
        label = "Disponible" if remaining >= 0 else "Sobre-budget"
        lines.append(f"  {label}:     {fmt(remaining)}")
    else:
        lines.append(f"  Sin budget cargado. Total gastado: {fmt(budget['total_actuals'])}")

    # Pending auth
    lines.append("")
    lines.append("GASTOS PENDIENTES")
    if pending["count"]:
        lines.append(f"  {pending['count']} gastos sin autorizar  ({fmt(pending['total'])})")
    else:
        lines.append("  Todos los gastos estan autorizados.")

    # Tasks
    lines.append("")
    lines.append("TAREAS")
    if tasks["total"]:
        parts = [f"{name}: {count}" for name, count in tasks["by_status"].items()]
        lines.append(f"  {' | '.join(parts)}")
        lines.append(f"  Total: {tasks['total']}")
    else:
        lines.append("  Sin tareas registradas.")

    # Receipts
    lines.append("")
    lines.append("RECEIPTS")
    if receipts["count"]:
        lines.append(f"  {receipts['count']} recibos pendientes de procesar")
    else:
        lines.append("  Todos los recibos estan procesados.")

    return "\n".join(lines)
