# api/routers/rd.py
# ============================================
# Residential Development (RD) Calculator API
# ============================================
# Persists saved "deals" for the RD calculator (build-to-rent / build-to-sell).
# The model engine runs client-side (pure function, ported from the 303 Sears
# Excel); this router only stores/retrieves snapshots. Mirrors fix_flip.py.
#
# One row = one saved calculation (inputs + computed outputs). Scoped per user;
# deal names unique per user (409 on duplicate). Workspace-scoped via company_id.

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from api.auth import get_current_user
from api.supabase_client import supabase

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/rd", tags=["Residential Development"])


# ====== MODELS ======

class DealCreate(BaseModel):
    name: str
    notes: Optional[str] = ""
    mode: Optional[str] = None
    irr_levered: Optional[float] = None
    net_profit: Optional[float] = None
    data: Dict[str, Any]              # { inputs, outputs } snapshot
    company_id: Optional[str] = None  # owning workspace; stamped by the active org


class DealUpdate(BaseModel):
    name: Optional[str] = None
    notes: Optional[str] = None
    mode: Optional[str] = None
    irr_levered: Optional[float] = None
    net_profit: Optional[float] = None
    data: Optional[Dict[str, Any]] = None


# ====== ENDPOINTS ======

@router.get("/deals")
async def list_deals(
    current_user: dict = Depends(get_current_user),
    company_id: Optional[str] = Query(
        None,
        description="Scope to the active workspace (deals tagged to it plus shared NULL ones). Omit for all of the user's deals.",
    ),
):
    """List the current user's saved RD deals (lightweight, newest first)."""
    try:
        q = (
            supabase.table("rd_deals")
            .select("id,name,notes,mode,irr_levered,net_profit,created_at,updated_at")
            .eq("user_id", str(current_user["user_id"]))
        )
        if company_id:
            q = q.or_(f"company_id.eq.{company_id},company_id.is.null")
        res = q.order("updated_at", desc=True).limit(200).execute()
    except Exception as e:
        logger.error("[RD] list deals failed: %r", e)
        raise HTTPException(status_code=500, detail=f"Could not list deals: {e}")
    return {"deals": res.data or []}


@router.get("/deals/{deal_id}")
async def get_deal(deal_id: str, current_user: dict = Depends(get_current_user)):
    """Return one saved deal (including the full inputs/outputs snapshot)."""
    try:
        res = (
            supabase.table("rd_deals")
            .select("*")
            .eq("id", deal_id)
            .eq("user_id", str(current_user["user_id"]))
            .limit(1)
            .execute()
        )
    except Exception as e:
        logger.error("[RD] get deal failed: %r", e)
        raise HTTPException(status_code=500, detail=f"Could not load deal: {e}")
    rows = res.data or []
    if not rows:
        raise HTTPException(status_code=404, detail="Deal not found")
    return {"deal": rows[0]}


@router.post("/deals")
async def create_deal(payload: DealCreate, current_user: dict = Depends(get_current_user)):
    """Persist a calculator run for the current user. Name must be unique per user."""
    name = (payload.name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Name is required")

    user_id = str(current_user["user_id"])
    try:
        existing = (
            supabase.table("rd_deals")
            .select("id")
            .eq("user_id", user_id)
            .eq("name", name)
            .limit(1)
            .execute()
        )
        if existing.data:
            raise HTTPException(
                status_code=409,
                detail=f'A deal named "{name}" already exists. Use a different name (e.g. "{name} - v2").',
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.warning("[RD] name pre-check failed: %r", e)

    row = {
        "user_id": user_id,
        "name": name,
        "notes": payload.notes or "",
        "mode": payload.mode,
        "irr_levered": payload.irr_levered,
        "net_profit": payload.net_profit,
        "data": payload.data,
    }
    if payload.company_id:
        row["company_id"] = payload.company_id
    try:
        res = supabase.table("rd_deals").insert(row).execute()
    except Exception as e:
        logger.error("[RD] save deal failed: %r", e)
        raise HTTPException(status_code=500, detail=f"Could not save deal: {e}")
    saved = (res.data or [{}])[0]
    return {"id": saved.get("id"), "message": "Deal saved"}


@router.put("/deals/{deal_id}")
async def update_deal(deal_id: str, payload: DealUpdate, current_user: dict = Depends(get_current_user)):
    """Update a saved deal (rename, edit notes, or replace the snapshot)."""
    user_id = str(current_user["user_id"])

    fields: Dict[str, Any] = {}
    if payload.name is not None:
        new_name = payload.name.strip()
        if new_name:
            try:
                dup = (
                    supabase.table("rd_deals")
                    .select("id")
                    .eq("user_id", user_id)
                    .eq("name", new_name)
                    .neq("id", deal_id)
                    .limit(1)
                    .execute()
                )
                if dup.data:
                    raise HTTPException(
                        status_code=409,
                        detail=f'A deal named "{new_name}" already exists. Use a different name.',
                    )
            except HTTPException:
                raise
            except Exception as e:
                logger.warning("[RD] rename pre-check failed: %r", e)
            fields["name"] = new_name
    if payload.notes is not None:
        fields["notes"] = payload.notes
    if payload.mode is not None:
        fields["mode"] = payload.mode
    if payload.irr_levered is not None:
        fields["irr_levered"] = payload.irr_levered
    if payload.net_profit is not None:
        fields["net_profit"] = payload.net_profit
    if payload.data is not None:
        fields["data"] = payload.data
    fields["updated_at"] = "now()"

    try:
        res = (
            supabase.table("rd_deals")
            .update(fields)
            .eq("id", deal_id)
            .eq("user_id", user_id)
            .execute()
        )
    except Exception as e:
        logger.error("[RD] update deal failed: %r", e)
        raise HTTPException(status_code=500, detail=f"Could not update deal: {e}")
    if not (res.data or []):
        raise HTTPException(status_code=404, detail="Deal not found")
    return {"message": "Deal updated"}


@router.delete("/deals/{deal_id}")
async def delete_deal(deal_id: str, current_user: dict = Depends(get_current_user)):
    """Delete one of the current user's saved deals."""
    try:
        res = (
            supabase.table("rd_deals")
            .delete()
            .eq("id", deal_id)
            .eq("user_id", str(current_user["user_id"]))
            .execute()
        )
    except Exception as e:
        logger.error("[RD] delete deal failed: %r", e)
        raise HTTPException(status_code=500, detail=f"Could not delete deal: {e}")
    if not (res.data or []):
        raise HTTPException(status_code=404, detail="Deal not found")
    return {"message": "Deal deleted"}
