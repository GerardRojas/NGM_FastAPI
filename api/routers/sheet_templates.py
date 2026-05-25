"""
Sheet Templates Router (Budget Sheet Manager)

Reusable export templates for the Estimator. A template bundles branding with a
view_config that decides what an exported sheet shows (line items, quantities,
material/labor breakdown, granularity, ...). The same template drives the PDF
caratula, the Excel export, and the estimate -> budget conversion.

Storage: Supabase table `sheet_templates` (see sql/create_sheet_templates.sql).
"""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api.auth import get_current_user
from api.supabase_client import supabase

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/sheet-templates", tags=["sheet-templates"])


# ================================
# MODELS
# ================================

class SheetTemplateCreate(BaseModel):
    name: str
    theme: str = "classic"
    branding: Dict[str, Any] = {}
    view_config: Dict[str, Any] = {}
    is_default: bool = False


class SheetTemplateUpdate(BaseModel):
    name: Optional[str] = None
    theme: Optional[str] = None
    branding: Optional[Dict[str, Any]] = None
    view_config: Optional[Dict[str, Any]] = None
    is_default: Optional[bool] = None


# ================================
# HELPERS
# ================================

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clear_other_defaults(keep_id: Optional[str] = None) -> None:
    """Only one template may be the default; unset every other row."""
    query = supabase.table("sheet_templates").update({"is_default": False}).eq("is_default", True)
    if keep_id:
        query = query.neq("id", keep_id)
    query.execute()


# ================================
# ENDPOINTS
# ================================

@router.get("")
def list_templates(current_user: dict = Depends(get_current_user)):
    """List all sheet templates (presets first, then alphabetical)."""
    try:
        res = (
            supabase.table("sheet_templates")
            .select("*")
            .order("is_preset", desc=True)
            .order("name")
            .execute()
        )
        return {"data": res.data or []}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error listing sheet templates: {e}")


@router.get("/{template_id}")
def get_template(template_id: str, current_user: dict = Depends(get_current_user)):
    try:
        res = supabase.table("sheet_templates").select("*").eq("id", template_id).single().execute()
        if not res.data:
            raise HTTPException(status_code=404, detail="Sheet template not found")
        return res.data
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error reading sheet template: {e}")


@router.post("", status_code=201)
def create_template(payload: SheetTemplateCreate, current_user: dict = Depends(get_current_user)):
    try:
        record = {
            "name": payload.name,
            "theme": payload.theme or "classic",
            "branding": payload.branding or {},
            "view_config": payload.view_config or {},
            "is_default": bool(payload.is_default),
            "is_preset": False,
            "created_by": current_user.get("id") if isinstance(current_user, dict) else None,
        }
        res = supabase.table("sheet_templates").insert(record).execute()
        created = res.data[0] if res.data else None
        if created and created.get("is_default"):
            _clear_other_defaults(keep_id=created.get("id"))
        return {"message": "Sheet template created", "template": created}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error creating sheet template: {e}")


@router.patch("/{template_id}")
def update_template(
    template_id: str,
    payload: SheetTemplateUpdate,
    current_user: dict = Depends(get_current_user),
):
    try:
        update_data = {k: v for k, v in payload.dict().items() if v is not None}
        if not update_data:
            raise HTTPException(status_code=400, detail="No fields to update")
        update_data["updated_at"] = _now_iso()

        res = supabase.table("sheet_templates").update(update_data).eq("id", template_id).execute()
        if not res.data:
            raise HTTPException(status_code=404, detail="Sheet template not found")
        if update_data.get("is_default"):
            _clear_other_defaults(keep_id=template_id)
        return {"message": "Sheet template updated", "template": res.data[0]}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error updating sheet template: {e}")


@router.delete("/{template_id}")
def delete_template(template_id: str, current_user: dict = Depends(get_current_user)):
    try:
        existing = supabase.table("sheet_templates").select("is_preset").eq("id", template_id).single().execute()
        if not existing.data:
            raise HTTPException(status_code=404, detail="Sheet template not found")
        if existing.data.get("is_preset"):
            raise HTTPException(status_code=400, detail="Built-in presets cannot be deleted")
        supabase.table("sheet_templates").delete().eq("id", template_id).execute()
        return {"message": "Sheet template deleted"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error deleting sheet template: {e}")
