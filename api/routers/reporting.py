# api/routers/reporting.py
# ================================
# Reporting endpoints — generate PDFs and save to Vault
# ================================

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional

from api.supabase_client import supabase

# Reuse existing handler functions
from services.arturito.handlers.bva_handler import fetch_expenses, fetch_accounts
from services.arturito.handlers.pnl_handler import (
    process_pnl_data,
    generate_and_upload_pnl_pdf,
    REPORTLAB_AVAILABLE,
)

router = APIRouter(prefix="/reports", tags=["Reporting"])


# ================================
# Models
# ================================

class PnlReportRequest(BaseModel):
    project_id: str
    start_date: Optional[str] = None
    end_date: Optional[str] = None


# ================================
# POST /reports/pnl
# ================================

@router.post("/pnl")
async def generate_pnl_report(body: PnlReportRequest):
    """
    Generate a P&L COGS PDF report and save it to the project's
    Vault Reports folder. Returns the public URL.
    """
    if not REPORTLAB_AVAILABLE:
        raise HTTPException(status_code=503, detail="PDF generation not available (reportlab not installed)")

    # 1. Look up project name
    try:
        proj_resp = supabase.table("projects").select(
            "project_id, project_name"
        ).eq("project_id", body.project_id).single().execute()
    except Exception:
        proj_resp = None

    if not proj_resp or not proj_resp.data:
        raise HTTPException(status_code=404, detail="Project not found")

    project_name = proj_resp.data.get("project_name") or "Unknown Project"
    project_id = body.project_id

    # 2. Fetch expenses (authorized only — filtering happens in process_pnl_data)
    expenses = fetch_expenses(project_id)

    # 3. Date filtering (if provided)
    if body.start_date or body.end_date:
        from datetime import datetime as dt

        def parse_date(d):
            for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%Y-%m-%dT%H:%M:%S"):
                try:
                    return dt.strptime(d, fmt)
                except ValueError:
                    continue
            return None

        start = parse_date(body.start_date) if body.start_date else None
        end = parse_date(body.end_date) if body.end_date else None

        def in_range(expense):
            date_str = expense.get("Date") or expense.get("date") or ""
            exp_date = parse_date(str(date_str)[:10]) if date_str else None
            if not exp_date:
                return True
            if start and exp_date < start:
                return False
            if end and exp_date > end:
                return False
            return True

        expenses = [e for e in expenses if in_range(e)]

    # 4. Fetch accounts
    accounts = fetch_accounts()

    # 5. Process into report data
    report_data = process_pnl_data(expenses, accounts)

    if not report_data["rows"]:
        raise HTTPException(status_code=404, detail="No authorized expenses found for this project")

    # 6. Generate PDF and upload to Vault
    pdf_url = generate_and_upload_pnl_pdf(project_name, report_data, project_id=project_id)

    if not pdf_url:
        raise HTTPException(status_code=500, detail="Failed to generate or upload PDF")

    return {
        "ok": True,
        "pdf_url": pdf_url,
        "project_name": project_name,
        "total_cogs": report_data["totals"]["actual"],
        "accounts_count": len(report_data["rows"]),
    }
