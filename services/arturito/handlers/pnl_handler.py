# services/arturito/handlers/pnl_handler.py
# ================================
# Handler: P&L COGS Report
# Same as BVA but without budget columns — only authorized actuals by account
# ================================

import logging
from typing import Dict, Any, Optional, List
from datetime import datetime
import io

logger = logging.getLogger(__name__)

from api.supabase_client import supabase
from api.services.vault_service import save_to_project_folder

# Reuse project resolution and data fetching from BVA handler
from .bva_handler import (
    resolve_project,
    fetch_recent_projects,
    fetch_company_name,
    fetch_expenses,
    fetch_accounts,
    fetch_account_overlay,
    upload_to_storage,
    _gpt_ask_missing_entity,
)
from services.reporting.engine import build_report, fetch_category_tree

# PDF generation
try:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
    from reportlab.lib.enums import TA_CENTER, TA_RIGHT
    REPORTLAB_AVAILABLE = True
except ImportError:
    REPORTLAB_AVAILABLE = False
    logger.warning("[PNL] reportlab not installed. PDF generation disabled.")


REPORTS_BUCKET = "bva-reports"


def handle_pnl_cogs(
    request: Dict[str, Any],
    context: Dict[str, Any] = None
) -> Dict[str, Any]:
    """
    Generates a P&L COGS report — authorized expenses grouped by account.
    Same flow as BVA but without the budget comparison.

    Args:
        request: {intent, entities: {project}, raw_text}
        context: {user, space_id, space_name}

    Returns:
        Dict with PDF URL or text fallback
    """
    entities = request.get("entities", {})
    ctx = context or {}
    company_id = ctx.get("company_id")

    # Extract project
    project_input = entities.get("project")
    if project_input:
        project_input = str(project_input).strip()

    # Fallback: use space name
    if not project_input:
        project_input = ctx.get("space_name", "")

    raw_text = request.get("raw_text", "")
    space_id = ctx.get("space_id", "default")

    # Validate project
    if not project_input or project_input.lower() in ["default", "general", "random", "none", "ngm hub web"]:
        recent_projects = fetch_recent_projects(limit=8, company_id=company_id)
        hint = ""
        data = None
        if recent_projects:
            hint = ", ".join([p.get("project_name", "") for p in recent_projects[:4]])
            data = {
                "projects": [{"id": p.get("project_id"), "name": p.get("project_name")} for p in recent_projects]
            }
        text = _gpt_ask_missing_entity(raw_text, "project", hint, space_id, report_type="P&L COGS")
        result = {
            "ok": False,
            "text": text,
            "action": "ask_project"
        }
        if data:
            data["command"] = "pnl"
            result["data"] = data
        else:
            result["data"] = {"command": "pnl"}
        return result

    try:
        # 1. Resolve project (scoped to the active workspace)
        project = resolve_project(project_input, company_id=company_id)
        if not project:
            return {
                "ok": False,
                "text": f"Could not find project '{project_input}'. Please check the name.",
                "action": "project_not_found"
            }

        project_id = project.get("project_id") or project.get("id")
        project_name = project.get("project_name") or project.get("name") or project_input

        # 2. Fetch data
        expenses = fetch_expenses(project_id)
        accounts = fetch_accounts()
        overlay = fetch_account_overlay()
        category_order, subcategory_index = fetch_category_tree()

        # 3. Process report with the unified engine (no budgets -> actuals only)
        report_data = build_report([], expenses, accounts, overlay, category_order, subcategory_index)

        # 4. Generate PDF
        if not REPORTLAB_AVAILABLE:
            response_text = format_pnl_response(project_name, report_data)
            return {
                "ok": True,
                "text": response_text + "\n\nPDF generation not available at this time.",
                "action": "pnl_report_text",
                "data": {
                    "project_id": project_id,
                    "project_name": project_name,
                    "totals": report_data["totals"]
                }
            }

        company_name = fetch_company_name(company_id)
        pdf_url = generate_and_upload_pnl_pdf(project_name, report_data, project_id=project_id, company_name=company_name)

        if not pdf_url:
            response_text = format_pnl_response(project_name, report_data)
            return {
                "ok": True,
                "text": response_text + "\n\nCould not generate PDF.",
                "action": "pnl_report_text",
                "data": report_data["totals"]
            }

        # 5. Success response
        totals = report_data["totals"]

        response_text = f"""P&L COGS: {project_name}

Total COGS: ${totals['actual']:,.2f}
Accounts: {len(report_data['rows'])}"""

        return {
            "ok": True,
            "text": response_text,
            "action": "pnl_report_pdf",
            "data": {
                "project_id": project_id,
                "project_name": project_name,
                "pdf_url": pdf_url,
                "totals": report_data["totals"]
            }
        }

    except Exception as e:
        logger.error("[PNL] Error generating report: %s", e)
        return {
            "ok": False,
            "text": "Error generating the report. Please try again.",
            "action": "pnl_error"
        }


def format_pnl_response(project_name: str, report_data: Dict[str, Any]) -> str:
    """Format P&L COGS as text (fallback without PDF)."""
    totals = report_data["totals"]
    rows = report_data["rows"]

    def fmt(amount: float) -> str:
        return f"${abs(amount):,.2f}"

    lines = [
        f"P&L COGS: {project_name}",
        "",
        f"Total COGS: {fmt(totals['actual'])}",
        "",
    ]

    if rows:
        lines.append("Top accounts by spend:")
        sorted_by_actual = sorted(rows, key=lambda x: x["actual"], reverse=True)[:5]
        for i, row in enumerate(sorted_by_actual, 1):
            lines.append(f"{i}. {row['account']}: {fmt(row['actual'])}")

    return "\n".join(lines)


