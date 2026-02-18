# NGM Backend — Technical Scoring Framework

## How to Use This Document

This framework evaluates the backend in **independent modules** so each can be
scored in a single Claude conversation without exceeding the context window.

### Workflow
1. Open a new Claude conversation
2. Paste the **Scoring Rubric** (Section 1) + **one Module Card** (Section 3)
3. Ask Claude to read the listed files and score that module
4. Record scores in the **Scorecard** (Section 4)
5. Repeat for each module

### Scoring Scale
| Score | Label | Meaning |
|-------|-------|---------|
| 9-10 | Production-ready | Robust, well-structured, handles edge cases |
| 7-8 | Solid | Works well, minor improvements possible |
| 5-6 | Functional | Works but has notable gaps |
| 3-4 | Fragile | Works in happy path, risky under stress |
| 1-2 | Critical | Broken or dangerous patterns |

---

## Section 1: Scoring Rubric (paste this in every evaluation)

Evaluate each module across these 10 dimensions.
**R9 and R10 are weighted 2x** in the final score — they are the highest
priority for this application (financial data + production stability).

### R1 — Error Handling (0-10)
- Are all Supabase/external calls wrapped in try/except?
- Do errors return meaningful HTTP status codes and messages?
- Are background task failures logged (not silently swallowed)?
- Is there distinction between user errors (4xx) and server errors (5xx)?

### R2 — Data Integrity (0-10)
- Are inputs validated before hitting the database?
- Are UUIDs validated? Are required fields enforced?
- Are there race conditions on concurrent writes?
- Is there audit trail for critical mutations (expenses, auth)?

### R3 — Security (0-10)
- Do all endpoints require `get_current_user` authentication?
- Is there authorization (role checks) beyond authentication?
- Are SQL injection / injection attacks prevented (parameterized queries)?
- Are secrets, tokens, or PII exposed in logs or responses?

### R4 — Performance (0-10)
- Are Supabase queries paginated (>1000 rows)?
- Are N+1 query patterns avoided?
- Is there unnecessary data fetching (SELECT * when only 2 fields needed)?
- Are expensive operations (GPT, OCR) properly async / backgrounded?

### R5 — Memory Safety (0-10)
- Are large objects (images, PDFs, base64) released after use?
- Are module-level caches bounded (max size + TTL)?
- Are clients (OpenAI, httpx) singletons or leaked per-call?
- Do background tasks hold large closures?

### R6 — Reliability (0-10)
- Do background tasks have timeout/retry logic?
- Can the module recover from partial failures?
- Are external API failures handled gracefully (timeouts, retries)?
- Is there dead-letter / fallback for failed async operations?

### R7 — Code Quality (0-10)
- Is the code readable and consistently structured?
- Are functions focused (single responsibility)?
- Is there duplication that should be extracted?
- Are magic strings/numbers avoided (constants, enums)?

### R8 — Interconnection Health (0-10)
- Are dependencies between modules explicit (imports, not implicit)?
- Does this module depend on too many other modules (high coupling)?
- Could a change in this module break other modules?
- Are shared data structures (tables, caches) well-documented?

### R9 — Financial Data Accuracy (0-10) [WEIGHT: 2x]
**This application manages real financial data (expenses, budgets, invoices).
Errors here mean wrong reports, lost money, or compliance issues.**
- Are monetary amounts preserved exactly (no float rounding issues)?
- Do queries that aggregate amounts produce the same result as the raw data?
- Are filters consistent across all views of the same data (table vs report)?
- Can an expense amount be silently lost (Amount vs amount field casing)?
- Are batch operations atomic (all succeed or all fail, no partial state)?
- Is there a single source of truth for each financial number?
- Do authorization status changes preserve the original amount?
- Are deleted/soft-deleted expenses properly excluded from all financial totals?
- Could a background task failure leave an expense in an inconsistent state
  (e.g., authorized but not counted in budget, or counted but not authorized)?
- Are date ranges applied consistently (same field: TxnDate everywhere)?

