"""
Analytics Router
Aggregated project health, cost trends, and budget-vs-actual endpoints
for the dashboard analytics layer.
"""

from fastapi import APIRouter, HTTPException, Depends, Query
from typing import Optional
from collections import defaultdict
from datetime import datetime, date
import logging
import math

from api.supabase_client import supabase
from api.auth import get_current_user

router = APIRouter(prefix="/analytics", tags=["Analytics"])
logger = logging.getLogger(__name__)

_PAGE_SIZE = 1000


# ============================================================
# Helpers
# ============================================================

def _paginated_fetch(table: str, select: str, filters: dict,
                     neq_filters: dict | None = None) -> list[dict]:
    """
    Fetch all rows from *table* with pagination to bypass the
    Supabase 1000-row default limit.

    Args:
        table:       Supabase table name
        select:      PostgREST select clause
        filters:     dict of {column: value} for .eq() filters
        neq_filters: optional dict of {column: value} for .neq() filters
    """
    all_rows: list[dict] = []
    offset = 0
    while True:
        query = supabase.table(table).select(select)
        for col, val in filters.items():
            query = query.eq(col, val)
        if neq_filters:
            for col, val in neq_filters.items():
                query = query.neq(col, val)
        query = query.range(offset, offset + _PAGE_SIZE - 1)
        try:
            batch = query.execute().data or []
        except Exception as exc:
            logger.error("[analytics] paginated fetch %s offset=%d: %s",
                         table, offset, exc)
            break
        all_rows.extend(batch)
        if len(batch) < _PAGE_SIZE:
            break
        offset += _PAGE_SIZE
    return all_rows


def _safe_float(val) -> float:
    """Convert a value to float, defaulting to 0.0 on failure."""
    try:
        return float(val)
    except (TypeError, ValueError):
        return 0.0


def _round2(val: float) -> float:
    return round(val, 2)


# ============================================================
# 1. GET /analytics/projects/{project_id}/health
# ============================================================

@router.get("/projects/{project_id}/health")
async def project_health(
    project_id: str,
    current_user: dict = Depends(get_current_user),
):
    """
    Aggregated project health snapshot: budget, expenses, receipts,
    tasks, vendor breakdown, monthly spend, and Daneel stats.
    """

    # --- Project name ---
    project_name = "Unknown Project"
    try:
        proj_resp = (
            supabase.table("projects")
            .select("project_name")
            .eq("project_id", project_id)
            .limit(1)
            .execute()
        )
        if proj_resp.data:
            project_name = proj_resp.data[0].get("project_name", project_name)
    except Exception as exc:
        logger.warning("[analytics:health] project lookup: %s", exc)

    # --- Budget ---
    budget_total = 0.0
    try:
        bud_resp = (
            supabase.table("budgets_qbo")
            .select("amount_sum")
            .eq("ngm_project_id", project_id)
            .eq("active", True)
            .execute()
        )
        for row in (bud_resp.data or []):
            budget_total += _safe_float(row.get("amount_sum"))
    except Exception as exc:
        logger.error("[analytics:health] budget fetch: %s", exc)

    # --- Expenses (paginated) ---
    all_expenses = _paginated_fetch(
        "expenses_manual_COGS",
        "expense_id, Amount, status, txn_type_id, vendor_id, TxnDate",
        {"project": project_id},
        neq_filters={"status": "review"},
    )

    # Separate authorized vs pending
    authorized_rows = []
    pending_rows = []
    for e in all_expenses:
        st = (e.get("status") or "").lower()
        if st in ("auth", "authorized"):
            authorized_rows.append(e)
        elif st == "pending":
            pending_rows.append(e)

    authorized_amount = sum(_safe_float(e.get("Amount")) for e in authorized_rows)
    pending_auth_amount = sum(_safe_float(e.get("Amount")) for e in pending_rows)
    spent_total = authorized_amount
    spent_percent = _round2((spent_total / budget_total * 100) if budget_total else 0.0)
    remaining = _round2(budget_total - spent_total)

    # --- by_category (txn_type_id -> name) ---
    # Fetch txn_types catalog
    txn_type_map: dict[str, str] = {}
    try:
        tt_resp = supabase.table("txn_types").select("txn_type_id, txn_type_name").execute()
        for t in (tt_resp.data or []):
            txn_type_map[str(t.get("txn_type_id", ""))] = t.get("txn_type_name", "Unknown")
    except Exception as exc:
        logger.warning("[analytics:health] txn_types fetch: %s", exc)

    cat_agg: dict[str, dict] = defaultdict(lambda: {"amount": 0.0, "count": 0})
    for e in all_expenses:
        tid = str(e.get("txn_type_id") or "")
        cat_name = txn_type_map.get(tid, "Uncategorized")
        cat_agg[cat_name]["amount"] += _safe_float(e.get("Amount"))
        cat_agg[cat_name]["count"] += 1

    by_category = sorted(
        [{"name": k, "amount": _round2(v["amount"]), "count": v["count"]}
         for k, v in cat_agg.items()],
        key=lambda x: x["amount"],
        reverse=True,
    )

    # --- top_vendors (vendor_id -> name) ---
    vendor_ids_in_expenses = {
        str(e.get("vendor_id")) for e in all_expenses if e.get("vendor_id")
    }
    vendor_map: dict[str, str] = {}
    if vendor_ids_in_expenses:
        try:
            v_resp = supabase.table("Vendors").select("id, vendor_name").execute()
            for v in (v_resp.data or []):
                vendor_map[str(v.get("id", ""))] = v.get("vendor_name", "Unknown")
        except Exception as exc:
            logger.warning("[analytics:health] vendors fetch: %s", exc)

    vendor_agg: dict[str, dict] = defaultdict(lambda: {"amount": 0.0, "count": 0})
    for e in all_expenses:
        vid = str(e.get("vendor_id") or "")
        if not vid:
            continue
        vname = vendor_map.get(vid, "Unknown Vendor")
        vendor_agg[vname]["amount"] += _safe_float(e.get("Amount"))
        vendor_agg[vname]["count"] += 1

    top_vendors = sorted(
        [{"vendor_name": k, "amount": _round2(v["amount"]), "count": v["count"]}
         for k, v in vendor_agg.items()],
        key=lambda x: x["amount"],
        reverse=True,
    )[:5]

    # --- monthly_spend (authorized only, grouped by YYYY-MM) ---
    month_agg: dict[str, float] = defaultdict(float)
    for e in authorized_rows:
        txn_date = e.get("TxnDate") or ""
        if len(txn_date) >= 7:
            month_key = txn_date[:7]  # YYYY-MM
            month_agg[month_key] += _safe_float(e.get("Amount"))

    monthly_spend = sorted(
        [{"month": m, "amount": _round2(a)} for m, a in month_agg.items()],
        key=lambda x: x["month"],
    )

    # --- Pending receipts ---
    pending_receipts_count = 0
    try:
        pr_resp = (
            supabase.table("pending_receipts")
            .select("id", count="exact")
            .eq("project_id", project_id)
            .is_("expense_id", "null")
            .execute()
        )
        pending_receipts_count = (
            pr_resp.count
            if hasattr(pr_resp, "count") and pr_resp.count is not None
            else len(pr_resp.data or [])
        )
    except Exception as exc:
        logger.warning("[analytics:health] pending_receipts: %s", exc)

    # --- Tasks ---
    tasks_payload = {"total": 0, "completed": 0, "in_progress": 0,
                     "backlog": 0, "completion_percent": 0.0}
    try:
        # Status catalog
        st_resp = supabase.table("tasks_status").select("task_status_id, task_status").execute()
        status_map = {
            s["task_status_id"]: (s.get("task_status") or "").lower()
            for s in (st_resp.data or [])
        }

        t_resp = (
            supabase.table("tasks")
            .select("task_status")
            .eq("project_id", project_id)
            .execute()
        )
        task_counts: dict[str, int] = defaultdict(int)
        for t in (t_resp.data or []):
            name = status_map.get(t.get("task_status"), "other")
            task_counts[name] += 1

        total_tasks = sum(task_counts.values())
        completed = task_counts.get("done", 0) + task_counts.get("completed", 0)
        in_progress = task_counts.get("in progress", 0) + task_counts.get("in_progress", 0)
        backlog = total_tasks - completed - in_progress

        tasks_payload = {
            "total": total_tasks,
            "completed": completed,
            "in_progress": in_progress,
            "backlog": max(backlog, 0),
            "completion_percent": _round2(
                (completed / total_tasks * 100) if total_tasks else 0.0
            ),
        }
    except Exception as exc:
        logger.warning("[analytics:health] tasks: %s", exc)

    # --- Daneel ---
    daneel_payload = {"authorization_rate": 0.0, "pending_info": 0,
                      "total_processed": 0}
    try:
        reports_resp = (
            supabase.table("daneel_auth_reports")
            .select("summary")
            .eq("project_id", project_id)
            .order("created_at", desc=True)
            .limit(50)
            .execute()
        )
        total_processed = 0
        total_authorized = 0
        for r in (reports_resp.data or []):
            s = r.get("summary") or {}
            if isinstance(s, str):
                import json
                try:
                    s = json.loads(s)
                except Exception:
                    s = {}
            total_processed += int(s.get("expenses_processed", 0))
            total_authorized += int(s.get("authorized", 0))

        auth_rate = _round2(
            (total_authorized / total_processed * 100) if total_processed else 0.0
        )

        # Pending info count
        pending_info = 0
        try:
            pi_resp = (
                supabase.table("daneel_pending_info")
                .select("expense_id", count="exact")
                .eq("project_id", project_id)
                .is_("resolved_at", "null")
                .execute()
            )
            pending_info = (
                pi_resp.count
                if hasattr(pi_resp, "count") and pi_resp.count is not None
                else len(pi_resp.data or [])
            )
        except Exception:
            pass

        daneel_payload = {
            "authorization_rate": auth_rate,
            "pending_info": pending_info,
            "total_processed": total_processed,
        }
    except Exception as exc:
        logger.warning("[analytics:health] daneel: %s", exc)

    return {
        "project_id": project_id,
        "project_name": project_name,
        "budget_total": _round2(budget_total),
        "spent_total": _round2(spent_total),
        "spent_percent": spent_percent,
        "remaining": remaining,
        "pending_auth_count": len(pending_rows),
        "pending_auth_amount": _round2(pending_auth_amount),
        "authorized_count": len(authorized_rows),
        "authorized_amount": _round2(authorized_amount),
        "pending_receipts_count": pending_receipts_count,
        "by_category": by_category,
        "top_vendors": top_vendors,
        "monthly_spend": monthly_spend,
        "daneel": daneel_payload,
        "tasks": tasks_payload,
    }


