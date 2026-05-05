# Module 02: Expenses Core CRUD + Status Machine — Scoring Report

**Evaluated:** 2026-02-18
**Status:** Partial fixes applied

---

## Files

| File | Role |
|------|------|
| `api/routers/expenses.py` | ~1950 lines — CRUD, batch, status machine, summaries, authorization |
| `api/services/budget_monitor.py` | Budget alert computation (actuals vs budgets) |
| `sql/expense_status_log.sql` | 117 lines — audit log schema, CHECK constraints |

---

## Scorecard

| Dimension | Before | After | Delta | Notes |
|-----------|--------|-------|-------|-------|
| R1 Error Handling | 7 | 8 | +1 | Background task lambdas now use `_bg_insert` with try/except + logger |
| R2 Data Integrity | 7 | 7 | — | `updated_by` now set in /status and /soft-delete (trigger crash prevented) |
| R3 Security | 7 | 7 | — | `user_id` as query param still present (impersonation risk, risky to change) |
| R4 Performance | 7 | 8 | +1 | All summary endpoints and `/all` now paginate (no 1000-row truncation) |
| R5 Memory Safety | 7 | 7 | — | ThreadPoolExecutor per request unchanged |
| R6 Reliability | 6 | 7 | +1 | `updated_by` prevents trigger crashes; bg tasks no longer silently swallowed |
| R7 Code Quality | 6 | 7 | +1 | `_bg_insert` helper replaces anonymous lambdas; `logger` added |
| R8 Interconnection | 6 | 8 | +2 | Budget monitor now queries correct table (`expenses_manual_COGS`) |
| R9 Financial (2x) | 4 | 6 | +2 | Summaries now return ALL rows (no silent truncation). `float` still used |
| R10 Mem Leak (2x) | 7 | 7 | — | No change needed |

```
Before: (7+7+7+7+7+6+6+6 + 4*2 + 7*2) / 12 = 75/12 = 6.25
After:  (8+7+7+8+7+7+7+8 + 6*2 + 7*2) / 12 = 85/12 = 7.08
```

**Weighted Score: 6.3 → 7.1 (+0.8)**

---

## Fixes Applied

### P0 Fix 1: Summary endpoints paginated (1000-row truncation)
- **Endpoints:** `/summary/by-txn-type`, `/summary/by-project`, `/pending-authorization/summary`
- **Pattern:** `while True` loop with `.range(offset, offset + PAGE_SIZE - 1)`, breaks when `len(batch) < PAGE_SIZE`
```python
# Before (silently capped at 1000 rows):
resp = query.execute()
raw_expenses = resp.data or []

# After (fetches ALL matching rows):
raw_expenses = []
offset = 0
while True:
    q = supabase.table("expenses_manual_COGS").select("txn_type, Amount")
    # ... filters ...
    batch = (q.range(offset, offset + _PAGE_SIZE - 1).execute()).data or []
    raw_expenses.extend(batch)
    if len(batch) < _PAGE_SIZE:
        break
    offset += _PAGE_SIZE
```

### P0 Fix 2: Budget monitor wrong table
- **File:** `api/services/budget_monitor.py` line 147
```python
# Before:
result = supabase.table("expenses")  # WRONG table

# After:
result = supabase.table("expenses_manual_COGS")  # Correct table
```

### P1 Fix 3: `updated_by` added to /status and /soft-delete
- **Files:** `expenses.py` — `/status` and `/soft-delete` endpoints
```python
# /status endpoint:
update_data = {"status": payload.status, "updated_by": user_id}

# /soft-delete endpoint:
update_data = {
    "status": "review",
    "status_reason": "Deletion requested",
    "auth_status": False,
    "auth_by": None,
    "updated_by": user_id
}
```

### P1 Fix 4: Background task lambdas → `_bg_insert` with logging
- **Impact:** Silent failures now logged via `logger.error()`
```python
# Before (silent failure):
background_tasks.add_task(
    lambda logs=change_logs: supabase.table("expense_change_log").insert(logs).execute()
)

# After (errors logged):
def _bg_insert(table_name: str, data, label: str = ""):
    try:
        supabase.table(table_name).insert(data).execute()
    except Exception as exc:
        logger.error("[BG %s] Insert into %s failed: %s", label, table_name, exc)

background_tasks.add_task(_bg_insert, "expense_change_log", change_logs, "CHANGE_LOG")
```

### P2 Fix 5: `/all` endpoint paginated
- **Pattern:** Same `.range()` pagination loop with `max_rows` cap
```python
# Before:
query = query.limit(limit)  # Silently capped at 1000 by Supabase
resp = query.execute()

# After:
raw_expenses = []
offset = 0
max_rows = limit or 10000
while offset < max_rows:
    page_end = min(offset + _PAGE_SIZE, max_rows) - 1
    resp = (
        supabase.table("expenses_manual_COGS").select("*")
        .order("TxnDate", desc=True)
        .range(offset, page_end)
        .execute()
    )
    batch = resp.data or []
    raw_expenses.extend(batch)
    if len(batch) < _PAGE_SIZE:
        break
    offset += _PAGE_SIZE
```

---

## Remaining Issues (not fixed)

| # | Issue | Risk to Fix | Why Deferred |
|---|-------|-------------|--------------|
| P0-3 | `Amount: float` not Decimal | **High** | Requires Pydantic model change, DB type change, all arithmetic changes. Frontend sends float. |
| P1-4 | `user_id` from query param (impersonation) | **Medium** | Requires frontend changes to stop sending user_id, use JWT sub instead |
| P1-5 | `auth_status`/`auth_by` settable via PATCH | **Medium** | Needs role check added to PATCH handler |
| P1-6 | Status update + log not atomic | **Low** | Would need DB transaction or stored procedure |
| P1-7 | Hard delete cascades to audit logs | **Medium** | Needs ON DELETE SET NULL or soft-delete-only policy |
| P2-10 | Batch PATCH partial state | **Low** | Would need transaction wrapper |
| P2-11 | Race condition on concurrent status changes | **Low** | Would need optimistic locking or SELECT FOR UPDATE |
| P3 | Status derivation duplicated 6x | **Low** | Refactor to helper function |
| P3 | ThreadPoolExecutor per request | **Low** | Move to module-level pool |

---

## Specific Questions Answered

1. **Status machine enforced server-side?** Partially. CHECK constraint limits values to pending/auth/review, but NO transition validation — any status can move to any other.
2. **Batch operations leave partial state?** Batch create is atomic (single insert). Batch update is NOT atomic (individual loop).
3. **Change/status logs guaranteed?** Best-effort with logging. `_bg_insert` helper now logs failures instead of swallowing silently.
4. **`updated_by` always set?** Now set in PATCH, /status, and /soft-delete. Still missing in `create` endpoint (low risk — triggers don't fire on INSERT).
5. **1000-row limit handled?** Now handled in `list_expenses`, all 3 summary endpoints, and `/all`. Metrics endpoints still uncapped (low volume).
