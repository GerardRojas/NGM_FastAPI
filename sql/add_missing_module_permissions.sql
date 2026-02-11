-- ========================================
-- Add all missing modules to role_permissions
-- ========================================
-- The sidebar MODULE_CONFIG has many modules that were never seeded
-- into role_permissions, so they never appear in the Roles UI matrix.
-- This script adds them all for every role (CEO/COO get can_view=true,
-- others get can_view=false by default so admins can toggle them on).

-- 1) process_manager
INSERT INTO role_permissions (rol_id, module_key, module_name, module_url, can_view, can_edit, can_delete)
SELECT r.rol_id, 'process_manager', 'Process Manager', 'process_manager.html',
    CASE WHEN r.rol_name IN ('CEO', 'COO') THEN true ELSE false END,
    CASE WHEN r.rol_name IN ('CEO', 'COO') THEN true ELSE false END,
    false
FROM rols r
WHERE NOT EXISTS (
    SELECT 1 FROM role_permissions rp WHERE rp.rol_id = r.rol_id AND rp.module_key = 'process_manager'
);

-- 2) my_work
INSERT INTO role_permissions (rol_id, module_key, module_name, module_url, can_view, can_edit, can_delete)
SELECT r.rol_id, 'my_work', 'My Work', 'my-work.html',
    true,  -- everyone can view my work
    false, false
FROM rols r
WHERE NOT EXISTS (
    SELECT 1 FROM role_permissions rp WHERE rp.rol_id = r.rol_id AND rp.module_key = 'my_work'
);

-- 3) messages
INSERT INTO role_permissions (rol_id, module_key, module_name, module_url, can_view, can_edit, can_delete)
SELECT r.rol_id, 'messages', 'Messages', 'messages.html',
    true,  -- everyone can view messages
    true, false
FROM rols r
WHERE NOT EXISTS (
    SELECT 1 FROM role_permissions rp WHERE rp.rol_id = r.rol_id AND rp.module_key = 'messages'
);

-- 4) arturito
INSERT INTO role_permissions (rol_id, module_key, module_name, module_url, can_view, can_edit, can_delete)
SELECT r.rol_id, 'arturito', 'Arturito', 'arturito.html',
    true,  -- everyone can view arturito
    false, false
FROM rols r
WHERE NOT EXISTS (
    SELECT 1 FROM role_permissions rp WHERE rp.rol_id = r.rol_id AND rp.module_key = 'arturito'
);

-- 5) company_expenses
INSERT INTO role_permissions (rol_id, module_key, module_name, module_url, can_view, can_edit, can_delete)
SELECT r.rol_id, 'company_expenses', 'Company Expenses', 'company-expenses.html',
    CASE WHEN r.rol_name IN ('CEO', 'COO', 'Accounting Manager', 'Bookkeeper') THEN true ELSE false END,
    CASE WHEN r.rol_name IN ('CEO', 'COO', 'Accounting Manager') THEN true ELSE false END,
    false
FROM rols r
WHERE NOT EXISTS (
    SELECT 1 FROM role_permissions rp WHERE rp.rol_id = r.rol_id AND rp.module_key = 'company_expenses'
);

-- 6) budget_monitor
INSERT INTO role_permissions (rol_id, module_key, module_name, module_url, can_view, can_edit, can_delete)
SELECT r.rol_id, 'budget_monitor', 'Budget Monitor', 'budget_monitor.html',
    CASE WHEN r.rol_name IN ('CEO', 'COO', 'Accounting Manager', 'Bookkeeper', 'Project Manager') THEN true ELSE false END,
    CASE WHEN r.rol_name IN ('CEO', 'COO') THEN true ELSE false END,
    false
FROM rols r
WHERE NOT EXISTS (
    SELECT 1 FROM role_permissions rp WHERE rp.rol_id = r.rol_id AND rp.module_key = 'budget_monitor'
);

-- 7) budgets
INSERT INTO role_permissions (rol_id, module_key, module_name, module_url, can_view, can_edit, can_delete)
SELECT r.rol_id, 'budgets', 'Budgets', 'budgets.html',
    CASE WHEN r.rol_name IN ('CEO', 'COO', 'Accounting Manager', 'Bookkeeper', 'Project Manager') THEN true ELSE false END,
    CASE WHEN r.rol_name IN ('CEO', 'COO', 'Accounting Manager') THEN true ELSE false END,
    false
FROM rols r
WHERE NOT EXISTS (
    SELECT 1 FROM role_permissions rp WHERE rp.rol_id = r.rol_id AND rp.module_key = 'budgets'
);

-- 8) pnl_report
INSERT INTO role_permissions (rol_id, module_key, module_name, module_url, can_view, can_edit, can_delete)
SELECT r.rol_id, 'pnl_report', 'P&L Report', 'pnl-report.html',
    CASE WHEN r.rol_name IN ('CEO', 'COO', 'Accounting Manager') THEN true ELSE false END,
    false, false
FROM rols r
WHERE NOT EXISTS (
    SELECT 1 FROM role_permissions rp WHERE rp.rol_id = r.rol_id AND rp.module_key = 'pnl_report'
);