# ============================================================
# 2. GET /analytics/projects/{project_id}/cost-trends
# ============================================================

@router.get("/projects/{project_id}/cost-trends")
async def cost_trends(
    project_id: str,
    current_user: dict = Depends(get_current_user),
):
    """
    Monthly spend series with cumulative totals, suitable for
    line / area charts.
    """

    # Fetch authorized expenses (paginated)
    expenses = _paginated_fetch(
        "expenses_manual_COGS",
        "Amount, TxnDate",
        {"project": project_id, "auth_status": True},
        neq_filters={"status": "review"},
    )

    month_agg: dict[str, float] = defaultdict(float)
    for e in expenses:
        txn_date = e.get("TxnDate") or ""
        if len(txn_date) >= 7:
            month_key = txn_date[:7]
            month_agg[month_key] += _safe_float(e.get("Amount"))

    # Sort chronologically and compute cumulative
    sorted_months = sorted(month_agg.keys())
    monthly: list[dict] = []
    cumulative = 0.0
    for m in sorted_months:
        amt = _round2(month_agg[m])
        cumulative += amt
        monthly.append({
            "month": m,
            "amount": amt,
            "cumulative": _round2(cumulative),
        })

    return {
        "project_id": project_id,
        "monthly": monthly,
    }


# ============================================================
# 3. GET /analytics/projects/{project_id}/budget-vs-actual
# ============================================================

@router.get("/projects/{project_id}/budget-vs-actual")
async def budget_vs_actual(
    project_id: str,
    year: Optional[int] = Query(default=None, description="Budget year (defaults to current)"),
    current_user: dict = Depends(get_current_user),
):
    """
    Budget variance analysis by account with projection and EAC.
    """
    budget_year = year or date.today().year

    # --- Accounts catalog ---
    accounts_map: dict[str, str] = {}  # account_id -> Name
    try:
        acc_resp = supabase.table("accounts").select("account_id, Name").execute()
        for a in (acc_resp.data or []):
            accounts_map[str(a.get("account_id", ""))] = a.get("Name", "Unknown")
    except Exception as exc:
        logger.error("[analytics:bva] accounts fetch: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to fetch accounts")

    # --- Budgets for project + year ---
    budget_by_account: dict[str, float] = defaultdict(float)
    try:
        bud_resp = (
            supabase.table("budgets_qbo")
            .select("account_id, account_name, amount_sum")
            .eq("ngm_project_id", project_id)
            .eq("year", budget_year)
            .eq("active", True)
            .execute()
        )
        for b in (bud_resp.data or []):
            aid = str(b.get("account_id") or "")
            # Prefer account_name from budget row, fallback to accounts catalog
            aname = b.get("account_name") or accounts_map.get(aid, "Unknown")
            budget_by_account[f"{aid}||{aname}"] += _safe_float(b.get("amount_sum"))
    except Exception as exc:
        logger.error("[analytics:bva] budgets fetch: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to fetch budgets")

    # --- Authorized expenses (paginated) ---
    expenses = _paginated_fetch(
        "expenses_manual_COGS",
        "Amount, TxnDate, account_id",
        {"project": project_id, "auth_status": True},
        neq_filters={"status": "review"},
    )

    actual_by_account: dict[str, float] = defaultdict(float)
    all_dates: list[str] = []
    month_actual: dict[str, float] = defaultdict(float)
    for e in expenses:
        aid = str(e.get("account_id") or "")
        aname = accounts_map.get(aid, "Unknown")
        key = f"{aid}||{aname}"
        amt = _safe_float(e.get("Amount"))
        actual_by_account[key] += amt

        txn_date = e.get("TxnDate") or ""
        if txn_date:
            all_dates.append(txn_date)
        if len(txn_date) >= 7:
            month_actual[txn_date[:7]] += amt

    # --- Build by_account rows ---
    all_keys = set(list(budget_by_account.keys()) + list(actual_by_account.keys()))
    by_account: list[dict] = []

    for key in all_keys:
        parts = key.split("||", 1)
        aid = parts[0] if len(parts) > 1 else ""
        aname = parts[1] if len(parts) > 1 else key
        bgt = budget_by_account.get(key, 0.0)
        act = actual_by_account.get(key, 0.0)
        var = bgt - act
        var_pct = _round2((var / bgt * 100) if bgt else 0.0)

        by_account.append({
            "account_name": aname,
            "account_id": aid,
            "budget": _round2(bgt),
            "actual": _round2(act),
            "variance": _round2(var),
            "variance_pct": var_pct,
        })

    by_account.sort(key=lambda x: x["account_name"])

    total_budget = _round2(sum(r["budget"] for r in by_account))
    total_actual = _round2(sum(r["actual"] for r in by_account))
    total_variance = _round2(total_budget - total_actual)

    # --- Cumulative actual by month ---
    sorted_months = sorted(month_actual.keys())
    cumulative_actual: list[dict] = []
    cum = 0.0
    for m in sorted_months:
        cum += month_actual[m]
        cumulative_actual.append({"month": m, "cumulative": _round2(cum)})

    # --- Projection (linear distribution of budget) ---
    # Determine project timeline from expense dates
    projection: list[dict] = []
    if all_dates:
        all_dates_sorted = sorted(all_dates)
        first_date = all_dates_sorted[0][:7]   # YYYY-MM
        last_date = all_dates_sorted[-1][:7]

        # Generate month range from first to Dec of budget_year
        end_month = f"{budget_year}-12"
        month_cursor = first_date
        month_list: list[str] = []
        while month_cursor <= end_month:
            month_list.append(month_cursor)
            # Advance month
            y, m = int(month_cursor[:4]), int(month_cursor[5:7])
            m += 1
            if m > 12:
                m = 1
                y += 1
            month_cursor = f"{y:04d}-{m:02d}"

        total_months = len(month_list)
        if total_months > 0:
            monthly_budget_rate = total_budget / total_months
            proj_cum = 0.0
            for mo in month_list:
                proj_cum += monthly_budget_rate
                projection.append({
                    "month": mo,
                    "projected_cumulative": _round2(proj_cum),
                })

    # --- EAC (Estimate at Completion) ---
    # burn_rate = actual per month so far
    months_elapsed = len(sorted_months) if sorted_months else 1
    burn_rate = total_actual / max(months_elapsed, 1)

    # remaining months in the year (from now to Dec of budget_year)
    today = date.today()
    if today.year == budget_year:
        remaining_months = max(12 - today.month, 0)
    elif today.year < budget_year:
        remaining_months = 12
    else:
        remaining_months = 0

    estimated_at_completion = _round2(total_actual + burn_rate * remaining_months)
    estimated_variance = _round2(total_budget - estimated_at_completion)

    # Status
    if total_budget > 0:
        if estimated_at_completion <= total_budget * 1.05:
            status = "on_track"
        elif estimated_at_completion <= total_budget * 1.15:
            status = "at_risk"
        else:
            status = "over_budget"
    else:
        status = "on_track" if total_actual == 0 else "over_budget"

    return {
        "project_id": project_id,
        "budget_year": budget_year,
        "by_account": by_account,
        "total_budget": total_budget,
        "total_actual": total_actual,
        "total_variance": total_variance,
        "cumulative_actual": cumulative_actual,
        "projection": projection,
        "estimated_at_completion": estimated_at_completion,
        "estimated_variance": estimated_variance,
        "status": status,
    }


