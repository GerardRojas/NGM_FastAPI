-- Add estimator_database module to role_permissions
-- Run this in Supabase SQL Editor

-- Insert permission for all existing roles
-- CEO and COO get full access (VED), Project Manager and Estimator get edit access

INSERT INTO role_permissions (rol_id, module_key, module_name, module_url, can_view, can_edit, can_delete)
SELECT
    r.rol_id,
    'estimator_database',
    'Estimator Database',
    'estimator_database.html',
    true,  -- can_view: todos
    CASE WHEN r.rol_name IN ('CEO', 'COO', 'Project Manager', 'Estimator') THEN true ELSE false END,  -- can_edit
    CASE WHEN r.rol_name IN ('CEO', 'COO') THEN true ELSE false END  -- can_delete
FROM rols r
WHERE NOT EXISTS (
    SELECT 1 FROM role_permissions rp
    WHERE rp.rol_id = r.rol_id AND rp.module_key = 'estimator_database'
);

-- Verify the insert
SELECT r.rol_name, rp.module_key, rp.module_name, rp.can_view, rp.can_edit, rp.can_delete
FROM role_permissions rp
JOIN rols r ON r.rol_id = rp.rol_id
WHERE rp.module_key = 'estimator_database'
ORDER BY r.rol_name;
