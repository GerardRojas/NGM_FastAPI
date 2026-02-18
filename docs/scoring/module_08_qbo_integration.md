# Module 08: QuickBooks Online Integration — Scoring Report

**Evaluated:** 2026-02-18
**Status:** Non-invasive fixes applied

---

## Files

| File | Role |
|------|------|
| `api/routers/qbo.py` | 2075 lines — OAuth, expense/budget/revenue sync, project mapping, migration |
| `services/qbo_service.py` | 578 lines — OAuth flow, token refresh, QBO API wrapper, data fetchers |
| `sql/create_expenses_qbo_import.sql` | 401 lines — staging table, indices, views |
| `sql/migrate_qbo_to_manual.sql` | 516 lines — migration with backup/rollback |

---

## Scorecard

| Dimension | Before | After | Delta | Notes |
|-----------|--------|-------|-------|-------|
| R1 Error Handling | 7 | 8 | +1 | 1 bare `except:` → `logger.debug()`, 3 print → proper log levels |
| R2 Data Integrity | 6 | 6 | — | `replace_all` deletes all rows — not atomic (invasive fix) |
| R3 Security | 7 | 7 | — | |
| R4 Performance | 5 | 5 | — | `MAXRESULTS 1000` still hardcoded (medium-risk fix) |
| R5 Memory Safety | 8 | 8 | — | |
| R6 Reliability | 7 | 7 | — | Token refresh already excellent |
| R7 Code Quality | 6 | 7 | +1 | 3 `print()` → `logger`; added `import logging` to qbo_service |
| R8 Interconnection | 7 | 7 | — | |
| R9 Financial (2x) | 5 | 5 | — | MAXRESULTS truncation risk remains (medium-risk fix) |
| R10 Mem Leak (2x) | 8 | 8 | — | |

```
Before: (7+6+7+5+8+7+6+7 + 5*2 + 8*2) / 12 = 79/12 = 6.58
After:  (8+6+7+5+8+7+7+7 + 5*2 + 8*2) / 12 = 81/12 = 6.75
```

**Weighted Score: 6.6 → 6.8 (+0.2)**

---

## Fixes Applied

### Fix 1: Add `import logging` + `logger` to `qbo_service.py`
- **Impact:** R7 — enables proper logging in service file

### Fix 2: 3 `print()` → `logger` in `qbo_service.py`
- Line 489: `print(f"[QBO] get_connection_status...")` → `logger.debug(...)`
- Line 524: `print(f"[QBO] Error processing token...")` → `logger.warning(...)`
- Line 543: `print(f"[QBO] Error in get_connection_status...")` → `logger.error(...)`
- **Impact:** R1 +0.5, R7 +0.5

### Fix 3: 1 bare `except Exception:` → `logger.debug()`
- **File:** `qbo_service.py` — `get_company_name()` line 298
- **Impact:** R1 +0.5

---

## Remaining Issues (not fixed)

| # | Issue | Risk to Fix | Why Deferred |
|---|-------|-------------|--------------|
| P0-1 | `replace_all` deletes ALL expenses without safeguard | **High** | Needs transaction + backup before delete |
| P0-2 | `MAXRESULTS 1000` hardcoded in 9 fetch functions | **Medium** | Needs QBO pagination with `startPosition` loop |
| P1-3 | No QBO API rate limiting | **Medium** | Needs throttle between sync calls |
| P3-4 | Magic numbers for batch sizes | **Low** | Cosmetic |

---

## Architecture Notes

**Token Refresh (well-implemented):**
```
Request → check expires_at - 5min buffer
    ↓ expired
Refresh token → new access_token
    ↓ 401 response
Auto-retry with fresh token (double-check safety)
```

**QBO Data Flow:**
```
QBO API → fetch_all_* (MAXRESULTS 1000) → qbo.py router
    ↓
Staging tables (qbo_expenses, budgets_qbo)
    ↓
Migration pipeline → expenses_manual_COGS (with backup + rollback)
```
