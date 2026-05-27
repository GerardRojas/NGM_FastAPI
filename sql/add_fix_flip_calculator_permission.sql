-- ============================================================================
-- FIX & FLIP CALCULATOR: role_permissions menu entry
-- ----------------------------------------------------------------------------
-- Adds the Fix & Flip Calculator module to role_permissions for every role so it
-- shows up in the sidebar (grouped under "Development" via the frontend
-- MODULE_CATEGORY_OVERRIDES) and in the Roles UI matrix.
--
-- Default: CEO/COO/Estimator can view+edit; everyone else off (admins toggle on),
-- mirroring how allowance_adu_calculator was seeded.
-- Idempotent. Run on staging first, then prod (Supabase SQL editor).
-- Path: C:\Users\germa\Desktop\NGM_API\sql\add_fix_flip_calculator_permission.sql
-- ============================================================================

INSERT INTO role_permissions (rol_id, module_key, module_name, module_url, can_view, can_edit, can_delete)
SELECT r.rol_id, 'fix_flip_calculator', 'Fix & Flip Calculator', 'fix-flip-calculator.html',
    CASE WHEN r.rol_name IN ('CEO', 'COO', 'Estimator') THEN true ELSE false END,
    CASE WHEN r.rol_name IN ('CEO', 'COO', 'Estimator') THEN true ELSE false END,
    false
FROM rols r
WHERE NOT EXISTS (
    SELECT 1 FROM role_permissions rp WHERE rp.rol_id = r.rol_id AND rp.module_key = 'fix_flip_calculator'
);

-- VERIFICATION ---------------------------------------------------------------
-- SELECT rp.rol_id, rp.module_key, rp.module_name, rp.can_view
--   FROM role_permissions rp WHERE rp.module_key = 'fix_flip_calculator';