# ============================================================
# 3b. GET /analytics/projects/{project_id}/expense-timeline
# ============================================================

@router.get("/projects/{project_id}/expense-timeline")
async def expense_timeline(
    project_id: str,
    year: Optional[int] = Query(default=None, description="Budget year (defaults to current)"),
    current_user: dict = Depends(get_current_user),
):
    """
    Returns individual expense records enriched with AccountCategory,
    plus budget data grouped by category. Frontend aggregates by day/week/month.
    """
    budget_year = year or date.today().year

    # --- Accounts catalog with AccountCategory ---
    accounts_map: dict[str, dict] = {}  # account_id -> {name, category}
    try:
        acc_resp = supabase.table("accounts").select(
            "account_id, Name, AccountCategory"
        ).execute()
        for a in (acc_resp.data or []):
            aid = str(a.get("account_id", ""))
            accounts_map[aid] = {
                "name": a.get("Name", "Unknown"),
                "category": a.get("AccountCategory") or "Uncategorized",
            }
    except Exception as exc:
        logger.error("[analytics:expense-timeline] accounts fetch: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to fetch accounts")

    # --- Authorized expenses (paginated) ---
    raw_expenses = _paginated_fetch(
        "expenses_manual_COGS",
        "Amount, TxnDate, account_id",
        {"project": project_id, "auth_status": True},
        neq_filters={"status": "review"},
    )

    expenses: list[dict] = []
    all_dates: list[str] = []
    for e in raw_expenses:
        txn_date = e.get("TxnDate") or ""
        if not txn_date:
            continue
        aid = str(e.get("account_id") or "")
        acc_info = accounts_map.get(aid, {"name": "Unknown", "category": "Uncategorized"})
        amt = _round2(_safe_float(e.get("Amount")))
        expenses.append({
            "date": txn_date,
            "amount": amt,
            "account_id": aid,
            "account_name": acc_info["name"],
            "account_category": acc_info["category"],
        })
        all_dates.append(txn_date)

    # --- Date range ---
    date_range = {"first_date": None, "last_date": None}
    if all_dates:
        sorted_dates = sorted(all_dates)
        date_range["first_date"] = sorted_dates[0]
        date_range["last_date"] = sorted_dates[-1]

    # --- Budgets grouped by AccountCategory ---
    budget_by_cat: dict[str, dict] = defaultdict(
        lambda: {"accounts": [], "total_budget": 0.0}
    )
    total_budget = 0.0
    try:
        bud_resp = (
            supabase.table("budgets_qbo")
            .select("account_id, account_name, amount_sum")
            .eq("ngm_project_id", project_id)
            .eq("year", budget_year)
            .eq("active", True)
            .execute()
        )
        for b in (bud_resp.data or []):
            aid = str(b.get("account_id") or "")
            aname = b.get("account_name") or accounts_map.get(aid, {}).get("name", "Unknown")
            cat = accounts_map.get(aid, {}).get("category", "Uncategorized")
            amt = _round2(_safe_float(b.get("amount_sum")))
            budget_by_cat[cat]["accounts"].append({
                "account_id": aid,
                "account_name": aname,
                "budget": amt,
            })
            budget_by_cat[cat]["total_budget"] = _round2(
                budget_by_cat[cat]["total_budget"] + amt
            )
            total_budget += amt
    except Exception as exc:
        logger.error("[analytics:expense-timeline] budgets fetch: %s", exc)
        # Non-fatal: return expenses without budget data
        pass

    budget_categories = [
        {
            "account_category": cat,
            "accounts": info["accounts"],
            "total_budget": _round2(info["total_budget"]),
        }
        for cat, info in sorted(budget_by_cat.items())
    ]

    return {
        "project_id": project_id,
        "date_range": date_range,
        "expenses": expenses,
        "budget_by_category": budget_categories,
        "total_budget": _round2(total_budget),
    }


# ============================================================
# 4. GET /analytics/executive/kpis
# ============================================================

