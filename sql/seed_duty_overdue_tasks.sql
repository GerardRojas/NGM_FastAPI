-- =============================================================================
-- Seed the "Overdue Tasks" connected duty.
-- Run in the Supabase SQL editor (STAGING first, then PROD). IDEMPOTENT.
-- Path: C:\Users\germa\Desktop\NGM_API\sql\seed_duty_overdue_tasks.sql
--
-- What: registers the overdue_tasks duty in automation_settings so it shows up in
-- the Responsibilities catalog (Roles Management) with an editable assignee and an
-- enabled toggle. The backend logic lives in api/routers/pipeline.py
-- (_collect_overdue_tasks + DUTY_REGISTRY). See docs/duties_playbook.md.
--
-- Starts DISABLED. An admin enables it and assigns a role/user from the UI. While
-- enabled, it creates one alert task per project that has work tasks past their
-- deadline (deadline or due_date before today, status not done/completed).
-- =============================================================================

INSERT INTO automation_settings (automation_type, display_name, is_enabled, default_priority, config)
VALUES (
    'overdue_tasks',
    'Overdue Tasks',
    false,
    2,
    '{"description": "Alerts for projects with tasks past their deadline", "department_hint": "coordination"}'
)
ON CONFLICT (automation_type) DO NOTHING;

-- Optional: default the responsible role to a Project Manager / Coordinator if one
-- exists. Safe no-op if the role or row is missing. Mirrors the pending-expenses
-- default-assignment pattern.
UPDATE public.automation_settings s
   SET responsible_role_id = r.rol_id,
       responsible_type = 'role'
  FROM public.rols r
 WHERE s.automation_type = 'overdue_tasks'
   AND s.responsible_role_id IS NULL
   AND r.rol_name ILIKE '%project manager%';

-- VERIFICATION ---------------------------------------------------------------
-- select automation_type, display_name, is_enabled, default_priority,
--        responsible_type, responsible_role_id, default_manager_id
--   from public.automation_settings where automation_type = 'overdue_tasks';
-- =============================================================================
