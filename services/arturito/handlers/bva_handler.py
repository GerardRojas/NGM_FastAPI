# services/arturito/handlers/bva_handler.py
# ================================
# Handler: Budget vs Actuals (BVA)
# ================================
# Genera reportes BVA en PDF y los sube a Supabase Storage

from typing import Dict, Any, Optional, List
from datetime import datetime
import io
import os

from api.supabase_client import supabase

# Para generación de PDF
try:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
    from reportlab.lib.enums import TA_CENTER, TA_RIGHT, TA_LEFT
    REPORTLAB_AVAILABLE = True
except ImportError:
    REPORTLAB_AVAILABLE = False
    print("[BVA] WARNING: reportlab not installed. PDF generation disabled.")


# Bucket de Supabase Storage para reportes
REPORTS_BUCKET = "bva-reports"


def handle_budget_vs_actuals(
    request: Dict[str, Any],
    context: Dict[str, Any] = None
) -> Dict[str, Any]:
    """
    Genera un reporte Budget vs Actuals en PDF y lo sube a Supabase Storage.

    Flujo:
    1. Busca el proyecto por nombre o ID
    2. Obtiene budgets, expenses autorizados, y accounts
    3. Procesa y compara datos
    4. Genera PDF con el mismo formato del frontend
    5. Sube a Supabase Storage
    6. Retorna URL del PDF

    Args:
        request: {intent, entities: {project, category?}, raw_text}
        context: {user, space_id, space_name}

    Returns:
        Dict con URL del PDF o mensaje de error
    """
    entities = request.get("entities", {})
    ctx = context or {}

    # Extraer proyecto
    project_input = entities.get("project", "").strip()

    # Fallback: usar nombre del espacio si no hay proyecto
    if not project_input:
        project_input = ctx.get("space_name", "")

    # Validar que tenemos proyecto
    if not project_input or project_input.lower() in ["default", "general", "random"]:
        return {
            "ok": False,
            "text": "⚠️ No pude identificar el proyecto. Por favor especifica el nombre.",
            "action": "missing_project"
        }

    try:
        # 1. Resolver proyecto (buscar por nombre o ID)
        project = resolve_project(project_input)
        if not project:
            return {
                "ok": False,
                "text": f"⚠️ No encontré el proyecto '{project_input}'. Verifica el nombre.",
                "action": "project_not_found"
            }

        project_id = project.get("project_id") or project.get("id")
        project_name = project.get("project_name") or project.get("name") or project_input

        # 2. Obtener datos
        budgets = fetch_budgets(project_id)
        expenses = fetch_expenses(project_id)
        accounts = fetch_accounts()

        # 3. Procesar reporte
        report_data = process_report_data(budgets, expenses, accounts)

        # 4. Generar PDF y subir a Storage
        if not REPORTLAB_AVAILABLE:
            # Fallback: solo texto si no hay reportlab
            response_text = format_bva_response(project_name, report_data)
            return {
                "ok": True,
                "text": response_text + "\n\n⚠️ _Generación de PDF no disponible en este momento._",
                "action": "bva_report_text",
                "data": {
                    "project_id": project_id,
                    "project_name": project_name,
                    "totals": report_data["totals"]
                }
            }

        pdf_url = generate_and_upload_pdf(project_name, report_data)

        if not pdf_url:
            # Fallback si falla el PDF
            response_text = format_bva_response(project_name, report_data)
            return {
                "ok": True,
                "text": response_text + "\n\n⚠️ _No se pudo generar el PDF._",
                "action": "bva_report_text",
                "data": report_data["totals"]
            }

        # 5. Respuesta exitosa con link al PDF
        totals = report_data["totals"]
        balance_emoji = "✅" if totals["balance"] >= 0 else "⚠️"

        response_text = f"""📊 *Budget vs Actuals: {project_name}*

💰 Budget: ${totals['budget']:,.2f}
💸 Actual: ${totals['actual']:,.2f}
{balance_emoji} Balance: ${totals['balance']:,.2f}
📈 % Used: {totals['percent_of_budget']:.1f}%

📄 *[Ver Reporte PDF]({pdf_url})*"""

        return {
            "ok": True,
            "text": response_text,
            "action": "bva_report_pdf",
            "data": {
                "project_id": project_id,
                "project_name": project_name,
                "pdf_url": pdf_url,
                "totals": report_data["totals"]
            }
        }

    except Exception as e:
        return {
            "ok": False,
            "text": f"⚠️ Error generando el reporte: {str(e)}",
            "action": "bva_error",
            "error": str(e)
        }


