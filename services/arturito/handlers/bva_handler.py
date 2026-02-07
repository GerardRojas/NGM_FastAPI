# services/arturito/handlers/bva_handler.py
# ================================
# Handler: Budget vs Actuals (BVA)
# ================================
# Genera reportes BVA en PDF y los sube a Supabase Storage

from typing import Dict, Any, Optional, List
from datetime import datetime
import io
import os
import re

from openai import OpenAI
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
    project_input = entities.get("project")
    if project_input:
        project_input = str(project_input).strip()

    # Fallback: usar nombre del espacio si no hay proyecto
    if not project_input:
        project_input = ctx.get("space_name", "")

    # Validar que tenemos proyecto
    raw_text = request.get("raw_text", "")
    space_id = ctx.get("space_id", "default")

    if not project_input or project_input.lower() in ["default", "general", "random", "none", "ngm hub web"]:
        recent_projects = fetch_recent_projects(limit=8)
        hint = ""
        data = None
        if recent_projects:
            hint = ", ".join([p.get("project_name", "") for p in recent_projects[:4]])
            data = {
                "projects": [{"id": p.get("project_id"), "name": p.get("project_name")} for p in recent_projects]
            }
        text = _gpt_ask_missing_entity(raw_text, "project", hint, space_id)
        result = {
            "ok": False,
            "text": text,
            "action": "ask_project"
        }
        if data:
            result["data"] = data
        return result

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

        response_text = f"""Budget vs Actuals: {project_name}

Budget: ${totals['budget']:,.2f}
Actual: ${totals['actual']:,.2f}
{balance_emoji} Balance: ${totals['balance']:,.2f}
% Used: {totals['percent_of_budget']:.1f}%"""

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
        print(f"[BVA] Error generating report: {e}")
        return {
            "ok": False,
            "text": "Error generando el reporte. Intenta de nuevo.",
            "action": "bva_error"
        }


def _word_similarity(a: str, b: str) -> float:
    """Simple character-level similarity ratio (0-1) using SequenceMatcher."""
    from difflib import SequenceMatcher
    return SequenceMatcher(None, a, b).ratio()


def resolve_project(project_input: str) -> Optional[Dict[str, Any]]:
    """
    Busca el proyecto por nombre o ID con busqueda fuzzy mejorada.

    Algoritmo de busqueda:
    1. Busqueda exacta por ID
    2. Busqueda directa por nombre (ilike)
    3. Busqueda por palabras (substring + typo tolerance via edit distance)
    4. GPT fallback

    Each step is isolated so a failure in one (e.g. UUID type mismatch)
    does not prevent subsequent steps from running.
    """
    # 1. Busqueda exacta por ID (may fail if project_id is UUID and input is text)
    try:
        result = supabase.table("projects").select("*").eq("project_id", project_input).execute()
        if result.data:
            return result.data[0]
    except Exception as e:
        print(f"[BVA] resolve_project step 1 (ID lookup) skipped: {e}")

    # 2. Buscar por nombre directo (case-insensitive, parcial)
    try:
        result = supabase.table("projects").select("*").ilike("project_name", f"%{project_input}%").execute()
        if result.data:
            matches = sorted(result.data, key=lambda x: len(x.get("project_name", "")))
            return matches[0]
    except Exception as e:
        print(f"[BVA] resolve_project step 2 (ilike) error: {e}")

    # 3. Busqueda fuzzy por palabras (substring + typo tolerance)
    try:
        search_words = [w.strip().lower() for w in project_input.split() if w.strip()]
        if not search_words:
            return None

        all_projects = supabase.table("projects").select("*").execute()
        if not all_projects.data:
            return None

        best_match = None
        best_score = 0

        for project in all_projects.data:
            project_name = project.get("project_name", "").lower()
            project_words = project_name.split()

            exact_hits = 0
            fuzzy_hits = 0

            for sw in search_words:
                # Exact substring match
                if sw in project_name:
                    exact_hits += 1
                    continue
                # Typo tolerance: compare each search word to each project word
                best_sim = max(
                    (_word_similarity(sw, pw) for pw in project_words),
                    default=0
                )
                if best_sim >= 0.7:
                    fuzzy_hits += 1

            total_hits = exact_hits + fuzzy_hits
            if total_hits == 0:
                continue

            score = total_hits / len(search_words)
            # Bonus for exact substring hits
            score += exact_hits * 0.1
            # Bonus if ALL words matched
            if total_hits == len(search_words):
                score += 0.5
            # Prefer shorter names (more specific)
            score += 0.1 / (len(project_name) + 1)

            if score > best_score:
                best_score = score
                best_match = project

        if best_match and best_score >= 0.5:
            return best_match

        # 4. GPT fallback
        project_names = [p.get("project_name", "") for p in all_projects.data if p.get("project_name")]
        gpt_match_name = _gpt_fuzzy_match(project_input, project_names)
        if gpt_match_name:
            for p in all_projects.data:
                if p.get("project_name") == gpt_match_name:
                    return p

        return None
    except Exception as e:
        print(f"[BVA] resolve_project step 3/4 (fuzzy/GPT) error: {e}")
        return None


