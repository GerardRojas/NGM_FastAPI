-- Add invoice_manager permission to all existing roles
INSERT INTO role_permissions (role_id, module_key, can_view, can_edit, can_delete)
SELECT role_id, 'invoice_manager', true, true, true
FROM roles
WHERE role_id NOT IN (
  SELECT role_id FROM role_permissions WHERE module_key = 'invoice_manager'
);
