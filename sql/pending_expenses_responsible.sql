-- ============================================================================
-- PENDING EXPENSES -> RESPONSIBLE ROLE/USER (config foundations)
-- ----------------------------------------------------------------------------
-- Lets an admin choose WHO owns pending-expense tasks: a whole role (default
-- "Accounting Manager") or one specific user. The pending_expenses_auth
-- automation reads these columns to decide the task's managers.
--
-- Idempotent. Run on staging first, then prod (Supabase SQL editor). No downtime.
-- ============================================================================

-- 1. How the responsible party is chosen for the pending-expenses automation.
--    'role' = assign the task to every user in responsible_role_id (one task per
--    project, all of them as managers). 'user' = assign to default_manager_id.
ALTER TABLE public.automation_settings
  ADD COLUMN IF NOT EXISTS responsible_type text NOT NULL DEFAULT 'role';

-- 2. Which role owns the tasks when responsible_type = 'role'. NULL keeps the
--    legacy name-based fallback (Accounting Manager -> CEO/COO) in the backend.
--    Holds a rols.rol_id (uuid). No FK declared on purpose so this stays a
--    safe, schema-agnostic ALTER; the backend validates the role on write.
--    (Drop-if-exists guards against a prior partial run that created it as bigint.)
DO $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM information_schema.columns
     WHERE table_schema = 'public'
       AND table_name = 'automation_settings'
       AND column_name = 'responsible_role_id'
       AND data_type <> 'uuid'
  ) THEN
    ALTER TABLE public.automation_settings DROP COLUMN responsible_role_id;
  END IF;
END $$;

ALTER TABLE public.automation_settings
  ADD COLUMN IF NOT EXISTS responsible_role_id uuid;

-- 3. Guard the allowed values.
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'check_automation_responsible_type'
  ) THEN
    ALTER TABLE public.automation_settings
      ADD CONSTRAINT check_automation_responsible_type
      CHECK (responsible_type IN ('role', 'user'));
  END IF;
END $$;

-- 4. Default the pending_expenses_auth row to the Accounting Manager role (if a
--    role by that name exists). Safe no-op if the role or row is missing.
UPDATE public.automation_settings s
   SET responsible_role_id = r.rol_id,
       responsible_type = 'role'
  FROM public.rols r
 WHERE s.automation_type = 'pending_expenses_auth'
   AND s.responsible_role_id IS NULL
   AND r.rol_name ILIKE '%accounting manager%';

-- VERIFICATION ---------------------------------------------------------------
-- Columns present:
-- select column_name from information_schema.columns
--   where table_name = 'automation_settings'
--     and column_name in ('responsible_type', 'responsible_role_id');
--
-- Current pending-expenses config:
-- select automation_type, is_enabled, responsible_type, responsible_role_id,
--        default_manager_id
--   from public.automation_settings where automation_type = 'pending_expenses_auth';