def _gpt_fuzzy_match(query: str, candidates: List[str]) -> Optional[str]:
    """
    Uses GPT as a last-resort fuzzy matcher.
    Given a user query and a list of candidate names, asks GPT which one
    (if any) the user most likely meant.
    Returns the exact candidate string or None.
    """
    if not candidates:
        return None
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return None
    try:
        client = OpenAI(api_key=api_key, timeout=10.0)
        candidates_str = "\n".join(f"- {c}" for c in candidates[:40])
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": (
                    "You are a fuzzy-matching helper. The user typed a name that may contain "
                    "typos, abbreviations, or be in a different language. Pick the BEST match "
                    "from the candidate list. Reply with ONLY the exact candidate string, or "
                    "\"NONE\" if nothing is a reasonable match. No explanation."
                )},
                {"role": "user", "content": f"User typed: \"{query}\"\n\nCandidates:\n{candidates_str}"}
            ],
            temperature=0,
            max_tokens=80
        )
        answer = resp.choices[0].message.content.strip().strip('"')
        if answer.upper() == "NONE" or answer not in candidates:
            return None
        return answer
    except Exception as e:
        print(f"[BVA] GPT fuzzy match error: {e}")
        return None


def _gpt_ask_missing_entity(
    raw_text: str,
    missing: str,
    hint: str,
    space_id: str = "default"
) -> str:
    """
    Uses GPT + persona to generate a natural follow-up question
    when the user didn't specify a required entity (project or category).

    Args:
        raw_text: The user's original message
        missing: What's missing ("project" or "category")
        hint: Extra context like available project names
        space_id: For personality level lookup
    """
    from services.arturito.persona import get_persona_prompt

    # Simple language detection for fallback messages
    is_spanish = any(w in raw_text.lower().split() for w in [
        "cuanto", "cuánto", "tengo", "tenemos", "hay", "para", "presupuesto", "gastado"
    ]) if raw_text else False

    def _fallback(missing_key: str, hint_str: str) -> str:
        if missing_key == "project":
            if is_spanish:
                return f"Para cual proyecto? {hint_str}" if hint_str else "Para cual proyecto?"
            return f"For which project? {hint_str}" if hint_str else "For which project?"
        if is_spanish:
            return f"Que categoria? {hint_str}" if hint_str else "Que categoria?"
        return f"Which category? {hint_str}" if hint_str else "Which category?"

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return _fallback(missing, hint)

    try:
        persona = get_persona_prompt(space_id)
        client = OpenAI(api_key=api_key, timeout=8.0)

        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": (
                    f"{persona}\n\n"
                    f"The user asked a budget question but didn't specify the {missing}. "
                    f"Generate a SHORT follow-up question asking for the {missing}. "
                    f"Reply in the SAME LANGUAGE the user wrote in. "
                    f"Keep it to 1-2 sentences max. "
                    f"If there's a hint with options, weave them in naturally."
                )},
                {"role": "user", "content": (
                    f"User said: \"{raw_text}\"\n"
                    f"Missing: {missing}\n"
                    f"Options hint: {hint}"
                )}
            ],
            temperature=0.7,
            max_tokens=100
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        print(f"[BVA] GPT ask missing entity error: {e}")
        return _fallback(missing, hint)


def fetch_recent_projects(limit: int = 8) -> List[Dict[str, Any]]:
    """Obtiene lista de proyectos recientes/activos para sugerir."""
    try:
        result = supabase.table("projects") \
            .select("project_id, project_name") \
            .order("created_at", desc=True) \
            .limit(limit) \
            .execute()
        return result.data or []
    except Exception:
        pass
    # Fallback: sin ordenamiento si created_at no existe
    try:
        result = supabase.table("projects") \
            .select("project_id, project_name") \
            .limit(limit) \
            .execute()
        return result.data or []
    except Exception as e:
        print(f"[BVA] Error fetching recent projects: {e}")
        return []


def fetch_budgets(project_id: str) -> List[Dict[str, Any]]:
    """Obtiene budgets del proyecto desde Supabase"""
    try:
        result = supabase.table("budgets_qbo").select("*").eq("ngm_project_id", project_id).eq("active", True).execute()
        return result.data or []
    except Exception as e:
        print(f"[BVA] Error fetching budgets: {e}")
        return []


def fetch_expenses(project_id: str) -> List[Dict[str, Any]]:
    """Obtiene expenses autorizados del proyecto desde expenses_manual_COGS"""
    try:
        result = (
            supabase.table("expenses_manual_COGS")
            .select("*")
            .eq("project", project_id)
            .eq("auth_status", True)
            .neq("status", "review")
            .execute()
        )
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


# ================================
# CONSULTA ESPECÍFICA Handler
# ================================

