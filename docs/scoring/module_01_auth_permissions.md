# Module 01: Authentication & Permissions — Scoring Report

**Evaluated:** 2026-02-18
**Status:** Fixes applied, pending deploy to staging

---

## Files

| File | Role |
|------|------|
| `api/auth.py` | Login, JWT, create_user, `get_current_user` dependency |
| `api/routers/permissions.py` | Role-based permission CRUD, check, batch-update |
| `api/routers/team.py` | Team/user CRUD, role CRUD, meta dropdowns |
| `utils/auth.py` | Centralized `hash_password` / `verify_password` (bcrypt) |

---

## Scorecard

| Dimension | Before | After | Delta | Notes |
|-----------|--------|-------|-------|-------|
| R1 Error Handling | 7 | 7 | — | All Supabase calls wrapped, proper 4xx/5xx |
| R2 Data Integrity | 6 | 6 | — | Pydantic validation, protected roles check |
| R3 Security | 4 | 5 | +1 | Login bug fixed (was blocking users w/o role) |
| R4 Performance | 5 | 8 | +3 | 5 optimizations applied (see below) |
| R5 Memory Safety | 4 | 8 | +4 | Singleton supabase, no duplicate CryptContext |
| R6 Reliability | 5 | 7 | +2 | Login works with or without role assigned |
| R7 Code Quality | 4 | 7 | +3 | Logger, centralized hash, clean imports |
| R8 Interconnection | 3 | 8 | +5 | Shared singleton + utils/auth.py as source |
| R9 Financial (2x) | 7 | 7 | — | Module doesn't handle financial data directly |
| R10 Mem Leak (2x) | 4 | 8 | +4 | Zero duplicate heavy objects |

```
Before: (7+6+4+5+4+5+4+3 + 7*2 + 4*2) / 12 = 60/12 = 5.0
After:  (7+6+5+8+8+7+7+8 + 7*2 + 8*2) / 12 = 86/12 = 7.2
```

**Weighted Score: 5.0 → 7.2 (+2.2)**

---

## Fixes Applied

### Critical Bug Fix
| Issue | File | Impact |
|-------|------|--------|
| JWT generation trapped inside `if role_id:` block | `auth.py` | Users without role got empty 200 response — login broken |

**Before (broken):**
```python
if role_id:
    # ... resolve role name ...
    access_token = make_access_token(...)  # <-- trapped inside if
    return { ... }  # <-- also trapped
# Users without role fall through → return None → empty 200
```

**After (fixed):**
```python
if role_id:
    # ... resolve role name (now via embedded join, no second call) ...
role_name = rols_data.get("rol_name") if rols_data else None
access_token = make_access_token(...)  # <-- always runs
return { ... }  # <-- always runs
```

---

### Memory Safety / Code Quality / Interconnection (R5, R7, R8)

| Fix | File | Before | After |
|-----|------|--------|-------|
| Duplicate Supabase client | `permissions.py` | Own `create_client()` | `from api.supabase_client import supabase` |
| Duplicate CryptContext | `team.py` | Own `CryptContext(bcrypt)` | `from utils.auth import hash_password` |
| print() debug logs | `auth.py` | `print()` | `logging.getLogger(__name__)` |
| Duplicate imports | `auth.py` | datetime, jwt imported twice | Cleaned |

---

### Performance (R4) — 5 Optimizations

#### 1. Login: 2 calls → 1 (embedded join)
```python
# Before: 2 HTTP round-trips
user = supabase.table("users").select("*").eq(...).single().execute()
role = supabase.table("rols").select("rol_name").eq(...).single().execute()

# After: 1 HTTP round-trip (PostgREST join)
user = supabase.table("users").select("*, rols!users_user_rol_fkey(rol_name)").eq(...).single().execute()
role_name = user.get("rols", {}).get("rol_name")
```
**Saving:** ~100ms per login

#### 2. permissions/user: 2 calls → 1 (nested embed)
```python
# Before: query users → then role_permissions (2 calls)
# After: single nested embed users → rols → role_permissions
supabase.table("users").select(
    "user_rol, rols!users_user_rol_fkey(rol_id, role_permissions(id, module_key, ...))"
).eq("user_id", user_id).single().execute()
```
**Saving:** ~100ms per call

#### 3. permissions/check: 2 calls → 1 (same pattern)
```python
# Same nested embed, filters module_key in Python with next()
perm = next((p for p in all_perms if p.get("module_key") == module_key), None)
```
**Saving:** ~100ms per permission check (called frequently on navigation)

#### 4. team/meta: 4 sequential → 4 parallel
```python
# Before: 4 sequential Supabase calls (~400ms total)
# After: asyncio.gather + asyncio.to_thread (~100ms total)
roles_res, sen_res, status_res, dept_res = await asyncio.gather(
    asyncio.to_thread(lambda: supabase.table("rols")...),
    asyncio.to_thread(lambda: supabase.table("users_seniority")...),
    asyncio.to_thread(lambda: supabase.table("users_status")...),
    asyncio.to_thread(lambda: supabase.table("task_departments")...),
)
```
**Saving:** ~300ms per /team/meta load (frontend dropdown init)

#### 5. batch-update: N calls → 1 (array upsert)
```python
# Before: loop with N individual upsert() calls
# After: single upsert() with array of all rows
supabase.table("role_permissions").upsert(upsert_rows, on_conflict="rol_id,module_key").execute()
```
**Saving:** ~(N-1)×100ms. For 30 permission updates: ~2.9 seconds saved.
Also changed `protected_rol_ids` from `list` to `set` for O(1) lookups.

---

## Remaining Issues (not fixed)

| Issue | Dimension | Score Impact | Risk to Fix |
|-------|-----------|-------------|-------------|
| No auth middleware on `/permissions/*`, `/team/*`, `/auth/create_user` | R3 Security | Could reach 8 | Medium — requires testing all frontend calls |
| No rate limiting on `/auth/login` | R3 Security | +0.5 | Low |
| `JWT_SECRET` default is "CHANGE_ME" | R3 Security | — | ENV var override expected, but risky if missed |
| `batch-update` now atomic (all-or-nothing) | R2 Data Integrity | Neutral | Was partial-state before, now atomic (better for permissions) |
| `create_user`/`update_user` in team.py do insert + fetch_by_id (2 calls) | R4 Performance | +0.5 | Low — needs testing if PostgREST supports joins on insert return |
| No pagination on `/team/users` | R4 Performance | — | Not needed until >100 users |

---

## How to Verify After Deploy

1. **Login** — log in with a user that has NO role assigned. Should get a valid JWT and response.
2. **team/meta** — open the team management page. Dropdowns should load noticeably faster.
3. **permissions/check** — browser DevTools Network tab: should show 1 Supabase call instead of 2.
4. **batch-update** — update permissions for a role in the admin panel. Should complete faster.
5. **Memory** — monitor Render dashboard. Baseline should stay lower (no duplicate clients).
