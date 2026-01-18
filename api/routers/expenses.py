from fastapi import APIRouter, HTTPException, File, UploadFile
from pydantic import BaseModel
from api.supabase_client import supabase
from typing import Optional, List
import base64
import os
from openai import OpenAI
import json
import io
from pdf2image import convert_from_bytes
from PIL import Image

router = APIRouter(prefix="/expenses", tags=["Expenses"])


# ====== MODELOS ======

class ExpenseCreate(BaseModel):
    project: str  # UUID del proyecto
    txn_type: Optional[str] = None  # UUID del tipo de transacción
    TxnDate: Optional[str] = None  # Fecha en formato ISO
    vendor_id: Optional[str] = None  # UUID del vendor
    payment_type: Optional[str] = None  # UUID del método de pago
    Amount: Optional[float] = None
    LineDescription: Optional[str] = None
    TxnId_QBO: Optional[str] = None
    LineUID: Optional[str] = None
    show_on_reports: Optional[bool] = None
    coinciliation_status: Optional[bool] = None
    account_id: Optional[str] = None
    created_by: Optional[str] = None


class ExpenseUpdate(BaseModel):
    txn_type: Optional[str] = None
    TxnDate: Optional[str] = None
    vendor_id: Optional[str] = None
    payment_type: Optional[str] = None
    Amount: Optional[float] = None
    LineDescription: Optional[str] = None
    TxnId_QBO: Optional[str] = None
    LineUID: Optional[str] = None
    show_on_reports: Optional[bool] = None
    coinciliation_status: Optional[bool] = None
    account_id: Optional[str] = None
    auth_status: Optional[bool] = None
    auth_by: Optional[str] = None


# ====== HELPERS ======

def extract_rel_value(row: dict, rel_name: str, field: str):
    """
    Extrae un valor desde una relación embebida de Supabase.
    Soporta tanto dict como list.
    """
    rel = row.get(rel_name)
    if rel is None:
        return None

    if isinstance(rel, list):
        if not rel:
            return None
        rel = rel[0]

    if isinstance(rel, dict):
        return rel.get(field)

    return None


# ====== ENDPOINTS ======

