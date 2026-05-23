"""
General Expenses (non-COGS) router.
Same CRUD patterns as expenses_manual_COGS but uses expenses_manual_general table.
Key difference: project is OPTIONAL (allows "General" entries with no project).
"""

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field, field_validator
from api.supabase_client import supabase
from api.auth import get_current_user
from typing import Optional, List
import math
import time
import uuid as _uuid
from datetime import date, datetime

router = APIRouter(prefix="/general-expenses", tags=["General Expenses"])

TABLE = "expenses_manual_general"
_PAGE_SIZE = 1000


# ====== VALIDATORS (shared with expenses router) ======

def _validate_uuid_or_none(v, field_name=''):
    if v is None:
        return None
    if isinstance(v, str):
        stripped = v.strip()
        if stripped == '':
            return None
        try:
            _uuid.UUID(stripped)
            return stripped
        except (ValueError, AttributeError):
            raise ValueError(f"{field_name} must be a valid UUID, got: {repr(v)}")
    return v


def _validate_amount(v):
    if v is None:
        return None
    if not isinstance(v, (int, float)):
        raise ValueError("Amount must be a number")
    if math.isnan(v) or math.isinf(v):
        raise ValueError("Amount cannot be NaN or Infinity")
    if abs(v) > 999_999.99:
        raise ValueError("Amount cannot exceed 999,999.99")
    return round(v, 2)


def _validate_txn_date(v):
    if v is None:
        return None
    if not isinstance(v, str):
        raise ValueError("TxnDate must be a string in ISO format (YYYY-MM-DD)")
    stripped = v.strip()
    if stripped == '':
        return None
    try:
        if len(stripped) == 10:
            date.fromisoformat(stripped)
        else:
            datetime.fromisoformat(stripped.replace('Z', '+00:00'))
    except (ValueError, TypeError):
        raise ValueError(f"TxnDate must be ISO format (YYYY-MM-DD), got: {repr(v)}")
    return stripped


def _retry(fn, retries=3):
    for attempt in range(retries):
        try:
            return fn()
        except Exception as exc:
            if "Resource temporarily unavailable" in str(exc) and attempt < retries - 1:
                time.sleep(0.3 * (attempt + 1))
                continue
            raise


# ====== MODELS ======

class GenExpenseCreate(BaseModel):
    project: Optional[str] = None  # nullable — None means "General"
    txn_type: Optional[str] = None
    TxnDate: Optional[str] = None
    bill_id: Optional[str] = Field(None, max_length=500)
    vendor_id: Optional[str] = None
    payment_type: Optional[str] = None
    Amount: Optional[float] = None
    LineDescription: Optional[str] = Field(None, max_length=5000)
    account_id: Optional[str] = None
    created_by: Optional[str] = None
    show_on_reports: Optional[bool] = None
    receipt_url: Optional[str] = None

    @field_validator('project', mode='before')
    @classmethod
    def project_uuid_or_none(cls, v):
        if v is None or (isinstance(v, str) and v.strip() == ''):
            return None
        return _validate_uuid_or_none(v, 'project')

    @field_validator('Amount', mode='before')
    @classmethod
    def validate_amount(cls, v):
        return _validate_amount(v)

    @field_validator('TxnDate', mode='before')
    @classmethod
    def validate_txn_date(cls, v):
        return _validate_txn_date(v)

    @field_validator('txn_type', 'vendor_id', 'payment_type', 'account_id', 'created_by', mode='before')
    @classmethod
    def uuid_field_validate(cls, v, info):
        return _validate_uuid_or_none(v, info.field_name)

    @field_validator('bill_id', mode='before')
    @classmethod
    def empty_str_to_none(cls, v):
        if isinstance(v, str) and v.strip() == '':
            return None
        return v


class GenExpenseUpdate(BaseModel):
    project: Optional[str] = None
    txn_type: Optional[str] = None
    TxnDate: Optional[str] = None
    bill_id: Optional[str] = Field(None, max_length=500)
    vendor_id: Optional[str] = None
    payment_type: Optional[str] = None
    Amount: Optional[float] = None
    LineDescription: Optional[str] = Field(None, max_length=5000)
    account_id: Optional[str] = None
    auth_status: Optional[bool] = None
    auth_by: Optional[str] = None
    receipt_url: Optional[str] = None
    status: Optional[str] = None
    status_reason: Optional[str] = Field(None, max_length=1000)
    show_on_reports: Optional[bool] = None

    @field_validator('project', mode='before')
    @classmethod
    def project_uuid_or_none(cls, v):
        if v is None or (isinstance(v, str) and v.strip() == ''):
            return None
        return _validate_uuid_or_none(v, 'project')

    @field_validator('Amount', mode='before')
    @classmethod
    def validate_amount(cls, v):
        return _validate_amount(v)

    @field_validator('TxnDate', mode='before')
    @classmethod
    def validate_txn_date(cls, v):
        return _validate_txn_date(v)

    @field_validator('txn_type', 'vendor_id', 'payment_type', 'account_id', 'auth_by', mode='before')
    @classmethod
    def uuid_field_validate(cls, v, info):
        return _validate_uuid_or_none(v, info.field_name)


class GenExpenseBatchCreate(BaseModel):
    expenses: List[GenExpenseCreate] = Field(..., max_length=500)


# ====== ENRICHMENT ======