def resolve_project(project_input: str) -> Optional[Dict[str, Any]]:
    """Busca el proyecto por nombre o ID con búsqueda fuzzy."""
    try:
        # Intentar búsqueda exacta por ID
        result = supabase.table("projects").select("*").eq("project_id", project_input).execute()
        if result.data:
            return result.data[0]

        # Buscar por nombre (case-insensitive, parcial)
        result = supabase.table("projects").select("*").ilike("project_name", f"%{project_input}%").execute()
        if result.data:
            matches = sorted(result.data, key=lambda x: len(x.get("project_name", "")))
            return matches[0]

        return None
    except Exception as e:
        print(f"[BVA] Error resolving project: {e}")
        return None


def fetch_budgets(project_id: str) -> List[Dict[str, Any]]:
    """Obtiene budgets del proyecto desde Supabase"""
    try:
        result = supabase.table("budgets_qbo").select("*").eq("ngm_project_id", project_id).eq("active", True).execute()
        return result.data or []
    except Exception as e:
        print(f"[BVA] Error fetching budgets: {e}")
        return []


def fetch_expenses(project_id: str) -> List[Dict[str, Any]]:
    """Obtiene expenses autorizados del proyecto"""
    try:
        result = supabase.table("expenses").select("*").eq("project", project_id).eq("auth_status", True).execute()
        return result.data or []
    except Exception as e:
        print(f"[BVA] Error fetching expenses: {e}")
        return []


def fetch_accounts() -> List[Dict[str, Any]]:
    """Obtiene catálogo de cuentas"""
    try:
        result = supabase.table("accounts").select("*").execute()
        return result.data or []
    except Exception as e:
        print(f"[BVA] Error fetching accounts: {e}")
        return []


def process_report_data(
    budgets: List[Dict[str, Any]],
    expenses: List[Dict[str, Any]],
    accounts: List[Dict[str, Any]]
) -> Dict[str, Any]:
    """Procesa datos y genera el reporte comparativo."""

    def get_account_name(account_id: str, account_name: str = None) -> str:
        if account_name:
            return account_name
        if account_id:
            for acc in accounts:
                if (acc.get("account_id") or acc.get("id")) == account_id:
                    return acc.get("Name") or acc.get("account_name") or "Unknown"
        return "Unknown Account"

    def get_account_number(account_name: str) -> int:
        for acc in accounts:
            if (acc.get("Name") or acc.get("account_name")) == account_name:
                return acc.get("AcctNum") or 99999
        return 99999

    # Agrupar budgets por cuenta
    budgets_by_account = {}
    for budget in budgets:
        account_name = get_account_name(budget.get("account_id"), budget.get("account_name"))
        budgets_by_account[account_name] = budgets_by_account.get(account_name, 0) + float(budget.get("amount_sum") or 0)

    # Agrupar expenses por cuenta
    expenses_by_account = {}
    for expense in expenses:
        account_name = get_account_name(expense.get("account_id"), expense.get("account_name"))
        amount = float(expense.get("Amount") or expense.get("amount") or 0)
        expenses_by_account[account_name] = expenses_by_account.get(account_name, 0) + amount

    # Construir filas
    all_accounts = set(list(budgets_by_account.keys()) + list(expenses_by_account.keys()))
    rows = []

    for account_name in all_accounts:
        budget_amount = budgets_by_account.get(account_name, 0)
        actual_amount = expenses_by_account.get(account_name, 0)
        balance = budget_amount - actual_amount
        percent = (actual_amount / budget_amount * 100) if budget_amount > 0 else 0

        rows.append({
            "account": account_name,
            "account_number": get_account_number(account_name),
            "budget": round(budget_amount, 2),
            "actual": round(actual_amount, 2),
            "balance": round(balance, 2),
            "percent_of_budget": round(percent, 2)
        })

    rows.sort(key=lambda x: (x["account_number"], x["account"]))

    # Totales
    total_budget = sum(r["budget"] for r in rows)
    total_actual = sum(r["actual"] for r in rows)
    total_balance = total_budget - total_actual
    total_percent = (total_actual / total_budget * 100) if total_budget > 0 else 0

    return {
        "rows": rows,
        "totals": {
            "budget": round(total_budget, 2),
            "actual": round(total_actual, 2),
            "balance": round(total_balance, 2),
            "percent_of_budget": round(total_percent, 2)
        }
    }


