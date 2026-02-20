# Module 04: Pending Receipts & Vault — Scoring Report

**Evaluated:** 2026-02-18
**Status:** Non-invasive fixes applied

---

## Files

| File | Role |
|------|------|
| `api/routers/pending_receipts.py` | ~5900 lines — upload, OCR orchestration, state machine, expense creation |
| `api/routers/vault.py` | Vault router — file/folder management, chunked upload |
| `api/services/vault_service.py` | Vault service layer — storage ops, versioning, folder resolution |

---

## Scorecard

| Dimension | Before | After | Delta | Notes |
|-----------|--------|-------|-------|-------|
| R1 Error Handling | 7 | 8 | +1 | 12 bare `except:pass` → `logger.debug()`, `_load_agent_config` now logs |
| R2 Data Integrity | 7 | 8 | +1 | check_receipt_status bug fixed (status_map reset), breadcrumb endpoint |
| R3 Security | 7 | 7 | — | Vault bucket still public (invasive fix) |
| R4 Performance | 6 | 7 | +1 | list_files paginated, soft_delete batch, search paginated, folder tree counts |
| R5 Memory Safety | 7 | 8 | +1 | `del file_content` after OCR phase, unused pdf2image import removed |
| R6 Reliability | 6 | 7 | +1 | HTTPException → ValueError in bg-callable, skipped items logged |
| R7 Code Quality | 5 | 6 | +1 | Bare pass patterns logged, unused import removed |
| R8 Interconnection | 7 | 7 | — | |
| R9 Financial (2x) | 7 | 7 | — | Skipped items now logged (visibility, not yet blocking) |
| R10 Mem Leak (2x) | 7 | 8 | +1 | `del file_content` frees up to 20MB before DB/message phase |

```
Before: (7+7+7+6+7+6+5+7 + 7*2 + 7*2) / 12 = 80/12 = 6.67
Pass 1: (8+7+7+6+8+7+6+7 + 7*2 + 8*2) / 12 = 86/12 = 7.17
Pass 2: (8+8+7+7+8+7+6+7 + 7*2 + 8*2) / 12 = 88/12 = 7.33
```

**Weighted Score: 6.7 → 7.2 → 7.3 (+0.6 total)**

---

## Fixes Applied

### Fix 1: `_load_agent_config` — silent failure now logged
- **File:** `pending_receipts.py` line 112
```python
# Before:
except Exception:
    return {}

# After:
except Exception as e:
    logger.warning("[AgentConfig] Failed to load agent_config: %s", e)
    return {}
```

### Fix 2: Remove unused `pdf2image` import
- **File:** `pending_receipts.py` line 75
- **Impact:** R7, R5 — removes unnecessary module-level import

### Fix 3: `del file_content` after OCR processing
- **File:** `pending_receipts.py` — `_agent_process_receipt_core()` after line ~2131
- **Impact:** R5, R10 — frees up to 20MB before the long DB/message phase
```python
# After last use of file_content (correction pass):
try:
    del file_content
except NameError:
    pass
```

### Fix 4: HTTPException → ValueError in background-callable function
- **File:** `pending_receipts.py` lines 1577, 1584, 2068
- **Impact:** R6 — HTTPException has no meaning when called as background task
```python
# Before:
raise HTTPException(status_code=404, detail="Receipt not found")

# After:
raise ValueError("Receipt not found")
```

### Fix 5: Log skipped items in partial expense creation (3 locations)
- **Files:** `pending_receipts.py` lines ~4700, ~4845, ~5170
- **Impact:** R9, R6 — previously items without account_id were silently skipped
```python
# Now logs when items are skipped:
skipped_items = 0
for item in line_items:
    if not item_account_id:
        skipped_items += 1
        continue
    ...
if skipped_items:
    logger.warning(f"[ReceiptFlow] {skipped_items}/{len(line_items)} items skipped (no account_id)")
```

### Fix 6: Bare `except: pass` → logged (12 locations)
- **Impact:** R1 — previously swallowed all diagnostics
```python
# Before:
except Exception:
    pass

# After:
except Exception as _exc:
    logger.debug("Suppressed: %s", _exc)
```

### Fix 7: Paginated `list_files` (session 2026-02-20)
- **File:** `vault_service.py` — `list_files()`
- **Impact:** R4 — folder navigation now returns `{data, total, pagination}` with limit/offset
- Previously returned ALL items in a folder; now defaults to 60 per page

### Fix 8: Bug fix `check_receipt_status` — status_map reset
- **File:** `vault_service.py` line ~809
- **Impact:** R2 — `status_map = {}` on line 809 was wiping processing/pending statuses from Step 1.5

### Fix 9: Folder tree with item counts
- **File:** `vault_service.py` — `get_folder_tree()`
- **Impact:** R4 — each folder now includes `item_count` (batch-counted in single query)

### Fix 10: Breadcrumb endpoint
- **File:** `vault.py` — `GET /vault/breadcrumb/{folder_id}`
- **Impact:** R4 — returns `[{id, name}, ...]` from root to folder; enables clickable breadcrumb UI

### Fix 11: Batch `soft_delete`
- **File:** `vault_service.py` — `soft_delete()`
- **Impact:** R4 — replaced recursive N+1 delete with BFS collect + single `.in_()` batch UPDATE

### Fix 12: Paginated `search_files`
- **File:** `vault_service.py` — `search_files()`
- **Impact:** R4 — added `offset` parameter; returns `{data, pagination}` instead of bare list

### Fix 13: Batch `create_default_folders`
- **File:** `vault_service.py` — `create_default_folders()`
- **Impact:** R4 — 9 individual INSERTs → single batch INSERT

---

## Remaining Issues (not fixed)

| # | Issue | Risk to Fix | Why Deferred |
|---|-------|-------------|--------------|
| P0-1 | Vault bucket PUBLIC | **High** | Requires signed URL generation for all reads + frontend changes |
| P0-2 | Partial expense → receipt marked "linked" | **Medium** | Needs response contract change (skipped_count) |
| P1-3 | No upload-time duplicate check | **Medium** | Hash exists but not checked before insert |
| P1-4 | Non-atomic vault writes | **High** | Needs transaction/rollback logic |
| P1-6 | No magic byte validation | **Low** | Trusts client MIME type |
| P1-7 | Chunk temp file leak on assembly failure | **Medium** | Needs cleanup in finally block |
| P2-8 | File is ~5900 lines | **Medium** | Major refactor to decompose |
| P2-9 | Expense creation duplicated 8+ times | **Medium** | Extract to shared function |
| P3 | Magic strings for receipt statuses | **Low** | Should be enum |

---

## Architecture Notes

**Receipt Pipeline:**
```
Upload → pending_receipts (status: "ready")
    ↓
OCR Processing (fast-beta → fast → heavy)
    ↓
Categorization (cache → affinity → ML → GPT)
    ↓
Agent flow (check_flow / receipt_flow)
    ↓
Expense creation → expenses_manual_COGS
    ↓
Status: "linked" + trigger daneel auto-auth
```