### R10 — Memory Leak Resistance (0-10) [WEIGHT: 2x]
**The server must stay responsive for days without redeployment.
Memory leaks cause freezing, data loss in background tasks, and downtime.**
- Are all client objects (OpenAI, httpx, Supabase) created as singletons?
- Are per-request objects (BytesIO, PIL Image, base64 strings) explicitly closed/deleted?
- Are module-level dicts bounded by both max size AND TTL?
- Do background tasks release their arguments after completion?
- Are closures capturing large objects (file_content, base64_images)?
- Could concurrent requests accumulate memory faster than GC releases it?
- Are there objects with circular references that prevent reference counting?
- Is gc.collect() being called periodically and is the task reference stored?
- After processing a large file (5-page PDF at 250 DPI), does memory return
  to baseline within 60 seconds?
- Over 5000 requests (one max-requests cycle), does baseline memory grow
  by more than 20% from startup?

### Score Calculation
```
weighted_score = (R1 + R2 + R3 + R4 + R5 + R6 + R7 + R8 + R9*2 + R10*2) / 12
```

---

## Section 2: Architecture Overview (reference only)

### Stack
- **Framework**: FastAPI 0.124.0
- **Server**: Gunicorn + UvicornWorker (1 worker, max-requests 5000)
- **Database**: Supabase (PostgreSQL via PostgREST) — 77 tables
- **External APIs**: OpenAI (GPT-5-mini, GPT-5.2, Vision, Assistants), Firebase FCM, QuickBooks Online
- **Deployment**: Render.com, Docker, 2GB RAM

### Layer Map
```
HTTP Request
    |
    v
[api/auth.py] ── authentication ──> get_current_user dependency
    |
    v
[api/routers/*] ── 38 routers, ~260 endpoints
    |
    v
[api/services/*] ── 15 service files (business logic)
    |
    v
[services/*] ── 22 handler/utility files (AI agents, OCR, QBO)
    |
    v
[api/supabase_client.py] ── singleton Supabase client
    |
    v
[Supabase PostgreSQL] ── 77 tables, 66 SQL migrations
```

### Background Task Flow
```
HTTP Response sent to user
    |
    v
BackgroundTasks / asyncio.create_task
    |
    ├── expense_change_log insert
    ├── expense_status_log insert
    ├── daneel auto-auth check
    ├── budget monitor check
    ├── agent brain (@mention processing)
    ├── push notifications (Firebase)
    └── vault batch OCR processing
```

### Known Shared State (in-memory, single worker)
| Cache | Location | TTL | Max |
|-------|----------|-----|-----|
| `_cooldowns` | agent_brain.py | 60s | 200 |
| `_user_name_cache` | agent_brain.py | 1h | 200 |
| `_unread_cache` | messages.py | 30s | 100 |
| `_thread_cache` | assistants.py | 2h | 150 |
| `_assistant_cache` | assistants.py | permanent | unbounded |
| `_ml_service` | categorization_ml.py | 6h retrain | 1 instance |
| `_lookup_cache` | receipt_scanner.py | 5min | 1 entry |
| `_openai_client` | assistants.py | permanent | 1 instance |
| `_sync_client` | gpt_client.py | permanent | 1 instance |
| `_async_client` | gpt_client.py | permanent | 1 instance |

---

## Section 3: Module Cards

Each card lists the files to read and specific questions to answer.
Paste the Scoring Rubric (Section 1) + ONE card per Claude session.

---

### MODULE 01: Authentication & Permissions

**Files to read:**
- `api/auth.py`
- `api/routers/permissions.py`
- `api/routers/team.py`
- `utils/auth.py`

**Context:** Handles user creation, login (JWT), role management, and
module-level permission checks. `get_current_user` is used as a FastAPI
Depends() across all protected endpoints.

**Specific questions:**
1. Is JWT validation secure (expiry, signing algorithm)?
2. Are passwords properly hashed (bcrypt, adequate rounds)?
3. Does permission checking happen at the endpoint level or only client-side?
4. Can a user escalate privileges by modifying their own role?
5. Are there any endpoints missing authentication?

