-- =============================================================================
-- Move the "NGM Connect" sidebar item into the Admin category
-- =============================================================================
-- Sharing approved estimate carátulas with clients is a Coordination/Admin task,
-- so NGM Connect (slug 'workspace', relabeled by rename_workspace_to_ngm_connect.sql)
-- belongs under Admin rather than General. Display-only move; the route
-- (/workspace) and role_permissions (module_key 'workspace') are unchanged.
--
-- Idempotent. Users refresh their session for the cached menu to pick it up.
-- Path: C:\Users\germa\Desktop\NGM_API\sql\move_ngm_connect_to_admin.sql
-- =============================================================================

UPDATE public.menu_items
   SET category_id = (SELECT id FROM public.menu_categories WHERE name = 'Admin' LIMIT 1)
 WHERE slug = 'workspace'
   AND EXISTS (SELECT 1 FROM public.menu_categories WHERE name = 'Admin');

-- =============================================================================
-- VERIFICATION (uncomment)
-- =============================================================================
-- select mi.slug, mi.item_name, mc.name as category
--   from public.menu_items mi
--   left join public.menu_categories mc on mc.id = mi.category_id
--  where mi.slug = 'workspace';
-- =============================================================================
