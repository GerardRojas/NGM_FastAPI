-- =============================================================================
-- Sidebar entry for the unified External Users page (route /external-users)
-- =============================================================================
-- The old "Clients" page is absorbed into one External Users directory (tiers:
-- client + team_member). Rather than add a duplicate menu row, we RELABEL the
-- surviving 'clients' menu_item in place -> "External Users" pointing at
-- /external-users, and carry its role_permissions over (so the same roles keep
-- access with no re-grant). The /clients route still redirects for old bookmarks.
--
-- Idempotent (guards on the old slug/module_key; a second run is a no-op). Users
-- refresh their session for the cached menu to pick up the change. Run on prod.
-- Path: C:\Users\germa\Desktop\NGM_API\sql\external_users_menu_item.sql
-- =============================================================================

-- 1) Relabel the menu item -> External Users --------------------------------
UPDATE public.menu_items
   SET slug = 'external-users',
       item_name = 'External Users',
       icon_text = 'diversity_3'
 WHERE slug = 'clients';

-- 2) Carry the role_permissions over (display + link) ------------------------
UPDATE public.role_permissions
   SET module_key = 'external-users',
       module_name = 'External Users',
       module_url = 'external-users'
 WHERE module_key = 'clients';

-- =============================================================================
-- VERIFICATION (uncomment)
-- =============================================================================
-- select mi.slug, mi.item_name, mc.name as category, mi."order"
--   from public.menu_items mi
--   left join public.menu_categories mc on mc.id = mi.category_id
--  where mi.slug in ('clients','external-users');
-- select r.rol_name, rp.can_view, rp.can_edit, rp.can_delete
--   from public.role_permissions rp join public.rols r on r.rol_id = rp.rol_id
--  where rp.module_key = 'external-users' order by r.rol_name;
-- =============================================================================
