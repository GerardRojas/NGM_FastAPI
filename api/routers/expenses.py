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
    bill_id: Optional[str] = None  # Invoice/Bill number
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


class ExpenseBatchCreate(BaseModel):
    """Modelo para crear múltiples gastos en una sola llamada"""
    expenses: List[ExpenseCreate]


class ExpenseUpdate(BaseModel):
    txn_type: Optional[str] = None
    TxnDate: Optional[str] = None
    bill_id: Optional[str] = None  # Invoice/Bill number
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


class ExpenseUpdateItem(BaseModel):
    """Un item para actualización batch - incluye el ID"""
    expense_id: str
    data: ExpenseUpdate


class ExpenseBatchUpdate(BaseModel):
    """Modelo para actualizar múltiples gastos en una sola llamada"""
    updates: List[ExpenseUpdateItem]


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

@router.post("", status_code=201)
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


@router.post("/batch", status_code=201)
def create_expenses_batch(payload: ExpenseBatchCreate):
    """
    Crea múltiples gastos en una sola operación (bulk insert).
    Mucho más eficiente que crear uno por uno.

    Returns:
        - created: lista de gastos creados exitosamente
        - failed: lista de errores (si los hay)
        - summary: resumen de la operación
    """
    try:
        expenses_data = []
        failed = []

        # Validar project una sola vez (asumimos mismo proyecto para todos)
        project_ids = set()
        for exp in payload.expenses:
            if exp.project:
                project_ids.add(exp.project)

        # Validar que todos los proyectos existan
        for project_id in project_ids:
            project = supabase.table("projects").select("project_id").eq("project_id", project_id).single().execute()
            if not project.data:
                raise HTTPException(status_code=400, detail=f"Invalid project: {project_id}")

        # Preparar datos para inserción bulk
        for idx, exp in enumerate(payload.expenses):
            try:
                data = exp.model_dump(exclude_none=True)
                expenses_data.append(data)
            except Exception as e:
                failed.append({
                    "index": idx,
                    "error": str(e),
                    "data": exp.model_dump() if exp else None
                })

        if not expenses_data:
            raise HTTPException(status_code=400, detail="No valid expenses to create")

        # Inserción bulk - una sola operación a la base de datos
        res = supabase.table("expenses_manual_COGS").insert(expenses_data).execute()

        created_expenses = res.data or []

        return {
            "message": f"Batch insert completed: {len(created_expenses)} created",
            "created": created_expenses,
            "failed": failed,
            "summary": {
                "total_requested": len(payload.expenses),
                "total_created": len(created_expenses),
                "total_failed": len(failed)
            }
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/batch")
def update_expenses_batch(payload: ExpenseBatchUpdate):
    """
    Actualiza múltiples gastos en una sola operación.
    Cada update se procesa individualmente pero en una sola llamada HTTP.

    Returns:
        - updated: lista de gastos actualizados exitosamente
        - failed: lista de errores (si los hay)
        - summary: resumen de la operación
    """
    try:
        updated = []
        failed = []

        for item in payload.updates:
            try:
                # Preparar datos para actualización (excluir None)
                update_data = item.data.model_dump(exclude_none=True)

                if not update_data:
                    failed.append({
                        "expense_id": item.expense_id,
                        "error": "No fields to update"
                    })
                    continue

                # Actualizar el gasto
                res = supabase.table("expenses_manual_COGS").update(update_data).eq(
                    "expense_id", item.expense_id
                ).execute()

                if res.data:
                    updated.append(res.data[0])
                else:
                    failed.append({
                        "expense_id": item.expense_id,
                        "error": "Expense not found or no changes made"
                    })

            except Exception as e:
                failed.append({
                    "expense_id": item.expense_id,
                    "error": str(e)
                })

        return {
            "message": f"Batch update completed: {len(updated)} updated, {len(failed)} failed",
            "updated": updated,
            "failed": failed,
            "summary": {
                "total_requested": len(payload.updates),
                "total_updated": len(updated),
                "total_failed": len(failed)
            }
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("")
def list_expenses(project: Optional[str] = None, limit: Optional[int] = None):
    """
    Lista todos los gastos, opcionalmente filtrados por project.
    Incluye información de tipo de transacción, proyecto, vendor, etc.
    Si no se especifica limit, devuelve todos los gastos.
    """
    try:
        # Obtener los gastos
        query = supabase.table("expenses_manual_COGS").select("*")

        if project:
            query = query.eq("project", project)

        query = query.order("TxnDate", desc=True)

        # Solo aplicar límite si se especifica
        if limit is not None:
            query = query.limit(limit)

        resp = query.execute()
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
        # base64_images es una lista para soportar PDFs multi-página
        base64_images = []
        media_type = file.content_type

        if file.content_type == "application/pdf":
            # Convertir PDF a imágenes (TODAS las páginas)
            try:
                # Detectar sistema operativo para path de Poppler
                import platform
                poppler_path = None
                if platform.system() == "Windows":
                    # Windows: usar instalación local
                    poppler_path = r'C:\poppler\poppler-24.08.0\Library\bin'
                # En Linux (Render), poppler-utils está en PATH por defecto

                # Convertir TODAS las páginas del PDF (sin límite de first_page/last_page)
                images = convert_from_bytes(
                    file_content,
                    dpi=200,
                    poppler_path=poppler_path
                )
                if not images:
                    raise HTTPException(status_code=400, detail="Could not convert PDF to image")

                # Convertir CADA página a base64
                for img in images:
                    buffer = io.BytesIO()
                    img.save(buffer, format='PNG')
                    buffer.seek(0)
                    base64_images.append(base64.b64encode(buffer.getvalue()).decode('utf-8'))

                media_type = "image/png"
                print(f"PDF processed: {len(base64_images)} page(s) converted to images")

            except Exception as pdf_error:
                raise HTTPException(
                    status_code=400,
                    detail=f"Error processing PDF: {str(pdf_error)}"
                )
        else:
            # Imagen directa (solo 1 imagen)
            base64_images = [base64.b64encode(file_content).decode('utf-8')]

        # Prompt para OpenAI - Instrucciones muy específicas
        # Indicar si es un documento multi-página
        page_count_hint = ""
        if len(base64_images) > 1:
            page_count_hint = f"\n\nIMPORTANT: This document has {len(base64_images)} pages. Analyze ALL pages and combine the data from all of them into a single response. The images are provided in page order (Page 1, Page 2, etc.).\n"

        prompt = f"""You are an expert at extracting expense data from receipts, invoices, and bills.
{page_count_hint}
Analyze this receipt/invoice and extract ALL expense items in JSON format.

AVAILABLE VENDORS (you MUST match to one of these, or use "Unknown"):
{json.dumps(vendors_list, indent=2)}

AVAILABLE TRANSACTION TYPES (you MUST match to one of these by name, or use "Unknown"):
{json.dumps(transaction_types_list, indent=2)}

AVAILABLE PAYMENT METHODS (you MUST match to one of these by name, or use "Unknown"):
{json.dumps(payment_methods_list, indent=2)}

IMPORTANT RULES:

1. ALWAYS USE LINE TOTALS - CRITICAL:
   - For each line item, ALWAYS use the LINE TOTAL (extended/calculated amount), NOT the unit price
   - Common column names for line totals: EXTENSION, EXT, AMOUNT, LINE TOTAL, TOTAL, SUBTOTAL (per line)
   - The line total is typically the RIGHTMOST dollar amount on each line
   - Examples:
     * "QTY: 80, PRICE EACH: $1.84, EXTENSION: $147.20" → amount is $147.20
     * "2 x $5.00 = $10.00" → amount is $10.00
     * "Widget (3 @ $25.00) ... $75.00" → amount is $75.00
     * "Service charge ..... $150.00" → amount is $150.00
   - NEVER use unit prices like "PRICE EACH", "UNIT PRICE", "per each", "@ $X.XX each"

2. DOCUMENT STRUCTURE - Adapt to ANY format:

   A) SIMPLE RECEIPTS (grocery stores, restaurants, retail):
      - Usually single page with items listed vertically
      - Look for: item name followed by price on the same line
      - Total at the bottom

   B) ITEMIZED INVOICES (contractors, services):
      - May have: Description, Quantity, Rate, Amount columns
      - Use the AMOUNT column (rightmost), not Rate

   C) COMPLEX MULTI-SECTION INVOICES (Home Depot, Lowe's, supply stores):
      - May have multiple sections: "CARRY OUT", "DELIVERY #1", "DELIVERY #2", etc.
      - May span multiple pages: "Page 1 of 2", "Continued on next page"
      - Extract items from ALL sections and ALL pages
      - Don't stop at section subtotals (MERCHANDISE TOTAL) - continue to find all items
      - The GRAND TOTAL at the end covers everything

   D) STATEMENTS/SUMMARIES:
      - May show only totals per category
      - Extract each category as a line item if no detail available

3. Extract EVERY line item as a separate expense (don't combine items)

4. For each item, extract:
   - date: Transaction date in YYYY-MM-DD format (look for: Date, Invoice Date, Transaction Date, or use document date)
   - bill_id: Invoice/Bill/Receipt number - extract from: "Invoice #", "Invoice No.", "Bill #", "Receipt #", "Ref #", "PO #", "Order #", "Transaction ID", "Document #", "Confirmation #", or any similar reference number at the top of the document. This is typically the same for all items in one receipt/invoice.
   - description: Item description (include quantity if shown, e.g., "3x Lumber 2x4", "Labor - 4 hours")
   - vendor: Match to AVAILABLE VENDORS list using partial/fuzzy matching. If not found, use "Unknown"
   - amount: The LINE TOTAL as a number (no currency symbols) - NOT the unit price!
   - category: Expense category (e.g., "Materials", "Labor", "Office Supplies", "Food & Beverage", "Transportation", "Utilities", "Equipment Rental", etc.)
   - transaction_type: Match to AVAILABLE TRANSACTION TYPES by document type. If uncertain, use "Unknown"
   - payment_method: Match to AVAILABLE PAYMENT METHODS by payment indicators on receipt. If uncertain, use "Unknown"

5. TAX DISTRIBUTION - CRITICAL:
   - If the receipt shows Sales Tax, Tax, HST, GST, VAT, IVA, or similar tax amounts, DO NOT create a separate tax line item
   - Instead, DISTRIBUTE the tax proportionally across all product/service line items based on each item's percentage of the subtotal
   - Example: Subtotal $100, Item A $60 (60%), Item B $40 (40%), Tax $8:
     * Item A final = $60 + ($8 × 0.60) = $64.80
     * Item B final = $40 + ($8 × 0.40) = $43.20
   - The sum of all final amounts MUST equal the receipt's TOTAL (including tax)
   - Add "tax_included" field to each item showing the tax amount added to it
   - NOTE: Even if tax rate shows 0%, check for actual tax line amounts

6. FEES ARE LINE ITEMS (not distributed):
   - These are NOT taxes and should be separate line items:
     * Delivery Fee, Shipping, Freight
     * Service Fee, Convenience Fee, Processing Fee
     * Handling Fee, Restocking Fee
     * Tip, Gratuity
     * Environmental fees (CA LUMBER FEE, recycling fee, etc.)
     * Fuel surcharge
   - Only actual TAX amounts (Sales Tax, VAT, GST, HST) get distributed

7. SINGLE TOTAL FALLBACK:
   - If the receipt shows only ONE total with no itemization, create ONE expense with that total amount
   - Use the vendor name or document title as the description

8. Use the currency shown on the receipt (default to USD if not specified)

9. CRITICAL: vendor, transaction_type, and payment_method MUST exactly match one from their respective lists, or use "Unknown"

VALIDATION - MANDATORY:
1. Find the GRAND TOTAL / TOTAL DUE / AMOUNT DUE shown on the receipt - this is "invoice_total"
2. Calculate the arithmetic sum of all your expense amounts - this is "calculated_sum"
3. Compare them:
   - If they match (within $0.02 tolerance), set "validation_passed" to true
   - If they DON'T match, set "validation_passed" to false and include a "validation_warning" message
4. The "invoice_total" must be the EXACT value printed on the receipt, not your calculation

Return ONLY valid JSON in this exact format:
{{
  "expenses": [
    {{
      "date": "2025-01-17",
      "bill_id": "INV-12345",
      "description": "Item name or description",
      "vendor": "Exact vendor name from VENDORS list or Unknown",
      "amount": 45.99,
      "category": "Category name",
      "transaction_type": "Exact name from TRANSACTION TYPES list or Unknown",
      "payment_method": "Exact name from PAYMENT METHODS list or Unknown",
      "tax_included": 3.45
    }}
  ],
  "tax_summary": {{
    "total_tax_detected": 8.00,
    "tax_label": "Sales Tax",
    "subtotal": 100.00,
    "grand_total": 108.00,
    "distribution": [
      {{"description": "Item A", "original_amount": 60.00, "tax_added": 4.80, "final_amount": 64.80}},
      {{"description": "Item B", "original_amount": 40.00, "tax_added": 3.20, "final_amount": 43.20}}
    ]
  }},
  "validation": {{
    "invoice_total": 108.00,
    "calculated_sum": 108.00,
    "validation_passed": true,
    "validation_warning": null
  }}
}}

IMPORTANT:
- If NO tax was detected on the receipt, set "tax_summary" to null
- The "tax_included" field in each expense should be the tax amount added to that specific item (0 if no tax was distributed to it, like for fees)
- The "invoice_total" MUST be the exact total shown on the receipt/invoice document
- If validation fails, explain in "validation_warning" why the numbers don't match (e.g., "Calculated sum $105.00 does not match invoice total $108.00 - possible missing item or rounding issue")

DO NOT include any text before or after the JSON. ONLY return the JSON object."""

        # Construir contenido del mensaje con TODAS las imágenes (para PDFs multi-página)
        message_content = [{"type": "text", "text": prompt}]

        # Agregar cada imagen/página al contenido
        for i, base64_img in enumerate(base64_images):
            message_content.append({
                "type": "image_url",
                "image_url": {
                    "url": f"data:{media_type};base64,{base64_img}",
                    "detail": "high"  # Alta calidad para mejor OCR
                }
            })

        print(f"Sending {len(base64_images)} image(s) to OpenAI Vision API")

        # Llamar a OpenAI Vision API
        response = client.chat.completions.create(
            model="gpt-4o",  # GPT-4 Vision model
            messages=[
                {
                    "role": "user",
                    "content": message_content
                }
            ],
            max_tokens=4000,  # Aumentado para documentos multi-página
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