@router.post("/", status_code=201)
def create_expense(payload: ExpenseCreate):
    """
    Crea un nuevo gasto
    """
    try:
        data = payload.model_dump(exclude_none=True)

        # Validar project
        if data.get("project"):
            project = supabase.table("projects").select("project_id").eq("project_id", data["project"]).single().execute()
            if not project.data:
                raise HTTPException(status_code=400, detail="Invalid project")

        # Validar txn_type si se proporciona
        if data.get("txn_type"):
            txn = supabase.table("txn_types").select("TnxType_id").eq("TnxType_id", data["txn_type"]).single().execute()
            if not txn.data:
                raise HTTPException(status_code=400, detail="Invalid txn_type")

        # Insertar gasto
        res = supabase.table("expenses_manual_COGS").insert(data).execute()

        return {
            "message": "Expense created",
            "expense": res.data[0],
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/")
def list_expenses(project: Optional[str] = None, limit: int = 100):
    """
    Lista todos los gastos, opcionalmente filtrados por project.
    Incluye información de tipo de transacción, proyecto, vendor, etc.
    """
    try:
        # Obtener los gastos
        query = supabase.table("expenses_manual_COGS").select("*")

        if project:
            query = query.eq("project", project)

        resp = query.order("TxnDate", desc=True).limit(limit).execute()
        raw_expenses = resp.data or []

        # Obtener tipos de transacción
        txn_types_resp = supabase.table("txn_types").select("TnxType_id, TnxType_name").execute()
        txn_types_map = {t["TnxType_id"]: t for t in (txn_types_resp.data or [])}

        # Obtener proyectos
        projects_resp = supabase.table("projects").select("project_id, project_name").execute()
        projects_map = {p["project_id"]: p for p in (projects_resp.data or [])}

        # Obtener vendors
        vendors_resp = supabase.table("Vendors").select("id, vendor_name").execute()
        vendors_map = {v["id"]: v for v in (vendors_resp.data or [])}

        # Enriquecer los gastos con datos relacionados
        expenses = []
        for row in raw_expenses:
            # Agregar nombre de tipo de transacción
            txn = txn_types_map.get(row.get("txn_type"))
            if txn:
                row["txn_type_name"] = txn.get("TnxType_name")

            # Agregar nombre de proyecto
            proj = projects_map.get(row.get("project"))
            if proj:
                row["project_name"] = proj.get("project_name")

            # Agregar nombre de vendor
            vendor = vendors_map.get(row.get("vendor_id"))
            if vendor:
                row["vendor_name"] = vendor.get("vendor_name")

            expenses.append(row)

        return {"data": expenses}

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error leyendo expenses: {e}")


@router.get("/meta")
def get_expenses_meta():
    """
    Devuelve catálogos necesarios para la UI de expenses:
      - txn_types: tipos de transacción
      - projects: proyectos
      - vendors: proveedores
      - payment_methods: métodos de pago
      - accounts: cuentas contables
    """
    try:
        # Tipos de transacción
        txn_types_resp = supabase.table("txn_types").select("*").order("TnxType_name").execute()

        # Proyectos
        projects_resp = supabase.table("projects").select("project_id, project_name").order("project_name").execute()

        # Vendors
        vendors_resp = supabase.table("Vendors").select("*").order("vendor_name").execute()

        # Métodos de pago
        payment_methods_resp = supabase.table("paymet_methods").select("*").execute()

        # Cuentas
        accounts_resp = supabase.table("accounts").select("*").execute()

        return {
            "txn_types": txn_types_resp.data or [],
            "projects": projects_resp.data or [],
            "vendors": vendors_resp.data or [],
            "payment_methods": payment_methods_resp.data or [],
            "accounts": accounts_resp.data or [],
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error leyendo meta de expenses: {e}")


@router.get("/{expense_id}")
def get_expense(expense_id: str):
    """
    Obtiene un gasto específico por ID.
    Incluye información de transacción, proyecto, vendor, etc.
    """
    try:
        resp = (
            supabase
            .table("expenses_manual_COGS")
            .select("*")
            .eq("expense_id", expense_id)
            .single()
            .execute()
        )

        if not resp.data:
            raise HTTPException(status_code=404, detail="Expense not found")

        row = resp.data

        # Obtener información de tipo de transacción
        if row.get("txn_type"):
            txn_resp = supabase.table("txn_types").select("*").eq("TnxType_id", row["txn_type"]).single().execute()
            if txn_resp.data:
                row["txn_type_name"] = txn_resp.data.get("TnxType_name")

        # Obtener información de proyecto
        if row.get("project"):
            proj_resp = supabase.table("projects").select("project_name").eq("project_id", row["project"]).single().execute()
            if proj_resp.data:
                row["project_name"] = proj_resp.data.get("project_name")

        # Obtener información de vendor
        if row.get("vendor_id"):
            vendor_resp = supabase.table("Vendors").select("*").eq("id", row["vendor_id"]).single().execute()
            if vendor_resp.data:
                row["vendor_name"] = vendor_resp.data.get("vendor_name")

        return {"data": row}

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error leyendo expense: {e}")


@router.put("/{expense_id}")
def update_expense(expense_id: str, payload: ExpenseUpdate):
    """
    Actualiza un gasto existente
    """
    try:
        # Verificar que el gasto existe
        existing = supabase.table("expenses_manual_COGS").select("expense_id").eq("expense_id", expense_id).single().execute()
        if not existing.data:
            raise HTTPException(status_code=404, detail="Expense not found")

        # Preparar datos para actualizar (solo campos no nulos)
        data = {k: v for k, v in payload.model_dump().items() if v is not None}

        if not data:
            raise HTTPException(status_code=400, detail="No fields to update")

        # Validar txn_type si se está actualizando
        if "txn_type" in data:
            txn = supabase.table("txn_types").select("TnxType_id").eq("TnxType_id", data["txn_type"]).single().execute()
            if not txn.data:
                raise HTTPException(status_code=400, detail="Invalid txn_type")

        # Actualizar
        res = supabase.table("expenses_manual_COGS").update(data).eq("expense_id", expense_id).execute()

        return {
            "message": "Expense updated",
            "expense": res.data[0] if res.data else None,
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/{expense_id}")
def patch_expense(expense_id: str, payload: ExpenseUpdate):
    """
    Actualiza parcialmente un gasto existente.
    Solo se actualizan los campos proporcionados en el body.

    Campos actualizables:
    - TxnDate: fecha de la transacción (formato ISO)
    - txn_type: UUID del tipo de transacción
    - vendor_id: UUID del vendor
    - payment_type: UUID del método de pago
    - Amount: monto del gasto
    - LineDescription: descripción del gasto
    - auth_status: estado de autorización (boolean)
    - auth_by: UUID del usuario que autorizó
    """
    try:
        # Verificar que el gasto existe
        existing = supabase.table("expenses_manual_COGS").select("expense_id").eq("expense_id", expense_id).single().execute()
        if not existing.data:
            raise HTTPException(status_code=404, detail="Expense not found")

        # Preparar datos para actualizar (solo campos proporcionados)
        data = {k: v for k, v in payload.model_dump().items() if v is not None}

        if not data:
            raise HTTPException(status_code=400, detail="No fields to update")

        # Validar txn_type si se está actualizando
        if "txn_type" in data:
            txn = supabase.table("txn_types").select("TnxType_id").eq("TnxType_id", data["txn_type"]).single().execute()
            if not txn.data:
                raise HTTPException(status_code=400, detail="Invalid txn_type")

        # Validar vendor_id si se está actualizando
        if "vendor_id" in data:
            vendor = supabase.table("Vendors").select("id").eq("id", data["vendor_id"]).single().execute()
            if not vendor.data:
                raise HTTPException(status_code=400, detail="Invalid vendor_id")

        # Validar payment_type si se está actualizando
        if "payment_type" in data:
            payment = supabase.table("paymet_methods").select("id").eq("id", data["payment_type"]).single().execute()
            if not payment.data:
                raise HTTPException(status_code=400, detail="Invalid payment_type")

        # Actualizar
        res = supabase.table("expenses_manual_COGS").update(data).eq("expense_id", expense_id).execute()

        return {
            "message": "Expense updated successfully",
            "expense": res.data[0] if res.data else None,
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{expense_id}")
def delete_expense(expense_id: str):
    """
    Elimina un gasto
    """
    try:
        # Verificar que el gasto existe
        existing = supabase.table("expenses_manual_COGS").select("expense_id").eq("expense_id", expense_id).single().execute()
        if not existing.data:
            raise HTTPException(status_code=404, detail="Expense not found")

        # Eliminar
        supabase.table("expenses_manual_COGS").delete().eq("expense_id", expense_id).execute()

        return {"message": "Expense deleted"}

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/summary/by-txn-type")
def get_expenses_summary_by_txn_type(project: Optional[str] = None):
    """
    Obtiene un resumen de gastos agrupados por tipo de transacción.
    Opcionalmente filtrado por proyecto.
    """
    try:
        query = supabase.table("expenses_manual_COGS").select("txn_type, Amount")

        if project:
            query = query.eq("project", project)

        resp = query.execute()
        raw_expenses = resp.data or []

        # Obtener todos los tipos de transacción
        txn_types_resp = supabase.table("txn_types").select("TnxType_id, TnxType_name").execute()
        txn_types_map = {t["TnxType_id"]: t for t in (txn_types_resp.data or [])}

        # Agrupar por tipo de transacción
        summary = {}
        for expense in raw_expenses:
            txn_id = expense.get("txn_type")
            amount = expense.get("Amount", 0) or 0
            txn = txn_types_map.get(txn_id)

            if txn_id not in summary:
                summary[txn_id] = {
                    "txn_type_id": txn_id,
                    "txn_type_name": txn.get("TnxType_name") if txn else None,
                    "total": 0,
                    "count": 0
                }

            summary[txn_id]["total"] += amount
            summary[txn_id]["count"] += 1

        return {"data": list(summary.values())}

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error calculando resumen: {e}")


@router.get("/summary/by-project")
def get_expenses_summary_by_project():
    """
    Obtiene un resumen de gastos agrupados por proyecto.
    """
    try:
        resp = supabase.table("expenses_manual_COGS").select("project, Amount").execute()
        raw_expenses = resp.data or []

        # Obtener todos los proyectos
        projects_resp = supabase.table("projects").select("project_id, project_name").execute()
        projects_map = {proj["project_id"]: proj for proj in (projects_resp.data or [])}

        # Agrupar por proyecto
        summary = {}
        for expense in raw_expenses:
            proj_id = expense.get("project")
            amount = expense.get("Amount", 0) or 0
            proj = projects_map.get(proj_id)

            if proj_id not in summary:
                summary[proj_id] = {
                    "project_id": proj_id,
                    "project_name": proj.get("project_name") if proj else None,
                    "total": 0,
                    "count": 0
                }

            summary[proj_id]["total"] += amount
            summary[proj_id]["count"] += 1

        return {"data": list(summary.values())}

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error calculando resumen: {e}")



@router.post("/auto-categorize")
async def auto_categorize_expenses(payload: dict):
    """
    Auto-categorizes expenses using GPT-4 based on construction stage and description.

    Request body:
    {
        "stage": "Framing",  // Construction stage
        "expenses": [
            {
                "rowIndex": 0,
                "description": "Wood stud 2x4"
            }
        ]
    }

    Response:
    {
        "success": true,
        "categorizations": [
            {
                "rowIndex": 0,
                "account_id": "account-uuid",
                "account_name": "Lumber & Materials",
                "confidence": 95
            }
        ]
    }
    """
    try:
        stage = payload.get("stage")
        expenses = payload.get("expenses", [])

        if not stage or not expenses:
            raise HTTPException(status_code=400, detail="Missing stage or expenses")

        # Get all accounts from database
        accounts_resp = supabase.table("accounts").select("account_id, Name, AcctNum").execute()
        accounts = accounts_resp.data or []

        if not accounts:
            raise HTTPException(status_code=500, detail="No accounts found in database")

        # Get OpenAI API key
        openai_api_key = os.getenv("OPENAI_API_KEY")
        if not openai_api_key or openai_api_key == "your-openai-api-key-here":
            raise HTTPException(status_code=500, detail="OpenAI API key not configured")

        # Initialize OpenAI client
        client = OpenAI(api_key=openai_api_key)

        # Build accounts list for GPT prompt
        accounts_list = []
        for acc in accounts:
            acc_info = {
                "account_id": acc.get("account_id"),
                "name": acc.get("Name"),
                "number": acc.get("AcctNum")
            }
            accounts_list.append(acc_info)

        # Build GPT prompt
        prompt = f"""You are an expert construction accountant specializing in categorizing expenses.

CONSTRUCTION STAGE: {stage}

AVAILABLE ACCOUNTS:
{json.dumps(accounts_list, indent=2)}

EXPENSE DESCRIPTIONS TO CATEGORIZE:
{json.dumps([{"rowIndex": e["rowIndex"], "description": e["description"]} for e in expenses], indent=2)}

INSTRUCTIONS:
1. For each expense description, determine the MOST APPROPRIATE account from the available accounts list.
2. Consider the construction stage when categorizing. For example:
   - "Wood stud" in "Framing" stage → likely "Lumber & Materials" or similar
   - "Wood stud" in "Roof" stage → likely "Roofing Materials" or similar
   - Same material can have different categorizations based on stage
3. Calculate a confidence score (0-100) based on:
   - How well the description matches the account (50% weight)
   - How appropriate the stage is for this account (30% weight)
   - How specific/detailed the description is (20% weight)
4. ONLY use account_id values from the provided accounts list - do NOT invent accounts
5. If no good match exists, use the most general/appropriate account with confidence <60

SPECIAL RULES - VERY IMPORTANT:
⚠️ POWER TOOLS (drills, saws, grinders, nail guns, etc.) are CAPITAL ASSETS and should NOT be categorized in COGS accounts.
   - If you detect a power tool (the tool itself, not consumables), set confidence to 0 and add "WARNING: Power tool - not a COGS expense" in reasoning
   - Consumables FOR power tools (drill bits, saw blades, nails, etc.) ARE valid COGS and should be categorized normally

✓ BEVERAGES & REFRESHMENTS (water bottles, energy drinks, coffee, sports drinks, etc.) should be categorized under "Base Materials" account
   - These are considered crew provisions and ARE valid construction expenses

Return ONLY valid JSON in this format:
{{
  "categorizations": [
    {{
      "rowIndex": 0,
      "account_id": "exact-account-id-from-list",
      "account_name": "exact-account-name-from-list",
      "confidence": 85,
      "reasoning": "Brief explanation of why this account was chosen",
      "warning": "Optional warning message for special cases like power tools"
    }}
  ]
}}

IMPORTANT:
- Match rowIndex from input to output
- Use EXACT account_id and Name from the accounts list
- Confidence must be 0-100
- Be conservative with confidence scores - better to under-estimate than over-estimate
- DO NOT include any text before or after the JSON"""

        # Call OpenAI
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {
                    "role": "system",
                    "content": "You are a construction accounting expert. You always return valid JSON with accurate account categorizations."
                },
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            temperature=0.3,  # Low temperature for more consistent results
            max_tokens=2000
        )

        # Parse response
        result_text = response.choices[0].message.content.strip()

        try:
            # Try direct JSON parse
            parsed_data = json.loads(result_text)
        except json.JSONDecodeError:
            # Try to extract JSON from markdown code blocks
            import re
            json_match = re.search(r'```json\s*(.*?)\s*```', result_text, re.DOTALL)
            if json_match:
                parsed_data = json.loads(json_match.group(1))
            else:
                # Try to find any JSON in the text
                json_match = re.search(r'\{.*\}', result_text, re.DOTALL)
                if json_match:
                    parsed_data = json.loads(json_match.group(0))
                else:
                    raise HTTPException(
                        status_code=500,
                        detail=f"OpenAI returned invalid JSON: {result_text[:500]}"
                    )

        # Validate response structure
        if "categorizations" not in parsed_data or not isinstance(parsed_data["categorizations"], list):
            raise HTTPException(
                status_code=500,
                detail="OpenAI response missing 'categorizations' array"
            )

        # Validate each categorization
        for cat in parsed_data["categorizations"]:
            required_fields = ["rowIndex", "account_id", "account_name", "confidence"]
            if not all(field in cat for field in required_fields):
                raise HTTPException(
                    status_code=500,
                    detail=f"Categorization missing required fields: {cat}"
                )

            # Validate confidence is 0-100
            if not (0 <= cat["confidence"] <= 100):
                cat["confidence"] = max(0, min(100, cat["confidence"]))

        return {
            "success": True,
            "categorizations": parsed_data["categorizations"]
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error auto-categorizing expenses: {str(e)}")


@router.post("/parse-receipt")
async def parse_receipt(file: UploadFile = File(...)):
    """
    Parsea un recibo/factura usando OpenAI Vision API.

    Acepta: imágenes (JPG, PNG, WebP, GIF) y PDFs

    Para PDFs, se convierte la primera página a imagen antes de procesarla.

    Retorna: JSON estructurado con los gastos detectados

    Formato de respuesta:
    {
        "success": true,
        "data": {
            "expenses": [
                {
                    "date": "2025-01-17",
                    "description": "Office supplies",
                    "vendor": "Staples",
                    "amount": 45.99,
                    "category": "Office Expenses"
                }
            ]
        },
        "count": 1
    }
    """
    try:
        # Validar tipo de archivo
        allowed_types = ["image/jpeg", "image/jpg", "image/png", "image/webp", "image/gif", "application/pdf"]
        if file.content_type not in allowed_types:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid file type. Allowed: JPG, PNG, WebP, GIF, PDF. Got: {file.content_type}"
            )

        # Leer archivo
        file_content = await file.read()

        # Validar tamaño (máx 20MB para OpenAI)
        if len(file_content) > 20 * 1024 * 1024:
            raise HTTPException(status_code=400, detail="File too large. Maximum size is 20MB.")

        # Obtener API key de OpenAI
        openai_api_key = os.getenv("OPENAI_API_KEY")
        if not openai_api_key or openai_api_key == "your-openai-api-key-here":
            raise HTTPException(status_code=500, detail="OpenAI API key not configured")

        # Inicializar cliente de OpenAI
        client = OpenAI(api_key=openai_api_key)

        # Obtener lista de vendors de la base de datos
        vendors_resp = supabase.table("Vendors").select("vendor_name").execute()
        vendors_list = [v.get("vendor_name") for v in (vendors_resp.data or []) if v.get("vendor_name")]

        # Agregar "Unknown" a la lista si no está
        if "Unknown" not in vendors_list:
            vendors_list.append("Unknown")

        # Obtener lista de transaction types de la base de datos
        transaction_types_resp = supabase.table("txn_types").select("TnxType_id, TnxType_name").execute()
        transaction_types_list = [
            {"id": t.get("TnxType_id"), "name": t.get("TnxType_name")}
            for t in (transaction_types_resp.data or [])
            if t.get("TnxType_name")
        ]

        # Obtener lista de payment methods de la base de datos
        payment_methods_resp = supabase.table("paymet_methods").select("id, payment_method_name").execute()
        payment_methods_list = [
            {"id": p.get("id"), "name": p.get("payment_method_name")}
            for p in (payment_methods_resp.data or [])
            if p.get("payment_method_name")
        ]

        # Procesar PDF o imagen
        base64_image = None
        media_type = file.content_type

        if file.content_type == "application/pdf":
            # Convertir PDF a imagen (primera página)
            try:
                # Detectar sistema operativo para path de Poppler
                import platform
                poppler_path = None
                if platform.system() == "Windows":
                    # Windows: usar instalación local
                    poppler_path = r'C:\poppler\poppler-24.08.0\Library\bin'
                # En Linux (Render), poppler-utils está en PATH por defecto

                images = convert_from_bytes(
                    file_content,
                    first_page=1,
                    last_page=1,
                    dpi=200,
                    poppler_path=poppler_path
                )
                if not images:
                    raise HTTPException(status_code=400, detail="Could not convert PDF to image")

                # Convertir la primera página a base64
                img = images[0]
                buffer = io.BytesIO()
                img.save(buffer, format='PNG')
                buffer.seek(0)
                base64_image = base64.b64encode(buffer.getvalue()).decode('utf-8')
                media_type = "image/png"

            except Exception as pdf_error:
                raise HTTPException(
                    status_code=400,
                    detail=f"Error processing PDF: {str(pdf_error)}"
                )
        else:
            # Imagen directa
            base64_image = base64.b64encode(file_content).decode('utf-8')

        # Prompt para OpenAI - Instrucciones muy específicas
        prompt = f"""You are an expert at extracting expense data from receipts, invoices, and bills.

Analyze this receipt/invoice and extract ALL expense items in JSON format.

AVAILABLE VENDORS (you MUST match to one of these, or use "Unknown"):
{json.dumps(vendors_list, indent=2)}

AVAILABLE TRANSACTION TYPES (you MUST match to one of these by name, or use "Unknown"):
{json.dumps(transaction_types_list, indent=2)}

AVAILABLE PAYMENT METHODS (you MUST match to one of these by name, or use "Unknown"):
{json.dumps(payment_methods_list, indent=2)}

IMPORTANT RULES:
1. Extract EVERY line item as a separate expense (don't combine items)
2. For each item, extract:
   - date: Transaction date in YYYY-MM-DD format (if not on item, use receipt date)
   - description: Item description (be specific, include item name/details)
   - vendor: Match the merchant name to one from the AVAILABLE VENDORS list. Use case-insensitive matching and look for partial matches (e.g., "Home Depot #1234" matches "Home Depot"). If no match found, use "Unknown"
   - amount: Item amount as a number (without currency symbols)
   - category: Expense category (e.g., "Office Supplies", "Food & Beverage", "Transportation", "Utilities", etc.)
   - transaction_type: Match to one from AVAILABLE TRANSACTION TYPES list by analyzing the receipt type (invoice, purchase order, credit card statement, etc.). Use the EXACT "name" field. If uncertain, use "Unknown"
   - payment_method: Match to one from AVAILABLE PAYMENT METHODS list by looking for payment indicators on receipt (Cash, Credit Card, Check, etc.). Use the EXACT "name" field. If uncertain, use "Unknown"

3. If there are subtotals, taxes, or fees, include them as separate items with clear descriptions
4. If the receipt shows only ONE total (no itemization), create ONE expense with that total
5. Use the currency shown on the receipt (or USD if not specified)
6. CRITICAL: The vendor, transaction_type, and payment_method fields MUST be EXACTLY one of the names from their respective lists above, or "Unknown"

Return ONLY valid JSON in this exact format:
{{
  "expenses": [
    {{
      "date": "2025-01-17",
      "description": "Item name or description",
      "vendor": "Exact vendor name from VENDORS list or Unknown",
      "amount": 45.99,
      "category": "Category name",
      "transaction_type": "Exact name from TRANSACTION TYPES list or Unknown",
      "payment_method": "Exact name from PAYMENT METHODS list or Unknown"
    }}
  ]
}}

DO NOT include any text before or after the JSON. ONLY return the JSON object."""

        # Llamar a OpenAI Vision API
        response = client.chat.completions.create(
            model="gpt-4o",  # GPT-4 Vision model
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:{media_type};base64,{base64_image}",
                                "detail": "high"  # Alta calidad para mejor OCR
                            }
                        }
                    ]
                }
            ],
            max_tokens=2000,
            temperature=0.1  # Baja temperatura para respuestas más deterministas
        )

        # Extraer respuesta
        result_text = response.choices[0].message.content.strip()

        # Parsear JSON
        try:
            # Intentar parsear directamente
            parsed_data = json.loads(result_text)
        except json.JSONDecodeError:
            # Si falla, intentar extraer JSON de texto con markdown
            import re
            json_match = re.search(r'```json\s*(.*?)\s*```', result_text, re.DOTALL)
            if json_match:
                parsed_data = json.loads(json_match.group(1))
            else:
                # Intentar encontrar cualquier JSON en el texto
                json_match = re.search(r'\{.*\}', result_text, re.DOTALL)
                if json_match:
                    parsed_data = json.loads(json_match.group(0))
                else:
                    raise HTTPException(
                        status_code=500,
                        detail=f"OpenAI returned invalid JSON: {result_text[:200]}"
                    )

        # Validar estructura
        if "expenses" not in parsed_data or not isinstance(parsed_data["expenses"], list):
            raise HTTPException(
                status_code=500,
                detail="OpenAI response missing 'expenses' array"
            )

        # Validar cada expense
        for expense in parsed_data["expenses"]:
            if not all(key in expense for key in ["date", "description", "amount"]):
                raise HTTPException(
                    status_code=500,
                    detail="Some expenses are missing required fields (date, description, amount)"
                )

        return {
            "success": True,
            "data": parsed_data,
            "count": len(parsed_data["expenses"])
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error parsing receipt: {str(e)}")