def _clean_category_input(raw: str) -> str:
    """
    Strip budget-related verbs/phrases from category input, keeping only the trade name.
    Safety net for when NLU (local or GPT) leaks budget verbs into the topic entity.
    E.g. "gastar en ventanas" -> "ventanas", "disponible para hvac" -> "hvac"
    """
    cleaned = raw.strip()
    # Strip leading budget verbs/nouns + preposition
    cleaned = re.sub(
        r'^(?:gastar|gastado|invertir|invertido|usar|usado|meter|poner|'
        r'tener|tenemos|tengo|hay|queda|quedan|'
        r'presupuesto|budget|disponible|balance|spent|available|remaining)\s+'
        r'(?:en|para|de|del|on|for|in|about)\s+',
        '', cleaned, flags=re.IGNORECASE
    ).strip()
    # Strip "cuanto/how much" phrases that leaked
    cleaned = re.sub(
        r'^(?:cu[aá]nto|how\s+much)\s+(?:tengo|tenemos|hay|queda|hemos|he|have\s+we|do\s+we\s+have)\s+'
        r'(?:gastado\s+)?(?:disponible\s+)?(?:en|para|de|del|on|for|in|about)\s+',
        '', cleaned, flags=re.IGNORECASE
    ).strip()
    # Strip orphan prepositions at start
    cleaned = re.sub(r'^(?:de|para|en|del)\s+', '', cleaned, flags=re.IGNORECASE).strip()
    return cleaned if cleaned else raw


def handle_consulta_especifica(
    request: Dict[str, Any],
    context: Dict[str, Any] = None
) -> Dict[str, Any]:
    """
    Responde consultas específicas sobre una categoría/cuenta del BVA.

    Ejemplos:
    - "¿Cuánto tengo disponible para ventanas en Del Rio?"
    - "¿Cuánto hemos gastado en HVAC en Thrasher Way?"
    - "¿Cuál es el presupuesto de plomería en Arthur Neal?"

    Args:
        request: {intent, entities: {project, topic/category}, raw_text}
        context: {user, space_id, space_name}

    Returns:
        Dict con respuesta sobre la categoría específica
    """
    entities = request.get("entities", {})
    ctx = context or {}

    # Extraer proyecto
    project_input = entities.get("project")
    if project_input:
        project_input = str(project_input).strip()

    # Extraer categoría/topic and clean budget-verb noise
    category_input = entities.get("topic") or entities.get("category") or entities.get("trade")
    if category_input:
        category_input = _clean_category_input(str(category_input))

    # Fallback: usar nombre del espacio si no hay proyecto
    if not project_input:
        project_input = ctx.get("space_name", "")

    # Validar proyecto
    raw_text = request.get("raw_text", "")
    space_id = ctx.get("space_id", "default")

    if not project_input or project_input.lower() in ["default", "general", "random", "none", "ngm hub web"]:
        recent_projects = fetch_recent_projects(limit=6)
        hint = ""
        data = None
        if recent_projects:
            hint = ", ".join([p.get("project_name", "") for p in recent_projects[:4]])
            data = {
                "projects": [{"id": p.get("project_id"), "name": p.get("project_name")} for p in recent_projects]
            }
        text = _gpt_ask_missing_entity(raw_text, "project", hint, space_id)
        result = {
            "ok": False,
            "text": text,
            "action": "ask_project"
        }
        if data:
            result["data"] = data
        return result

    # Validar categoria
    if not category_input:
        hint = "windows, HVAC, plumbing, framing, labor, materials"
        text = _gpt_ask_missing_entity(raw_text, "category", hint, space_id)
        return {
            "ok": False,
            "text": text,
            "action": "ask_category"
        }

    try:
        # Resolver proyecto
        project = resolve_project(project_input)
        if not project:
            return {
                "ok": False,
                "text": f"⚠️ No encontré el proyecto '{project_input}'. Verifica el nombre.",
                "action": "project_not_found"
            }

        project_id = project.get("project_id") or project.get("id")
        project_name = project.get("project_name") or project.get("name") or project_input

        # Obtener datos
        budgets = fetch_budgets(project_id)
        expenses = fetch_expenses(project_id)
        accounts = fetch_accounts()

        # Buscar grupo de cuentas (por AccountCategory)
        group_data = find_group_data(category_input, budgets, expenses, accounts)

        if not group_data:
            # Sugerir categorias similares usando TODAS las cuentas de la tabla accounts
            available_categories = get_available_categories(budgets, expenses, accounts, only_with_data=False)
            suggestions = find_similar_categories(category_input, available_categories)

            if suggestions:
                suggestion_text = ", ".join(suggestions[:5])
                return {
                    "ok": False,
                    "text": f"Could not find '{category_input}' in {project_name}.\n\nDid you mean: {suggestion_text}?",
                    "action": "category_not_found",
                    "data": {"suggestions": suggestions}
                }
            else:
                return {
                    "ok": False,
                    "text": f"Could not find category '{category_input}' in {project_name}.",
                    "action": "category_not_found"
                }

        # Formatear respuesta agrupada
        response = format_group_response(project_name, group_data)

        return {
            "ok": True,
            "text": response,
            "action": "category_query_response",
            "data": {
                "project_id": project_id,
                "project_name": project_name,
                "group_name": group_data.get("group_name"),
                "match_type": group_data.get("match_type"),
                "accounts": group_data.get("accounts"),
                "group_totals": group_data.get("group_totals")
            }
        }

    except Exception as e:
        print(f"[BVA] Error in consulta especifica: {e}")
        return {
            "ok": False,
            "text": "Error querying data. Please try again.",
            "action": "query_error"
        }


