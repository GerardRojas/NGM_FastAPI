"""
Router para Cost Codes (codigos de costo CSI asignados a line items del estimator).
CRUD + import por CSV. Patron NGM: supabase singleton compartido.
"""
import csv
import io
import logging
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, UploadFile, File
from pydantic import BaseModel

from api.supabase_client import supabase

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/cost-codes", tags=["cost-codes"])

_ALLOWED_FIELDS = {"code", "description", "division", "category", "unit", "is_active", "sort_order"}


@router.get("")
@router.get("/")
async def list_cost_codes(
    active: bool = True,
    division: Optional[str] = None,
    q: Optional[str] = None,
) -> dict[str, Any]:
    """Lista cost codes. Filtra por activos, division y busqueda de texto (q)."""
    try:
        query = supabase.table("cost_codes").select("*").order("sort_order").order("code")
        if active:
            query = query.eq("is_active", True)
        if division:
            query = query.eq("division", division)
        if q:
            pat = f"%{q}%"
            query = query.or_(f"code.ilike.{pat},description.ilike.{pat},category.ilike.{pat}")
        rows = query.execute().data or []
        return {"cost_codes": rows, "count": len(rows)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{code_id}")
async def get_cost_code(code_id: str) -> dict[str, Any]:
    try:
        rows = supabase.table("cost_codes").select("*").eq("id", code_id).limit(1).execute().data
        if not rows:
            raise HTTPException(status_code=404, detail="cost code not found")
        return rows[0]
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class CostCodeBody(BaseModel):
    code: str
    description: str
    division: Optional[str] = None
    category: Optional[str] = None
    unit: Optional[str] = None
    is_active: bool = True
    sort_order: int = 0


@router.post("", status_code=201)
@router.post("/", status_code=201)
async def create_cost_code(body: CostCodeBody) -> dict[str, Any]:
    try:
        row = supabase.table("cost_codes").insert(body.model_dump()).execute().data
        return row[0] if row else {}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/{code_id}")
async def update_cost_code(code_id: str, body: dict[str, Any]) -> dict[str, Any]:
    try:
        patch = {k: v for k, v in body.items() if k in _ALLOWED_FIELDS}
        if not patch:
            raise HTTPException(status_code=422, detail="nothing to update")
        row = supabase.table("cost_codes").update(patch).eq("id", code_id).execute().data
        if not row:
            raise HTTPException(status_code=404, detail="cost code not found")
        return row[0]
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{code_id}")
async def delete_cost_code(code_id: str) -> dict[str, str]:
    try:
        supabase.table("cost_codes").delete().eq("id", code_id).execute()
        return {"ok": "deleted"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/import-csv")
async def import_csv(file: UploadFile = File(...)) -> dict[str, Any]:
    """Import cost codes from CSV. Columns: code, description, division?, category?, unit?, sort_order?"""
    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=422, detail="file must be .csv")
    content = await file.read()
    text = content.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))

    rows = []
    errors = []
    for i, r in enumerate(reader, 2):
        code = (r.get("code") or "").strip()
        desc = (r.get("description") or "").strip()
        if not code or not desc:
            errors.append(f"row {i}: missing code or description")
            continue
        try:
            sort_order = int(r.get("sort_order") or 0)
        except (TypeError, ValueError):
            sort_order = 0
        rows.append({
            "code": code,
            "description": desc,
            "division": (r.get("division") or "").strip() or None,
            "category": (r.get("category") or "").strip() or None,
            "unit": (r.get("unit") or "").strip() or None,
            "sort_order": sort_order,
        })

    imported = 0
    for row in rows:
        try:
            supabase.table("cost_codes").upsert(row, on_conflict="code").execute()
            imported += 1
        except Exception as e:
            errors.append(f"{row['code']}: {str(e)[:80]}")

    return {"imported": imported, "errors": errors, "total_rows": len(rows) + len(errors)}
