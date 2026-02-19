"""
Router para gestión de Bills (Facturas/Recibos)
Tabla: bills
"""
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from typing import Optional, List

from api.supabase_client import supabase

router = APIRouter(prefix="/bills", tags=["bills"])

_PAGE_SIZE = 1000


# ========================================
# Modelos Pydantic
# ========================================

class BillCreate(BaseModel):
    bill_id: str
    vendor_id: Optional[str] = None
    expected_total: Optional[float] = None
    status: Optional[str] = "open"  # open, closed, split
    split_projects: Optional[List[int]] = None
    receipt_url: Optional[str] = None
    notes: Optional[str] = None


class BillUpdate(BaseModel):
    vendor_id: Optional[str] = None
    expected_total: Optional[float] = None
    status: Optional[str] = None
    split_projects: Optional[List[int]] = None
    receipt_url: Optional[str] = None
    notes: Optional[str] = None


# ========================================
# Endpoints
# ========================================

@router.get("")
async def list_bills():
    """
    Lista todos los bills ordenados por fecha de creación (más recientes primero)
    """
    try:
        response = supabase.table("bills").select("*").order("created_at", desc=True).execute()
        return {"data": response.data or []}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching bills: {str(e)}")


@router.get("/{bill_id}")
async def get_bill(bill_id: str):
    """
    Obtiene un bill específico por ID
    """
    try:
        response = supabase.table("bills").select("*").eq("bill_id", bill_id).single().execute()

        if not response.data:
            raise HTTPException(status_code=404, detail="Bill not found")

        return response.data
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching bill: {str(e)}")


