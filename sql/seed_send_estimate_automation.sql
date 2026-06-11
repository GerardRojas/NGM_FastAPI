-- =============================================================================
-- SEED — 'send_estimate' automation. When a reviewer APPROVES an estimate branch,
-- the backend (create_send_estimate_task) auto-creates an actionable task handing
-- the approved branch off to COORDINATION to send the estimate to the client. This
-- is the coordination-side counterpart to 'estimate_to_budget' (the costs-side
-- handoff): approval finishes the review task and fans out into two parallel next
-- steps — Budgets import (Costs) and Send estimate (Coordination).
--
-- This row registers + configures the automation so it shows in the Responsibilities
-- catalog / the Operations automations page, where an admin can enable it, set the
-- assignee (person/role) and re-point the department.
--
-- Default department resolves by NAME ('Coordination') so it is portable across
-- environments. Event-driven (not scheduled).
--
-- Idempotent. Run on STAGING first, verify, then PROD.
-- Path: C:\Users\germa\Desktop\NGM_API\sql\seed_send_estimate_automation.sql
-- =============================================================================

INSERT INTO public.automation_settings
    (automation_type, display_name, is_enabled, default_priority, default_department_id, config)
SELECT
    'send_estimate',
    'Send Estimate (Coordination)',
    true,
    3,
    (SELECT department_id FROM public.task_departments
      WHERE department_name ILIKE '%coordination%' LIMIT 1),
    '{"assignee_user_ids": []}'::jsonb
ON CONFLICT (automation_type) DO NOTHING;

-- VERIFICATION:
-- select automation_type, display_name, is_enabled, default_department_id, config
--   from public.automation_settings where automation_type = 'send_estimate';
-- =============================================================================