@router.get("/executive/kpis")
async def executive_kpis(current_user: dict = Depends(get_current_user)):
    """
    Multi-project KPI aggregation for the executive dashboard.
    Requires the `project_kpis` permission (can_view) on the caller's role.
    """

    # --- Permission check ---
    user_role_id = current_user.get("user_rol")
    if not user_role_id:
        raise HTTPException(status_code=403, detail="No role assigned to user")

    try:
        perm_resp = (
            supabase.table("role_permissions")
            .select("can_view")
            .eq("rol_id", user_role_id)
            .eq("module_key", "project_kpis")
            .eq("can_view", True)
            .limit(1)
            .execute()
        )
        if not (perm_resp.data):
            raise HTTPException(
                status_code=403,
                detail="You do not have permission to view executive KPIs",
            )
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("[analytics:executive_kpis] permission check: %s", exc)
        raise HTTPException(status_code=500, detail="Permission check failed")

    # --- Active projects ---
    active_projects: list[dict] = []
    try:
        proj_resp = (
            supabase.table("projects")
            .select("project_id, project_name, project_status(status)")
            .execute()
        )
        for p in (proj_resp.data or []):
            ps = p.get("project_status") or {}
            if isinstance(ps, list):
                ps = ps[0] if ps else {}
            if (ps.get("status") or "").lower() == "active":
                active_projects.append({
                    "project_id": str(p.get("project_id", "")),
                    "project_name": p.get("project_name", "Unknown"),
                })
    except Exception as exc:
        logger.error("[analytics:executive_kpis] projects fetch: %s", exc)

    # --- ALL authorized expenses (paginated) ---
    all_expenses = _paginated_fetch(
        "expenses_manual_COGS",
        "expense_id, Amount, project, vendor_id, TxnDate, auth_status, status",
        {"auth_status": True},
        neq_filters={"status": "review"},
    )

    # Group expenses by project
    expenses_by_project: dict[str, list[dict]] = defaultdict(list)
    for e in all_expenses:
        pid = str(e.get("project") or "")
        if pid:
            expenses_by_project[pid].append(e)

    # --- ALL budgets ---
    all_budgets: list[dict] = []
    try:
        bud_offset = 0
        while True:
            bud_resp = (
                supabase.table("budgets_qbo")
                .select("ngm_project_id, amount_sum")
                .eq("active", True)
                .range(bud_offset, bud_offset + _PAGE_SIZE - 1)
                .execute()
            )
            batch = bud_resp.data or []
            all_budgets.extend(batch)
            if len(batch) < _PAGE_SIZE:
                break
            bud_offset += _PAGE_SIZE
    except Exception as exc:
        logger.error("[analytics:executive_kpis] budgets fetch: %s", exc)

    budget_by_project: dict[str, float] = defaultdict(float)
    for b in all_budgets:
        pid = str(b.get("ngm_project_id") or "")
        if pid:
            budget_by_project[pid] += _safe_float(b.get("amount_sum"))

    # --- Pending receipts count by project ---
    pending_receipts_by_project: dict[str, int] = defaultdict(int)
    try:
        pr_offset = 0
        while True:
            pr_resp = (
                supabase.table("pending_receipts")
                .select("project_id")
                .is_("expense_id", "null")
                .range(pr_offset, pr_offset + _PAGE_SIZE - 1)
                .execute()
            )
            batch = pr_resp.data or []
            for r in batch:
                pid = str(r.get("project_id") or "")
                if pid:
                    pending_receipts_by_project[pid] += 1
            if len(batch) < _PAGE_SIZE:
                break
            pr_offset += _PAGE_SIZE
    except Exception as exc:
        logger.warning("[analytics:executive_kpis] pending_receipts: %s", exc)

    # --- Pending auth count by project (expenses with status=pending) ---
    pending_auth_by_project: dict[str, int] = defaultdict(int)
    try:
        pa_offset = 0
        while True:
            pa_resp = (
                supabase.table("expenses_manual_COGS")
                .select("project")
                .eq("status", "pending")
                .range(pa_offset, pa_offset + _PAGE_SIZE - 1)
                .execute()
            )
            batch = pa_resp.data or []
            for r in batch:
                pid = str(r.get("project") or "")
                if pid:
                    pending_auth_by_project[pid] += 1
            if len(batch) < _PAGE_SIZE:
                break
            pa_offset += _PAGE_SIZE
    except Exception as exc:
        logger.warning("[analytics:executive_kpis] pending_auth: %s", exc)

    # --- Tasks completion % per project ---
    tasks_completion_by_project: dict[str, float] = {}
    try:
        st_resp = supabase.table("tasks_status").select("task_status_id, task_status").execute()
        status_map = {
            s["task_status_id"]: (s.get("task_status") or "").lower()
            for s in (st_resp.data or [])
        }

        tasks_offset = 0
        all_tasks: list[dict] = []
        while True:
            t_resp = (
                supabase.table("tasks")
                .select("project_id, task_status")
                .range(tasks_offset, tasks_offset + _PAGE_SIZE - 1)
                .execute()
            )
            batch = t_resp.data or []
            all_tasks.extend(batch)
            if len(batch) < _PAGE_SIZE:
                break
            tasks_offset += _PAGE_SIZE

        # Group by project
        tasks_by_proj: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        for t in all_tasks:
            pid = str(t.get("project_id") or "")
            sname = status_map.get(t.get("task_status"), "other")
            tasks_by_proj[pid][sname] += 1

        for pid, counts in tasks_by_proj.items():
            total = sum(counts.values())
            completed = counts.get("done", 0) + counts.get("completed", 0)
            tasks_completion_by_project[pid] = _round2(
                (completed / total * 100) if total else 0.0
            )
    except Exception as exc:
        logger.warning("[analytics:executive_kpis] tasks: %s", exc)

    # --- Daneel auth rate (global) ---
    daneel_auth_rate = 0.0
    try:
        import json as _json
        dr_offset = 0
        total_processed = 0
        total_authorized = 0
        while True:
            dr_resp = (
                supabase.table("daneel_auth_reports")
                .select("summary")
                .range(dr_offset, dr_offset + _PAGE_SIZE - 1)
                .execute()
            )
            batch = dr_resp.data or []
            for r in batch:
                s = r.get("summary") or {}
                if isinstance(s, str):
                    try:
                        s = _json.loads(s)
                    except Exception:
                        s = {}
                total_processed += int(s.get("expenses_processed", 0))
                total_authorized += int(s.get("authorized", 0))
            if len(batch) < _PAGE_SIZE:
                break
            dr_offset += _PAGE_SIZE

        daneel_auth_rate = _round2(
            (total_authorized / total_processed * 100) if total_processed else 0.0
        )
    except Exception as exc:
        logger.warning("[analytics:executive_kpis] daneel: %s", exc)

    # --- Build per-project summaries ---
    total_spend = 0.0
    total_budget = 0.0
    margins: list[float] = []
    projects_list: list[dict] = []

    for proj in active_projects:
        pid = proj["project_id"]
        proj_expenses = expenses_by_project.get(pid, [])
        actual = sum(_safe_float(e.get("Amount")) for e in proj_expenses)
        budget = budget_by_project.get(pid, 0.0)
        burn_pct = _round2((actual / budget * 100) if budget > 0 else 0.0)

        if budget > 0:
            if burn_pct <= 85:
                status = "on_track"
            elif burn_pct <= 100:
                status = "at_risk"
            else:
                status = "over_budget"
        else:
            status = "on_track" if actual == 0 else "over_budget"

        margin = _round2(((budget - actual) / budget * 100) if budget > 0 else 0.0)
        margins.append(margin)

        total_spend += actual
        total_budget += budget

        projects_list.append({
            "project_id": pid,
            "project_name": proj["project_name"],
            "budget": _round2(budget),
            "actual": _round2(actual),
            "burn_pct": burn_pct,
            "status": status,
            "pending_auth": pending_auth_by_project.get(pid, 0),
            "pending_receipts": pending_receipts_by_project.get(pid, 0),
            "tasks_completion_pct": tasks_completion_by_project.get(pid, 0.0),
        })

    avg_margin_pct = _round2(
        (sum(margins) / len(margins)) if margins else 0.0
    )

    # --- Monthly spend total (all authorized expenses) ---
    month_spend_agg: dict[str, float] = defaultdict(float)
    for e in all_expenses:
        txn_date = e.get("TxnDate") or ""
        if len(txn_date) >= 7:
            month_spend_agg[txn_date[:7]] += _safe_float(e.get("Amount"))

    monthly_spend_total = sorted(
        [{"month": m, "amount": _round2(a)} for m, a in month_spend_agg.items()],
        key=lambda x: x["month"],
    )

    # --- Top vendors across all projects ---
    vendor_agg: dict[str, dict] = defaultdict(
        lambda: {"amount": 0.0, "projects": set()}
    )
    for e in all_expenses:
        vid = str(e.get("vendor_id") or "")
        if not vid:
            continue
        vendor_agg[vid]["amount"] += _safe_float(e.get("Amount"))
        pid = str(e.get("project") or "")
        if pid:
            vendor_agg[vid]["projects"].add(pid)

    # Resolve vendor names
    vendor_name_map: dict[str, str] = {}
    if vendor_agg:
        try:
            vn_resp = supabase.table("Vendors").select("id, vendor_name").execute()
            for v in (vn_resp.data or []):
                vendor_name_map[str(v.get("id", ""))] = v.get("vendor_name", "Unknown")
        except Exception as exc:
            logger.warning("[analytics:executive_kpis] vendors fetch: %s", exc)

    top_vendors = sorted(
        [
            {
                "vendor_name": vendor_name_map.get(vid, "Unknown Vendor"),
                "amount": _round2(data["amount"]),
                "project_count": len(data["projects"]),
            }
            for vid, data in vendor_agg.items()
        ],
        key=lambda x: x["amount"],
        reverse=True,
    )[:10]

    return {
        "active_projects": len(active_projects),
        "total_spend": _round2(total_spend),
        "total_budget": _round2(total_budget),
        "avg_margin_pct": avg_margin_pct,
        "daneel_auth_rate": daneel_auth_rate,
        "projects": projects_list,
        "monthly_spend_total": monthly_spend_total,
        "top_vendors": top_vendors,
    }


# ============================================================
# 5. GET /analytics/vendors/summary
# ============================================================