**Interconnections to evaluate:**
- Every router depends on `get_current_user` from auth.py
- permissions.py reads `role_permissions` and `rols` tables
- team.py manages `users` and `rols` (can modify who has what role)

---

### MODULE 02: Expenses (Core CRUD + Status Machine)

**Files to read:**
- `api/routers/expenses.py` (lines 1-500 for CRUD, 750-1000 for status)
- `sql/expense_status_log.sql`

**Context:** The most critical router. Handles expense creation (single +
batch), updates, soft-delete, authorization workflow, and audit logging.
Triggers background tasks for auto-auth and budget monitoring.

**Specific questions:**
1. Is the status machine (pending -> auth -> review) enforced server-side?
2. Can batch operations leave partial state on failure?
3. Are change_log and status_log inserts guaranteed or best-effort?
4. Is `updated_by` always set to prevent trigger crashes?
5. Is the Supabase 1000-row limit handled in all query paths?

**Interconnections to evaluate:**
- Triggers `daneel_auto_auth.trigger_auto_auth_check` (background)
- Triggers `budget_monitor.trigger_project_budget_check` (background)
- Writes to `expense_change_log` and `expense_status_log` (background)
- Read by reporting (bva_handler, pnl_handler) — filter mismatch risk
- Read by reconciliations router

---

### MODULE 03: Expenses (Categorization + OCR Pipeline)

**Files to read:**
- `api/routers/expenses.py` (lines 1400-end, auto-categorize section)
- `services/receipt_scanner.py` (lines 1-200 for OCR, 1200-end for categorization)
- `services/receipt_regex.py` (first 100 lines for overview)
- `api/services/categorization_ml.py` (first 200 lines)

**Context:** Multi-tier categorization: cache -> vendor affinity -> ML ->
GPT-mini -> GPT-heavy. Receipt scanning via pdfplumber (text) or GPT Vision
(image). ML model trains on historical corrections.

**Specific questions:**
1. Is the escalation chain (cache -> ML -> GPT) correctly implemented?
2. Are confidence scores consistent across tiers?
3. Is the ML model retrain frequency appropriate (6h)?
4. Are PIL images and BytesIO buffers properly released?
5. Is there a cost control mechanism for GPT Vision calls?

**Interconnections to evaluate:**
- receipt_scanner.py is called by pending_receipts.py and agent_brain.py
- categorization_ml.py trains on `categorization_corrections` table
- categorization_cache is shared between receipt_scanner and expenses router
- ocr_metrics logs to `ocr_metrics` table

---

### MODULE 04: Pending Receipts & Vault

**Files to read:**
- `api/routers/pending_receipts.py` (first 400 lines + vault batch section)
- `api/routers/vault.py`
- `api/services/vault_service.py`

**Context:** Receipt upload -> OCR processing -> expense creation pipeline.
Vault provides file storage with versioning, folder structure per project,
and chunked upload for large files.

**Specific questions:**
1. Can a receipt be processed twice (duplicate protection)?
2. Is the vault batch processing idempotent?
3. Are file uploads validated (size, type, content)?
4. Is chunked upload assembly atomic?
5. Are storage bucket permissions properly scoped?

**Interconnections to evaluate:**
- pending_receipts calls receipt_scanner for OCR
- pending_receipts creates expenses (writes to expenses_manual_COGS)
- pending_receipts triggers daneel auto-auth (background)
- vault_service is used by reporting (PDF upload) and pending_receipts
- vault.py manages vault_files and vault_file_versions tables

---

### MODULE 05: AI Agents — Daneel (Auto-Auth)

**Files to read:**
- `api/routers/daneel_auto_auth.py`
- `api/services/daneel_auto_auth.py` (first 300 lines + trigger functions)
- `api/services/daneel_smart_layer.py` (first 150 lines)
- `api/helpers/daneel_messenger.py`

