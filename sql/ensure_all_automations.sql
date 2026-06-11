-- =============================================================================
-- ENSURE — every system automation is registered in automation_settings so it
-- shows in the Responsibilities catalog / Operations -> Automations page with an
-- on/off switch. One idempotent script that guarantees all 8 rows exist in any
-- environment, regardless of which individual seeds were run before.
--
-- ON CONFLICT DO NOTHING: existing rows (and their configured enable state /
-- assignee / department) are preserved untouched. Only missing rows are added,
-- with the canonical display name + sensible defaults (configure the rest in the
-- Operations -> Automations page).
--
-- Idempotent. Run on STAGING first, verify, then PROD.
-- Path: C:\Users\germa\Desktop\NGM_API\sql\ensure_all_automations.sql
-- =============================================================================

-- 1) Duty automations (scheduled / on-demand). Disabled by default — enable from
--    the Automations page when you want them generating standing tasks.
INSERT INTO public.automation_settings
    (automation_type, display_name, is_enabled, default_priority, config)
VALUES
    ('pending_expenses_auth',       'Pending Expenses to Authorize',  false, 2, '{}'::jsonb),
    ('pending_expenses_categorize', 'Pending Expenses to Categorize', false, 3, '{}'::jsonb),
    ('pending_health_check',        'Pending Health Check',           false, 3, '{}'::jsonb),
    ('overdue_tasks',               'Overdue Tasks',                  false, 2, '{}'::jsonb)
ON CONFLICT (automation_type) DO NOTHING;

-- 2) Event-driven estimate-flow automations. Enabled by default.
INSERT INTO public.automation_settings
    (automation_type, display_name, is_enabled, default_priority, config)
VALUES
    ('estimate_review', 'Estimate Review',               true, 3, '{"reviewer_user_ids": []}'::jsonb),
    ('send_to_review',  'Send to Review (status change)', true, 3, '{}'::jsonb)
ON CONFLICT (automation_type) DO NOTHING;

-- estimate_to_budget -> Costs and Estimates department (resolved by name).
INSERT INTO public.automation_settings
    (automation_type, display_name, is_enabled, default_priority, default_department_id, config)
SELECT 'estimate_to_budget', 'Estimate -> Budgets Handoff', true, 3,
       (SELECT department_id FROM public.task_departments WHERE department_name = 'Costs and Estimates' LIMIT 1),
       '{"assignee_user_ids": []}'::jsonb
ON CONFLICT (automation_type) DO NOTHING;

-- send_estimate -> Coordination department (resolved by name).
INSERT INTO public.automation_settings
    (automation_type, display_name, is_enabled, default_priority, default_department_id, config)
SELECT 'send_estimate', 'Send Estimate (Coordination)', true, 3,
       (SELECT department_id FROM public.task_departments WHERE department_name ILIKE '%coordination%' LIMIT 1),
       '{"assignee_user_ids": []}'::jsonb
ON CONFLICT (automation_type) DO NOTHING;

-- VERIFICATION — should list all 8:
-- select automation_type, display_name, is_enabled, default_priority
--   from public.automation_settings order by automation_type;
-- =============================================================================