@router.get("/{bill_id}/expenses")
async def get_bill_expenses(
    bill_id: str,
    project_id: Optional[str] = Query(None, description="Filter expenses by project UUID"),
):
    """
    Obtiene todos los gastos asociados a un bill específico.
    Si se proporciona project_id, filtra solo los gastos de ese proyecto
    (evita sumar gastos de otros proyectos que comparten el mismo bill_id).
    """
    try:
        # Paginated fetch to avoid Supabase 1000-row silent truncation
        all_expenses = []
        offset = 0
        while True:
            q = supabase.table("expenses_manual_COGS").select("*").eq("bill_id", bill_id)
            if project_id:
                q = q.eq("project", project_id)
            batch = q.range(offset, offset + _PAGE_SIZE - 1).execute().data or []
            all_expenses.extend(batch)
            if len(batch) < _PAGE_SIZE:
                break
            offset += _PAGE_SIZE

        return {
            "bill_id": bill_id,
            "expenses": all_expenses,
            "count": len(all_expenses)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching bill expenses: {str(e)}")


@router.post("")
async def create_bill(bill: BillCreate):
    """
    Crea un nuevo bill
    """
    try:
        # Verificar que no exista un bill con el mismo ID
        existing = supabase.table("bills").select("bill_id").eq("bill_id", bill.bill_id).execute()

        if existing.data and len(existing.data) > 0:
            raise HTTPException(status_code=400, detail="Bill with this ID already exists")

        # Validar status
        valid_statuses = ["open", "closed", "split"]
        if bill.status and bill.status.lower() not in valid_statuses:
            raise HTTPException(status_code=400, detail=f"Invalid status. Must be one of: {', '.join(valid_statuses)}")

        insert_data = {
            "bill_id": bill.bill_id,
            "status": (bill.status or "open").lower()
        }

        if bill.vendor_id:
            insert_data["vendor_id"] = bill.vendor_id
        if bill.expected_total is not None:
            insert_data["expected_total"] = bill.expected_total
        if bill.split_projects:
            insert_data["split_projects"] = bill.split_projects
        if bill.receipt_url:
            insert_data["receipt_url"] = bill.receipt_url
        if bill.notes:
            insert_data["notes"] = bill.notes

        response = supabase.table("bills").insert(insert_data).execute()

        return {"message": "Bill created successfully", "data": response.data[0] if response.data else None}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error creating bill: {str(e)}")


@router.patch("/{bill_id}")
async def update_bill(bill_id: str, bill: BillUpdate):
    """
    Actualiza un bill existente (actualización parcial)
    """
    try:
        # Verificar que el bill exista
        existing = supabase.table("bills").select("bill_id").eq("bill_id", bill_id).execute()

        if not existing.data:
            raise HTTPException(status_code=404, detail="Bill not found")

        # Validar status si se proporciona
        valid_statuses = ["open", "closed", "split"]
        if bill.status and bill.status.lower() not in valid_statuses:
            raise HTTPException(status_code=400, detail=f"Invalid status. Must be one of: {', '.join(valid_statuses)}")

        # Construir datos a actualizar
        update_data = {}

        if bill.vendor_id is not None:
            update_data["vendor_id"] = bill.vendor_id if bill.vendor_id else None
        if bill.expected_total is not None:
            update_data["expected_total"] = bill.expected_total
        if bill.status is not None:
            update_data["status"] = bill.status.lower()
        if bill.split_projects is not None:
            update_data["split_projects"] = bill.split_projects
        if bill.receipt_url is not None:
            update_data["receipt_url"] = bill.receipt_url if bill.receipt_url else None
        if bill.notes is not None:
            update_data["notes"] = bill.notes if bill.notes else None

        if not update_data:
            raise HTTPException(status_code=400, detail="No fields to update")

        response = supabase.table("bills").update(update_data).eq("bill_id", bill_id).execute()

        return {"message": "Bill updated successfully", "data": response.data[0] if response.data else None}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error updating bill: {str(e)}")


@router.delete("/{bill_id}")
async def delete_bill(bill_id: str):
    """
    Elimina un bill
    Devuelve información sobre los gastos que quedarán sin bill asociado
    """
    try:
        # Verificar que el bill exista
        existing = supabase.table("bills").select("bill_id").eq("bill_id", bill_id).execute()

        if not existing.data:
            raise HTTPException(status_code=404, detail="Bill not found")

        # Verificar si hay expenses asociados
        expenses_check = supabase.table("expenses_manual_COGS").select(
            "expense_id, LineDescription, Amount"
        ).eq("bill_id", bill_id).execute()

        affected_expenses = []
        if expenses_check.data and len(expenses_check.data) > 0:
            affected_expenses = [
                {
                    "expense_id": exp.get("expense_id"),
                    "description": exp.get("LineDescription", ""),
                    "amount": exp.get("Amount", 0)
                }
                for exp in expenses_check.data
            ]

        # Eliminar el bill
        response = supabase.table("bills").delete().eq("bill_id", bill_id).execute()

        # Devolver mensaje con información de expenses afectados
        if affected_expenses:
            return {
                "message": "Bill deleted successfully",
                "info": f"{len(affected_expenses)} expense(s) still reference this bill_id",
                "affected_expenses": affected_expenses
            }
        else:
            return {"message": "Bill deleted successfully"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error deleting bill: {str(e)}")


@router.post("/{bill_id}/close")
async def close_bill(bill_id: str):
    """
    Marca un bill como cerrado (closed)
    """
    try:
        existing = supabase.table("bills").select("bill_id, status").eq("bill_id", bill_id).execute()

        if not existing.data:
            raise HTTPException(status_code=404, detail="Bill not found")

        response = supabase.table("bills").update({"status": "closed"}).eq("bill_id", bill_id).execute()

        return {"message": "Bill closed successfully", "data": response.data[0] if response.data else None}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error closing bill: {str(e)}")


@router.post("/{bill_id}/reopen")
async def reopen_bill(bill_id: str):
    """
    Reabre un bill (cambia status a open)
    """
    try:
        existing = supabase.table("bills").select("bill_id, status").eq("bill_id", bill_id).execute()

        if not existing.data:
            raise HTTPException(status_code=404, detail="Bill not found")

        response = supabase.table("bills").update({"status": "open"}).eq("bill_id", bill_id).execute()

        return {"message": "Bill reopened successfully", "data": response.data[0] if response.data else None}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error reopening bill: {str(e)}")


@router.post("/{bill_id}/mark-split")
async def mark_bill_split(bill_id: str, split_projects: List[int] = None):
    """
    Marca un bill como split (gastos distribuidos en múltiples proyectos)
    """
    try:
        existing = supabase.table("bills").select("bill_id, status").eq("bill_id", bill_id).execute()

        if not existing.data:
            raise HTTPException(status_code=404, detail="Bill not found")

        update_data = {"status": "split"}
        if split_projects:
            update_data["split_projects"] = split_projects

        response = supabase.table("bills").update(update_data).eq("bill_id", bill_id).execute()

        return {"message": "Bill marked as split successfully", "data": response.data[0] if response.data else None}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error marking bill as split: {str(e)}")
