-- ========================================
-- Add vault module to role_permissions
-- ========================================
-- CEO, COO: full access (view, edit, delete)
-- Architect, General Coordinator, Project Coordinator, Bookkeeper, Accounting Manager: view + edit
-- Others: no access by default (admins can toggle via Roles UI)

INSERT INTO role_permissions (rol_id, module_key, module_name, module_url, can_view, can_edit, can_delete)
SELECT r.rol_id, 'vault', 'Vault', 'vault.html',
    CASE WHEN r.rol_name IN ('CEO', 'COO', 'Architect', 'General Coordinator', 'Project Coordinator', 'Bookkeeper', 'Accounting Manager') THEN true ELSE false END,
    CASE WHEN r.rol_name IN ('CEO', 'COO', 'Architect', 'General Coordinator', 'Project Coordinator', 'Bookkeeper', 'Accounting Manager') THEN true ELSE false END,
    CASE WHEN r.rol_name IN ('CEO', 'COO') THEN true ELSE false END
FROM rols r
WHERE NOT EXISTS (
    SELECT 1 FROM role_permissions rp WHERE rp.rol_id = r.rol_id AND rp.module_key = 'vault'
);
