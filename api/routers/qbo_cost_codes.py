"""
Router para QBO Cost Codes (Accounting).

Los 11 cost codes de QuickBooks. Se gestionan en la página de Accounting y son el
link contabilidad<->costos: cada Category (categories_rearch) referencia uno por
FK. Por ahora CRUD manual; el sync en vivo con QBO es un follow-up.
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
from api.supabase_client import supabase

router = APIRouter(prefix="/qbo-cost-codes", tags=["qbo-cost-codes"])

TABLE = "qbo_cost_codes"


class CostCodeCreate(BaseModel):
    code: str
    name: str
    qbo_class_ref_id: Optional[str] = None
    is_cogs: Optional[bool] = True
    sort_order: Optional[int] = 0


class CostCodeUpdate(BaseModel):
    code: Optional[str] = None
    name: Optional[str] = None
    qbo_class_ref_id: Optional[str] = None
    is_cogs: Optional[bool] = None
    sort_order: Optional[int] = None


@router.get("")
async def list_cost_codes():
    """Lista los cost codes (ordenados por sort_order, luego code)."""
    try:
        resp = supabase.table(TABLE).select("*").order("sort_order").order("code").execute()
        return {"data": resp.data or []}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching cost codes: {str(e)}")


@router.get("/{cost_code_id}")
async def get_cost_code(cost_code_id: str):
    try:
        resp = supabase.table(TABLE).select("*").eq("id", cost_code_id).single().execute()
        if not resp.data:
            raise HTTPException(status_code=404, detail="Cost code not found")
        return resp.data
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching cost code: {str(e)}")


@router.post("")
async def create_cost_code(payload: CostCodeCreate):
    try:
        code = (payload.code or "").strip()
        name = (payload.name or "").strip()
        if not code or not name:
            raise HTTPException(status_code=400, detail="code and name are required")

        dup = supabase.table(TABLE).select("id").eq("code", code).execute()
        if dup.data:
            raise HTTPException(status_code=400, detail=f"A cost code with code '{code}' already exists")

        row = {
            "code": code,
            "name": name,
            "qbo_class_ref_id": payload.qbo_class_ref_id,
            "is_cogs": payload.is_cogs if payload.is_cogs is not None else True,
            "sort_order": payload.sort_order or 0,
        }
        resp = supabase.table(TABLE).insert(row).execute()
        return {"message": "Cost code created", "data": (resp.data or [{}])[0]}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error creating cost code: {str(e)}")


@router.patch("/{cost_code_id}")
async def update_cost_code(cost_code_id: str, payload: CostCodeUpdate):
    try:
        updates = {k: v for k, v in payload.model_dump(exclude_unset=True).items()}
        if "code" in updates:
            updates["code"] = (updates["code"] or "").strip()
            if not updates["code"]:
                raise HTTPException(status_code=400, detail="code cannot be empty")
            dup = supabase.table(TABLE).select("id").eq("code", updates["code"]).neq("id", cost_code_id).execute()
            if dup.data:
                raise HTTPException(status_code=400, detail=f"A cost code with code '{updates['code']}' already exists")
        if "name" in updates:
            updates["name"] = (updates["name"] or "").strip()
            if not updates["name"]:
                raise HTTPException(status_code=400, detail="name cannot be empty")
        if not updates:
            raise HTTPException(status_code=400, detail="No fields to update")

        resp = supabase.table(TABLE).update(updates).eq("id", cost_code_id).execute()
        if not resp.data:
            raise HTTPException(status_code=404, detail="Cost code not found")
        return {"message": "Cost code updated", "data": resp.data[0]}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error updating cost code: {str(e)}")


@router.delete("/{cost_code_id}")
async def delete_cost_code(cost_code_id: str):
    """Borra un cost code. Las categorías que lo referencian quedan con
    cost_code_id NULL (FK ON DELETE SET NULL)."""
    try:
        supabase.table(TABLE).delete().eq("id", cost_code_id).execute()
        return {"message": "Cost code deleted"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error deleting cost code: {str(e)}")
