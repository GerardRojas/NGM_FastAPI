# api/routers/fix_flip.py
# ================================
# Fix & Flip Calculator API Router
# ================================
# Persists saved "deals" for the Fix & Flip Calculator module. The calculation
# engine itself runs client-side (it is a pure function ported from the original
# FIX_N_FLIP_CALCULATOR repo); this router only stores/retrieves the snapshots.
#
# One row = one saved calculation (inputs + computed outputs). Scoped per user.
# Deal names are unique per user, so saving a duplicate name returns 409 (mirrors
# the original SQLite app's behavior).

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api.auth import get_current_user
from api.supabase_client import supabase

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/fix-flip", tags=["Fix & Flip"])


# ====== MODELS ======

class DealCreate(BaseModel):
    name: str
    notes: Optional[str] = ""
    scenario: Optional[str] = None
    net_profit_hm: Optional[float] = None
    roi_hm: Optional[float] = None
    data: Dict[str, Any]            # { inputs, outputs } snapshot


class DealUpdate(BaseModel):
    name: Optional[str] = None
    notes: Optional[str] = None
    scenario: Optional[str] = None
    net_profit_hm: Optional[float] = None
    roi_hm: Optional[float] = None
    data: Optional[Dict[str, Any]] = None


# ====== ENDPOINTS ======

@router.get("/deals")
async def list_deals(current_user: dict = Depends(get_current_user)):
    """List the current user's saved deals (lightweight, newest first)."""
    try:
        res = (
            supabase.table("fix_flip_deals")
            .select("id,name,notes,scenario,net_profit_hm,roi_hm,created_at,updated_at")
            .eq("user_id", str(current_user["user_id"]))
            .order("updated_at", desc=True)
            .limit(200)
            .execute()
        )
    except Exception as e:
        logger.error("[FIX_FLIP] list deals failed: %r", e)
        raise HTTPException(status_code=500, detail=f"Could not list deals: {e}")
    return {"deals": res.data or []}


@router.get("/deals/{deal_id}")
async def get_deal(deal_id: str, current_user: dict = Depends(get_current_user)):
    """Return one saved deal (including the full inputs/outputs snapshot)."""
    try:
        res = (
            supabase.table("fix_flip_deals")
            .select("*")
            .eq("id", deal_id)
            .eq("user_id", str(current_user["user_id"]))
            .limit(1)
            .execute()
        )
    except Exception as e:
        logger.error("[FIX_FLIP] get deal failed: %r", e)
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
    # Pre-check the per-user name uniqueness for a friendly 409 message.
    try:
        existing = (
            supabase.table("fix_flip_deals")
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
        logger.warning("[FIX_FLIP] name pre-check failed: %r", e)

    row = {
        "user_id": user_id,
        "name": name,
        "notes": payload.notes or "",
        "scenario": payload.scenario,
        "net_profit_hm": payload.net_profit_hm,
        "roi_hm": payload.roi_hm,
        "data": payload.data,
    }
    try:
        res = supabase.table("fix_flip_deals").insert(row).execute()
    except Exception as e:
        logger.error("[FIX_FLIP] save deal failed: %r", e)
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
            # Reject a rename that collides with another of the user's deals.
            try:
                dup = (
                    supabase.table("fix_flip_deals")
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
                logger.warning("[FIX_FLIP] rename pre-check failed: %r", e)
            fields["name"] = new_name
    if payload.notes is not None:
        fields["notes"] = payload.notes
    if payload.scenario is not None:
        fields["scenario"] = payload.scenario
    if payload.net_profit_hm is not None:
        fields["net_profit_hm"] = payload.net_profit_hm
    if payload.roi_hm is not None:
        fields["roi_hm"] = payload.roi_hm
    if payload.data is not None:
        fields["data"] = payload.data
    fields["updated_at"] = "now()"

    try:
        res = (
            supabase.table("fix_flip_deals")
            .update(fields)
            .eq("id", deal_id)
            .eq("user_id", user_id)
            .execute()
        )
    except Exception as e:
        logger.error("[FIX_FLIP] update deal failed: %r", e)
        raise HTTPException(status_code=500, detail=f"Could not update deal: {e}")
    if not (res.data or []):
        raise HTTPException(status_code=404, detail="Deal not found")
    return {"message": "Deal updated"}


@router.delete("/deals/{deal_id}")
async def delete_deal(deal_id: str, current_user: dict = Depends(get_current_user)):
    """Delete one of the current user's saved deals."""
    try:
        res = (
            supabase.table("fix_flip_deals")
            .delete()
            .eq("id", deal_id)
            .eq("user_id", str(current_user["user_id"]))
            .execute()
        )
    except Exception as e:
        logger.error("[FIX_FLIP] delete deal failed: %r", e)
        raise HTTPException(status_code=500, detail=f"Could not delete deal: {e}")
    if not (res.data or []):
        raise HTTPException(status_code=404, detail="Deal not found")
    return {"message": "Deal deleted"}
