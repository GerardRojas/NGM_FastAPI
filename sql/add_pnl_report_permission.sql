-- ============================================================================
-- P&L REPORT: role_permissions entry (Bookkeeper can generate P&L reports)
-- ----------------------------------------------------------------------------
-- The React P&L Report page (/pnl-report) is a sub-page reached from the
-- Reporting hub. Access is gated by a "view" permission on the /pnl-report
-- route, which resolves to a role_permissions row whose module_url normalizes
-- to "pnl-report". No such row existed, so the route was effectively orphaned.
--
-- This seeds module_key 'pnl_report' for every role (idempotent), granting
-- view+edit to the finance/admin roles that generate P&L reports
-- (CEO, COO, Accounting Manager, Bookkeeper); everyone else off (admins toggle
-- on in the Roles UI). No menu_item link: P&L stays a sub-page of Reporting,
-- so it does not add a sidebar entry. The "generate report" action only needs
-- can_view; edit is granted for parity with the other finance modules.
-- Idempotent. Run on staging first, then prod (Supabase SQL editor).
-- Path: C:\Users\germa\Desktop\NGM_API\sql\add_pnl_report_permission.sql
-- ============================================================================

INSERT INTO role_permissions (rol_id, module_key, module_name, module_url, can_view, can_edit, can_delete)
SELECT r.rol_id, 'pnl_report', 'P&L Report', 'pnl-report.html',
    CASE WHEN r.rol_name IN ('CEO', 'COO', 'Accounting Manager', 'Bookkeeper') THEN true ELSE false END,
    CASE WHEN r.rol_name IN ('CEO', 'COO', 'Accounting Manager', 'Bookkeeper') THEN true ELSE false END,
    false
FROM rols r
WHERE NOT EXISTS (
    SELECT 1 FROM role_permissions rp WHERE rp.rol_id = r.rol_id AND rp.module_key = 'pnl_report'
);

-- If the row already exists for these roles but with view off, flip it on
-- without disturbing any role an admin has already toggled.
UPDATE role_permissions rp
SET can_view = true, can_edit = true, updated_at = now()
FROM rols r
WHERE rp.rol_id = r.rol_id
  AND rp.module_key = 'pnl_report'
  AND r.rol_name IN ('CEO', 'COO', 'Accounting Manager', 'Bookkeeper')
  AND rp.can_view = false;

-- VERIFICATION ---------------------------------------------------------------
-- SELECT r.rol_name, rp.module_key, rp.module_url, rp.can_view, rp.can_edit
--   FROM role_permissions rp JOIN rols r ON r.rol_id = rp.rol_id
--   WHERE rp.module_key = 'pnl_report' ORDER BY r.rol_name;