def generate_and_upload_pdf(project_name: str, report_data: Dict[str, Any]) -> Optional[str]:
    """
    Genera el PDF del reporte BVA y lo sube a Supabase Storage.
    Retorna la URL pública del archivo.
    """
    try:
        # Generar PDF en memoria
        pdf_buffer = io.BytesIO()
        generate_bva_pdf(pdf_buffer, project_name, report_data)
        pdf_buffer.seek(0)

        # Nombre del archivo con timestamp
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_project_name = "".join(c if c.isalnum() or c in " -_" else "_" for c in project_name)
        filename = f"{safe_project_name}_BVA_{timestamp}.pdf"

        # Subir a Supabase Storage
        pdf_url = upload_to_storage(pdf_buffer.getvalue(), filename)

        return pdf_url

    except Exception as e:
        print(f"[BVA] Error generating/uploading PDF: {e}")
        return None


def generate_bva_pdf(buffer: io.BytesIO, project_name: str, report_data: Dict[str, Any]):
    """
    Genera el PDF con el mismo formato que el frontend.
    Replica el estilo de budget-vs-actuals.js
    """
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

    # Estilos personalizados
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
    elements.append(Paragraph("KD Developers LLC", title_style))
    elements.append(Paragraph(f"Budget Vs Actuals Report: {project_name}", subtitle_style))
    elements.append(Paragraph("All Dates (Not Use this Report for Accounting Purposes)", note_style))
    elements.append(Spacer(1, 12))

    # Fecha
    today = datetime.now().strftime("%m/%d/%Y")
    elements.append(Paragraph(today, date_style))
    elements.append(Spacer(1, 20))

    # Tabla de datos
    rows = report_data["rows"]
    totals = report_data["totals"]

    # Headers de la tabla
    table_data = [
        ["ACCOUNT", "ACTUAL", "BUDGET", "% OF BUDGET", "BALANCE"]
    ]

    # Filas de datos
    for row in rows:
        balance_display = f"${row['balance']:,.2f}"
        if row['balance'] < 0:
            balance_display = f"(${abs(row['balance']):,.2f})"

        table_data.append([
            row["account"],
            f"${row['actual']:,.2f}",
            f"${row['budget']:,.2f}",
            f"{row['percent_of_budget']:.2f}%",
            balance_display
        ])

    # Fila de totales
    total_balance_display = f"${totals['balance']:,.2f}"
    if totals['balance'] < 0:
        total_balance_display = f"(${abs(totals['balance']):,.2f})"

    table_data.append([
        "TOTAL",
        f"${totals['actual']:,.2f}",
        f"${totals['budget']:,.2f}",
        f"{totals['percent_of_budget']:.2f}%",
        total_balance_display
    ])

    # Crear tabla
    col_widths = [2.5 * inch, 1.2 * inch, 1.2 * inch, 1 * inch, 1.2 * inch]
    table = Table(table_data, colWidths=col_widths)

    # Estilo de la tabla (replica el frontend)
    table_style = TableStyle([
        # Header
        ('BACKGROUND', (0, 0), (-1, 0), colors.Color(0.96, 0.96, 0.96)),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.black),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 10),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 10),
        ('TOPPADDING', (0, 0), (-1, 0), 10),

        # Bordes del header
        ('LINEABOVE', (0, 0), (-1, 0), 2, colors.black),
        ('LINEBELOW', (0, 0), (-1, 0), 2, colors.black),

        # Datos
        ('FONTNAME', (0, 1), (-1, -2), 'Helvetica'),
        ('FONTSIZE', (0, 1), (-1, -2), 9),
        ('BOTTOMPADDING', (0, 1), (-1, -2), 8),
        ('TOPPADDING', (0, 1), (-1, -2), 8),

        # Alineación
        ('ALIGN', (0, 0), (0, -1), 'LEFT'),  # Account column
        ('ALIGN', (1, 0), (-1, -1), 'RIGHT'),  # Numeric columns

        # Líneas entre filas
        ('LINEBELOW', (0, 1), (-1, -2), 0.5, colors.Color(0.9, 0.9, 0.9)),

        # Fila de totales
        ('BACKGROUND', (0, -1), (-1, -1), colors.Color(0.96, 0.96, 0.96)),
        ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),
        ('FONTSIZE', (0, -1), (-1, -1), 10),
        ('LINEABOVE', (0, -1), (-1, -1), 2, colors.black),
        ('BOTTOMPADDING', (0, -1), (-1, -1), 10),
        ('TOPPADDING', (0, -1), (-1, -1), 10),
    ])

    # Colorear balances negativos en rojo
    for i, row in enumerate(rows, start=1):
        if row["balance"] < 0:
            table_style.add('TEXTCOLOR', (4, i), (4, i), colors.Color(0.86, 0.15, 0.15))

    if totals["balance"] < 0:
        table_style.add('TEXTCOLOR', (4, -1), (4, -1), colors.Color(0.86, 0.15, 0.15))

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
    elements.append(Paragraph("Generated from NGM Hub - Budget Vs Actuals Report", footer_style))

    # Generar PDF
    doc.build(elements)


