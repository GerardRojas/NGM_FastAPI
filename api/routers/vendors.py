"""
Router para gestion de Vendors (Proveedores)
Phase 1: QuickBooks-level vendor profiles
"""
from fastapi import APIRouter, HTTPException, Query, Depends
from api.auth import require_internal
from pydantic import BaseModel
from typing import Optional, List
from api.supabase_client import supabase

router = APIRouter(dependencies=[Depends(require_internal)], prefix="/vendors", tags=["vendors"])


# ========================================
# Modelos Pydantic
# ========================================

class VendorCreate(BaseModel):
    vendor_name: str
    company_name: Optional[str] = None
    contact_name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    address_line1: Optional[str] = None
    address_line2: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    zip_code: Optional[str] = None
    country: Optional[str] = "US"
    tax_id: Optional[str] = None
    tax_id_type: Optional[str] = None
    is_1099: Optional[bool] = False
    vendor_type: Optional[str] = "supplier"
    payment_terms: Optional[str] = "due_on_receipt"
    default_account_id: Optional[str] = None
    status: Optional[str] = "active"
    notes: Optional[str] = None
    company_id: Optional[str] = None


class VendorUpdate(BaseModel):
    vendor_name: Optional[str] = None
    company_name: Optional[str] = None
    contact_name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    address_line1: Optional[str] = None
    address_line2: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    zip_code: Optional[str] = None
    country: Optional[str] = None
    tax_id: Optional[str] = None
    tax_id_type: Optional[str] = None
    is_1099: Optional[bool] = None
    w9_status: Optional[str] = None
    w9_file_url: Optional[str] = None
    w9_received_date: Optional[str] = None
    w8_status: Optional[str] = None
    w8_file_url: Optional[str] = None
    w8_received_date: Optional[str] = None
    vendor_type: Optional[str] = None
    payment_terms: Optional[str] = None
    default_account_id: Optional[str] = None
    status: Optional[str] = None
    notes: Optional[str] = None
    company_id: Optional[str] = None


# ========================================
# Endpoints
# ========================================

@router.get("")
async def list_vendors(
    status: Optional[str] = Query(None, description="Filter by status: active, inactive"),
    vendor_type: Optional[str] = Query(None, description="Filter by type"),
    is_1099: Optional[bool] = Query(None, description="Filter 1099 vendors"),
    w9_status: Optional[str] = Query(None, description="Filter by W-9 status"),
    company_id: Optional[str] = Query(None, description="Scope to one organization; shared (NULL) rows always included")
):
    """
    Lista todos los vendors ordenados por nombre, con filtros opcionales
    """
    try:
        query = supabase.table("Vendors").select("*").order("vendor_name")

        if status:
            query = query.eq("status", status)
        if vendor_type:
            query = query.eq("vendor_type", vendor_type)
        if is_1099 is not None:
            query = query.eq("is_1099", is_1099)
        if w9_status:
            query = query.eq("w9_status", w9_status)
        if company_id:
            query = query.or_(f"company_id.eq.{company_id},company_id.is.null")

        response = query.execute()
        return {"data": response.data or []}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching vendors: {str(e)}")


@router.get("/1099-summary")
async def get_1099_summary(year: Optional[int] = Query(None, description="Tax year")):
    """
    Returns YTD payment totals for all 1099-eligible vendors
    """
    try:
        # Get 1099 vendors
        vendors_resp = supabase.table("Vendors").select(
            "id, vendor_name, tax_id, tax_id_type, w9_status, w9_received_date"
        ).eq("is_1099", True).order("vendor_name").execute()

        vendors_1099 = vendors_resp.data or []
        if not vendors_1099:
            return {"data": [], "year": year}

        # Get expenses for these vendors in the given year
        vendor_ids = [v["id"] for v in vendors_1099]

        expenses_query = supabase.table("expenses_manual_COGS").select(
            "vendor_id, Amount, TxnDate"
        ).in_("vendor_id", vendor_ids).eq("is_deleted", False)

        if year:
            expenses_query = expenses_query.gte("TxnDate", f"{year}-01-01").lte("TxnDate", f"{year}-12-31")

        expenses_resp = expenses_query.execute()
        expenses = expenses_resp.data or []

        # Aggregate by vendor
        vendor_totals = {}
        for exp in expenses:
            vid = exp.get("vendor_id")
            amt = float(exp.get("Amount") or 0)
            if vid not in vendor_totals:
                vendor_totals[vid] = {"total_paid": 0, "txn_count": 0}
            vendor_totals[vid]["total_paid"] += amt
            vendor_totals[vid]["txn_count"] += 1

        # Merge
        result = []
        for v in vendors_1099:
            totals = vendor_totals.get(v["id"], {"total_paid": 0, "txn_count": 0})
            result.append({
                **v,
                "total_paid": round(totals["total_paid"], 2),
                "txn_count": totals["txn_count"],
                "above_threshold": totals["total_paid"] >= 600
            })

        return {"data": result, "year": year}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching 1099 summary: {str(e)}")


