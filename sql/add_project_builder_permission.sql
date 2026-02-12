-- ========================================
-- Add project_builder module to role_permissions
-- ========================================
-- CEO, COO: full access (view, edit, delete)
-- Architect, Estimator, General Coordinator, Project Coordinator: view + edit
-- Others: no access by default (admins can toggle via Roles UI)

INSERT INTO role_permissions (rol_id, module_key, module_name, module_url, can_view, can_edit, can_delete)
SELECT r.rol_id, 'project_builder', 'Project Builder', 'project-builder.html',
    CASE WHEN r.rol_name IN ('CEO', 'COO', 'Architect', 'Estimator', 'General Coordinator', 'Project Coordinator') THEN true ELSE false END,
    CASE WHEN r.rol_name IN ('CEO', 'COO', 'Architect', 'Estimator', 'General Coordinator', 'Project Coordinator') THEN true ELSE false END,
    CASE WHEN r.rol_name IN ('CEO', 'COO') THEN true ELSE false END
FROM rols r
WHERE NOT EXISTS (
    SELECT 1 FROM role_permissions rp WHERE rp.rol_id = r.rol_id AND rp.module_key = 'project_builder'
);
