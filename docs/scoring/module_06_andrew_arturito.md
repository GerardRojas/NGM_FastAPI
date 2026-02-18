# Module 06: Andrew (Mismatch) & Arturito (Chat) — Scoring Report

**Evaluated:** 2026-02-18
**Status:** Non-invasive fixes applied

---

## Files

| File | Role |
|------|------|
| `api/services/andrew_smart_layer.py` | 747 lines — missing info analysis, follow-up logic, message crafting |
| `api/services/andrew_mismatch_protocol.py` | 858 lines — Vision OCR extraction, reconciliation, auto-corrections |
| `api/helpers/andrew_messenger.py` | 108 lines — bot user creation, message posting |
| `api/routers/andrew_mismatch.py` | 170 lines — router for reconciliation endpoints |
| `api/routers/arturito.py` | 1106 lines — chat bot, intent detection, semantic search, entity cache |

---

## Scorecard

| Dimension | Before | After | Delta | Notes |
|-----------|--------|-------|-------|-------|
| R1 Error Handling | 6 | 7 | +1 | 3 bare `except:pass` → `logger.debug()` |
| R2 Data Integrity | 6 | 6 | — | Expense corrections still without audit trail (invasive) |
| R3 Security | 7 | 7 | — | |
| R4 Performance | 7 | 7 | — | |
| R5 Memory Safety | 7 | 7 | — | |
| R6 Reliability | 6 | 7 | +1 | Bot retry bug fixed — `_bot_user_verified` not set on error |
| R7 Code Quality | 5 | 7 | +2 | 21 `print()` → `logger`; 0 print() remain across all files |
| R8 Interconnection | 7 | 7 | — | |
| R9 Financial (2x) | 6 | 6 | — | Expense amount corrections still lack audit trail |
| R10 Mem Leak (2x) | 7 | 7 | — | |

```
Before: (6+6+7+7+7+6+5+7 + 6*2 + 7*2) / 12 = 77/12 = 6.42
After:  (7+6+7+7+7+7+7+7 + 6*2 + 7*2) / 12 = 81/12 = 6.75
```

**Weighted Score: 6.4 → 6.8 (+0.4)**

---

## Fixes Applied

### Fix 1: 12 `print()` → `logger` in `arturito.py`
- Added `import logging` + `logger_art = logging.getLogger(__name__)`
- 8 → `logger_art.error()`, 3 → `logger_art.warning()`, 1 → `logger_art.info()`
- **Impact:** R7 +1 — all error handlers now visible in centralized logging

### Fix 2: 9 `print()` → `logger` in `andrew_messenger.py`
- Full rewrite to use logging module
- **Impact:** R7 +1 — bot lifecycle visible in logs

### Fix 3: Bot retry bug fixed
- **File:** `andrew_messenger.py` line 47
- **Impact:** R6 +1 — same fix as daneel_messenger
```python
# Before: _bot_user_verified = True  (even on failure)
# After: removed — allows retry on next call
```

### Fix 4: 2 bare `except:pass` → `logger.debug()` in `mismatch_protocol.py`
- **Lines:** 63 (JSON parse), 795 (vendor lookup)
- **Impact:** R1 +0.5

### Fix 5: 1 bare `except:` → `logger.debug()` in `smart_layer.py`
- **Line:** 745 (bookkeeping mentions resolve)
- **Impact:** R1 +0.5

---

## Remaining Issues (not fixed)

| # | Issue | Risk to Fix | Why Deferred |
|---|-------|-------------|--------------|
| P1-1 | Expense amount correction without audit trail | **Medium** | Needs change_log insert in mismatch_protocol |
| P1-2 | Race condition on `_bot_user_verified` global | **Medium** | Needs threading.Lock |
| P1-3 | TOCTOU on config update | **Low** | Race window is small |
| P2-4 | Entity cache race condition in arturito.py | **Low** | Needs asyncio.Lock |
