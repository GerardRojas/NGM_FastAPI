# Duties Playbook — adding a connected system duty

A **duty** (a.k.a. system responsibility / automation) turns some condition in the
data into **one idempotent task per project**, assigned to the role or user
configured in `automation_settings`. The task shows up in the **Responsibilities**
catalog (Roles Management) and in each owner's **My Work**.

This doc is the step-by-step for adding a NEW duty that is wired exactly like the
ones we already ship (`pending_expenses_auth`, `pending_expenses_categorize`,
`pending_health_check`).

> Code lives in [`api/routers/pipeline.py`](../api/routers/pipeline.py) under the
> "DUTY ENGINE" section. The generic machinery (settings lookup, assignee
> resolution, department fallback, idempotent upsert, obsolete cleanup) is written
> **once** in `run_duty()`. A new duty is a small `collect()` + a `DutySpec`.

---

## The model

```
DutySpec            # declarative definition (what makes the duty "connected")
  ├─ automation_type      unique key. Matches automation_settings.automation_type
  │                       and tasks.automation_type. Lowercase snake_case.
  ├─ collect(ctx)         THE ONLY per-duty logic. Returns {project_id: DutyTask}.
  ├─ resolve_managers     optional. If set -> role-managed shared task (managers_ids[]),
  │                       refreshed on update. If None -> single default_manager_id.
  ├─ department_hint      optional. Name fragment for the fallback department when
  │                       automation_settings has no default_department_id.
  └─ cleanup_obsolete     delete automated tasks for projects collect() no longer returns.

DutyContext         # passed to collect()
  ├─ settings             the automation_settings row (dict)
  └─ existing_tasks       {project_id: existing automated task row} (has created_at)

DutyTask            # what collect() produces per project
  ├─ description          task_description text
  ├─ notes                task_notes text (convention: f"{AUTOMATION_MARKER}:<type> | ...")
  └─ metadata             automation_metadata JSON (counts, reasons, etc.)
```

`run_duty(spec)` does everything else: reads settings, resolves the responsible
manager(s), resolves the department, upserts one task per project keyed by
`automation_type` + `project_id`, and (optionally) removes obsolete tasks.

**Enabled gate:** `run_duty()` does NOT check `is_enabled` (so `/automations/run`
can force a run). The enabled flag is honored by `trigger_duty()` (event path) and
by whatever the scheduler chooses to run.

---

## Recipe — add a duty in 3 steps

### 1. Write `collect()` + register a `DutySpec`

In `pipeline.py`, add a collect function. It only has to answer: *which projects
need a task right now, and what does each task say?*

```python
def _collect_overdue_tasks(ctx: DutyContext) -> Dict[Any, DutyTask]:
    # ... query your source, group by project_id ...
    out: Dict[Any, DutyTask] = {}
    for project_id, data in by_project.items():
        out[project_id] = DutyTask(
            description=f"...{project_name}",
            notes=f"{AUTOMATION_MARKER}:overdue_tasks | {data['count']} ...",
            metadata={"count": data["count"]},
        )
    return out
```

Then add it to `DUTY_REGISTRY`:

```python
"overdue_tasks": DutySpec(
    automation_type="overdue_tasks",
    cleanup_obsolete=True,
    collect=_collect_overdue_tasks,
    # resolve_managers=_resolve_pending_expense_managers,  # only if role-managed
    # department_hint="coordination",                       # only if you want a fallback dept
),
```

Registering it auto-wires the duty into `POST /pipeline/automations/run` and
`trigger_duty()` — no dispatch edits needed.

**Pick the assignee model:**
- *Role-managed shared task* (like `pending_expenses_auth`): pass
  `resolve_managers=_resolve_pending_expense_managers`. The task gets every member
  of the configured role in `managers_ids[]` and is refreshed on update.
  > Note: that resolver's name-based fallback is Accounting-Manager specific. For a
  > role-managed duty in another domain, write a dedicated resolver.
- *Single owner* (like categorize / health check): leave `resolve_managers` unset;
  the task uses `default_manager_id`.

### 2. Seed one `automation_settings` row

Create `sql/seed_duty_<type>.sql` (idempotent) so the duty appears in the
Responsibilities catalog with editable assignment:

```sql
INSERT INTO automation_settings (automation_type, display_name, is_enabled, default_priority, config)
VALUES ('overdue_tasks', 'Overdue Tasks', false, 3,
        '{"description": "Alerts for tasks past their due date", "department_hint": "coordination"}')
ON CONFLICT (automation_type) DO NOTHING;
```

Run it on **staging** Supabase first, then prod. The duty starts **disabled**
(`is_enabled=false`); an admin enables and assigns it from Roles Management →
Responsibilities.

### 3. (Optional) Wire an event trigger

If the source changes on a known write path and you want near-real-time tasks
(instead of only the scheduler cadence), fire-and-forget from that path:

```python
from api.routers.pipeline import trigger_duty
background_tasks.add_task(trigger_duty, "overdue_tasks")
```

`trigger_duty` no-ops when the duty is disabled and never raises, so it's safe in
any write path. (See `expenses.py` calling `trigger_pending_expenses_automation`,
which is now a thin wrapper over `trigger_duty("pending_expenses_auth")`.)

---

## How it runs in production

- **Scheduler:** an external scheduler calls `POST /pipeline/automations/run` with
  `{"automations": ["pending_expenses_auth", ...]}`. Add your new id to that list
  (or see the Phase-C idea below to run "all enabled" so the scheduler never needs
  per-duty edits).
- **Event:** the optional `trigger_duty()` call above.
- **Manual:** the Responsibilities panel "Run now" button (manual responsibilities)
  and `/automations/run` for system duties.

## Frontend — nothing to do

The unified catalog (`GET /responsibilities`) reads `automation_settings`, so a
seeded duty shows up automatically as a `kind:system` row with editable assignment
(role or user) and an enabled toggle. The role detail → Responsibilities tab lists
the ones owned by that role. No UI change needed to add a duty.

---

## Gotchas / invariants

- **`automation_type` is the join key** across `automation_settings`, `tasks`, and
  `automation_owner_overrides`. Keep it stable once shipped.
- **Idempotency** is by `automation_type` + `project_id` + `is_automated=true`.
  Re-running updates in place; it does not duplicate.
- **Empty result never deletes.** If `collect()` returns `{}`, `run_duty()` returns
  early without touching existing tasks (guards against a transient empty read
  wiping the cluster). Cleanup only runs when there's a non-empty result and a
  project dropped out of it.
- **Per-project overrides** (`automation_owner_overrides`) still apply for free —
  the engine checks them per project.

## Phase-C ideas (not built yet)

- `/automations/run` with no ids → run every **enabled** duty in `DUTY_REGISTRY`,
  so the scheduler needs zero changes per new duty.
- A reconciler that upserts a missing `automation_settings` row for any registry
  duty on boot — makes `DUTY_REGISTRY` the single source of truth and drops step 2.