def generate_and_upload_pnl_pdf(
    project_name: str,
    report_data: Dict[str, Any],
    project_id: str = None,
    company_name: str = None
) -> Optional[str]:
    """Generate P&L COGS PDF and upload to Vault."""
    try:
        pdf_buffer = io.BytesIO()
        generate_pnl_pdf(pdf_buffer, project_name, report_data, company_name=company_name)
        pdf_buffer.seek(0)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_name = "".join(c if c.isalnum() or c in " -_" else "_" for c in project_name)
        filename = f"{safe_name}_PNL_COGS_{timestamp}.pdf"

        pdf_bytes = pdf_buffer.getvalue()

        # Upload to Vault
        if project_id:
            vault_result = save_to_project_folder(project_id, "Reports", pdf_bytes, filename, "application/pdf")
            if vault_result and vault_result.get("public_url"):
                return vault_result["public_url"]

        # Fallback: legacy bucket
        return upload_to_storage(pdf_bytes, filename)

    except Exception as e:
        logger.error("[PNL] Error generating/uploading PDF: %s", e)
        return None


def generate_pnl_pdf(buffer: io.BytesIO, project_name: str, report_data: Dict[str, Any], company_name: str = None):
    """Generate P&L COGS PDF — same style as BVA but only ACCOUNT + ACTUAL columns."""
    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        rightMargin=0.5 * inch,
        leftMargin=0.5 * inch,
        topMargin=0.5 * inch,
        bottomMargin=0.75 * inch
    )

    elements = []
    styles = getSampleStyleSheet()

    title_style = ParagraphStyle(
        'CustomTitle',
        parent=styles['Heading1'],
        fontSize=18,
        alignment=TA_CENTER,
        spaceAfter=6
    )

    subtitle_style = ParagraphStyle(
        'CustomSubtitle',
        parent=styles['Normal'],
        fontSize=12,
        alignment=TA_CENTER,
        spaceAfter=4
    )

    note_style = ParagraphStyle(
        'Note',
        parent=styles['Normal'],
        fontSize=9,
        alignment=TA_CENTER,
        textColor=colors.gray
    )

    date_style = ParagraphStyle(
        'DateStyle',
        parent=styles['Normal'],
        fontSize=11,
        alignment=TA_RIGHT,
        fontName='Helvetica-Bold'
    )

    # Header
    elements.append(Paragraph(company_name or "KD Developers LLC", title_style))
    elements.append(Paragraph(f"P&L COGS Report: {project_name}", subtitle_style))
    elements.append(Paragraph("All Dates (Not Use this Report for Accounting Purposes)", note_style))
    elements.append(Spacer(1, 12))

    # Date
    today = datetime.now().strftime("%m/%d/%Y")
    elements.append(Paragraph(today, date_style))
    elements.append(Spacer(1, 20))

    # Table data
    rows = report_data["rows"]
    totals = report_data["totals"]

    table_data = [
        ["ACCOUNT", "ACTUAL"]
    ]

    for row in rows:
        table_data.append([
            row["account"],
            f"${row['actual']:,.2f}"
        ])

    # Total row
    table_data.append([
        "TOTAL",
        f"${totals['actual']:,.2f}"
    ])

    # Create table
    col_widths = [5 * inch, 2.1 * inch]
    table = Table(table_data, colWidths=col_widths)

    table_style = TableStyle([
        # Header
        ('BACKGROUND', (0, 0), (-1, 0), colors.Color(0.96, 0.96, 0.96)),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.black),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 10),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 10),
        ('TOPPADDING', (0, 0), (-1, 0), 10),

        # Header borders
        ('LINEABOVE', (0, 0), (-1, 0), 2, colors.black),
        ('LINEBELOW', (0, 0), (-1, 0), 2, colors.black),

        # Data
        ('FONTNAME', (0, 1), (-1, -2), 'Helvetica'),
        ('FONTSIZE', (0, 1), (-1, -2), 9),
        ('BOTTOMPADDING', (0, 1), (-1, -2), 8),
        ('TOPPADDING', (0, 1), (-1, -2), 8),

        # Alignment
        ('ALIGN', (0, 0), (0, -1), 'LEFT'),
        ('ALIGN', (1, 0), (1, -1), 'RIGHT'),

        # Row lines
        ('LINEBELOW', (0, 1), (-1, -2), 0.5, colors.Color(0.9, 0.9, 0.9)),

        # Total row
        ('BACKGROUND', (0, -1), (-1, -1), colors.Color(0.96, 0.96, 0.96)),
        ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),
        ('FONTSIZE', (0, -1), (-1, -1), 10),
        ('LINEABOVE', (0, -1), (-1, -1), 2, colors.black),
        ('BOTTOMPADDING', (0, -1), (-1, -1), 10),
        ('TOPPADDING', (0, -1), (-1, -1), 10),
    ])

    table.setStyle(table_style)
    elements.append(table)

    # Footer
    elements.append(Spacer(1, 30))
    footer_style = ParagraphStyle(
        'Footer',
        parent=styles['Normal'],
        fontSize=8,
        textColor=colors.gray
    )
    elements.append(Paragraph("Generated from NGM Hub - P&L COGS Report", footer_style))

    doc.build(elements)
