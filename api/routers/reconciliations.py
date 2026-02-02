# =============================================================================
# @process: QBO_Reconciliation
# @process_name: QuickBooks Expense Reconciliation
# @process_category: bookkeeping
# @process_trigger: manual
# @process_description: Match manual COGS expenses with QuickBooks Online transactions for accounting reconciliation
# @process_owner: Accountant
#
# @step: 1
# @step_name: Load QBO Transactions
# @step_type: action
# @step_description: Fetch unreconciled transactions from QuickBooks for project
# @step_connects_to: 2
#
# @step: 2
# @step_name: Load Manual Expenses
# @step_type: action
# @step_description: Get unreconciled manual COGS entries for matching
# @step_connects_to: 3
#
# @step: 3
# @step_name: Match Expenses
# @step_type: condition
# @step_description: Compare amounts and dates to find matching pairs
# @step_connects_to: 4, 5
#
# @step: 4
# @step_name: Create Link
# @step_type: action
# @step_description: Create reconciliation record linking QBO to manual expense
# @step_connects_to: 6
#
# @step: 5
# @step_name: Flag Unmatched
# @step_type: notification
# @step_description: Alert accountant about expenses that couldn't be matched
# @step_connects_to: 6
#
# @step: 6
# @step_name: Update Status
# @step_type: action
# @step_description: Mark both expenses as reconciled
# =============================================================================

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from api.supabase_client import supabase
from typing import Optional, List
from datetime import datetime

router = APIRouter(prefix="/expenses/reconciliations", tags=["Reconciliations"])


# ====== MODELOS ======

class ReconciliationItem(BaseModel):
    """Un item de reconciliación: 1 QBO expense -> múltiples manual expenses"""
    qbo_expense_id: str
    manual_expense_ids: List[str]
    matched_amount: Optional[float] = None


class ReconciliationCreate(BaseModel):
    """Payload para crear reconciliaciones"""
    project_id: str
    reconciliations: List[ReconciliationItem]


class ReconciliationSingle(BaseModel):
    """Para crear una reconciliación individual"""
    project_id: str
    qbo_expense_id: str
    qbo_txn_date: Optional[str] = None
    qbo_amount: Optional[float] = None
    qbo_description: Optional[str] = None
    qbo_vendor_name: Optional[str] = None
    manual_expense_id: str
    manual_amount: Optional[float] = None
    reconciled_by: Optional[str] = None
    notes: Optional[str] = None


# ====== ENDPOINTS ======