@router.get("/vendors/summary")
async def vendors_summary(current_user: dict = Depends(get_current_user)):
    """
    Enriched vendor listing with spend metrics, transaction counts,
    and concentration percentages across all projects.
    """

    # --- Vendors catalog ---
    vendors: list[dict] = []
    try:
        v_resp = supabase.table("Vendors").select("id, vendor_name").execute()
        vendors = v_resp.data or []
    except Exception as exc:
        logger.error("[analytics:vendors_summary] vendors fetch: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to fetch vendors")

    vendor_map: dict[str, str] = {}
    for v in vendors:
        vendor_map[str(v.get("id", ""))] = v.get("vendor_name", "Unknown")

    # --- ALL authorized expenses (paginated) ---
    all_expenses = _paginated_fetch(
        "expenses_manual_COGS",
        "Amount, vendor_id, project, TxnDate",
        {"auth_status": True},
        neq_filters={"status": "review"},
    )

    # --- Aggregate by vendor ---
    vendor_agg: dict[str, dict] = defaultdict(
        lambda: {
            "amount": 0.0,
            "count": 0,
            "projects": set(),
            "dates": [],
        }
    )
    for e in all_expenses:
        vid = str(e.get("vendor_id") or "")
        if not vid:
            continue
        amt = _safe_float(e.get("Amount"))
        vendor_agg[vid]["amount"] += amt
        vendor_agg[vid]["count"] += 1
        pid = str(e.get("project") or "")
        if pid:
            vendor_agg[vid]["projects"].add(pid)
        txn_date = e.get("TxnDate") or ""
        if txn_date:
            vendor_agg[vid]["dates"].append(txn_date)

    total_spend_all = sum(d["amount"] for d in vendor_agg.values())

    # --- Build response list ---
    vendors_list: list[dict] = []
    for vid, data in vendor_agg.items():
        vname = vendor_map.get(vid, "Unknown Vendor")
        total_amount = data["amount"]
        txn_count = data["count"]
        dates = sorted(data["dates"])
        avg_txn = _round2(total_amount / txn_count) if txn_count else 0.0
        concentration = _round2(
            (total_amount / total_spend_all * 100) if total_spend_all > 0 else 0.0
        )

        vendors_list.append({
            "id": vid,
            "vendor_name": vname,
            "total_amount": _round2(total_amount),
            "txn_count": txn_count,
            "project_count": len(data["projects"]),
            "first_txn_date": dates[0] if dates else None,
            "last_txn_date": dates[-1] if dates else None,
            "avg_txn_amount": avg_txn,
            "concentration_pct": concentration,
        })

    # Sort by total_amount descending
    vendors_list.sort(key=lambda x: x["total_amount"], reverse=True)

    return {
        "vendors": vendors_list,
        "total_spend_all_vendors": _round2(total_spend_all),
    }


# ============================================================
# 6. GET /analytics/vendors/{vendor_id}/scorecard
# ============================================================

@router.get("/vendors/{vendor_id}/scorecard")
async def vendor_scorecard(
    vendor_id: str,
    current_user: dict = Depends(get_current_user),
):
    """
    Detailed vendor analytics scorecard: spend trends, project breakdown,
    category analysis with price-change detection, and concentration risk.
    """

    # --- Vendor name ---
    vendor_name = "Unknown Vendor"
    try:
        vn_resp = (
            supabase.table("Vendors")
            .select("vendor_name")
            .eq("vendor_id", vendor_id)
            .limit(1)
            .execute()
        )
        if vn_resp.data:
            vendor_name = vn_resp.data[0].get("vendor_name", vendor_name)
        else:
            raise HTTPException(status_code=404, detail="Vendor not found")
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("[analytics:vendor_scorecard] vendor lookup: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to fetch vendor")

    # --- Vendor expenses (paginated) ---
    vendor_expenses = _paginated_fetch(
        "expenses_manual_COGS",
        "expense_id, Amount, project, TxnDate, txn_type_id, auth_status",
        {"vendor_id": vendor_id, "auth_status": True},
        neq_filters={"status": "review"},
    )

    total_amount = sum(_safe_float(e.get("Amount")) for e in vendor_expenses)
    txn_count = len(vendor_expenses)
    avg_txn_amount = _round2(total_amount / txn_count) if txn_count else 0.0

    # --- Monthly spend ---
    month_agg: dict[str, float] = defaultdict(float)
    all_dates: list[str] = []
    for e in vendor_expenses:
        txn_date = e.get("TxnDate") or ""
        if txn_date:
            all_dates.append(txn_date)
        if len(txn_date) >= 7:
            month_agg[txn_date[:7]] += _safe_float(e.get("Amount"))

    monthly_spend = sorted(
        [{"month": m, "amount": _round2(a)} for m, a in month_agg.items()],
        key=lambda x: x["month"],
    )

    first_txn = min(all_dates) if all_dates else None

    # --- Active projects for this vendor ---
    project_ids = {str(e.get("project") or "") for e in vendor_expenses if e.get("project")}
    active_projects = len(project_ids)

    # --- By project (resolve names) ---
    project_name_map: dict[str, str] = {}
    if project_ids:
        try:
            pn_resp = supabase.table("projects").select("project_id, project_name").execute()
            for p in (pn_resp.data or []):
                project_name_map[str(p.get("project_id", ""))] = p.get("project_name", "Unknown")
        except Exception as exc:
            logger.warning("[analytics:vendor_scorecard] projects fetch: %s", exc)

    proj_agg: dict[str, dict] = defaultdict(lambda: {"amount": 0.0, "count": 0})
    for e in vendor_expenses:
        pid = str(e.get("project") or "")
        if pid:
            proj_agg[pid]["amount"] += _safe_float(e.get("Amount"))
            proj_agg[pid]["count"] += 1

    by_project = sorted(
        [
            {
                "project_name": project_name_map.get(pid, "Unknown Project"),
                "amount": _round2(data["amount"]),
                "count": data["count"],
            }
            for pid, data in proj_agg.items()
        ],
        key=lambda x: x["amount"],
        reverse=True,
    )

    # --- By category (txn_type) with price trends ---
    txn_type_map: dict[str, str] = {}
    try:
        tt_resp = supabase.table("txn_types").select("txn_type_id, txn_type_name").execute()
        for t in (tt_resp.data or []):
            txn_type_map[str(t.get("txn_type_id", ""))] = t.get("txn_type_name", "Unknown")
    except Exception as exc:
        logger.warning("[analytics:vendor_scorecard] txn_types fetch: %s", exc)

    # Determine time windows for price trend analysis
    today = date.today()
    # Current window: last 3 months
    current_start = date(today.year, today.month, 1)
    for _ in range(2):
        # Go back 2 months from current month start
        if current_start.month <= 1:
            current_start = date(current_start.year - 1, 12, 1)
        else:
            current_start = date(current_start.year, current_start.month - 1, 1)
    current_start_str = current_start.strftime("%Y-%m-%d")

    # Old window: 3 months before current window
    old_end = current_start
    old_start = date(old_end.year, old_end.month, 1)
    for _ in range(3):
        if old_start.month <= 1:
            old_start = date(old_start.year - 1, 12, 1)
        else:
            old_start = date(old_start.year, old_start.month - 1, 1)
    old_start_str = old_start.strftime("%Y-%m-%d")
    old_end_str = old_end.strftime("%Y-%m-%d")

    # Aggregate by txn_type — totals and per-window amounts
    cat_agg: dict[str, dict] = defaultdict(
        lambda: {
            "total": 0.0,
            "count": 0,
            "old_amounts": [],
            "current_amounts": [],
        }
    )
    for e in vendor_expenses:
        tid = str(e.get("txn_type_id") or "")
        amt = _safe_float(e.get("Amount"))
        cat_agg[tid]["total"] += amt
        cat_agg[tid]["count"] += 1

        txn_date = e.get("TxnDate") or ""
        if txn_date >= current_start_str:
            cat_agg[tid]["current_amounts"].append(amt)
        elif old_start_str <= txn_date < old_end_str:
            cat_agg[tid]["old_amounts"].append(amt)

    by_category: list[dict] = []
    for tid, data in cat_agg.items():
        tname = txn_type_map.get(tid, "Uncategorized")
        old_amts = data["old_amounts"]
        cur_amts = data["current_amounts"]
        avg_old = _round2(sum(old_amts) / len(old_amts)) if old_amts else 0.0
        avg_cur = _round2(sum(cur_amts) / len(cur_amts)) if cur_amts else 0.0
        change_pct = _round2(
            ((avg_cur - avg_old) / avg_old * 100) if avg_old > 0 else 0.0
        )

        by_category.append({
            "txn_type": tname,
            "total": _round2(data["total"]),
            "avg_unit_price_3mo_ago": avg_old,
            "avg_unit_price_current": avg_cur,
            "price_change_pct": change_pct,
        })

    by_category.sort(key=lambda x: x["total"], reverse=True)

    # --- Concentration ---
    # Get total ALL expenses spend
    total_all_spend = 0.0
    try:
        all_auth_expenses = _paginated_fetch(
            "expenses_manual_COGS",
            "Amount",
            {"auth_status": True},
            neq_filters={"status": "review"},
        )
        total_all_spend = sum(_safe_float(e.get("Amount")) for e in all_auth_expenses)
    except Exception as exc:
        logger.warning("[analytics:vendor_scorecard] total spend: %s", exc)

    pct_of_total = _round2(
        (total_amount / total_all_spend * 100) if total_all_spend > 0 else 0.0
    )
    if pct_of_total < 20:
        risk_level = "low"
    elif pct_of_total <= 40:
        risk_level = "medium"
    else:
        risk_level = "high"

    return {
        "vendor_id": vendor_id,
        "vendor_name": vendor_name,
        "total_amount": _round2(total_amount),
        "txn_count": txn_count,
        "avg_txn_amount": avg_txn_amount,
        "active_projects": active_projects,
        "first_txn": first_txn,
        "monthly_spend": monthly_spend,
        "by_project": by_project,
        "by_category": by_category,
        "concentration": {
            "pct_of_total_spend": pct_of_total,
            "risk_level": risk_level,
        },
    }


