"""
Router para gestión de Payment Methods (Métodos de Pago)
NOTA: La tabla en Supabase se llama "paymet_methods" (con typo)
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
from supabase import create_client, Client
import os

router = APIRouter(prefix="/payment-methods", tags=["payment_methods"])

# Inicializar cliente de Supabase
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    raise RuntimeError("Missing SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY in environment")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)


# ========================================
# Modelos Pydantic
# ========================================

class PaymentMethodCreate(BaseModel):
    payment_method_name: str


class PaymentMethodUpdate(BaseModel):
    payment_method_name: Optional[str] = None


# ========================================
# Endpoints
# ========================================

@router.get("/")
async def list_payment_methods():
    """
    Lista todos los payment methods ordenados por nombre
    """
    try:
        response = supabase.table("paymet_methods").select("*").order("payment_method_name").execute()
        return {"data": response.data or []}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching payment methods: {str(e)}")


@router.get("/{payment_method_id}")
async def get_payment_method(payment_method_id: str):
    """
    Obtiene un payment method específico por ID
    """
    try:
        response = supabase.table("paymet_methods").select("*").eq("id", payment_method_id).single().execute()

        if not response.data:
            raise HTTPException(status_code=404, detail="Payment method not found")

        return response.data
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching payment method: {str(e)}")


@router.post("/")
async def create_payment_method(payment_method: PaymentMethodCreate):
    """
    Crea un nuevo payment method
    """
    try:
        # Verificar que no exista un payment method con el mismo nombre (case-insensitive)
        existing = supabase.table("paymet_methods").select("id, payment_method_name").execute()

        if existing.data:
            for item in existing.data:
                if item.get("payment_method_name", "").lower().strip() == payment_method.payment_method_name.lower().strip():
                    raise HTTPException(status_code=400, detail="Payment method with this name already exists")

        response = supabase.table("paymet_methods").insert({
            "payment_method_name": payment_method.payment_method_name
        }).execute()

        return {"message": "Payment method created successfully", "data": response.data[0] if response.data else None}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error creating payment method: {str(e)}")


@router.patch("/{payment_method_id}")
async def update_payment_method(payment_method_id: str, payment_method: PaymentMethodUpdate):
    """
    Actualiza un payment method existente (actualización parcial)
    """
    try:
        # Verificar que el payment method exista
        existing = supabase.table("paymet_methods").select("id").eq("id", payment_method_id).execute()

        if not existing.data:
            raise HTTPException(status_code=404, detail="Payment method not found")

        # Construir datos a actualizar
        update_data = {}
        if payment_method.payment_method_name is not None:
            # Verificar que el nuevo nombre no esté en uso por otro payment method
            name_check = supabase.table("paymet_methods").select("id").eq("payment_method_name", payment_method.payment_method_name).neq("id", payment_method_id).execute()
            if name_check.data and len(name_check.data) > 0:
                raise HTTPException(status_code=400, detail="Payment method name already in use")
            update_data["payment_method_name"] = payment_method.payment_method_name

        if not update_data:
            raise HTTPException(status_code=400, detail="No fields to update")

        response = supabase.table("paymet_methods").update(update_data).eq("id", payment_method_id).execute()

        return {"message": "Payment method updated successfully", "data": response.data[0] if response.data else None}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error updating payment method: {str(e)}")


@router.delete("/{payment_method_id}")
async def delete_payment_method(payment_method_id: str):
    """
    Elimina un payment method
    NOTA: Esto fallará si hay gastos (expenses) asociados a este payment method debido a foreign key constraint
    """
    try:
        # Verificar que el payment method exista
        existing = supabase.table("paymet_methods").select("id").eq("id", payment_method_id).execute()

        if not existing.data:
            raise HTTPException(status_code=404, detail="Payment method not found")

        # Verificar si hay expenses asociados
        expenses_check = supabase.table("expenses").select("expense_id").eq("payment_type", payment_method_id).limit(1).execute()

        if expenses_check.data and len(expenses_check.data) > 0:
            raise HTTPException(
                status_code=400,
                detail="Cannot delete payment method: there are expenses associated with this payment method"
            )

        response = supabase.table("paymet_methods").delete().eq("id", payment_method_id).execute()

        return {"message": "Payment method deleted successfully"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error deleting payment method: {str(e)}")
