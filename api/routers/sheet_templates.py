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

from fastapi import APIRouter, Depends, HTTPException, Query
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
    company_id: Optional[str] = None


class SheetTemplateUpdate(BaseModel):
    name: Optional[str] = None
    theme: Optional[str] = None
    branding: Optional[Dict[str, Any]] = None
    view_config: Optional[Dict[str, Any]] = None
    is_default: Optional[bool] = None
    company_id: Optional[str] = None


# ================================
# HELPERS
# ================================

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clear_other_defaults(keep_id: Optional[str] = None, company_id: Optional[str] = None) -> None:
    """Only one template may be the default *within an organization*; unset every
    other row in the same company scope (or the shared NULL scope)."""
    query = supabase.table("sheet_templates").update({"is_default": False}).eq("is_default", True)
    if company_id:
        query = query.eq("company_id", company_id)
    else:
        query = query.is_("company_id", "null")
    if keep_id:
        query = query.neq("id", keep_id)
    query.execute()


def provision_default_templates_for_company(company_id: str, company_name: str) -> int:
    """Clone the shared preset templates into per-company copies, stamping the
    company's name into the branding header so reports/exports carry it. The
    copies are editable (but kept is_preset=True so the UI marks them built-in
    and they can't be deleted). Idempotent: skips presets already cloned for the
    company (matched by name). Returns how many were created."""
    presets = (
        supabase.table("sheet_templates")
        .select("*")
        .eq("is_preset", True)
        .is_("company_id", "null")
        .execute()
        .data
        or []
    )
    if not presets:
        return 0

    existing = (
        supabase.table("sheet_templates")
        .select("name")
        .eq("company_id", company_id)
        .execute()
        .data
        or []
    )
    existing_names = {r.get("name") for r in existing}

    rows = []
    for preset in presets:
        if preset.get("name") in existing_names:
            continue
        branding = dict(preset.get("branding") or {})
        branding["companyName"] = company_name
        branding["companyInfo"] = company_name
        rows.append({
            "name": preset.get("name"),
            "theme": preset.get("theme") or "classic",
            "branding": branding,
            "view_config": preset.get("view_config") or {},
            "is_default": bool(preset.get("is_default")),
            "is_preset": True,
            "company_id": company_id,
        })

    if not rows:
        return 0
    supabase.table("sheet_templates").insert(rows).execute()
    return len(rows)


# ================================
# ENDPOINTS
# ================================

@router.get("")
def list_templates(
    company_id: Optional[str] = Query(None, description="Scope to one organization; shared (NULL) rows always included"),
    current_user: dict = Depends(get_current_user),
):
    """List all sheet templates (presets first, then alphabetical)."""
    try:
        query = supabase.table("sheet_templates").select("*")
        if company_id:
            # The company's own rows + shared CUSTOM templates. Global presets
            # (company_id NULL, is_preset true) are hidden because every company
            # gets its own personalized copies via provisioning — avoids dupes.
            query = query.or_(
                f"company_id.eq.{company_id},and(company_id.is.null,is_preset.is.false)"
            )
        res = (
            query
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
            "company_id": payload.company_id,
            "created_by": current_user.get("id") if isinstance(current_user, dict) else None,
        }
        res = supabase.table("sheet_templates").insert(record).execute()
        created = res.data[0] if res.data else None
        if created and created.get("is_default"):
            _clear_other_defaults(keep_id=created.get("id"), company_id=created.get("company_id"))
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
            _clear_other_defaults(keep_id=template_id, company_id=res.data[0].get("company_id"))
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


@router.post("/provision")
def provision_templates(
    company_id: Optional[str] = Query(None, description="Provision a single company; omit to backfill all"),
    current_user: dict = Depends(get_current_user),
):
    """Ensure every organization has its own personalized copies of the preset
    templates. Idempotent — safe to re-run. Use to backfill existing companies."""
    try:
        if company_id:
            comp = supabase.table("companies").select("id, name").eq("id", company_id).single().execute()
            if not comp.data:
                raise HTTPException(status_code=404, detail="Company not found")
            companies = [comp.data]
        else:
            companies = supabase.table("companies").select("id, name").execute().data or []

        total = 0
        for c in companies:
            total += provision_default_templates_for_company(c.get("id"), c.get("name") or "")
        return {"message": "Templates provisioned", "companies": len(companies), "created": total}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error provisioning templates: {e}")
