-- ============================================================
-- Grant can_delete on the 'expenses' module to specific roles.
--
-- Pairs with the role-based soft-delete change (api/routers/expenses.py):
-- DELETE /expenses/{id} now requires role_permissions.can_delete = true on the
-- 'expenses' module. This restores the delete ability for roles that had the
-- button before the gate was added.
--
-- Idempotent (UPDATE on existing rows). Run on staging, then prod.
-- ============================================================

update public.role_permissions rp
set can_delete = true,
    updated_at = now()
from public.rols r
where rp.rol_id = r.rol_id
  and rp.module_key = 'expenses'
  and r.rol_name in ('Project Coordinator', 'Accounting Manager');

-- Verify:
-- select r.rol_name, rp.module_key, rp.can_view, rp.can_edit, rp.can_delete
-- from public.role_permissions rp
-- join public.rols r on r.rol_id = rp.rol_id
-- where rp.module_key = 'expenses'
-- order by r.rol_name;