def find_category_data(
    category_input: str,
    budgets: List[Dict[str, Any]],
    expenses: List[Dict[str, Any]],
    accounts: List[Dict[str, Any]]
) -> Optional[Dict[str, Any]]:
    """
    Busca una categoría en los datos del BVA y retorna sus métricas.
    Usa la tabla accounts como fuente de verdad para nombres de categorías.
    Hace búsqueda fuzzy para encontrar coincidencias parciales.
    """
    category_lower = category_input.lower()

    # Mapeo de términos comunes español-inglés
    CATEGORY_ALIASES = {
        "ventanas": ["window", "windows", "ventana"],
        "hvac": ["hvac", "heating", "cooling", "aire acondicionado", "calefaccion"],
        "plomeria": ["plumbing", "plomeria", "plumber"],
        "electricidad": ["electrical", "electric", "electricidad", "electrico"],
        "framing": ["framing", "frame", "estructura"],
        "drywall": ["drywall", "sheetrock"],
        "pintura": ["paint", "painting", "pintura"],
        "piso": ["flooring", "floor", "piso", "pisos"],
        "techo": ["roof", "roofing", "techo"],
        "cocina": ["kitchen", "cocina", "cabinets", "gabinetes"],
        "baño": ["bathroom", "bath", "baño", "bano"],
        "puertas": ["door", "doors", "puerta", "puertas"],
        "concreto": ["concrete", "concreto", "foundation", "cimentacion"],
        "landscaping": ["landscaping", "landscape", "jardineria", "jardin"],
        "appliances": ["appliances", "appliance", "electrodomesticos"],
        "insulation": ["insulation", "aislamiento"],
        "labor": ["labor", "mano de obra", "trabajadores"],
        "materials": ["materials", "materiales", "material"],
    }

    # Expandir términos de búsqueda
    search_terms = [category_lower]
    used_alias = False
    for alias_key, alias_values in CATEGORY_ALIASES.items():
        if category_lower in alias_values or category_lower == alias_key:
            search_terms.extend(alias_values)
            search_terms.append(alias_key)
            used_alias = True

    # Fallback: if no alias matched, try individual words from the input
    if not used_alias and len(category_lower.split()) > 1:
        for word in category_lower.split():
            for alias_key, alias_values in CATEGORY_ALIASES.items():
                if word in alias_values or word == alias_key:
                    search_terms.extend(alias_values)
                    search_terms.append(alias_key)
                    used_alias = True

    search_terms = list(set(search_terms))

    # Helper para obtener nombre de cuenta
    def get_account_name(account_id: str, account_name: str = None) -> str:
        if account_name:
            return account_name
        if account_id:
            for acc in accounts:
                if (acc.get("account_id") or acc.get("id")) == account_id:
                    return acc.get("Name") or acc.get("account_name") or "Unknown"
        return "Unknown Account"

    # Función para verificar si es coincidencia exacta
    def is_exact_match(name: str, query: str) -> bool:
        return name.lower() == query.lower()

    # PASO 1: Buscar coincidencia EXACTA primero (en accounts y data)
    exact_match = None
    for acc in accounts:
        acc_name = acc.get("Name") or acc.get("account_name")
        if acc_name and is_exact_match(acc_name, category_input):
            exact_match = acc_name
            break

    # PASO 2: Buscar coincidencia en la tabla accounts (fuente de verdad)
    matched_account_from_table = None
    match_type = "exact"  # Tipo de coincidencia encontrada

    if exact_match:
        matched_account_from_table = exact_match
    else:
        for acc in accounts:
            acc_name = acc.get("Name") or acc.get("account_name")
            if not acc_name:
                continue
            acc_lower = acc_name.lower()
            for term in search_terms:
                if term in acc_lower or acc_lower in term:
                    matched_account_from_table = acc_name
                    match_type = "fuzzy"  # No fue exacta
                    break
            if matched_account_from_table:
                break

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

    # PASO 3: Buscar coincidencia en datos existentes (budgets + expenses)
    all_data_accounts = set(list(budgets_by_account.keys()) + list(expenses_by_account.keys()))
    matched_account_from_data = None

    # Primero buscar exacta en datos
    for account_name in all_data_accounts:
        if is_exact_match(account_name, category_input):
            matched_account_from_data = account_name
            match_type = "exact"
            break

    # Si no hay exacta, buscar fuzzy en datos
    if not matched_account_from_data:
        for account_name in all_data_accounts:
            account_lower = account_name.lower()
            for term in search_terms:
                if term in account_lower or account_lower in term:
                    matched_account_from_data = account_name
                    if match_type != "exact":
                        match_type = "fuzzy"
                    break
            if matched_account_from_data:
                break

    # Usar coincidencia de datos si existe, sino de tabla accounts
    matched_account = matched_account_from_data or matched_account_from_table

    if not matched_account:
        return None

    # Determinar si hay que comunicar que fue búsqueda aproximada
    # Solo si el nombre encontrado es diferente al buscado
    is_approximate = (
        match_type == "fuzzy" and
        matched_account.lower() != category_input.lower()
    )

    # Calcular métricas
    budget_amount = budgets_by_account.get(matched_account, 0)
    actual_amount = expenses_by_account.get(matched_account, 0)
    balance = budget_amount - actual_amount
    percent = (actual_amount / budget_amount * 100) if budget_amount > 0 else 0

    return {
        "matched_name": matched_account,
        "searched_term": category_input,  # Lo que el usuario buscó
        "is_approximate": is_approximate,  # Si fue coincidencia aproximada
        "budget": round(budget_amount, 2),
        "actual": round(actual_amount, 2),
        "balance": round(balance, 2),
        "percent_of_budget": round(percent, 2),
        "has_data": budget_amount > 0 or actual_amount > 0  # Indica si tiene datos
    }


