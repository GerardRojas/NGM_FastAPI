-- ============================================================================
-- RESPONSIBILITIES (manual duties) — foundations
-- ----------------------------------------------------------------------------
-- Generalizes the pending-expenses pattern into a full catalog of duties.
-- SYSTEM responsibilities keep living in automation_settings (already wired);
-- this table only holds MANUAL responsibilities an admin creates by hand.
-- The unified API (GET /responsibilities) merges both into one shape.
--
-- A manual responsibility is owned by a whole role (one shared task, every
-- member as manager) or one specific user, and is turned into pipeline tasks
-- by _run_manual_responsibilities() so it surfaces in each owner's "My Work".
--
-- Idempotent. Run on staging first, then prod (Supabase SQL editor). No downtime.
-- ============================================================================

CREATE TABLE IF NOT EXISTS public.responsibilities (
    responsibility_id   uuid PRIMARY KEY DEFAULT gen_random_uuid(),

    -- What the duty is (shown as the task description).
    title               text NOT NULL,
    description         text,

    -- Who owns it. 'role' = one shared task, every member of responsible_role_id
    -- as managers; 'user' = a single owner (responsible_user_id).
    responsible_type    text NOT NULL DEFAULT 'role'
                          CHECK (responsible_type IN ('role', 'user')),
    responsible_role_id uuid,   -- rols.rol_id (no FK on purpose: schema-agnostic)
    responsible_user_id uuid,   -- users.user_id

    -- Task generation defaults.
    department_id       uuid,   -- task_departments.department_id
    priority            integer NOT NULL DEFAULT 3,

    -- Recurrence. 'none' = a single standing task; otherwise re-generated on
    -- the given cadence by the automation runner.
    recurrence          text NOT NULL DEFAULT 'none'
                          CHECK (recurrence IN ('none', 'daily', 'weekly', 'monthly')),
    recurrence_config   jsonb NOT NULL DEFAULT '{}',  -- e.g. {"weekday":1,"day_of_month":1}

    -- Project scope. 'none' = standalone task; 'all_active' = one task per active
    -- project; 'specific' = only project_id.
    project_scope       text NOT NULL DEFAULT 'none'
                          CHECK (project_scope IN ('none', 'all_active', 'specific')),
    project_id          uuid,   -- when project_scope = 'specific'

    is_enabled          boolean NOT NULL DEFAULT true,

    -- Recurrence bookkeeping / idempotency for the runner.
    last_generated_at   timestamptz,
    source_task_id      uuid,   -- the standing task when recurrence = 'none'

    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now(),
    created_by          uuid,
    updated_by          uuid
);

CREATE INDEX IF NOT EXISTS idx_responsibilities_role
    ON public.responsibilities(responsible_role_id);
CREATE INDEX IF NOT EXISTS idx_responsibilities_user
    ON public.responsibilities(responsible_user_id);
CREATE INDEX IF NOT EXISTS idx_responsibilities_enabled
    ON public.responsibilities(is_enabled) WHERE is_enabled = true;

-- VERIFICATION ---------------------------------------------------------------
-- select column_name, data_type from information_schema.columns
--   where table_schema = 'public' and table_name = 'responsibilities'
--   order by ordinal_position;
