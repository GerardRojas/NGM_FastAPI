"""
Budgets Router
Handles budget import from QuickBooks Online CSV exports
"""

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import List, Optional
import uuid
from datetime import datetime, date

from api.auth import get_current_user
from api.supabase_client import supabase

router = APIRouter(prefix="/budgets", tags=["budgets"])


# ================================
# MODELS
# ================================

class BudgetCSVRow(BaseModel):
    """Single row from QuickBooks budget CSV export, or from the estimator
    save-as-budget flow (which also carries the categories rearch classification:
    CategoryId / SubcategoryId / CostType)."""
    BudgetName: str
    BudgetId: str
    Year: Optional[int] = None
    StartDate: Optional[str] = None
    EndDate: Optional[str] = None
    Active: Optional[str] = None  # "true"/"false" string from CSV
    AccountId: Optional[str] = None
    AccountName: Optional[str] = None
    Amount_SUM: Optional[str] = None  # String because CSV may have formatting
    # Phase B (categories rearch): direct classification when the producer knows
    # it (estimator). For QBO-imported rows these stay None and we derive them
    # from AccountId via the overlay below.
    CategoryId: Optional[str] = None
    SubcategoryId: Optional[str] = None
    CostType: Optional[str] = None


class BudgetImportRequest(BaseModel):
    """Request to import budget CSV data"""
    project_id: str  # NGM project ID to link budgets to
    headers: List[str]  # CSV headers
    data: List[List[str]]  # CSV rows as arrays


# ================================
# HELPERS
# ================================

def parse_date(date_str: Optional[str]) -> Optional[date]:
    """Parse date string from CSV (format: YYYY-MM-DD or MM/DD/YYYY)"""
    if not date_str or not date_str.strip():
        return None

    date_str = date_str.strip()

    # Try common date formats
    formats = [
        "%Y-%m-%d",      # 2024-01-01
        "%m/%d/%Y",      # 01/01/2024
        "%Y/%m/%d",      # 2024/01/01
        "%m-%d-%Y",      # 01-01-2024
        "%d/%m/%Y",      # 01/01/2024 (European)
    ]

    for fmt in formats:
        try:
            return datetime.strptime(date_str, fmt).date()
        except ValueError:
            continue

    return None


def parse_amount(amount_str: Optional[str]) -> Optional[float]:
    """Parse amount string from CSV (handles $ and commas)"""
    if not amount_str or not amount_str.strip():
        return None

    # Remove currency symbols, commas, spaces
    cleaned = amount_str.strip().replace('$', '').replace(',', '').replace(' ', '')

    try:
        return float(cleaned)
    except ValueError:
        return None


def parse_boolean(bool_str: Optional[str]) -> bool:
    """Parse boolean from CSV string"""
    if not bool_str:
        return True  # Default to active

    bool_str = bool_str.strip().lower()
    return bool_str in ['true', 't', 'yes', 'y', '1', 'active']


def _budget_classification_overlay() -> dict:
    """Pull the account_category_map overlay once per /budgets/import call so we
    can auto-derive (category_id, subcategory_id, cost_type) for rows that only
    carry an account_id. Mirrors the helper used in expenses.py for symmetry."""
    out: dict = {}
    try:
        sub_rows = supabase.table("subcategories").select("id, category_id").execute().data or []
        sub_to_cat = {row["id"]: row.get("category_id") for row in sub_rows}
        map_rows = supabase.table("account_category_map").select("account_id, subcategory_id, cost_type").execute().data or []
        for row in map_rows:
            aid = row.get("account_id")
            sid = row.get("subcategory_id")
            if not aid or not sid:
                continue
            out[aid] = {
                "subcategory_id": sid,
                "cost_type": row.get("cost_type"),
                "category_id": sub_to_cat.get(sid),
            }
    except Exception as e:
        # Overlay is optional — without it, rows simply land unclassified and
        # BVA falls back to the legacy account-name JOIN.
        print(f"[BUDGETS] Could not load classification overlay: {e}")
    return out


# ================================
# ROUTES
# ================================

@router.get("")
async def get_budgets(
    project: Optional[str] = None,
    year: Optional[int] = None,
    active_only: bool = True,
    current_user: dict = Depends(get_current_user)
):
    """
    Get budgets, optionally filtered by project and/or year

    Query params:
    - project: Filter by NGM project ID
    - year: Filter by budget year
    - active_only: Only return active budgets (default: true)
    """
    try:
        # Build query
        query = supabase.table("budgets_qbo").select("*")

        if project:
            query = query.eq("ngm_project_id", project)

        if year:
            query = query.eq("year", year)

        if active_only:
            query = query.eq("active", True)

        # Order by year desc, then budget name
        query = query.order("year", desc=True).order("budget_name")

        result = query.execute()

        return {"data": result.data or []}

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching budgets: {str(e)}")