def _enrich(rows: list) -> list:
    """Add project_name and vendor_name to expense rows."""
    projects_resp = supabase.table("projects").select("project_id, project_name").execute()
    projects_map = {p["project_id"]: p["project_name"] for p in (projects_resp.data or [])}

    vendors_resp = supabase.table("Vendors").select("id, vendor_name").execute()
    vendors_map = {v["id"]: v["vendor_name"] for v in (vendors_resp.data or [])}

    txn_resp = supabase.table("txn_types").select("TnxType_id, TnxType_name").execute()
    txn_map = {t["TnxType_id"]: t["TnxType_name"] for t in (txn_resp.data or [])}

    for row in rows:
        row["project_name"] = projects_map.get(row.get("project"), "General")
        row["vendor_name"] = vendors_map.get(row.get("vendor_id"))
        row["txn_type_name"] = txn_map.get(row.get("txn_type"))
    return rows


# ====== ENDPOINTS ======

@router.get("")
def list_general_expenses(
    project: Optional[str] = None,
    general_only: Optional[bool] = None,
    limit: Optional[int] = None,
    current_user: dict = Depends(get_current_user)
):
    """
    List general expenses.
    - project=<uuid>  -> filter by project
    - general_only=true -> only entries with no project (General)
    - no filter -> all general expenses
    """
    try:
        raw: list = []
        offset = 0
        cap = min(limit, 50000) if limit else None

        while True:
            query = supabase.table(TABLE).select("*")
            if general_only:
                query = query.is_("project", "null")
            elif project:
                query = query.eq("project", project)
            query = query.order("TxnDate", desc=True)

            fetch = min(_PAGE_SIZE, cap - len(raw)) if cap else _PAGE_SIZE
            query = query.range(offset, offset + fetch - 1)
            batch = (query.execute()).data or []
            raw.extend(batch)

            if len(batch) < fetch:
                break
            if cap and len(raw) >= cap:
                break
            offset += fetch

        return {"data": _enrich(raw)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error reading general expenses: {e}")


@router.get("/all")
def list_all_general_expenses(
    limit: Optional[int] = 1000,
    current_user: dict = Depends(get_current_user)
):
    """List all general expenses across all projects + unassigned."""
    try:
        raw: list = []
        offset = 0
        cap = min(limit or 1000, 50000)

        while True:
            fetch = min(_PAGE_SIZE, cap - len(raw))
            query = (
                supabase.table(TABLE).select("*")
                .order("TxnDate", desc=True)
                .range(offset, offset + fetch - 1)
            )
            batch = (query.execute()).data or []
            raw.extend(batch)

            if len(batch) < fetch or len(raw) >= cap:
                break
            offset += fetch

        return {"data": _enrich(raw)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/meta")
def get_general_expenses_meta(current_user: dict = Depends(get_current_user)):
    """Catalogs for dropdowns (same as COGS + 'General' project option)."""
    try:
        txn = supabase.table("txn_types").select("*").execute()
        projects = supabase.table("projects").select("project_id, project_name").execute()
        vendors = supabase.table("Vendors").select("id, vendor_name").execute()
        payments = supabase.table("paymet_methods").select("*").execute()
        accounts = supabase.table("accounts").select("*").execute()

        return {
            "txn_types": txn.data or [],
            "projects": projects.data or [],
            "vendors": vendors.data or [],
            "payment_methods": payments.data or [],
            "accounts": accounts.data or [],
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("")
def create_general_expense(
    expense: GenExpenseCreate,
    current_user: dict = Depends(get_current_user)
):
    """Create a single general expense."""
    try:
        data = expense.model_dump(exclude_none=True)
        res = supabase.table(TABLE).insert(data).execute()
        return {"data": res.data[0] if res.data else None}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/batch")
def batch_create_general_expenses(
    payload: GenExpenseBatchCreate,
    current_user: dict = Depends(get_current_user)
):
    """Batch create up to 500 general expenses."""
    try:
        data = [e.model_dump(exclude_none=True) for e in payload.expenses]
        res = supabase.table(TABLE).insert(data).execute()
        return {
            "data": res.data or [],
            "count": len(res.data or [])
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{expense_id}")
def get_general_expense(expense_id: str, current_user: dict = Depends(get_current_user)):
    """Get a single general expense by ID."""
    try:
        res = supabase.table(TABLE).select("*").eq("expense_id", expense_id).execute()
        if not res.data:
            raise HTTPException(status_code=404, detail="Expense not found")
        rows = _enrich(res.data)
        return {"data": rows[0]}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/{expense_id}")
def update_general_expense(
    expense_id: str,
    expense: GenExpenseUpdate,
    current_user: dict = Depends(get_current_user)
):
    """Update a general expense."""
    try:
        data = expense.model_dump(exclude_none=True)
        if not data:
            raise HTTPException(status_code=400, detail="No fields to update")
        res = supabase.table(TABLE).update(data).eq("expense_id", expense_id).execute()
        if not res.data:
            raise HTTPException(status_code=404, detail="Expense not found")
        return {"data": res.data[0]}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{expense_id}")
def delete_general_expense(expense_id: str, current_user: dict = Depends(get_current_user)):
    """Delete a general expense."""
    try:
        res = supabase.table(TABLE).delete().eq("expense_id", expense_id).execute()
        if not res.data:
            raise HTTPException(status_code=404, detail="Expense not found")
        return {"message": "Deleted", "expense_id": expense_id}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