# ============================================================
# 7. GET /analytics/expense-intelligence
# ============================================================

@router.get("/expense-intelligence")
async def expense_intelligence(
    project_id: Optional[str] = Query(default=None, description="Filter expenses to a specific project"),
    current_user: dict = Depends(get_current_user),
):
    """
    Expense intelligence dashboard: monthly spend trends, category/vendor/
    payment-method breakdowns, totals, and statistical anomaly detection.
    """
    try:
        # --- Fetch authorized expenses (paginated) ---
        filters: dict = {"status": "auth"}
        if project_id:
            filters["project"] = project_id

        all_expenses = _paginated_fetch(
            "expenses_manual_COGS",
            "expense_id, TxnDate, Amount, vendor_id, txn_type, payment_type, status, account_id, LineDescription",
            filters,
        )

        # --- Lookup tables ---
        vendor_map: dict[str, str] = {}
        try:
            v_resp = supabase.table("Vendors").select("id, vendor_name").execute()
            for v in (v_resp.data or []):
                vendor_map[str(v.get("id", ""))] = v.get("vendor_name", "Unknown")
        except Exception as exc:
            logger.warning("[analytics:expense-intelligence] vendors fetch: %s", exc)

        txn_type_map: dict[str, str] = {}
        try:
            tt_resp = supabase.table("txn_types").select("TnxType_id, TnxType_name").execute()
            for t in (tt_resp.data or []):
                txn_type_map[str(t.get("TnxType_id", ""))] = t.get("TnxType_name", "Unknown")
        except Exception as exc:
            logger.warning("[analytics:expense-intelligence] txn_types fetch: %s", exc)

        payment_map: dict[str, str] = {}
        try:
            pm_resp = supabase.table("paymet_methods").select("id, payment_method_name").execute()
            for p in (pm_resp.data or []):
                payment_map[str(p.get("id", ""))] = p.get("payment_method_name", "Unknown")
        except Exception as exc:
            logger.warning("[analytics:expense-intelligence] payment_methods fetch: %s", exc)

        # --- Monthly spend (last 12 months) ---
        month_agg: dict[str, dict] = defaultdict(lambda: {"amount": 0.0, "count": 0})
        for e in all_expenses:
            txn_date = e.get("TxnDate") or ""
            if len(txn_date) >= 7:
                month_key = txn_date[:7]
                month_agg[month_key]["amount"] += _safe_float(e.get("Amount"))
                month_agg[month_key]["count"] += 1

        # Determine last 12 months from today
        today = date.today()
        last_12: list[str] = []
        y, m = today.year, today.month
        for _ in range(12):
            last_12.append(f"{y:04d}-{m:02d}")
            m -= 1
            if m < 1:
                m = 12
                y -= 1
        last_12.reverse()

        monthly_spend = [
            {
                "month": mo,
                "amount": _round2(month_agg[mo]["amount"]) if mo in month_agg else 0.0,
                "count": month_agg[mo]["count"] if mo in month_agg else 0,
            }
            for mo in last_12
        ]

        # --- By category (txn_type) ---
        cat_agg: dict[str, dict] = defaultdict(lambda: {"amount": 0.0, "count": 0})
        for e in all_expenses:
            tid = str(e.get("txn_type") or "")
            cat_name = txn_type_map.get(tid, "Uncategorized")
            cat_agg[cat_name]["amount"] += _safe_float(e.get("Amount"))
            cat_agg[cat_name]["count"] += 1

        total_cat_amount = sum(v["amount"] for v in cat_agg.values())
        by_category = sorted(
            [
                {
                    "category": k,
                    "amount": _round2(v["amount"]),
                    "count": v["count"],
                    "pct": _round2((v["amount"] / total_cat_amount * 100) if total_cat_amount > 0 else 0.0),
                }
                for k, v in cat_agg.items()
            ],
            key=lambda x: x["amount"],
            reverse=True,
        )

        # --- By vendor (top 15) ---
        vend_agg: dict[str, dict] = defaultdict(lambda: {"amount": 0.0, "count": 0})
        for e in all_expenses:
            vid = str(e.get("vendor_id") or "")
            if not vid:
                continue
            vname = vendor_map.get(vid, "Unknown Vendor")
            vend_agg[vname]["amount"] += _safe_float(e.get("Amount"))
            vend_agg[vname]["count"] += 1

        by_vendor = sorted(
            [
                {"vendor": k, "amount": _round2(v["amount"]), "count": v["count"]}
                for k, v in vend_agg.items()
            ],
            key=lambda x: x["amount"],
            reverse=True,
        )[:15]

        # --- By payment method ---
        pay_agg: dict[str, dict] = defaultdict(lambda: {"amount": 0.0, "count": 0})
        for e in all_expenses:
            pid = str(e.get("payment_type") or "")
            pname = payment_map.get(pid, "Unknown") if pid else "Unknown"
            pay_agg[pname]["amount"] += _safe_float(e.get("Amount"))
            pay_agg[pname]["count"] += 1

        by_payment_method = sorted(
            [
                {"method": k, "amount": _round2(v["amount"]), "count": v["count"]}
                for k, v in pay_agg.items()
            ],
            key=lambda x: x["amount"],
            reverse=True,
        )

        # --- Totals ---
        total_amount = sum(_safe_float(e.get("Amount")) for e in all_expenses)
        total_count = len(all_expenses)
        avg_per_expense = _round2(total_amount / total_count) if total_count > 0 else 0.0

        totals = {
            "total_amount": _round2(total_amount),
            "total_count": total_count,
            "avg_per_expense": avg_per_expense,
        }

        # --- Anomaly detection (z-score > 2) ---
        amounts = [_safe_float(e.get("Amount")) for e in all_expenses]
        top_anomalies: list[dict] = []

        if len(amounts) >= 2:
            mean_amt = sum(amounts) / len(amounts)
            variance = sum((a - mean_amt) ** 2 for a in amounts) / len(amounts)
            stddev_amt = math.sqrt(variance) if variance > 0 else 0.0
            threshold = mean_amt + 2 * stddev_amt

            if stddev_amt > 0:
                anomalies: list[dict] = []
                for e in all_expenses:
                    amt = _safe_float(e.get("Amount"))
                    if amt > threshold:
                        z = _round2((amt - mean_amt) / stddev_amt)
                        vid = str(e.get("vendor_id") or "")
                        anomalies.append({
                            "expense_id": e.get("expense_id"),
                            "amount": _round2(amt),
                            "vendor": vendor_map.get(vid, "Unknown Vendor"),
                            "date": e.get("TxnDate"),
                            "description": e.get("LineDescription") or "",
                            "z_score": z,
                        })

                anomalies.sort(key=lambda x: x["z_score"], reverse=True)
                top_anomalies = anomalies[:10]

        return {
            "monthly_spend": monthly_spend,
            "by_category": by_category,
            "by_vendor": by_vendor,
            "by_payment_method": by_payment_method,
            "totals": totals,
            "top_anomalies": top_anomalies,
        }

    except HTTPException:
        raise
    except Exception as exc:
        logger.error("[analytics:expense-intelligence] %s", exc)
        raise HTTPException(status_code=500, detail="Failed to compute expense intelligence")