@router.post("/import")
async def import_budgets(
    request: BudgetImportRequest,
    current_user: dict = Depends(get_current_user)
):
    """
    Import budgets from CSV data

    Expected CSV structure:
    BudgetName, BudgetId, Year, StartDate, EndDate, Active, AccountId, AccountName, Amount_SUM

    Each row represents one budget account line item.
    All rows are linked to the specified NGM project.
    """
    try:
        # Validate headers. CategoryId / SubcategoryId / CostType are accepted
        # (Phase B of categories rearch) but stay optional — legacy QBO CSVs
        # still import unchanged.
        expected_headers = [
            "BudgetName", "BudgetId", "Year", "StartDate", "EndDate",
            "Active", "AccountId", "AccountName", "Amount_SUM",
            "CategoryId", "SubcategoryId", "CostType"
        ]

        # Create header mapping (case-insensitive)
        header_map = {}
        for i, header in enumerate(request.headers):
            header_clean = header.strip()
            # Find matching expected header (case-insensitive)
            for expected in expected_headers:
                if header_clean.lower() == expected.lower():
                    header_map[expected] = i
                    break

        # Check required fields
        required = ["BudgetName", "BudgetId", "Amount_SUM"]
        missing = [h for h in required if h not in header_map]
        if missing:
            raise HTTPException(
                status_code=400,
                detail=f"Missing required CSV columns: {', '.join(missing)}"
            )

        # Generate batch ID for this import
        batch_id = f"batch_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_{str(uuid.uuid4())[:8]}"

        # Pull the overlay once per import so we can auto-classify rows that
        # only ship AccountId. Producers that already know the triple (the
        # estimator) win — we only consult the overlay when SubcategoryId is
        # empty.
        classification_overlay = _budget_classification_overlay()

        # Prepare records for bulk insert
        records = []

        for row_idx, row in enumerate(request.data):
            try:
                # Extract values by header position
                def get_val(header: str) -> Optional[str]:
                    idx = header_map.get(header)
                    if idx is not None and idx < len(row):
                        val = row[idx]
                        return val if val and val.strip() else None
                    return None

                budget_name = get_val("BudgetName")
                budget_id = get_val("BudgetId")
                amount_str = get_val("Amount_SUM")

                # Skip rows with missing required fields
                if not budget_name or not budget_id or not amount_str:
                    continue

                # Parse values
                year_val = get_val("Year")
                year = int(year_val) if year_val and year_val.isdigit() else None

                start_date = parse_date(get_val("StartDate"))
                end_date = parse_date(get_val("EndDate"))
                active = parse_boolean(get_val("Active"))
                amount_sum = parse_amount(amount_str)

                # Classification (Phase B). Producer-provided columns win; when
                # absent and we have an AccountId in the overlay, derive the
                # triple from there. None means "couldn't classify" — the row
                # will still match by account name in BVA.
                account_id_val = get_val("AccountId")
                subcategory_id_val = get_val("SubcategoryId")
                category_id_val = get_val("CategoryId")
                cost_type_val = get_val("CostType")
                if not subcategory_id_val and account_id_val:
                    overlay_hit = classification_overlay.get(account_id_val)
                    if overlay_hit:
                        subcategory_id_val = subcategory_id_val or overlay_hit.get("subcategory_id")
                        cost_type_val = cost_type_val or overlay_hit.get("cost_type")
                        category_id_val = category_id_val or overlay_hit.get("category_id")

                # Build record
                record = {
                    "budget_name": budget_name,
                    "budget_id_qbo": budget_id,
                    "year": year,
                    "start_date": start_date.isoformat() if start_date else None,
                    "end_date": end_date.isoformat() if end_date else None,
                    "active": active,
                    "account_id": account_id_val,
                    "account_name": get_val("AccountName"),
                    "amount_sum": amount_sum,
                    "ngm_project_id": request.project_id,
                    "import_batch_id": batch_id,
                    "import_source": "csv",
                    "category_id": category_id_val,
                    "subcategory_id": subcategory_id_val,
                    "cost_type": cost_type_val,
                }

                records.append(record)

            except Exception as row_err:
                print(f"[BUDGETS] Error processing row {row_idx}: {row_err}")
                continue

        if not records:
            raise HTTPException(
                status_code=400,
                detail="No valid budget records found in CSV"
            )

        # Bulk insert
        result = supabase.table("budgets_qbo").insert(records).execute()

        inserted_count = len(result.data) if result.data else len(records)

        return {
            "message": f"Successfully imported {inserted_count} budget records",
            "count": inserted_count,
            "batch_id": batch_id,
            "project_id": request.project_id
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error importing budgets: {str(e)}")


@router.delete("/batch/{batch_id}")
async def delete_batch(
    batch_id: str,
    current_user: dict = Depends(get_current_user)
):
    """Delete all budgets from a specific import batch"""
    try:
        result = supabase.table("budgets_qbo").delete().eq("import_batch_id", batch_id).execute()

        deleted_count = len(result.data) if result.data else 0

        return {
            "message": f"Deleted {deleted_count} budget records from batch {batch_id}",
            "count": deleted_count
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error deleting batch: {str(e)}")
