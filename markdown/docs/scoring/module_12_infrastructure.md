# Module 12: Infrastructure & Cross-Cutting — Scoring Report

**Evaluated:** 2026-02-18
**Status:** Non-invasive fixes applied

---

## Files

| File | Role |
|------|------|
| `api/main.py` | 382 lines — app bootstrap, CORS, memory management loop, 47 router registrations |
| `api/supabase_client.py` | 10 lines — singleton Supabase client with env validation |
| `api/services/gpt_client.py` | 291 lines — singleton OpenAI sync/async clients, mini/heavy wrappers |
| `Dockerfile` | 27 lines — python:3.13-slim, gunicorn+uvicorn, max-requests 5000 |
| `render.yaml` | 12 lines — Render deployment config, poppler-utils |
| `requirements.txt` | 53 lines — all 53 packages now pinned with == |

---

## Scorecard

| Dimension | Before | After | Delta | Notes |
|-----------|--------|-------|-------|-------|
| R1 Error Handling | 8 | 8 | — | |
| R2 Data Integrity | 7 | 7 | — | |
| R3 Security | 7 | 8 | +1 | All 53 deps pinned; CORS methods restricted to specific verbs |
| R4 Performance | 8 | 8 | — | |
| R5 Memory Safety | 8 | 8 | — | |
| R6 Reliability | 5 | 7 | +2 | Health check now verifies Supabase connectivity |
| R7 Code Quality | 9 | 9 | — | Already clean (0 print, 0 bare except) |
| R8 Interconnection | 8 | 8 | — | |
| R9 Financial (2x) | 7 | 7 | — | |
| R10 Mem Leak (2x) | 8 | 8 | — | |

```
Before: (8+7+7+8+8+5+9+8 + 7*2 + 8*2) / 12 = 90/12 = 7.50
After:  (8+7+8+8+8+7+9+8 + 7*2 + 8*2) / 12 = 93/12 = 7.75
```

**Weighted Score: 7.5 → 7.8 (+0.3)**

---

## Fixes Applied

### Fix 1: Pin all 10 floating dependencies
- **File:** `requirements.txt`
- Changed `>=` to `==` for: gunicorn, openai, pdfplumber, Pillow, reportlab, firebase-admin, scikit-learn, pandas, numpy, psutil
- All 53 packages now deterministically pinned
- **Impact:** R3 +0.5 — prevents breaking upgrades

### Fix 2: Enhance /health endpoint
- **File:** `api/main.py`
- Now checks Supabase connectivity via lightweight query
- Returns `{"status": "ok", "database": true}` or `{"status": "degraded", "database": false}`
- **Impact:** R6 +2 — enables real monitoring, catches silent DB outages

### Fix 3: Restrict CORS allow_methods
- **File:** `api/main.py`
- `allow_methods=["*"]` → `allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]`
- **Impact:** R3 +0.5 — reduces attack surface

---

## Remaining Issues (not fixed)

| # | Issue | Risk to Fix | Why Deferred |
|---|-------|-------------|--------------|
| P2-1 | No memory trend monitoring | Low | Add % growth per cycle |
| P3-2 | No liveness vs readiness probe split | Low | K8s-style probes |
| P3-3 | No startup validation for circular imports | Low | Proactive safety |
