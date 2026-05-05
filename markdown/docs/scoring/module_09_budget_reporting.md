# Module 09: Budget Monitoring & Reporting — Scoring Report

**Evaluated:** 2026-02-18
**Status:** Non-invasive fixes applied

---

## Files

| File | Role |
|------|------|
| `api/routers/budget_alerts.py` | 825 lines — alert config, dedup, threshold checks, push notifications |
| `api/services/budget_monitor.py` | 695 lines — actuals vs budget calculation, alert detection |
| `api/routers/reporting.py` | 113 lines — PDF report generation, vault upload |
| `services/arturito/handlers/bva_handler.py` | 1640 lines — BVA report data, PDF rendering, category mapping |
| `services/arturito/handlers/pnl_handler.py` | 402 lines — P&L COGS report handler |

---

## Scorecard

| Dimension | Before | After | Delta | Notes |
|-----------|--------|-------|-------|-------|
| R1 Error Handling | 7 | 8 | +1 | 20 `print()` → `logger`; bare excepts now log; f-string loggers → %s |
| R2 Data Integrity | 7 | 7 | — | |
| R3 Security | 8 | 8 | — | |
| R4 Performance | 8 | 8 | — | |
| R5 Memory Safety | 7 | 7 | — | |
| R6 Reliability | 6 | 6 | — | |
| R7 Code Quality | 6 | 7 | +1 | 0 `print()` remain; 0 bare excepts; proper log levels |
| R8 Interconnection | 7 | 7 | — | |
| R9 Financial (2x) | 7 | 7 | — | |
| R10 Mem Leak (2x) | 6 | 6 | — | |

```
Before: (7+7+8+8+7+6+6+7 + 7*2 + 6*2) / 12 = 82/12 = 6.83
After:  (8+7+8+8+7+6+7+7 + 7*2 + 6*2) / 12 = 84/12 = 7.00
```

**Weighted Score: 6.8 → 7.0 (+0.2)**

---

## Fixes Applied

### Fix 1: 20 `print()` → `logger` across 3 files
- **budget_monitor.py:** 1 print + 15 f-string loggers → `%s` format
- **bva_handler.py:** 15 `print()` → logger (added `import logging` + `logger`)
- **pnl_handler.py:** 3 `print()` → logger (added `import logging` + `logger`)
- **Impact:** R1 +1, R7 +1

### Fix 2: Bare except patterns → logged
- **reporting.py:** 1 bare `except:` → `except Exception as _exc: logger.debug()`
- **bva_handler.py:** 1 bare `except:` → `except Exception as _exc: logger.debug()`
- Added `import logging` + `logger` to reporting.py
- **Impact:** R1 +0.5

---

## Remaining Issues (not fixed)

| # | Issue | Risk to Fix | Why Deferred |
|---|-------|-------------|--------------|
| P1-1 | No timeout on Supabase queries | Medium | Global timeout config needed |
| P1-2 | PDF BytesIO not explicitly freed | Low | GC handles it |
| P2-3 | get_account_name duplicated 4x | Medium | Needs shared helper refactor |
| P2-4 | Category aliases duplicated | Medium | Needs config extraction |
| P3-5 | Float arithmetic (not Decimal) | High | System-wide change |