# ============================================================
# 8. GET /analytics/budget-health
# ============================================================

@router.get("/budget-health")
async def budget_health(
    project_id: Optional[str] = Query(default=None, description="Filter to a specific project"),
    year: Optional[int] = Query(default=None, description="Filter to a specific budget year"),
    current_user: dict = Depends(get_current_user),
):
    """
    Budget health analysis across projects: per-project budget vs actual,
    burn rates, account-level variance, and overall summary.
    """
    try:
        # --- Active budgets ---
        bud_query = (
            supabase.table("budgets_qbo")
            .select("id, budget_name, year, amount_sum, account_name, ngm_project_id")
            .eq("active", True)
        )
        if project_id:
            bud_query = bud_query.eq("ngm_project_id", project_id)
        if year:
            bud_query = bud_query.eq("year", year)

        budgets: list[dict] = []
        offset = 0
        while True:
            batch = bud_query.range(offset, offset + _PAGE_SIZE - 1).execute().data or []
            budgets.extend(batch)
            if len(batch) < _PAGE_SIZE:
                break
            offset += _PAGE_SIZE
            # Rebuild query for next page (PostgREST requires fresh range)
            bud_query = (
                supabase.table("budgets_qbo")
                .select("id, budget_name, year, amount_sum, account_name, ngm_project_id")
                .eq("active", True)
            )
            if project_id:
                bud_query = bud_query.eq("ngm_project_id", project_id)
            if year:
                bud_query = bud_query.eq("year", year)

        # --- Projects for names ---
        project_map: dict[str, str] = {}
        try:
            proj_resp = supabase.table("projects").select("project_id, project_name").execute()
            for p in (proj_resp.data or []):
                project_map[str(p.get("project_id", ""))] = p.get("project_name", "Unknown")
        except Exception as exc:
            logger.warning("[analytics:budget-health] projects fetch: %s", exc)

        # --- Authorized expenses (paginated) ---
        expense_filters: dict = {"status": "auth"}
        if project_id:
            expense_filters["project"] = project_id

        all_expenses = _paginated_fetch(
            "expenses_manual_COGS",
            "project, Amount, TxnDate, account_id",
            expense_filters,
        )

        # Group expenses by project
        expenses_by_project: dict[str, list[dict]] = defaultdict(list)
        for e in all_expenses:
            pid = str(e.get("project") or "")
            if pid:
                expenses_by_project[pid].append(e)

        # --- Accounts lookup for by_account matching ---
        accounts_map: dict[str, str] = {}  # account_id -> Name
        try:
            acc_resp = supabase.table("accounts").select("account_id, Name").execute()
            for a in (acc_resp.data or []):
                accounts_map[str(a.get("account_id", ""))] = a.get("Name", "Unknown")
        except Exception as exc:
            logger.warning("[analytics:budget-health] accounts fetch: %s", exc)

        # --- Budget aggregation by project ---
        budget_by_project: dict[str, float] = defaultdict(float)
        for b in budgets:
            pid = str(b.get("ngm_project_id") or "")
            if pid:
                budget_by_project[pid] += _safe_float(b.get("amount_sum"))

        # --- Per-project results ---
        projects_list: list[dict] = []
        total_budget = 0.0
        total_spent = 0.0
        projects_over_budget = 0
        projects_healthy = 0
        projects_warning = 0

        # Collect all project IDs referenced in budgets or expenses
        all_pids = set(budget_by_project.keys()) | set(expenses_by_project.keys())
        if project_id:
            all_pids = {project_id}

        for pid in all_pids:
            bgt = budget_by_project.get(pid, 0.0)
            proj_expenses = expenses_by_project.get(pid, [])
            actual = sum(_safe_float(e.get("Amount")) for e in proj_expenses)

            variance = bgt - actual
            variance_pct = _round2((variance / bgt * 100) if bgt > 0 else 0.0)

            # Burn rate: actual / months elapsed
            dates = [e.get("TxnDate") or "" for e in proj_expenses if e.get("TxnDate")]
            if dates:
                earliest = min(dates)
                try:
                    earliest_date = datetime.strptime(earliest[:10], "%Y-%m-%d").date()
                    days_elapsed = (date.today() - earliest_date).days
                    months_elapsed = max(days_elapsed / 30.0, 1.0)
                except (ValueError, TypeError):
                    months_elapsed = 1.0
            else:
                months_elapsed = 1.0

            burn_rate = _round2(actual / months_elapsed)

            # Health classification
            if bgt > 0:
                if variance_pct > 20:
                    health = "healthy"
                elif variance_pct >= 0:
                    health = "warning"
                else:
                    health = "critical"
            else:
                health = "critical" if actual > 0 else "healthy"

            if health == "healthy":
                projects_healthy += 1
            elif health == "warning":
                projects_warning += 1
            else:
                projects_over_budget += 1

            total_budget += bgt
            total_spent += actual

            spent_pct = _round2((actual / bgt * 100) if bgt > 0 else 0.0)

            projects_list.append({
                "project_id": pid,
                "project_name": project_map.get(pid, "Unknown Project"),
                "budget_total": _round2(bgt),
                "actual_spent": _round2(actual),
                "spent_pct": spent_pct,
                "variance": _round2(variance),
                "variance_pct": variance_pct,
                "burn_rate_monthly": burn_rate,
                "health": health,
            })

        projects_list.sort(key=lambda x: x["variance_pct"])

        # --- By account ---
        # Budget side: group by account_name
        budget_by_account: dict[str, float] = defaultdict(float)
        for b in budgets:
            aname = b.get("account_name") or "Unknown"
            budget_by_account[aname] += _safe_float(b.get("amount_sum"))

        # Expense side: group by account_id -> account_name
        actual_by_account: dict[str, float] = defaultdict(float)
        for e in all_expenses:
            aid = str(e.get("account_id") or "")
            aname = accounts_map.get(aid, "Unknown")
            actual_by_account[aname] += _safe_float(e.get("Amount"))

        all_account_names = set(budget_by_account.keys()) | set(actual_by_account.keys())
        by_account: list[dict] = []
        for aname in all_account_names:
            bgt = budget_by_account.get(aname, 0.0)
            act = actual_by_account.get(aname, 0.0)
            var = bgt - act
            var_pct = _round2((var / bgt * 100) if bgt > 0 else 0.0)
            by_account.append({
                "account_name": aname,
                "budgeted": _round2(bgt),
                "actual": _round2(act),
                "variance": _round2(var),
                "variance_pct": var_pct,
            })

        by_account.sort(key=lambda x: x["variance_pct"])

        # --- Summary ---
        overall_variance_pct = _round2(
            ((total_budget - total_spent) / total_budget * 100) if total_budget > 0 else 0.0
        )

        summary = {
            "total_budget": _round2(total_budget),
            "total_spent": _round2(total_spent),
            "overall_variance_pct": overall_variance_pct,
            "projects_over_budget": projects_over_budget,
            "projects_healthy": projects_healthy,
            "projects_warning": projects_warning,
        }

        return {
            "projects": projects_list,
            "by_account": by_account,
            "summary": summary,
        }

    except HTTPException:
        raise
    except Exception as exc:
        logger.error("[analytics:budget-health] %s", exc)
        raise HTTPException(status_code=500, detail="Failed to compute budget health")