**Context:** Automated expense authorization engine. Compares receipt data
against bill amounts using Levenshtein distance and GPT Vision. Posts
decisions to chat via daneel_messenger.

**Specific questions:**
1. Can Daneel authorize an expense that should be rejected?
2. Is the matching threshold (Levenshtein) appropriate?
3. Are GPT Vision costs controlled (when is vision used vs skipped)?
4. Is there a human override mechanism?
5. What happens when Daneel and a human authorize simultaneously?

**Interconnections to evaluate:**
- Triggered by expenses.py and pending_receipts.py (background task)
- Writes to `expenses_manual_COGS` (auth_status, status)
- Triggers `budget_monitor.trigger_project_budget_check` after auth
- Posts messages via daneel_messenger -> messages table
- Config stored in `agent_config` and `daneel_*` tables

---

### MODULE 06: AI Agents — Andrew (Mismatch) & Arturito (Chat)

**Files to read:**
- `api/routers/andrew_mismatch.py`
- `api/services/andrew_mismatch_protocol.py`
- `api/services/andrew_smart_layer.py`
- `api/routers/arturito.py`
- `services/arturito/assistants.py`
- `services/arturito/router.py`

**Context:** Andrew detects bill/receipt mismatches via Vision OCR.
Arturito is the general-purpose chat bot using OpenAI Assistants API
with intent detection and handler routing.

**Specific questions:**
1. Is Andrew's mismatch detection accurate (false positive rate)?
2. Can Andrew's corrections corrupt expense data?
3. Is Arturito's thread cache consistent (singleton client fix)?
4. Is the intent detection reliable (NLU + GPT fallback)?
5. Are bot permissions properly enforced (role-based)?

**Interconnections to evaluate:**
- Andrew modifies `expenses_manual_COGS` directly
- Andrew downloads files via httpx (receipt URLs) — timeout handling?
- Arturito uses OpenAI Assistants API (thread management)
- Both agents post to messages table via helper messengers
- agent_brain.py routes @mentions to the correct agent

---

### MODULE 07: Agent Brain & Messaging

**Files to read:**
- `api/services/agent_brain.py`
- `api/services/agent_registry.py`
- `api/services/agent_personas.py`
- `api/routers/messages.py`
- `api/services/firebase_notifications.py`

**Context:** The agent brain is the central dispatcher for @mentions in
chat. It builds context (project, user, history), calls GPT to decide
action (function_call / free_chat / cross_agent), then executes.
Messages router handles all chat CRUD + push notifications.

**Specific questions:**
1. Can the agent brain enter an infinite loop (cross-agent delegation)?
2. Is the cooldown system effective (5s per user/agent)?
3. Are push notifications sent for the right events?
4. Is message search performant (text search on large tables)?
5. Are soft-deleted messages properly excluded from all queries?

**Interconnections to evaluate:**
- agent_brain imports from agent_registry (function catalog)
- agent_brain triggers receipt_scanner, vault operations, expense queries
- messages.py triggers agent_brain and firebase_notifications (background)
- _unread_cache shared only within single worker (OK with 1 worker)
- _cooldowns prevent duplicate AI triggers but are in-memory only

---

### MODULE 08: QuickBooks Online Integration

**Files to read:**
- `api/routers/qbo.py`
- `services/qbo_service.py`
- `sql/create_expenses_qbo_import.sql`
- `sql/migrate_qbo_to_manual.sql`

**Context:** Full OAuth 2.0 integration with QBO. Syncs expenses, bills,
accounts, budgets, vendors. Migration pipeline converts QBO expenses to
manual expenses for tracking in NGM.

**Specific questions:**
1. Is the OAuth token refresh robust (handles expired tokens)?
2. Is the QBO sync idempotent (can be re-run safely)?
3. Does the migration pipeline handle partial failures?
4. Are QBO rate limits respected?
5. Is financial data (amounts, dates) accurately preserved during migration?