-- 9) reporting
INSERT INTO role_permissions (rol_id, module_key, module_name, module_url, can_view, can_edit, can_delete)
SELECT r.rol_id, 'reporting', 'Reporting', 'reporting.html',
    CASE WHEN r.rol_name IN ('CEO', 'COO', 'Accounting Manager', 'Bookkeeper') THEN true ELSE false END,
    false, false
FROM rols r
WHERE NOT EXISTS (
    SELECT 1 FROM role_permissions rp WHERE rp.rol_id = r.rol_id AND rp.module_key = 'reporting'
);

-- 10) companies
INSERT INTO role_permissions (rol_id, module_key, module_name, module_url, can_view, can_edit, can_delete)
SELECT r.rol_id, 'companies', 'Companies', 'companies.html',
    CASE WHEN r.rol_name IN ('CEO', 'COO') THEN true ELSE false END,
    CASE WHEN r.rol_name IN ('CEO', 'COO') THEN true ELSE false END,
    false
FROM rols r
WHERE NOT EXISTS (
    SELECT 1 FROM role_permissions rp WHERE rp.rol_id = r.rol_id AND rp.module_key = 'companies'
);

-- 11) roles
INSERT INTO role_permissions (rol_id, module_key, module_name, module_url, can_view, can_edit, can_delete)
SELECT r.rol_id, 'roles', 'Roles Management', 'roles.html',
    CASE WHEN r.rol_name IN ('CEO', 'COO') THEN true ELSE false END,
    CASE WHEN r.rol_name IN ('CEO', 'COO') THEN true ELSE false END,
    false
FROM rols r
WHERE NOT EXISTS (
    SELECT 1 FROM role_permissions rp WHERE rp.rol_id = r.rol_id AND rp.module_key = 'roles'
);

-- 12) god_view
INSERT INTO role_permissions (rol_id, module_key, module_name, module_url, can_view, can_edit, can_delete)
SELECT r.rol_id, 'god_view', 'God View', 'god-view.html',
    CASE WHEN r.rol_name IN ('CEO', 'COO') THEN true ELSE false END,
    false, false
FROM rols r
WHERE NOT EXISTS (
    SELECT 1 FROM role_permissions rp WHERE rp.rol_id = r.rol_id AND rp.module_key = 'god_view'
);

-- 13) arturito_settings (Agent Hub)
INSERT INTO role_permissions (rol_id, module_key, module_name, module_url, can_view, can_edit, can_delete)
SELECT r.rol_id, 'arturito_settings', 'Agent Hub', 'agents-settings.html',
    CASE WHEN r.rol_name IN ('CEO', 'COO') THEN true ELSE false END,
    CASE WHEN r.rol_name IN ('CEO', 'COO') THEN true ELSE false END,
    false
FROM rols r
WHERE NOT EXISTS (
    SELECT 1 FROM role_permissions rp WHERE rp.rol_id = r.rol_id AND rp.module_key = 'arturito_settings'
);

-- 14) settings
INSERT INTO role_permissions (rol_id, module_key, module_name, module_url, can_view, can_edit, can_delete)
SELECT r.rol_id, 'settings', 'Settings', 'settings.html',
    CASE WHEN r.rol_name IN ('CEO', 'COO') THEN true ELSE false END,
    CASE WHEN r.rol_name IN ('CEO', 'COO') THEN true ELSE false END,
    false
FROM rols r
WHERE NOT EXISTS (
    SELECT 1 FROM role_permissions rp WHERE rp.rol_id = r.rol_id AND rp.module_key = 'settings'
);

-- 15) audit
INSERT INTO role_permissions (rol_id, module_key, module_name, module_url, can_view, can_edit, can_delete)
SELECT r.rol_id, 'audit', 'Audit Logs', 'audit.html',
    CASE WHEN r.rol_name IN ('CEO', 'COO') THEN true ELSE false END,
    false, false
FROM rols r
WHERE NOT EXISTS (
    SELECT 1 FROM role_permissions rp WHERE rp.rol_id = r.rol_id AND rp.module_key = 'audit'
);

-- 16) allowance_adu_calculator
INSERT INTO role_permissions (rol_id, module_key, module_name, module_url, can_view, can_edit, can_delete)
SELECT r.rol_id, 'allowance_adu_calculator', 'Allowance ADU Calculator', 'allowance-adu-calculator.html',
    CASE WHEN r.rol_name IN ('CEO', 'COO', 'Estimator') THEN true ELSE false END,
    CASE WHEN r.rol_name IN ('CEO', 'COO', 'Estimator') THEN true ELSE false END,
    false
FROM rols r
WHERE NOT EXISTS (
    SELECT 1 FROM role_permissions rp WHERE rp.rol_id = r.rol_id AND rp.module_key = 'allowance_adu_calculator'
);

-- ========================================
-- Verify: show all modules now in the system
-- ========================================
SELECT DISTINCT rp.module_key, rp.module_name
FROM role_permissions rp
ORDER BY rp.module_key;