def get_available_categories(
    budgets: List[Dict[str, Any]],
    expenses: List[Dict[str, Any]],
    accounts: List[Dict[str, Any]],
    only_with_data: bool = False
) -> List[str]:
    """
    Retorna lista de categorías disponibles.

    Args:
        accounts: Lista de todas las cuentas de la tabla accounts
        budgets: Budgets del proyecto (para filtrar si only_with_data=True)
        expenses: Expenses del proyecto (para filtrar si only_with_data=True)
        only_with_data: Si True, solo retorna categorías con budgets o expenses

    Returns:
        Lista de nombres de categorías ordenadas
    """
    # Usar accounts como fuente principal de categorías
    all_account_names = []
    for acc in accounts:
        name = acc.get("Name") or acc.get("account_name")
        if name:
            all_account_names.append(name)

    if not only_with_data:
        # Retornar TODAS las categorías de la tabla accounts
        return sorted(list(set(all_account_names)))

    # Si only_with_data, filtrar solo las que tienen budgets o expenses
    def get_account_name(account_id: str, account_name: str = None) -> str:
        if account_name:
            return account_name
        if account_id:
            for acc in accounts:
                if (acc.get("account_id") or acc.get("id")) == account_id:
                    return acc.get("Name") or acc.get("account_name") or "Unknown"
        return "Unknown Account"

    categories_with_data = set()
    for budget in budgets:
        categories_with_data.add(get_account_name(budget.get("account_id"), budget.get("account_name")))
    for expense in expenses:
        categories_with_data.add(get_account_name(expense.get("account_id"), expense.get("account_name")))

    return sorted([c for c in categories_with_data if c != "Unknown Account"])


def find_similar_categories(query: str, categories: List[str]) -> List[str]:
    """
    Encuentra categorías similares a la consulta usando fuzzy matching.
    Ordena por relevancia (coincidencias más cercanas primero).
    """
    query_lower = query.lower()
    query_words = query_lower.split()

    # Categorías con puntuación de relevancia
    scored_matches = []

    # Mapeo de términos comunes español-inglés para mejorar búsqueda
    TERM_TRANSLATIONS = {
        "ventanas": "window", "ventana": "window",
        "plomeria": "plumbing", "plomería": "plumbing",
        "electricidad": "electrical", "electrico": "electrical", "eléctrico": "electrical",
        "pintura": "paint",
        "piso": "floor", "pisos": "floor",
        "techo": "roof",
        "cocina": "kitchen",
        "baño": "bathroom", "bano": "bathroom",
        "puertas": "door", "puerta": "door",
        "concreto": "concrete",
        "jardin": "landscape", "jardín": "landscape",
        "aislamiento": "insulation",
    }

    # Expandir query con traducciones
    expanded_query = [query_lower] + query_words
    for word in query_words:
        if word in TERM_TRANSLATIONS:
            expanded_query.append(TERM_TRANSLATIONS[word])

    for cat in categories:
        cat_lower = cat.lower()
        score = 0

        # Coincidencia exacta (mayor puntuación)
        if query_lower == cat_lower:
            score = 100
        # Query está contenido en categoría
        elif query_lower in cat_lower:
            score = 80
        # Categoría está contenida en query
        elif cat_lower in query_lower:
            score = 70
        else:
            # Coincidencia de palabras individuales
            for term in expanded_query:
                if term in cat_lower:
                    score = max(score, 50)
                # Coincidencia parcial de palabra
                elif any(term in word or word in term for word in cat_lower.split()):
                    score = max(score, 30)

        if score > 0:
            scored_matches.append((cat, score))

    # Ordenar por puntuación y retornar top 5
    scored_matches.sort(key=lambda x: x[1], reverse=True)
    return [cat for cat, score in scored_matches[:5]]


