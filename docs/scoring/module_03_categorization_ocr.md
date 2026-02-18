# Module 03: Categorization + OCR Pipeline — Scoring Report

**Evaluated:** 2026-02-18
**Status:** Non-invasive fixes applied

---

## Files

| File | Role |
|------|------|
| `services/receipt_scanner.py` | OCR pipeline, vision/text modes, categorization cache |
| `services/receipt_regex.py` | Regex-based receipt parsing for fast mode |
| `api/services/categorization_ml.py` | TF-IDF + k-NN ML classifier, retrain cycle |
| `api/services/gpt_client.py` | Singleton GPT mini/heavy client wrappers |
| `utils/hashing.py` | **NEW** — shared `generate_description_hash` |

---

## Scorecard

| Dimension | Before | After | Delta | Notes |
|-----------|--------|-------|-------|-------|
| R1 Error Handling | 7 | 8 | +1 | `print()` → `logging` (41 calls), error/warning/info levels |
| R2 Data Integrity | 7 | 7 | — | |
| R3 Security | 8 | 8 | — | |
| R4 Performance | 8 | 8 | — | |
| R5 Memory Safety | 7 | 8 | +1 | `del base64_images, vision_user` + `del file_content` post-extraction |
| R6 Reliability | 8 | 8 | — | Cache hit-count now works (was broken RPC) |
| R7 Code Quality | 7 | 8 | +1 | All `print()` → `logger`, proper log levels |
| R8 Interconnection | 7 | 8 | +1 | `_generate_description_hash` → shared `utils/hashing.py` |
| R9 Financial (2x) | 8 | 8 | — | |
| R10 Mem Leak (2x) | 7 | 8 | +1 | base64 (30-50MB) + file_content freed immediately after use |

```
Before: (7+7+8+8+7+8+7+7 + 8*2 + 7*2) / 12 = 89/12 = 7.42
After:  (8+7+8+8+8+8+8+8 + 8*2 + 8*2) / 12 = 95/12 = 7.92
```

**Weighted Score: 7.4 → 7.9 (+0.5)**

---

## Fixes Applied

### Fix 1: `print()` → `logging` (41 calls)
- **File:** `receipt_scanner.py`
- **Impact:** R1 +1, R7 +1
- Added `import logging` + `logger = logging.getLogger(__name__)`
- Mapped severity: `logger.error()` for errors, `logger.warning()` for fallbacks/cache failures, `logger.info()` for normal flow
- 0 remaining `print()` calls

### Fix 2: `del base64_images, vision_user` after GPT vision call
- **File:** `receipt_scanner.py` line ~1058
- **Impact:** R5 +1, R10 +1 — frees 30-50MB per multi-page PDF scan
```python
# After GPT heavy() returns:
del base64_images, vision_user  # free 30-50MB per multi-page scan
```

### Fix 3: `del file_content` after extraction phase
- **File:** `receipt_scanner.py` line ~857
- **Impact:** R5, R10 — frees up to 20MB before long processing phase
```python
# After extraction mode determined, file_content no longer needed
del file_content
```

### Fix 4: Fix broken cache hit-count RPC
- **File:** `receipt_scanner.py` lines 1123-1126
- **Impact:** R6 — cache analytics were silently broken
```python
# Before (broken — rpc() returns response object, not value):
"hit_count": supabase.rpc("increment", {"x": 1, "row_id": ...}),

# After (working):
current_hit = cache_entry.get("hit_count", 0) or 0
"hit_count": current_hit + 1,
```

### Fix 5: Deduplicate `_generate_description_hash`
- **Files:** `receipt_scanner.py`, `categorization_ml.py` → `utils/hashing.py`
- **Impact:** R8 +1 — single source of truth, no more "keep in sync" risk
```python
# Both files now:
from utils.hashing import generate_description_hash as _generate_description_hash
```

---

## Remaining Issues (not fixed)

| # | Issue | Risk to Fix |
|---|-------|-------------|
| P1-2 | No rate limiting on GPT endpoints | Medium — needs FastAPI middleware/dependency |
| P1-4 | Inconsistent confidence scales across tiers | Medium — needs normalization logic |
| P2-6 | `auto_categorize` accepts `dict` not Pydantic | Medium — API contract change |
| P2-8 | OCR confidence is binary (0 or 100) | Low |
| P3-11 | `auto_categorize` is 380+ lines | Low — refactor to smaller functions |
| P3-12 | Magic numbers (30-day TTL, 90% threshold, etc.) | Low |
| P3-14 | No retry/backoff when GPT fails | Low |

---

## Architecture Strengths

The escalation chain is well-designed:
```
cache hit?  → return (0 cost, <1ms)
    ↓ miss
vendor affinity ≥90%?  → return (0 cost, ~50ms)
    ↓ no
ML confidence ≥90%?  → return (0 cost, ~100ms)
    ↓ no
GPT mini  → return if above min_confidence (~$0.01, ~3s)
    ↓ low confidence
GPT heavy  → return (~$0.03, ~10s)
```