def upload_to_storage(file_bytes: bytes, filename: str) -> Optional[str]:
    """
    Sube el archivo a Supabase Storage y retorna la URL pública.
    """
    try:
        # Intentar subir al bucket
        result = supabase.storage.from_(REPORTS_BUCKET).upload(
            path=filename,
            file=file_bytes,
            file_options={"content-type": "application/pdf"}
        )

        # Obtener URL pública
        public_url = supabase.storage.from_(REPORTS_BUCKET).get_public_url(filename)

        return public_url

    except Exception as e:
        error_msg = str(e)

        # Si el bucket no existe, intentar crearlo
        if "not found" in error_msg.lower() or "bucket" in error_msg.lower():
            print(f"[BVA] Bucket '{REPORTS_BUCKET}' may not exist. Attempting to create...")
            try:
                # Crear bucket público
                supabase.storage.create_bucket(REPORTS_BUCKET, options={"public": True})

                # Reintentar upload
                supabase.storage.from_(REPORTS_BUCKET).upload(
                    path=filename,
                    file=file_bytes,
                    file_options={"content-type": "application/pdf"}
                )

                return supabase.storage.from_(REPORTS_BUCKET).get_public_url(filename)

            except Exception as create_err:
                print(f"[BVA] Error creating bucket: {create_err}")
                return None

        print(f"[BVA] Error uploading to storage: {e}")
        return None


def format_bva_response(project_name: str, report_data: Dict[str, Any]) -> str:
    """Formatea el reporte BVA como texto (fallback sin PDF)."""
    totals = report_data["totals"]
    rows = report_data["rows"]

    balance_emoji = "✅" if totals["balance"] >= 0 else "⚠️"

    def fmt(amount: float) -> str:
        return f"${abs(amount):,.2f}"

    lines = [
        f"📊 *Budget vs Actuals: {project_name}*",
        "",
        f"💰 Total Budget: {fmt(totals['budget'])}",
        f"💸 Total Actual: {fmt(totals['actual'])}",
        f"{balance_emoji} Balance: {fmt(totals['balance'])}",
        f"📈 % of Budget: {totals['percent_of_budget']:.1f}%",
        "",
    ]

    if rows:
        lines.append("*Top cuentas por gasto:*")
        sorted_by_actual = sorted(rows, key=lambda x: x["actual"], reverse=True)[:5]
        for i, row in enumerate(sorted_by_actual, 1):
            indicator = "🔴" if row["balance"] < 0 else "🟢"
            lines.append(f"{i}. {row['account']}: {fmt(row['actual'])} / {fmt(row['budget'])} {indicator}")

    over_budget = [r for r in rows if r["balance"] < 0]
    if over_budget:
        lines.append("")
        lines.append(f"⚠️ *{len(over_budget)} cuenta(s) sobre presupuesto*")

    return "\n".join(lines)