# ============================================================
# 9. GET /analytics/project-scorecard
# ============================================================

@router.get("/project-scorecard")
async def project_scorecard(
    current_user: dict = Depends(get_current_user),
):
    """
    Cross-project scorecard: budget health, timeline progress, milestone
    status, and composite health score (0-10) for every project.
    """
    try:
        # --- All projects with status ---
        projects_raw: list[dict] = []
        try:
            proj_resp = (
                supabase.table("projects")
                .select("project_id, project_name, project_status(status)")
                .execute()
            )
            projects_raw = proj_resp.data or []
        except Exception as exc:
            logger.error("[analytics:project-scorecard] projects fetch: %s", exc)
            raise HTTPException(status_code=500, detail="Failed to fetch projects")

        # --- All active budgets (paginated) ---
        all_budgets: list[dict] = []
        bud_offset = 0
        while True:
            try:
                bud_resp = (
                    supabase.table("budgets_qbo")
                    .select("ngm_project_id, amount_sum")
                    .eq("active", True)
                    .range(bud_offset, bud_offset + _PAGE_SIZE - 1)
                    .execute()
                )
                batch = bud_resp.data or []
            except Exception as exc:
                logger.error("[analytics:project-scorecard] budgets fetch offset=%d: %s", bud_offset, exc)
                break
            all_budgets.extend(batch)
            if len(batch) < _PAGE_SIZE:
                break
            bud_offset += _PAGE_SIZE

        budget_by_project: dict[str, float] = defaultdict(float)
        for b in all_budgets:
            pid = str(b.get("ngm_project_id") or "")
            if pid:
                budget_by_project[pid] += _safe_float(b.get("amount_sum"))

        # --- All authorized expenses (paginated) ---
        all_expenses = _paginated_fetch(
            "expenses_manual_COGS",
            "project, Amount",
            {"status": "auth"},
        )

        spent_by_project: dict[str, float] = defaultdict(float)
        for e in all_expenses:
            pid = str(e.get("project") or "")
            if pid:
                spent_by_project[pid] += _safe_float(e.get("Amount"))

        # --- All phases ---
        all_phases: list[dict] = []
        try:
            ph_offset = 0
            while True:
                ph_resp = (
                    supabase.table("project_phases")
                    .select("project_id, status, progress_pct")
                    .range(ph_offset, ph_offset + _PAGE_SIZE - 1)
                    .execute()
                )
                batch = ph_resp.data or []
                all_phases.extend(batch)
                if len(batch) < _PAGE_SIZE:
                    break
                ph_offset += _PAGE_SIZE
        except Exception as exc:
            logger.warning("[analytics:project-scorecard] phases fetch: %s", exc)

        phases_by_project: dict[str, list[dict]] = defaultdict(list)
        for ph in all_phases:
            pid = str(ph.get("project_id") or "")
            if pid:
                phases_by_project[pid].append(ph)

        # --- All milestones ---
        all_milestones: list[dict] = []
        try:
            ms_offset = 0
            while True:
                ms_resp = (
                    supabase.table("project_milestones")
                    .select("project_id, status, due_date")
                    .range(ms_offset, ms_offset + _PAGE_SIZE - 1)
                    .execute()
                )
                batch = ms_resp.data or []
                all_milestones.extend(batch)
                if len(batch) < _PAGE_SIZE:
                    break
                ms_offset += _PAGE_SIZE
        except Exception as exc:
            logger.warning("[analytics:project-scorecard] milestones fetch: %s", exc)

        milestones_by_project: dict[str, list[dict]] = defaultdict(list)
        for ms in all_milestones:
            pid = str(ms.get("project_id") or "")
            if pid:
                milestones_by_project[pid].append(ms)

        # --- Aggregate per project ---
        projects_list: list[dict] = []
        total_budget_all = 0.0
        total_spent_all = 0.0
        timeline_pcts: list[float] = []
        at_risk_count = 0

        for proj in projects_raw:
            pid = str(proj.get("project_id", ""))
            pname = proj.get("project_name", "Unknown")

            # Status name
            ps = proj.get("project_status") or {}
            if isinstance(ps, list):
                ps = ps[0] if ps else {}
            status_name = ps.get("status", "Unknown") if isinstance(ps, dict) else "Unknown"

            budget_total = budget_by_project.get(pid, 0.0)
            spent = spent_by_project.get(pid, 0.0)
            spent_pct = _round2((spent / budget_total * 100) if budget_total > 0 else 0.0)

            # Budget health (same as endpoint 2)
            if budget_total > 0:
                variance_pct = (budget_total - spent) / budget_total * 100
                if variance_pct > 20:
                    budget_health_label = "healthy"
                elif variance_pct >= 0:
                    budget_health_label = "warning"
                else:
                    budget_health_label = "critical"
            else:
                budget_health_label = "critical" if spent > 0 else "healthy"

            # Phases
            proj_phases = phases_by_project.get(pid, [])
            phases_total = len(proj_phases)
            phases_completed = sum(
                1 for ph in proj_phases
                if (ph.get("status") or "").lower() == "completed"
            )
            if phases_total > 0:
                progress_values = [float(ph.get("progress_pct") or 0) for ph in proj_phases]
                timeline_pct = _round2(sum(progress_values) / phases_total)
            else:
                timeline_pct = 0.0

            # Milestones
            proj_milestones = milestones_by_project.get(pid, [])
            milestones_total = len(proj_milestones)
            milestones_overdue = sum(
                1 for ms in proj_milestones
                if (ms.get("status") or "").lower() == "overdue"
            )

            # --- Health score (0-10 composite) ---
            # Budget component (0-4)
            if budget_total <= 0:
                budget_score = 4.0 if spent <= 0 else 0.0
            elif spent_pct < 80:
                budget_score = 4.0
            elif spent_pct < 90:
                budget_score = 3.0
            elif spent_pct < 100:
                budget_score = 2.0
            else:
                budget_score = 0.0

            # Timeline component (0-3)
            if phases_total == 0:
                timeline_score = 3.0
            elif timeline_pct >= 50:
                timeline_score = 3.0
            elif timeline_pct >= 25:
                timeline_score = 2.0
            elif timeline_pct > 0:
                timeline_score = 1.0
            else:
                timeline_score = 0.0

            # Milestone component (0-3)
            if milestones_overdue == 0:
                milestone_score = 3.0
            elif milestones_overdue <= 1:
                milestone_score = 2.0
            elif milestones_overdue <= 3:
                milestone_score = 1.0
            else:
                milestone_score = 0.0

            health_score = _round2(budget_score + timeline_score + milestone_score)

            if health_score < 5:
                at_risk_count += 1

            total_budget_all += budget_total
            total_spent_all += spent
            timeline_pcts.append(timeline_pct)

            projects_list.append({
                "project_id": pid,
                "project_name": pname,
                "status": status_name,
                "budget_total": _round2(budget_total),
                "spent": _round2(spent),
                "spent_pct": spent_pct,
                "budget_health": budget_health_label,
                "phases_total": phases_total,
                "phases_completed": phases_completed,
                "timeline_pct": timeline_pct,
                "milestones_total": milestones_total,
                "milestones_overdue": milestones_overdue,
                "health_score": health_score,
            })

        # Sort by health_score ascending (worst first)
        projects_list.sort(key=lambda x: x["health_score"])

        avg_timeline = _round2(
            (sum(timeline_pcts) / len(timeline_pcts)) if timeline_pcts else 0.0
        )

        summary = {
            "total_projects": len(projects_raw),
            "total_budget": _round2(total_budget_all),
            "total_spent": _round2(total_spent_all),
            "avg_timeline_pct": avg_timeline,
            "projects_at_risk": at_risk_count,
        }

        return {
            "projects": projects_list,
            "summary": summary,
        }

    except HTTPException:
        raise
    except Exception as exc:
        logger.error("[analytics:project-scorecard] %s", exc)
        raise HTTPException(status_code=500, detail="Failed to compute project scorecard")
