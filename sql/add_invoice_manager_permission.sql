-- Add invoice_manager permission to all existing roles
INSERT INTO role_permissions (rol_id, module_key, module_name, module_url, can_view, can_edit, can_delete)
SELECT r.rol_id, 'invoice_manager', 'Invoice Manager', 'invoice-manager.html', true, true, true
FROM rols r
WHERE r.rol_id NOT IN (
  SELECT rol_id FROM role_permissions WHERE module_key = 'invoice_manager'
);