**Interconnections to evaluate:**
- QBO syncs data into staging tables (`expenses_qbo_import`, `expenses_qbo_cogs`)
- Migration moves data from QBO tables to `expenses_manual_COGS`
- Budget sync feeds `budgets_qbo` (used by BVA reports)
- Account sync feeds `accounts` (used everywhere)
- Vendor sync feeds `Vendors` table

---

### MODULE 09: Budget Monitoring & Reporting

**Files to read:**
- `api/routers/budget_alerts.py`
- `api/services/budget_monitor.py`
- `api/routers/reporting.py`
- `services/arturito/handlers/bva_handler.py`
- `services/arturito/handlers/pnl_handler.py`

**Context:** Budget vs Actuals monitoring with configurable thresholds,
alert recipients, and push notifications. Reporting generates P&L COGS
PDFs via ReportLab and uploads to Vault.

**Specific questions:**
1. Are budget alerts deduplicated (same alert not sent twice)?
2. Is the BVA calculation correct (budget - actual = balance)?
3. Does the report match what the expenses table shows?
4. Is the date filter working correctly (TxnDate field)?
5. Are all authorized expenses included (pagination for >1000)?

**Interconnections to evaluate:**
- budget_monitor reads `expenses` table (different from `expenses_manual_COGS`!)
- bva_handler reads `expenses_manual_COGS` + `budgets_qbo` + `accounts`
- reporting.py reuses fetch_expenses from bva_handler
- PDF upload uses vault_service.save_to_project_folder
- Triggered as background task after expense authorization

---

### MODULE 10: Pipeline & Task Management

**Files to read:**
- `api/routers/pipeline.py` (first 300 lines for CRUD, then automations)
- `sql/task_automations.sql`
- `sql/task_dependencies.sql`
- `sql/workload_scheduling.sql`

**Context:** Project task management with dependencies, automations
(auto-assign, auto-status), workload scheduling, and review workflows.

**Specific questions:**
1. Can task automations create circular dependencies?
2. Is workload scheduling respecting capacity limits?
3. Are task status transitions validated server-side?
4. Can concurrent updates cause inconsistent task state?
5. Is the workflow log complete (audit trail for all changes)?

**Interconnections to evaluate:**
- Pipeline reads `projects`, `users`, `task_departments`
- Task automations write to `tasks` based on trigger rules
- Workflow log provides audit trail for task changes
- User capacity settings affect workload distribution

---

### MODULE 11: Materials Database & Estimator

**Files to read:**
- `api/routers/materials.py`
- `api/routers/concepts.py`
- `api/routers/estimator.py`
- `sql/create_materials_schema.sql`
- `sql/create_concepts_schema.sql`

**Context:** Construction materials catalog with categories, classes, units.
Concepts group materials into reusable assemblies. Estimator creates
project cost estimates using concepts.

**Specific questions:**
1. Are material prices consistent across all references?
2. Can deleting a material break existing concepts?
3. Is the estimator calculation correct (quantity * price + percentages)?
4. Are bulk operations atomic?
5. Is the category tree properly maintained (parent-child integrity)?

**Interconnections to evaluate:**
- concepts reference materials via `concept_materials` join table
- estimator reads concepts for cost calculations
- percentage_items add overhead costs to estimates
- materials use `units` table for measurement units

---

### MODULE 12: Infrastructure & Cross-Cutting

**Files to read:**
- `api/main.py`
- `api/supabase_client.py`
- `api/services/gpt_client.py`
- `Dockerfile`
- `render.yaml`
- `requirements.txt`

**Context:** Application bootstrap, middleware, health checks, memory
management, deployment configuration.

**Specific questions:**
1. Is the startup sequence correct (no circular imports)?
2. Is the memory management loop effective?
3. Are CORS origins properly restricted?
4. Is the health check meaningful (checks DB connectivity)?
5. Are dependencies pinned to prevent breaking upgrades?