@router.get("")
def get_reconciliations(project: str):
    """
    Obtiene todas las reconciliaciones de un proyecto.
    Agrupa por qbo_expense_id para retornar en formato 1:N
    """
    try:
        if not project:
            raise HTTPException(status_code=400, detail="project query parameter is required")

        # Obtener todas las reconciliaciones del proyecto
        resp = supabase.table("expense_reconciliations") \
            .select("*") \
            .eq("project_id", project) \
            .order("reconciled_at", desc=True) \
            .execute()

        if not resp.data:
            return []

        # Agrupar por qbo_expense_id
        grouped = {}
        for row in resp.data:
            qbo_id = row.get("qbo_expense_id")
            if qbo_id not in grouped:
                grouped[qbo_id] = {
                    "qbo_expense_id": qbo_id,
                    "qbo_amount": row.get("qbo_amount"),
                    "qbo_description": row.get("qbo_description"),
                    "qbo_vendor_name": row.get("qbo_vendor_name"),
                    "qbo_txn_date": row.get("qbo_txn_date"),
                    "manual_expense_ids": [],
                    "matched_amount": 0,
                    "reconciled_at": row.get("reconciled_at"),
                    "reconciliation_ids": []
                }

            manual_id = row.get("manual_expense_id")
            if manual_id:
                grouped[qbo_id]["manual_expense_ids"].append(manual_id)
                grouped[qbo_id]["matched_amount"] += float(row.get("manual_amount") or 0)
                grouped[qbo_id]["reconciliation_ids"].append(row.get("reconciliation_id"))

        return list(grouped.values())

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("", status_code=201)
def create_reconciliations(payload: ReconciliationCreate):
    """
    Crea múltiples reconciliaciones.
    Cada item puede tener 1 QBO expense vinculado a múltiples manual expenses.
    """
    try:
        project_id = payload.project_id
        reconciliations = payload.reconciliations

        if not project_id:
            raise HTTPException(status_code=400, detail="project_id is required")

        if not reconciliations:
            raise HTTPException(status_code=400, detail="reconciliations array is required")

        # Validar que el proyecto existe
        project_check = supabase.table("projects") \
            .select("project_id") \
            .eq("project_id", project_id) \
            .single() \
            .execute()

        if not project_check.data:
            raise HTTPException(status_code=400, detail="Invalid project_id")

        # Obtener datos de QBO expenses para enriquecer
        qbo_ids = [r.qbo_expense_id for r in reconciliations]
        # Nota: Asumimos que tienes una tabla de QBO expenses o los datos vienen del frontend

        # Obtener datos de manual expenses para enriquecer
        all_manual_ids = []
        for r in reconciliations:
            all_manual_ids.extend(r.manual_expense_ids)

        manual_expenses_resp = supabase.table("expenses_manual_COGS") \
            .select("expense_id, Amount, LineDescription") \
            .in_("expense_id", all_manual_ids) \
            .execute()

        manual_map = {}
        if manual_expenses_resp.data:
            manual_map = {e["expense_id"]: e for e in manual_expenses_resp.data}

        # Preparar inserts
        rows_to_insert = []
        now = datetime.utcnow().isoformat()

        for recon in reconciliations:
            qbo_id = recon.qbo_expense_id
            matched_amount = recon.matched_amount or 0

            for manual_id in recon.manual_expense_ids:
                manual_exp = manual_map.get(manual_id, {})
                manual_amount = manual_exp.get("Amount")

                row = {
                    "project_id": project_id,
                    "qbo_expense_id": qbo_id,
                    "manual_expense_id": manual_id,
                    "manual_amount": manual_amount,
                    "reconciled_at": now,
                }
                rows_to_insert.append(row)

        if not rows_to_insert:
            raise HTTPException(status_code=400, detail="No valid reconciliation pairs to insert")

        # Eliminar reconciliaciones existentes para los mismos manual_expense_ids
        # (ya que un gasto manual solo puede estar en una reconciliación)
        existing_manual_ids = [r["manual_expense_id"] for r in rows_to_insert]
        supabase.table("expense_reconciliations") \
            .delete() \
            .in_("manual_expense_id", existing_manual_ids) \
            .execute()

        # Insertar nuevas reconciliaciones
        res = supabase.table("expense_reconciliations") \
            .insert(rows_to_insert) \
            .execute()

        return {
            "message": f"Successfully created {len(res.data)} reconciliation(s)",
            "count": len(res.data),
            "data": res.data
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{reconciliation_id}")
def delete_reconciliation(reconciliation_id: str):
    """
    Elimina una reconciliación específica por su ID
    """
    try:
        # Verificar que existe
        existing = supabase.table("expense_reconciliations") \
            .select("reconciliation_id") \
            .eq("reconciliation_id", reconciliation_id) \
            .single() \
            .execute()

        if not existing.data:
            raise HTTPException(status_code=404, detail="Reconciliation not found")

        # Eliminar
        supabase.table("expense_reconciliations") \
            .delete() \
            .eq("reconciliation_id", reconciliation_id) \
            .execute()

        return {"message": "Reconciliation deleted"}

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/by-qbo/{qbo_expense_id}")
def delete_reconciliations_by_qbo(qbo_expense_id: str, project: str):
    """
    Elimina todas las reconciliaciones de una factura QBO específica
    """
    try:
        if not project:
            raise HTTPException(status_code=400, detail="project query parameter is required")

        # Eliminar todas las reconciliaciones de este QBO expense en el proyecto
        supabase.table("expense_reconciliations") \
            .delete() \
            .eq("qbo_expense_id", qbo_expense_id) \
            .eq("project_id", project) \
            .execute()

        return {"message": f"Reconciliations for QBO expense {qbo_expense_id} deleted"}

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/by-manual/{manual_expense_id}")
def delete_reconciliation_by_manual(manual_expense_id: str):
    """
    Elimina la reconciliación de un gasto manual específico
    """
    try:
        # Eliminar
        supabase.table("expense_reconciliations") \
            .delete() \
            .eq("manual_expense_id", manual_expense_id) \
            .execute()

        return {"message": f"Reconciliation for manual expense {manual_expense_id} deleted"}

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/summary")
def get_reconciliation_summary(project: str):
    """
    Obtiene un resumen de reconciliaciones para un proyecto:
    - Total de facturas QBO reconciliadas
    - Total de gastos manuales reconciliados
    - Monto total reconciliado
    """
    try:
        if not project:
            raise HTTPException(status_code=400, detail="project query parameter is required")

        resp = supabase.table("expense_reconciliations") \
            .select("qbo_expense_id, manual_expense_id, manual_amount, qbo_amount") \
            .eq("project_id", project) \
            .execute()

        if not resp.data:
            return {
                "total_qbo_reconciled": 0,
                "total_manual_reconciled": 0,
                "total_matched_amount": 0,
                "reconciliation_count": 0
            }

        # Contar únicos
        qbo_ids = set()
        manual_ids = set()
        total_amount = 0

        for row in resp.data:
            qbo_ids.add(row.get("qbo_expense_id"))
            manual_ids.add(row.get("manual_expense_id"))
            total_amount += float(row.get("manual_amount") or 0)

        return {
            "total_qbo_reconciled": len(qbo_ids),
            "total_manual_reconciled": len(manual_ids),
            "total_matched_amount": round(total_amount, 2),
            "reconciliation_count": len(resp.data)
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
