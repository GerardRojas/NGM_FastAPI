# api/routers/reporting.py
# ================================
# Reporting endpoints — canonical Budget vs Actuals / P&L COGS computation.
# ================================
# These go through the unified engine (services/reporting/engine.py), the SAME
# engine Art's PDF handlers use, so every surface reconciles to the same numbers
# and classification. /bva returns the computed report as JSON (single source of
# truth the web pages can adopt); /pnl additionally renders a PDF to the Vault.

import logging
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional

logger = logging.getLogger(__name__)

from api.supabase_client import supabase

# Shared data fetchers + PDF generators (one source of truth).
from services.arturito.handlers.bva_handler import (
    fetch_budgets,
    fetch_expenses,
    fetch_accounts,
    fetch_account_overlay,
    fetch_company_name,
    generate_and_upload_pdf,
)
from services.arturito.handlers.pnl_handler import (
    generate_and_upload_pnl_pdf,
    REPORTLAB_AVAILABLE,
)
from services.reporting.engine import build_report, fetch_category_tree

router = APIRouter(prefix="/reports", tags=["Reporting"])


# ================================
# Models
# ================================

class BvaReportRequest(BaseModel):
    project_id: str
    company_id: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    generate_pdf: bool = False


class PnlReportRequest(BaseModel):
    project_id: str
    company_id: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    generate_pdf: bool = False


# ================================
# Helpers
# ================================

def _parse_date(value: str):
    from datetime import datetime as dt
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%Y-%m-%dT%H:%M:%S"):
        try:
            return dt.strptime(value, fmt)
        except ValueError:
            continue
    return None


def _filter_by_date(expenses, start_date: Optional[str], end_date: Optional[str]):
    """Keep expenses whose TxnDate falls in [start, end]. Undated rows are kept."""
    if not start_date and not end_date:
        return expenses
    start = _parse_date(start_date) if start_date else None
    end = _parse_date(end_date) if end_date else None

    def in_range(expense):
        date_str = expense.get("TxnDate") or expense.get("Date") or expense.get("date") or ""
        exp_date = _parse_date(str(date_str)[:10]) if date_str else None
        if not exp_date:
            return True
        if start and exp_date < start:
            return False
        if end and exp_date > end:
            return False
        return True

    return [e for e in expenses if in_range(e)]


def _lookup_project_name(project_id: str) -> str:
    try:
        resp = supabase.table("projects").select("project_id, project_name").eq(
            "project_id", project_id).single().execute()
    except Exception as _exc:
        logger.debug("Suppressed: %s", _exc)
        resp = None
    if not resp or not resp.data:
        raise HTTPException(status_code=404, detail="Project not found")
    return resp.data.get("project_name") or "Unknown Project"


# ================================
# POST /reports/bva
# ================================

@router.post("/bva")
async def generate_bva_report(body: BvaReportRequest):
    """Compute a Budget vs Actuals report (canonical engine) and return it as
    JSON. Set generate_pdf=true to also render + upload the PDF and get a url."""
    project_name = _lookup_project_name(body.project_id)

    budgets = fetch_budgets(body.project_id)
    expenses = _filter_by_date(fetch_expenses(body.project_id), body.start_date, body.end_date)
    accounts = fetch_accounts()
    overlay = fetch_account_overlay()
    category_order, subcategory_index = fetch_category_tree()

    report_data = build_report(budgets, expenses, accounts, overlay, category_order, subcategory_index)

    pdf_url = None
    if body.generate_pdf:
        if not REPORTLAB_AVAILABLE:
            raise HTTPException(status_code=503, detail="PDF generation not available (reportlab not installed)")
        company_name = fetch_company_name(body.company_id)
        pdf_url = generate_and_upload_pdf(project_name, report_data, project_id=body.project_id, company_name=company_name)
        if not pdf_url:
            raise HTTPException(status_code=500, detail="Failed to generate or upload PDF")

    return {
        "ok": True,
        "project_id": body.project_id,
        "project_name": project_name,
        "rows": report_data["rows"],
        "categories": report_data["categories"],
        "totals": report_data["totals"],
        "pdf_url": pdf_url,
    }


# ================================
# POST /reports/pnl
# ================================

@router.post("/pnl")
async def generate_pnl_report(body: PnlReportRequest):
    """Compute a P&L COGS report (canonical engine, actuals only) and return it
    as JSON. Set generate_pdf=true to also render + upload the PDF and get a url."""
    project_name = _lookup_project_name(body.project_id)

    expenses = _filter_by_date(fetch_expenses(body.project_id), body.start_date, body.end_date)
    accounts = fetch_accounts()
    overlay = fetch_account_overlay()
    category_order, subcategory_index = fetch_category_tree()

    # No budgets -> P&L (actuals only); same engine as BVA so totals reconcile.
    report_data = build_report([], expenses, accounts, overlay, category_order, subcategory_index)

    pdf_url = None
    if body.generate_pdf:
        if not REPORTLAB_AVAILABLE:
            raise HTTPException(status_code=503, detail="PDF generation not available (reportlab not installed)")
        if not report_data["rows"]:
            raise HTTPException(status_code=404, detail="No authorized expenses found for this project")
        company_name = fetch_company_name(body.company_id)
        pdf_url = generate_and_upload_pnl_pdf(project_name, report_data, project_id=body.project_id, company_name=company_name)
        if not pdf_url:
            raise HTTPException(status_code=500, detail="Failed to generate or upload PDF")

    return {
        "ok": True,
        "project_id": body.project_id,
        "project_name": project_name,
        "rows": report_data["rows"],
        "categories": report_data["categories"],
        "totals": report_data["totals"],
        "total_cogs": report_data["totals"]["actual"],
        "accounts_count": len(report_data["rows"]),
        "pdf_url": pdf_url,
    }