@router.get("/{vendor_id}")
async def get_vendor(vendor_id: str):
    """
    Obtiene un vendor especifico por ID
    """
    try:
        response = supabase.table("Vendors").select("*").eq("id", vendor_id).single().execute()

        if not response.data:
            raise HTTPException(status_code=404, detail="Vendor not found")

        return response.data
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching vendor: {str(e)}")


@router.get("/{vendor_id}/summary")
async def get_vendor_summary(vendor_id: str, year: Optional[int] = Query(None)):
    """
    Returns vendor summary: total spend, transaction count, open bills
    """
    try:
        # Get vendor
        vendor_resp = supabase.table("Vendors").select("*").eq("id", vendor_id).single().execute()
        if not vendor_resp.data:
            raise HTTPException(status_code=404, detail="Vendor not found")

        # Get expenses
        exp_query = supabase.table("expenses_manual_COGS").select("Amount, TxnDate").eq("vendor_id", vendor_id).eq("is_deleted", False)
        if year:
            exp_query = exp_query.gte("TxnDate", f"{year}-01-01").lte("TxnDate", f"{year}-12-31")
        exp_resp = exp_query.execute()
        expenses = exp_resp.data or []

        total_spend = sum(float(e.get("Amount") or 0) for e in expenses)
        txn_count = len(expenses)

        # Get open bills
        bills_resp = supabase.table("bills").select("bill_id, expected_total, status").eq("vendor_id", vendor_id).eq("status", "open").execute()
        open_bills = bills_resp.data or []
        open_balance = sum(float(b.get("expected_total") or 0) for b in open_bills)

        return {
            "vendor": vendor_resp.data,
            "total_spend": round(total_spend, 2),
            "txn_count": txn_count,
            "open_bills_count": len(open_bills),
            "open_balance": round(open_balance, 2),
            "year": year
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching vendor summary: {str(e)}")


@router.post("")
async def create_vendor(vendor: VendorCreate):
    """
    Crea un nuevo vendor con perfil completo
    """
    try:
        # Verificar que no exista un vendor con el mismo nombre
        existing = supabase.table("Vendors").select("id").eq("vendor_name", vendor.vendor_name).execute()

        if existing.data and len(existing.data) > 0:
            raise HTTPException(status_code=400, detail="Vendor with this name already exists")

        # Build insert data, excluding None values
        insert_data = {}
        for field, value in vendor.model_dump().items():
            if value is not None:
                insert_data[field] = value

        response = supabase.table("Vendors").insert(insert_data).execute()

        return {"message": "Vendor created successfully", "data": response.data[0] if response.data else None}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error creating vendor: {str(e)}")


@router.patch("/{vendor_id}")
async def update_vendor(vendor_id: str, vendor: VendorUpdate):
    """
    Actualiza un vendor existente (actualizacion parcial)
    """
    try:
        # Verificar que el vendor exista
        existing = supabase.table("Vendors").select("id").eq("id", vendor_id).execute()

        if not existing.data:
            raise HTTPException(status_code=404, detail="Vendor not found")

        # Construir datos a actualizar (solo campos no-None)
        update_data = {}
        for field, value in vendor.model_dump().items():
            if value is not None:
                update_data[field] = value

        if not update_data:
            raise HTTPException(status_code=400, detail="No fields to update")

        # Verificar nombre unico si se esta cambiando
        if "vendor_name" in update_data:
            name_check = supabase.table("Vendors").select("id").eq(
                "vendor_name", update_data["vendor_name"]
            ).neq("id", vendor_id).execute()
            if name_check.data and len(name_check.data) > 0:
                raise HTTPException(status_code=400, detail="Vendor name already in use")

        response = supabase.table("Vendors").update(update_data).eq("id", vendor_id).execute()

        return {"message": "Vendor updated successfully", "data": response.data[0] if response.data else None}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error updating vendor: {str(e)}")


@router.delete("/{vendor_id}")
async def delete_vendor(vendor_id: str):
    """
    Elimina un vendor
    Devuelve una advertencia si hay gastos (expenses) asociados
    """
    try:
        # Verificar que el vendor exista
        existing = supabase.table("Vendors").select("id").eq("id", vendor_id).execute()

        if not existing.data:
            raise HTTPException(status_code=404, detail="Vendor not found")

        # Verificar si hay expenses asociados
        expenses_check = supabase.table("expenses_manual_COGS").select(
            "expense_id, LineDescription, Amount"
        ).eq("vendor_id", vendor_id).execute()

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

        # Eliminar el vendor
        response = supabase.table("Vendors").delete().eq("id", vendor_id).execute()

        if affected_expenses:
            return {
                "message": "Vendor deleted successfully",
                "warning": f"{len(affected_expenses)} expense(s) now have no vendor assigned",
                "affected_expenses": affected_expenses
            }
        else:
            return {"message": "Vendor deleted successfully"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error deleting vendor: {str(e)}")
