# Module 11: Materials Database & Estimator — Scoring Report

**Evaluated:** 2026-02-18
**Status:** Non-invasive fixes applied

---

## Files

| File | Role |
|------|------|
| `api/routers/materials.py` | 376 lines — materials CRUD, categories, bulk create |
| `api/routers/concepts.py` | 665 lines — concept assemblies, material lines, cost calculation |
| `api/routers/estimator.py` | 615 lines — project estimates, templates, snapshots |
| `sql/create_materials_schema.sql` | 254 lines — materials, categories, classes, units |
| `sql/create_concepts_schema.sql` | 230 lines — concepts, concept_materials, cost triggers |

---

## Scorecard

| Dimension | Before | After | Delta | Notes |
|-----------|--------|-------|-------|-------|
| R1 Error Handling | 7 | 8 | +1 | 4 `print()` → logger; 3 bare `except:` → logged |
| R2 Data Integrity | 4 | 5 | +1 | Material delete now checks concept references (HTTP 409) |
| R3 Security | 2 | 2 | — | No auth still missing (invasive) |
| R4 Performance | 5 | 5 | — | |
| R5 Memory Safety | 7 | 7 | — | |
| R6 Reliability | 5 | 5 | — | |
| R7 Code Quality | 6 | 7 | +1 | 0 `print()` remain; logging added to all 3 routers |
| R8 Interconnection | 5 | 5 | — | |
| R9 Financial (2x) | 3 | 3 | — | Float + ignored percentages (invasive) |
| R10 Mem Leak (2x) | 6 | 6 | — | |

```
Before: (7+4+2+5+7+5+6+5 + 3*2 + 6*2) / 12 = 59/12 = 4.92
After:  (8+5+2+5+7+5+7+5 + 3*2 + 6*2) / 12 = 62/12 = 5.17
```

**Weighted Score: 4.9 → 5.2 (+0.3)**

---

## Fixes Applied

### Fix 1: `print()` → `logger` across 3 files
- **concepts.py:** 1 `print()` → `logger.error()`, added `import logging` + `logger`
- **estimator.py:** 3 `print()` → logger, added `import logging` + `logger`
- **materials.py:** Added `import logging` + `logger` (no prints existed)
- **Impact:** R1 +0.5, R7 +1

### Fix 2: 3 bare `except:` → logged in estimator.py
- Bucket existence checks and status checks now log suppressed exceptions
- **Impact:** R1 +0.5

### Fix 3: Referential integrity check on material delete
- **File:** `materials.py` — delete endpoint
- Queries `concept_materials` before delete; returns HTTP 409 if references exist
- **Impact:** R2 +1
```python
refs = supabase.table("concept_materials").select("id", count="exact").eq("material_id", material_id).execute()
if (refs.count or 0) > 0:
    raise HTTPException(status_code=409,
        detail=f"Material is referenced by {refs.count} concept(s). Remove material from concepts before deleting.")
```

---

## Remaining Issues (not fixed — mostly invasive)

| # | Issue | Risk to Fix | Why Deferred |
|---|-------|-------------|--------------|
| P0-1 | **No authentication on ANY endpoint** | Medium | Needs `Depends(get_current_user)` on all routes |
| P0-2 | **overhead/waste percentages ignored** in cost calc | Medium | Needs formula update |
| P1-3 | Float arithmetic in price calculations | High | Requires Decimal pipeline |
| P1-4 | No FK constraint concept_materials → materials | Medium | Schema migration |
| P1-5 | N+1 query in get_concept | Medium | Batch fetch |
| P2-6 | Bulk operations not atomic | Medium | Transaction wrapper |
