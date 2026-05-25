-- =============================================================================
-- Sidebar entry for the Categories page (route /categories)
-- =============================================================================
-- Adds a "Categories" menu_item under "Costs and Estimates" and grants it to
-- every role that already has the Accounts permission (mirroring view/edit/delete
-- — so role-based editing works out of the box). Idempotent. After running,
-- users refresh their session for the cached menu to pick it up. The page already
-- works by URL (/categories) via the accounts permission alias.
--
-- Accounts (legacy flat chart) stays for now; retire it at cutover. Run on prod.
-- =============================================================================

INSERT INTO public.menu_items (id, slug, item_name, icon_type, icon_text, category_id, "order")
SELECT gen_random_uuid(), 'categories', 'Categories', 'material', 'category',
       (SELECT id FROM public.menu_categories WHERE name = 'Costs and Estimates' LIMIT 1),
       3
WHERE NOT EXISTS (SELECT 1 FROM public.menu_items WHERE slug = 'categories');

INSERT INTO public.role_permissions
       (id, rol_id, menu_item_id, module_key, module_name, module_url, can_view, can_edit, can_delete)
SELECT gen_random_uuid(), rp.rol_id,
       (SELECT id FROM public.menu_items WHERE slug = 'categories' LIMIT 1),
       'categories', 'Categories', 'categories',
       rp.can_view, rp.can_edit, rp.can_delete
FROM public.role_permissions rp
WHERE rp.module_key = 'accounts'
  AND NOT EXISTS (
      SELECT 1 FROM public.role_permissions x
      WHERE x.rol_id = rp.rol_id AND x.module_key = 'categories'
  );

-- VERIFICATION ----------------------------------------------------------------
-- select * from public.menu_items where slug = 'categories';
-- select r.rol_name, rp.can_view, rp.can_edit, rp.can_delete
--   from public.role_permissions rp join public.rols r on r.rol_id = rp.rol_id
--  where rp.module_key = 'categories' order by r.rol_name;