def format_category_response(project_name: str, category_query: str, data: Dict[str, Any]) -> str:
    """Formatea la respuesta de consulta específica."""
    def fmt(amount: float) -> str:
        if amount < 0:
            return f"-${abs(amount):,.2f}"
        return f"${amount:,.2f}"

    matched_name = data["matched_name"]
    searched_term = data.get("searched_term", category_query)
    is_approximate = data.get("is_approximate", False)
    budget = data["budget"]
    actual = data["actual"]
    balance = data["balance"]
    percent = data["percent_of_budget"]
    has_data = data.get("has_data", True)

    # Build header with approximate match message if applicable
    if is_approximate:
        header = f"No exact match for *{searched_term}*, closest: *{matched_name}*:\n\n"
    else:
        header = ""

    # Special case: category exists but has no data
    if not has_data:
        no_data_msg = f"""*{matched_name}* in *{project_name}*

This category exists but has no budget or expenses recorded yet.

You can add a budget from the Reporting page."""
        return header + no_data_msg if header else no_data_msg

    # Determine status
    if balance > 0:
        status_text = "available"
    elif balance == 0:
        status_text = "depleted"
    else:
        status_text = "over budget"

    response = f"""*{matched_name}* in *{project_name}*

Budget: {fmt(budget)}
Actual: {fmt(actual)}
Available: {fmt(balance)} ({status_text})
Used: {percent:.1f}%"""

    # Add context based on status
    if balance < 0:
        response += f"\n\nThis category is *{fmt(abs(balance))}* over budget."
    elif percent >= 90:
        response += f"\n\nAlmost depleted - only {100-percent:.1f}% remaining."
    elif percent <= 10 and budget > 0:
        response += f"\n\nBarely used so far."

    return header + response if header else response


# ================================
# GROUP-LEVEL QUERIES (AccountCategory)
# ================================

