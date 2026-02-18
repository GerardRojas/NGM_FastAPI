# Module 07: Agent Brain & Messaging — Scoring Report

**Evaluated:** 2026-02-18
**Status:** Non-invasive fixes applied

---

## Files

| File | Role |
|------|------|
| `api/services/agent_brain.py` | 2081 lines — central dispatcher, GPT routing, built-in handlers, cooldown |
| `api/routers/messages.py` | 1307 lines — chat CRUD, channels, reactions, threads, push notifications |
| `api/services/agent_personas.py` | 140 lines — persona definitions, bot detection, cross-agent routing |
| `api/services/agent_registry.py` | 237 lines — function catalog for Andrew + Daneel agents |

---

## Scorecard

| Dimension | Before | After | Delta | Notes |
|-----------|--------|-------|-------|-------|
| R1 Error Handling | 7 | 8 | +1 | 12 bare `except:` in agent_brain now log via `logger.debug()` |
| R2 Data Integrity | 7 | 7 | — | |
| R3 Security | 7 | 7 | — | |
| R4 Performance | 7 | 7 | — | |
| R5 Memory Safety | 7 | 7 | — | |
| R6 Reliability | 7 | 7 | — | |
| R7 Code Quality | 6 | 7 | +1 | 10 `print()` → `logger` in messages.py |
| R8 Interconnection | 8 | 8 | — | |
| R9 Financial (2x) | 7 | 7 | — | |
| R10 Mem Leak (2x) | 6 | 8 | +2 | `_cooldowns` now has hard cap (200) + eviction |

```
Before: (7+7+7+7+7+7+6+8 + 7*2 + 6*2) / 12 = 82/12 = 6.83
After:  (8+7+7+7+7+7+7+8 + 7*2 + 8*2) / 12 = 88/12 = 7.33
```

**Weighted Score: 6.8 → 7.3 (+0.5)**

---

## Fixes Applied

### Fix 1: `_cooldowns` hard cap + eviction
- **File:** `agent_brain.py` — `_check_cooldown()` function
- **Impact:** R10 +2 — prevents unbounded growth under sustained load
```python
# Added after stale purge:
if len(_cooldowns) > _COOLDOWN_MAX_SIZE:
    sorted_keys = sorted(_cooldowns, key=_cooldowns.get)
    for k in sorted_keys[:len(sorted_keys) // 2]:
        del _cooldowns[k]
```

### Fix 2: 10 `print()` → `logger` in `messages.py`
- 3 → `logger.error()`, 5 → `logger.warning()`, 2 → `logger.info()`
- **Impact:** R7 +1 — all message lifecycle events visible in centralized logs

### Fix 3: 12 bare `except Exception:` → `logger.debug()` in `agent_brain.py`
- All 12 locations now capture exception and log at DEBUG level
- **Impact:** R1 +1 — suppressed errors visible for diagnostics

---

## Remaining Issues (not fixed)

| # | Issue | Risk to Fix | Why Deferred |
|---|-------|-------------|--------------|
| P2-1 | Cooldown race condition (thread safety) | **Low** | Less critical in async context |
| P2-2 | Unread cache cleanup not atomic | **Low** | Single-worker deployment |
| P3-3 | Hardcoded `min_confidence = 0.9` | **Low** | Should read from agent_config |
