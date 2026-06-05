"""
Router para gestión de Accounts (Cuentas Contables)
"""
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from typing import Optional
from api.supabase_client import supabase

router = APIRouter(prefix="/accounts", tags=["accounts"])


# ========================================
# Modelos Pydantic
# ========================================

class AccountCreate(BaseModel):
    Name: str
    AcctNum: Optional[int] = None
    AccountCategory: Optional[str] = None
    account_id: Optional[str] = None  # Si es auto-generado por Supabase, hacerlo opcional
    is_cogs: Optional[bool] = False  # True si es cuenta COGS (Cost of Goods Sold)
    company_id: Optional[str] = None  # Owning workspace; stamped by the active org


class AccountUpdate(BaseModel):
    Name: Optional[str] = None
    AcctNum: Optional[int] = None
    AccountCategory: Optional[str] = None
    is_cogs: Optional[bool] = None  # True si es cuenta COGS


# ========================================
# Endpoints
# ========================================

@router.get("")
async def list_accounts(
    company_id: Optional[str] = Query(
        None,
        description="Scope to the active workspace. Returns that company's accounts plus shared (company_id NULL) ones. Omit for all.",
    ),
):
    """
    Lista las cuentas ordenadas por nombre. Si se provee company_id, devuelve las
    de esa compañía mas las compartidas (company_id NULL); sin el parametro, todas.
    """
    try:
        query = supabase.table("accounts").select("*")
        if company_id:
            query = query.or_(f"company_id.eq.{company_id},company_id.is.null")
        response = query.order("Name").execute()
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


@router.post("")
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
        if account.is_cogs is not None:
            insert_data["is_cogs"] = account.is_cogs
        if account.company_id:
            insert_data["company_id"] = account.company_id

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

        if account.is_cogs is not None:
            update_data["is_cogs"] = account.is_cogs

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
    NOTA: Esto fallará si hay gastos (qbo_expenses) asociados a esta cuenta debido a foreign key constraint
    """
    try:
        # Verificar que la cuenta exista
        existing = supabase.table("accounts").select("account_id").eq("account_id", account_id).execute()

        if not existing.data:
            raise HTTPException(status_code=404, detail="Account not found")

        # Verificar si hay expenses asociados
        expenses_check = supabase.table("qbo_expenses").select("id").eq("account_id", account_id).limit(1).execute()

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
