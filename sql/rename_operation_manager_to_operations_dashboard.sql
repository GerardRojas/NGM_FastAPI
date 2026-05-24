-- ================================================================
-- Rename: Operation Manager  ->  Operations Dashboard
-- ================================================================
-- The legacy "operation_manager" module (Gantt timeline) was rewritten into
-- the "Operations Dashboard" (weekly ops review: KPIs, project status, task
-- progress, team capacity). This migrates the menu/permission rows so the
-- sidebar label, slug and route all reflect the new module.
--
-- We keep every role's existing can_view/can_edit/can_delete grant intact --
-- only the module_key, module_name and module_url change.
--
-- Idempotent. Run on staging first, verify, then prod.
-- ================================================================

UPDATE role_permissions
SET module_key  = 'operations_dashboard',
    module_name = 'Operations Dashboard',
    module_url  = 'operations-dashboard.html'
WHERE module_key = 'operation_manager';

-- Verification
SELECT
    r.rol_name,
    rp.module_key,
    rp.module_name,
    rp.module_url,
    rp.can_view,
    rp.can_edit,
    rp.can_delete
FROM role_permissions rp
INNER JOIN rols r ON rp.rol_id = r.rol_id
WHERE rp.module_key IN ('operations_dashboard', 'operation_manager')
ORDER BY r.rol_name;
