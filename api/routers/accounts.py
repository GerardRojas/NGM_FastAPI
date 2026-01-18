"""
Router para gestión de Accounts (Cuentas Contables)
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
from supabase import create_client, Client
import os

router = APIRouter(prefix="/accounts", tags=["accounts"])

# Inicializar cliente de Supabase
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    raise RuntimeError("Missing SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY in environment")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)


# ========================================
# Modelos Pydantic
# ========================================

class AccountCreate(BaseModel):
    Name: str
    AcctNum: Optional[int] = None
    AccountCategory: Optional[str] = None
    account_id: Optional[str] = None  # Si es auto-generado por Supabase, hacerlo opcional


class AccountUpdate(BaseModel):
    Name: Optional[str] = None
    AcctNum: Optional[int] = None
    AccountCategory: Optional[str] = None


# ========================================
# Endpoints
# ========================================

@router.get("/")
async def list_accounts():
    """
    Lista todas las cuentas ordenadas por nombre
    """
    try:
        response = supabase.table("accounts").select("*").order("Name").execute()
        return {"data": response.data or []}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching accounts: {str(e)}")


@router.get("/{account_id}")
async def get_account(account_id: str):
    """
    Obtiene una cuenta específica por ID
    """
    try:
        response = supabase.table("accounts").select("*").eq("account_id", account_id).single().execute()

        if not response.data:
            raise HTTPException(status_code=404, detail="Account not found")

        return response.data
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching account: {str(e)}")


@router.post("/")
async def create_account(account: AccountCreate):
    """
    Crea una nueva cuenta
    """
    try:
        # Verificar que no exista una cuenta con el mismo nombre
        existing = supabase.table("accounts").select("account_id").eq("Name", account.Name).execute()

        if existing.data and len(existing.data) > 0:
            raise HTTPException(status_code=400, detail="Account with this name already exists")

        insert_data = {"Name": account.Name}
        if account.AcctNum is not None:
            insert_data["AcctNum"] = account.AcctNum
        if account.AccountCategory is not None:
            insert_data["AccountCategory"] = account.AccountCategory
        if account.account_id:
            insert_data["account_id"] = account.account_id

        response = supabase.table("accounts").insert(insert_data).execute()

        return {"message": "Account created successfully", "data": response.data[0] if response.data else None}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error creating account: {str(e)}")


@router.patch("/{account_id}")
async def update_account(account_id: str, account: AccountUpdate):
    """
    Actualiza una cuenta existente (actualización parcial)
    """
    try:
        # Verificar que la cuenta exista
        existing = supabase.table("accounts").select("account_id").eq("account_id", account_id).execute()

        if not existing.data:
            raise HTTPException(status_code=404, detail="Account not found")

        # Construir datos a actualizar
        update_data = {}
        if account.Name is not None:
            # Verificar que el nuevo nombre no esté en uso por otra cuenta
            name_check = supabase.table("accounts").select("account_id").eq("Name", account.Name).neq("account_id", account_id).execute()
            if name_check.data and len(name_check.data) > 0:
                raise HTTPException(status_code=400, detail="Account name already in use")
            update_data["Name"] = account.Name

        if account.AcctNum is not None:
            update_data["AcctNum"] = account.AcctNum

        if account.AccountCategory is not None:
            update_data["AccountCategory"] = account.AccountCategory

        if not update_data:
            raise HTTPException(status_code=400, detail="No fields to update")

        response = supabase.table("accounts").update(update_data).eq("account_id", account_id).execute()

        return {"message": "Account updated successfully", "data": response.data[0] if response.data else None}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error updating account: {str(e)}")


@router.delete("/{account_id}")
async def delete_account(account_id: str):
    """
    Elimina una cuenta
    NOTA: Esto fallará si hay gastos (expenses) asociados a esta cuenta debido a foreign key constraint
    """
    try:
        # Verificar que la cuenta exista
        existing = supabase.table("accounts").select("account_id").eq("account_id", account_id).execute()

        if not existing.data:
            raise HTTPException(status_code=404, detail="Account not found")

        # Verificar si hay expenses asociados
        expenses_check = supabase.table("expenses").select("expense_id").eq("account_id", account_id).limit(1).execute()

        if expenses_check.data and len(expenses_check.data) > 0:
            raise HTTPException(
                status_code=400,
                detail="Cannot delete account: there are expenses associated with this account"
            )

        response = supabase.table("accounts").delete().eq("account_id", account_id).execute()

        return {"message": "Account deleted successfully"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error deleting account: {str(e)}")
