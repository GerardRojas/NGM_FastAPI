-- =============================================================================
-- Sidebar entry for the Accounting > Cost Codes page (route /accounting)
-- =============================================================================
-- The React sidebar is built from role_permissions -> menu_items -> menu_categories.
-- This adds one menu_item ("Cost Codes" under "Accounting & Bookkeeping") and grants
-- it to every role that already has the Accounts permission, mirroring their access.
--
-- Idempotent (guarded by NOT EXISTS). After running, users must refresh their
-- session/permissions (re-login or reload) for the cached menu to pick it up.
-- The page already works by URL (/accounting) via the accounts permission alias;
-- this just surfaces it in the sidebar. Run on staging, then prod.
-- =============================================================================

-- 1. The menu item (slug "accounting" -> React route /accounting).
INSERT INTO public.menu_items (id, slug, item_name, icon_type, icon_text, category_id, "order")
SELECT gen_random_uuid(), 'accounting', 'Cost Codes', 'material', 'sell',
       (SELECT id FROM public.menu_categories WHERE name = 'Accounting & Bookkeeping' LIMIT 1),
       1
WHERE NOT EXISTS (SELECT 1 FROM public.menu_items WHERE slug = 'accounting');

-- 2. Grant it to every role that already has the Accounts permission, copying
--    their view/edit/delete flags so access mirrors the chart of accounts.
INSERT INTO public.role_permissions
       (id, rol_id, menu_item_id, module_key, module_name, module_url, can_view, can_edit, can_delete)
SELECT gen_random_uuid(), rp.rol_id,
       (SELECT id FROM public.menu_items WHERE slug = 'accounting' LIMIT 1),
       'accounting', 'Cost Codes', 'accounting',
       rp.can_view, rp.can_edit, rp.can_delete
FROM public.role_permissions rp
WHERE rp.module_key = 'accounts'
  AND NOT EXISTS (
      SELECT 1 FROM public.role_permissions x
      WHERE x.rol_id = rp.rol_id AND x.module_key = 'accounting'
  );

-- VERIFICATION ----------------------------------------------------------------
-- select * from public.menu_items where slug = 'accounting';
-- select rp.rol_id, r.rol_name, rp.can_view, rp.can_edit, rp.can_delete
--   from public.role_permissions rp join public.rols r on r.rol_id = rp.rol_id
--  where rp.module_key = 'accounting' order by r.rol_name;
