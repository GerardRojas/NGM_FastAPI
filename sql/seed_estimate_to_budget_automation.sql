-- =============================================================================
-- SEED — 'estimate_to_budget' automation. When a reviewer APPROVES an estimate
-- branch, the backend (create_estimate_to_budget_task) auto-creates an actionable
-- task handing the approved branch off to the Costs department to import it into
-- Budgets. This row registers + configures that automation so it shows in the
-- Responsibilities catalog / the Operations automations page, where an admin can
-- enable it, set the assignee (person/role) and re-point the department.
--
-- Default department resolves by NAME ('Costs and Estimates') so it is portable
-- across environments. Event-driven (not scheduled).
--
-- Idempotent. Run on STAGING first, verify, then PROD.
-- Path: C:\Users\germa\Desktop\NGM_API\sql\seed_estimate_to_budget_automation.sql
-- =============================================================================

INSERT INTO public.automation_settings
    (automation_type, display_name, is_enabled, default_priority, default_department_id, config)
SELECT
    'estimate_to_budget',
    'Estimate -> Budgets Handoff',
    true,
    3,
    (SELECT department_id FROM public.task_departments
      WHERE department_name = 'Costs and Estimates' LIMIT 1),
    '{"assignee_user_ids": []}'::jsonb
ON CONFLICT (automation_type) DO NOTHING;

-- VERIFICATION:
-- select automation_type, display_name, is_enabled, default_department_id, config
--   from public.automation_settings where automation_type = 'estimate_to_budget';
-- =============================================================================
