# Module 05: Daneel Auto-Auth — Scoring Report

**Evaluated:** 2026-02-18
**Status:** Non-invasive fixes applied

---

## Files

| File | Role |
|------|------|
| `api/services/daneel_auto_auth.py` | 2229 lines — auto-auth engine, duplicate detection, GPT Vision, health check |
| `api/helpers/daneel_messenger.py` | 94 lines — bot user creation, message posting |

---

## Scorecard

| Dimension | Before | After | Delta | Notes |
|-----------|--------|-------|-------|-------|
| R1 Error Handling | 6 | 7 | +1 | 5 bare `except:` now log via `logger.debug()` |
| R2 Data Integrity | 6 | 6 | — | TOCTOU race on authorize remains (invasive fix) |
| R3 Security | 7 | 7 | — | |
| R4 Performance | 7 | 7 | — | |
| R5 Memory Safety | 6 | 7 | +1 | `del buf, images, file_content` in vision extract |
| R6 Reliability | 5 | 6 | +1 | Bot retry bug fixed — `_bot_user_verified` not set on error |
| R7 Code Quality | 5 | 7 | +2 | 27 `print()` → `logger`; 0 print() remain in both files |
| R8 Interconnection | 7 | 7 | — | |
| R9 Financial (2x) | 6 | 6 | — | TOCTOU risk remains (invasive) |
| R10 Mem Leak (2x) | 6 | 7 | +1 | PIL/BytesIO freed before GPT call |

```
Before: (6+6+7+7+6+5+5+7 + 6*2 + 6*2) / 12 = 73/12 = 6.08
After:  (7+6+7+7+7+6+7+7 + 6*2 + 7*2) / 12 = 80/12 = 6.67
```

**Weighted Score: 6.1 → 6.7 (+0.6)**

---

## Fixes Applied

### Fix 1: 19 `print()` → `logger` in `trigger_auto_auth_for_bill()`
- **File:** `daneel_auto_auth.py` lines 1705-1997
- **Impact:** R7 +1 — all operational messages now visible in centralized logging
- 16 → `logger.info()`, 2 → `logger.warning()` (COLLISION, AMBIGUOUS), 1 removed (redundant with existing `logger.error`)

### Fix 2: 8 `print()` → `logger` in `daneel_messenger.py`
- **File:** `daneel_messenger.py` — full rewrite to use logging
- Added `import logging` + `logger = logging.getLogger(__name__)`
- **Impact:** R7 +1 — bot lifecycle visible in logs

### Fix 3: Bot retry bug fixed
- **File:** `daneel_messenger.py` line 47
- **Impact:** R6 +1
```python
# Before (broken — never retries):
except Exception as e:
    print(...)
    _bot_user_verified = True  # ← marked as done even on failure

# After (will retry on next call):
except Exception as e:
    logger.error("[DaneelMessenger] Error ensuring bot user exists: %s", e)
    # Do NOT mark as verified on failure — allow retry on next call
```

### Fix 4: 5 bare `except Exception:` → `logger.debug()`
- **File:** `daneel_auto_auth.py` lines 88, 991, 1007, 1113, 1649
- **Impact:** R1 +1 — suppressed errors now visible at DEBUG level

### Fix 5: `del buf, images, file_content` in vision extract
- **File:** `daneel_auto_auth.py` — `gpt_vision_extract_bill_total()`
- **Impact:** R5 +1, R10 +1 — frees PIL images (10-30MB) and download buffer before GPT call
```python
buf.close()
del buf, images  # free PIL + BytesIO before GPT call
# ...
del file_content  # free download buffer before GPT call
```

---

## Remaining Issues (not fixed)

| # | Issue | Risk to Fix | Why Deferred |
|---|-------|-------------|--------------|
| P1-1 | TOCTOU race on expense authorization | **High** | Needs row-level locking or version check |
| P1-2 | Race condition on `_bot_user_verified` global | **Medium** | Needs threading.Lock (multi-thread concern) |
| P1-3 | Poppler path hardcoded for Windows | **Low** | Works in current deploy, cosmetic |
| P2-4 | Float comparisons for amounts | **High** | Requires Decimal across entire pipeline |