def find_group_data(
    category_input: str,
    budgets: List[Dict[str, Any]],
    expenses: List[Dict[str, Any]],
    accounts: List[Dict[str, Any]]
) -> Optional[Dict[str, Any]]:
    """
    Finds BvA data grouped by AccountCategory.

    Logic:
    1. Try matching input against AccountCategory names (group-level match).
    2. If no group match, find the individual account via find_category_data(),
       then look up its AccountCategory to get the full group.
    3. In both cases, return all accounts in the group with individual BvA + totals.

    Returns dict with: matched_name, searched_term, group_name, match_type,
                       accounts[], group_totals{}
    """
    input_lower = category_input.lower().strip()

    # -- Build lookups --
    account_info = {}
    for acc in accounts:
        aid = acc.get("account_id") or acc.get("id")
        name = acc.get("Name") or acc.get("account_name") or "Unknown"
        group = acc.get("AccountCategory") or ""
        account_info[aid] = {"name": name, "category": group}

    # AccountCategory -> [account_names]
    groups = {}
    for acc in accounts:
        cat = acc.get("AccountCategory")
        name = acc.get("Name") or acc.get("account_name")
        if cat and name:
            groups.setdefault(cat, []).append(name)

    # -- Expand search with aliases (reuse same map as find_category_data) --
    CATEGORY_ALIASES = {
        "ventanas": ["window", "windows", "ventana"],
        "hvac": ["hvac", "heating", "cooling", "aire acondicionado", "calefaccion"],
        "plomeria": ["plumbing", "plomeria", "plumber"],
        "electricidad": ["electrical", "electric", "electricidad", "electrico"],
        "framing": ["framing", "frame", "estructura"],
        "drywall": ["drywall", "sheetrock"],
        "pintura": ["paint", "painting", "pintura"],
        "piso": ["flooring", "floor", "piso", "pisos"],
        "techo": ["roof", "roofing", "techo"],
        "cocina": ["kitchen", "cocina", "cabinets", "gabinetes"],
        "bano": ["bathroom", "bath", "bano"],
        "puertas": ["door", "doors", "puerta", "puertas"],
        "concreto": ["concrete", "concreto", "foundation", "cimentacion"],
        "landscaping": ["landscaping", "landscape", "jardineria", "jardin"],
        "appliances": ["appliances", "appliance", "electrodomesticos"],
        "insulation": ["insulation", "aislamiento"],
        "labor": ["labor", "mano de obra", "trabajadores"],
        "materials": ["materials", "materiales", "material"],
    }

    search_terms = [input_lower]
    for alias_key, alias_values in CATEGORY_ALIASES.items():
        if input_lower in alias_values or input_lower == alias_key:
            search_terms.extend(alias_values)
            search_terms.append(alias_key)
    # Fallback: if no alias matched, try individual words from the input
    # (e.g. "gastar en ventanas" -> check "ventanas" against aliases)
    if len(search_terms) == 1 and len(input_lower.split()) > 1:
        for word in input_lower.split():
            for alias_key, alias_values in CATEGORY_ALIASES.items():
                if word in alias_values or word == alias_key:
                    search_terms.extend(alias_values)
                    search_terms.append(alias_key)
    search_terms = list(set(search_terms))

    # -- Step 1: Keyword aggregation (checked FIRST) --
    # Keywords like "labor" or "materials" appear across many accounts
    # (e.g. "Rough Framing Labor", "Plumbing Labor", "HVAC Labor").
    # If the search term matches multiple account names as a substring,
    # build a virtual group from all of them.
    # This runs before group matching so that cross-category keywords
    # return ALL matching accounts, not just one AccountCategory group.
    KEYWORD_ALIASES = {
        "labor": ["labor", "mano de obra", "trabajadores"],
        "materials": ["materials", "materiales", "material"],
    }
    is_keyword_search = False
    keyword_terms = list(search_terms)
    for kw_key, kw_values in KEYWORD_ALIASES.items():
        if input_lower in kw_values or input_lower == kw_key:
            keyword_terms.append(kw_key)
            keyword_terms.extend(kw_values)
            is_keyword_search = True
    keyword_terms = list(set(keyword_terms))

    if is_keyword_search:
        # Collect all account names that contain any keyword term
        keyword_matched_names = []
        for acc in accounts:
            acc_name = acc.get("Name") or acc.get("account_name") or ""
            acc_lower = acc_name.lower()
            for term in keyword_terms:
                if term in acc_lower:
                    keyword_matched_names.append(acc_name)
                    break

        # Use keyword aggregation if we match 1+ accounts
        if keyword_matched_names:
            def _get_acc_name(account_id, account_name=None):
                if account_name:
                    return account_name
                info = account_info.get(account_id)
                return info["name"] if info else "Unknown"

            bba = {}
            for b in budgets:
                n = _get_acc_name(b.get("account_id"), b.get("account_name"))
                bba[n] = bba.get(n, 0) + float(b.get("amount_sum") or 0)
            eba = {}
            for e in expenses:
                n = _get_acc_name(e.get("account_id"), e.get("account_name"))
                amt = float(e.get("Amount") or e.get("amount") or 0)
                eba[n] = eba.get(n, 0) + amt

            kw_details = []
            kw_budget = 0
            kw_actual = 0
            for acc_name in sorted(keyword_matched_names):
                b = bba.get(acc_name, 0)
                a = eba.get(acc_name, 0)
                if b == 0 and a == 0:
                    continue
                bal = b - a
                pct = (a / b * 100) if b > 0 else 0
                kw_details.append({
                    "matched_name": acc_name,
                    "budget": round(b, 2),
                    "actual": round(a, 2),
                    "balance": round(bal, 2),
                    "percent_of_budget": round(pct, 2),
                    "has_data": True,
                    "is_matched": False,
                })
                kw_budget += b
                kw_actual += a

            if kw_details:
                kw_balance = kw_budget - kw_actual
                kw_pct = (kw_actual / kw_budget * 100) if kw_budget > 0 else 0
                display_name = category_input.strip().title()
                return {
                    "matched_name": display_name,
                    "searched_term": category_input,
                    "is_approximate": False,
                    "group_name": display_name,
                    "match_type": "keyword",
                    "accounts": kw_details,
                    "group_totals": {
                        "budget": round(kw_budget, 2),
                        "actual": round(kw_actual, 2),
                        "balance": round(kw_balance, 2),
                        "percent_of_budget": round(kw_pct, 2),
                    }
                }

    # -- Step 2: Try matching an AccountCategory name directly --
    matched_group = None
    match_type = None   # "group" or "account"
    matched_account_name = None

    for group_name in groups:
        group_lower = group_name.lower()
        for term in search_terms:
            if term == group_lower or term in group_lower or group_lower in term:
                matched_group = group_name
                match_type = "group"
                break
        if matched_group:
            break

    # -- Step 3: If no group match, find account then its group --
    if not matched_group:
        single = find_category_data(category_input, budgets, expenses, accounts)

        # Step 3b: GPT fallback - ask GPT to match against group + account names
        if not single:
            all_names = list(groups.keys()) + [
                acc.get("Name") or acc.get("account_name") or ""
                for acc in accounts if acc.get("Name") or acc.get("account_name")
            ]
            gpt_match = _gpt_fuzzy_match(category_input, list(set(all_names)))
            if gpt_match:
                # Check if GPT matched a group name
                if gpt_match in groups:
                    matched_group = gpt_match
                    match_type = "group"
                else:
                    # Re-run find_category_data with the GPT-resolved name
                    single = find_category_data(gpt_match, budgets, expenses, accounts)

        if not single and not matched_group:
            return None

        # If we matched a group via GPT, skip account lookup (single is None)
        if single:
            matched_account_name = single["matched_name"]

            # Look up the AccountCategory of the matched account
            for acc in accounts:
                name = acc.get("Name") or acc.get("account_name")
                if name and name.lower() == matched_account_name.lower():
                    matched_group = acc.get("AccountCategory")
                    match_type = "account"
                    break

        # Account has no group — return single account wrapped in group format
        if not matched_group and single:
            return {
                "matched_name": matched_account_name,
                "searched_term": category_input,
                "is_approximate": single.get("is_approximate", False),
                "group_name": None,
                "match_type": "account",
                "accounts": [{**single, "is_matched": True}],
                "group_totals": {
                    "budget": single["budget"],
                    "actual": single["actual"],
                    "balance": single["balance"],
                    "percent_of_budget": single["percent_of_budget"]
                }
            }

    # -- Step 4: Build BvA for every account in the group --
    group_account_names = groups.get(matched_group, [])

    def get_account_name(account_id, account_name=None):
        if account_name:
            return account_name
        info = account_info.get(account_id)
        return info["name"] if info else "Unknown"

    budgets_by_acc = {}
    for b in budgets:
        name = get_account_name(b.get("account_id"), b.get("account_name"))
        budgets_by_acc[name] = budgets_by_acc.get(name, 0) + float(b.get("amount_sum") or 0)

    expenses_by_acc = {}
    for e in expenses:
        name = get_account_name(e.get("account_id"), e.get("account_name"))
        amount = float(e.get("Amount") or e.get("amount") or 0)
        expenses_by_acc[name] = expenses_by_acc.get(name, 0) + amount

    account_details = []
    total_budget = 0
    total_actual = 0

    for acc_name in sorted(group_account_names):
        b = budgets_by_acc.get(acc_name, 0)
        a = expenses_by_acc.get(acc_name, 0)
        if b == 0 and a == 0:
            continue  # Skip accounts with no data in this project

        bal = b - a
        pct = (a / b * 100) if b > 0 else 0

        account_details.append({
            "matched_name": acc_name,
            "budget": round(b, 2),
            "actual": round(a, 2),
            "balance": round(bal, 2),
            "percent_of_budget": round(pct, 2),
            "has_data": True,
            "is_matched": (
                matched_account_name is not None
                and acc_name.lower() == matched_account_name.lower()
            )
        })
        total_budget += b
        total_actual += a

    if not account_details:
        return None  # Group exists but no data for this project

    total_balance = total_budget - total_actual
    total_pct = (total_actual / total_budget * 100) if total_budget > 0 else 0

    is_approximate = (
        match_type == "account"
        and matched_account_name is not None
        and matched_account_name.lower() != category_input.lower()
    )

    return {
        "matched_name": matched_account_name or matched_group,
        "searched_term": category_input,
        "is_approximate": is_approximate,
        "group_name": matched_group,
        "match_type": match_type,
        "accounts": account_details,
        "group_totals": {
            "budget": round(total_budget, 2),
            "actual": round(total_actual, 2),
            "balance": round(total_balance, 2),
            "percent_of_budget": round(total_pct, 2)
        }
    }


