-- ========================================
-- Grant "Budgets" (view) to accounting & bookkeeping roles
-- ========================================
-- Originally only CEO and COO had can_view=true for the 'budgets' module.
-- This grants view access to the accounting/bookkeeping roles.
--
-- Idempotent: inserts a view-only row when missing, or flips can_view=true
-- if a row already exists (preserving any existing edit/delete grants).
-- Relies on the unique constraint (rol_id, module_key).

INSERT INTO role_permissions (rol_id, module_key, module_name, module_url, can_view, can_edit, can_delete)
SELECT r.rol_id, 'budgets', 'Budgets', 'budgets.html', true, false, false
FROM rols r
WHERE r.rol_name IN ('Accounting Manager', 'Bookkeeper')
ON CONFLICT (rol_id, module_key) DO UPDATE
  SET can_view = true;

-- Verify
SELECT r.rol_name, rp.can_view, rp.can_edit, rp.can_delete
FROM role_permissions rp
JOIN rols r ON r.rol_id = rp.rol_id
WHERE rp.module_key = 'budgets'
ORDER BY r.rol_name;
