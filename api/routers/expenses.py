from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from api.supabase_client import supabase
from typing import Optional

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