**Interconnections to evaluate:**
- main.py imports ALL routers (circular import risk)
- supabase_client.py is imported by every module
- gpt_client.py is the central OpenAI gateway
- _purge_stale_caches imports from multiple modules
- Dockerfile/render.yaml define runtime behavior

---

## Section 4: Consolidated Scorecard

After evaluating all modules, fill in this table:

| # | Module | R1 | R2 | R3 | R4 | R5 | R6 | R7 | R8 | R9(2x) | R10(2x) | Weighted | Critical Issues |
|---|--------|----|----|----|----|----|----|----|----|--------|---------|----------|-----------------|
| 01 | Auth & Permissions | | | | | | | | | | | | |
| 02 | Expenses CRUD | | | | | | | | | | | | |
| 03 | Categorization & OCR | | | | | | | | | | | | |
| 04 | Pending Receipts & Vault | | | | | | | | | | | | |
| 05 | Daneel Auto-Auth | | | | | | | | | | | | |
| 06 | Andrew & Arturito | | | | | | | | | | | | |
| 07 | Agent Brain & Messaging | | | | | | | | | | | | |
| 08 | QBO Integration | | | | | | | | | | | | |
| 09 | Budget & Reporting | | | | | | | | | | | | |
| 10 | Pipeline & Tasks | | | | | | | | | | | | |
| 11 | Materials & Estimator | | | | | | | | | | | | |
| 12 | Infrastructure | | | | | | | | | | | | |
| | **OVERALL** | | | | | | | | | | | | |

### Dimension Averages (system-wide health)
| Dimension | Avg Score | Weight | Status |
|-----------|-----------|--------|--------|
| R1 Error Handling | | 1x | |
| R2 Data Integrity | | 1x | |
| R3 Security | | 1x | |
| R4 Performance | | 1x | |
| R5 Memory Safety | | 1x | |
| R6 Reliability | | 1x | |
| R7 Code Quality | | 1x | |
| R8 Interconnection | | 1x | |
| **R9 Financial Accuracy** | | **2x** | |
| **R10 Memory Leak Resistance** | | **2x** | |

### Priority Matrix
| Priority | Module | Issue | Impact |
|----------|--------|-------|--------|
| P0 — Fix now | | | |
| P1 — Fix this sprint | | | |
| P2 — Plan for next sprint | | | |
| P3 — Tech debt backlog | | | |

---

## Section 5: Dependency Map (for interconnection scoring)

```
expenses.py ─────────┬──> daneel_auto_auth.py ──> budget_monitor.py
                     ├──> expense_change_log (table)
                     ├──> expense_status_log (table)
                     └──> receipt_scanner.py ──> gpt_client.py
                                             ──> categorization_ml.py

pending_receipts.py ─┬──> receipt_scanner.py
                     ├──> vault_service.py
                     ├──> daneel_auto_auth.py
                     └──> andrew_smart_layer.py

messages.py ─────────┬──> agent_brain.py ──> agent_registry.py
                     ├──> firebase_notifications.py
                     └──> _unread_cache (in-memory)

agent_brain.py ──────┬──> gpt_client.py
                     ├──> receipt_scanner.py (via function registry)
                     ├──> vault operations (via function registry)
                     └──> expense queries (via function registry)

arturito.py ─────────┬──> assistants.py ──> OpenAI Assistants API
                     ├──> nlu.py ──> gpt_client.py (fallback)
                     └──> router.py ──> handlers/*.py

qbo.py ──────────────┬──> qbo_service.py ──> Intuit API
                     ├──> expenses_qbo_import (staging table)
                     └──> migrate_qbo_to_manual (stored procedure)

reporting.py ────────┬──> bva_handler.py ──> expenses_manual_COGS
                     ├──> pnl_handler.py     budgets_qbo
                     └──> vault_service.py   accounts

budget_alerts.py ────┬──> budget_monitor.py ──> expenses (!)
                     └──> firebase_notifications.py
```

### Known Table Inconsistency
`budget_monitor.py` queries table `expenses` while all other modules
query `expenses_manual_COGS`. This may produce different totals.
