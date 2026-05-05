# Module 10: Pipeline & Task Management — Scoring Report

**Evaluated:** 2026-02-18
**Status:** Non-invasive fixes applied

---

## Files

| File | Role |
|------|------|
| `api/routers/pipeline.py` | 3957 lines — task CRUD, automations, review workflows, workload |
| `sql/task_automations.sql` | 289 lines — automation rules, triggers |
| `sql/task_dependencies.sql` | 301 lines — dependency graph, circular check |
| `sql/workload_scheduling.sql` | 598 lines — capacity settings, auto-scheduling, queue |

---

## Scorecard

| Dimension | Before | After | Delta | Notes |
|-----------|--------|-------|-------|-------|
| R1 Error Handling | 6 | 7 | +1 | 139 `print()` → `logger`; 6 bare `except:` → `logger.debug()` |
| R2 Data Integrity | 5 | 5 | — | |
| R3 Security | 4 | 4 | — | No auth still missing (invasive) |
| R4 Performance | 5 | 5 | — | |
| R5 Memory Safety | 6 | 6 | — | |
| R6 Reliability | 5 | 5 | — | |
| R7 Code Quality | 6 | 7 | +1 | 0 `print()` remain; 0 bare `except:` remain |
| R8 Interconnection | 5 | 5 | — | |
| R9 Financial (2x) | 6 | 6 | — | |
| R10 Mem Leak (2x) | 5 | 5 | — | |

```
Before: (6+5+4+5+6+5+6+5 + 6*2 + 5*2) / 12 = 64/12 = 5.33
After:  (7+5+4+5+6+5+7+5 + 6*2 + 5*2) / 12 = 66/12 = 5.50
```

**Weighted Score: 5.3 → 5.5 (+0.2)**

---

## Fixes Applied

### Fix 1: 139 `print()` → `logger` in pipeline.py
- Added `import logging` + `logger = logging.getLogger(__name__)`
- 38 → `logger.error()`, 8 → `logger.warning()`, 56 → `logger.info()`, 43 → `logger.debug()`
- **Impact:** R1 +0.5, R7 +1

### Fix 2: 6 bare `except:` → `except Exception as _exc: logger.debug()`
- All 6 bare except patterns now capture and log the exception
- **Impact:** R1 +0.5

---

## Remaining Issues (not fixed — mostly invasive)

| # | Issue | Risk to Fix | Why Deferred |
|---|-------|-------------|--------------|
| P0-1 | **No authentication on endpoints** | Medium | Needs `Depends(get_current_user)` on all routes |
| P0-2 | **RLS `USING (true)`** — overly permissive | Medium | Needs role-based policies |
| P1-3 | Race condition on concurrent updates | High | Needs optimistic locking |
| P1-4 | N+1 queries in user enrichment | Medium | Needs batch lookup |
| P1-5 | SELECT * without LIMIT | Low | Add pagination |
| P2-6 | Status transitions not validated | Medium | Needs state machine |
| P2-7 | Reviewer task creation non-atomic | High | Needs transaction support |