def format_group_response(project_name: str, data: Dict[str, Any]) -> str:
    """Formats a grouped BvA query response for the chat widget."""
    def fmt(amount: float) -> str:
        if amount < 0:
            return f"-${abs(amount):,.2f}"
        return f"${amount:,.2f}"

    group_name = data.get("group_name")
    accts = data.get("accounts", [])
    totals = data.get("group_totals", {})
    searched = data.get("searched_term", "")
    matched = data.get("matched_name", "")
    is_approx = data.get("is_approximate", False)
    match_type = data.get("match_type", "account")

    lines = []

    # -- Header --
    if group_name:
        if match_type in ("group", "keyword"):
            lines.append(f"**{group_name}** in **{project_name}**")
        else:
            if is_approx:
                lines.append(f'"{searched}" -> **{matched}**')
            lines.append(f"Group **{group_name}** in **{project_name}**")
    else:
        lines.append(f"**{matched}** in **{project_name}**")

    lines.append("")

    # -- Per-account lines --
    for acc in accts:
        name = acc["matched_name"]
        bal = acc["balance"]
        marker = " <--" if acc.get("is_matched") else ""
        over = " (!)" if bal < 0 else ""
        lines.append(f"**{name}**{marker}")
        lines.append(f"  Budget: {fmt(acc['budget'])}  |  Actual: {fmt(acc['actual'])}  |  Avail: {fmt(bal)}{over}")
        lines.append("")

    # -- Group total (only if multiple accounts) --
    if len(accts) > 1:
        total_bal = totals["balance"]
        over_msg = " (over budget)" if total_bal < 0 else ""
        lines.append(f"--- **TOTAL {group_name or matched}** ---")
        lines.append(f"Budget: {fmt(totals['budget'])}  |  Actual: {fmt(totals['actual'])}  |  Avail: {fmt(total_bal)}{over_msg}")
        lines.append(f"Used: {totals['percent_of_budget']:.1f}%")
    else:
        # Single account — add status context
        total_bal = totals["balance"]
        pct = totals["percent_of_budget"]
        if total_bal < 0:
            lines.append(f"Over budget by {fmt(abs(total_bal))}")
        elif pct >= 90:
            lines.append(f"Almost depleted - {100 - pct:.1f}% remaining")

    return "\n".join(lines)
