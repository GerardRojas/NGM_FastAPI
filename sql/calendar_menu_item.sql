-- =============================================================================
-- Calendar — sidebar menu_item + role permissions for all roles
-- =============================================================================
-- Adds the /calendar React page to the General sidebar group. Calendar is meant
-- to be visible for every role (everyone needs to see schedule + project
-- deadlines), so this grants can_view=true to every existing role. Edit/delete
-- defaults are kept conservative (coordination roles only) and can be widened
-- from Roles Management as the team decides.
--
-- The React sidebar is built from menu_items JOINed to role_permissions via
-- role_permissions.menu_item_id, so a calendar module needs all three pieces:
--   1) per-role permissions  (granted to every role; view=true)
--   2) menu_items row        (slug = 'calendar', category = General)
--   3) role_permissions.menu_item_id linked to that menu_items row
--
-- Idempotent. Run on staging first, then prod (Supabase SQL editor). After
-- running, users must refresh their session for the cached menu to pick it up.
-- Path: C:\Users\germa\Desktop\NGM_API\sql\calendar_menu_item.sql
-- =============================================================================

-- 1) per-role permissions ----------------------------------------------------
INSERT INTO role_permissions (rol_id, module_key, module_name, module_url, can_view, can_edit, can_delete)
SELECT r.rol_id, 'calendar', 'Calendar', 'calendar',
    true,  -- can_view: everyone sees the calendar
    CASE WHEN r.rol_name IN ('CEO', 'COO', 'General Coordinator', 'Project Coordinator') THEN true ELSE false END,
    CASE WHEN r.rol_name IN ('CEO', 'COO') THEN true ELSE false END
FROM rols r
WHERE NOT EXISTS (
    SELECT 1 FROM role_permissions rp
    WHERE rp.rol_id = r.rol_id AND rp.module_key = 'calendar'
);

-- 2) menu item (General) -----------------------------------------------------
INSERT INTO menu_items (slug, item_name, icon_type, icon_text, category_id, "order")
SELECT 'calendar', 'Calendar', 'material', 'event',
       (SELECT id FROM public.menu_categories WHERE name = 'General' LIMIT 1),
       50
ON CONFLICT (slug) DO NOTHING;

-- 3) link role_permissions -> menu_item --------------------------------------
UPDATE role_permissions rp
SET menu_item_id = mi.id
FROM menu_items mi
WHERE mi.slug = 'calendar'
  AND rp.module_key = 'calendar'
  AND (rp.menu_item_id IS NULL OR rp.menu_item_id <> mi.id);


-- =============================================================================
-- VERIFICATION
-- =============================================================================
-- select mi.slug, mi.item_name, mc.name as category, mi."order"
--   from public.menu_items mi
--   left join public.menu_categories mc on mc.id = mi.category_id
--  where mi.slug = 'calendar';
--
-- select r.rol_name, rp.can_view, rp.can_edit, rp.can_delete, mi.slug
--   from public.role_permissions rp
--   join public.rols r on r.rol_id = rp.rol_id
--   left join public.menu_items mi on mi.id = rp.menu_item_id
--  where rp.module_key = 'calendar'
--  order by r.rol_name;
