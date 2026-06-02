-- =============================================================================
-- Client Portal — sidebar menu_items + role permissions
-- =============================================================================
-- Surfaces the two internal modules of the client portal in the React sidebar:
--   * Clients   -> /clients   (CRM + per-project access + invites)         → "Admin"
--   * Workspace -> /workspace (unified client/external/team-preview shell) → "General"
--
-- The React sidebar is built from menu_items JOINed to role_permissions via
-- role_permissions.menu_item_id (NULL menu_item_id rows are silently dropped),
-- so each module needs all three pieces wired:
--   1) per-role permissions
--   2) menu_items row (slug = React route token, no leading slash, no .html)
--   3) role_permissions.menu_item_id linked to that menu_items row
--
-- Default access: CEO, COO, General Coordinator, Project Coordinator get
-- view+edit; everyone else off (admins toggle on from Roles Management).
-- Mirrors the discipline used by add_fix_flip_calculator_permission.sql.
--
-- Idempotent. Run on staging first, then prod (Supabase SQL editor).
-- After running, users must refresh their session for the cached menu to pick
-- it up. The pages already work by URL (/clients, /connect); this just exposes
-- them in the sidebar.
-- Path: C:\Users\germa\Desktop\NGM_API\sql\client_portal_menu_items.sql
-- =============================================================================


-- =============================================================================
-- CLIENTS  →  category "Admin"
-- =============================================================================

-- 1) per-role permissions ----------------------------------------------------
INSERT INTO role_permissions (rol_id, module_key, module_name, module_url, can_view, can_edit, can_delete)
SELECT r.rol_id, 'clients', 'Clients', 'clients',
    CASE WHEN r.rol_name IN ('CEO', 'COO', 'General Coordinator', 'Project Coordinator') THEN true ELSE false END,  -- can_view
    CASE WHEN r.rol_name IN ('CEO', 'COO', 'General Coordinator', 'Project Coordinator') THEN true ELSE false END,  -- can_edit
    CASE WHEN r.rol_name IN ('CEO', 'COO') THEN true ELSE false END                                                 -- can_delete
FROM rols r
WHERE NOT EXISTS (
    SELECT 1 FROM role_permissions rp
    WHERE rp.rol_id = r.rol_id AND rp.module_key = 'clients'
);

-- 2) menu item (Admin) -------------------------------------------------------
INSERT INTO menu_items (slug, item_name, icon_type, icon_text, category_id, "order")
SELECT 'clients', 'Clients', 'material', 'groups',
       (SELECT id FROM public.menu_categories WHERE name = 'Admin' LIMIT 1),
       2
ON CONFLICT (slug) DO NOTHING;

-- 3) link role_permissions -> menu_item --------------------------------------
UPDATE role_permissions rp
SET menu_item_id = mi.id
FROM menu_items mi
WHERE mi.slug = 'clients'
  AND rp.module_key = 'clients'
  AND (rp.menu_item_id IS NULL OR rp.menu_item_id <> mi.id);


-- =============================================================================
-- WORKSPACE  →  category "General"
-- =============================================================================

-- 1) per-role permissions ----------------------------------------------------
INSERT INTO role_permissions (rol_id, module_key, module_name, module_url, can_view, can_edit, can_delete)
SELECT r.rol_id, 'workspace', 'Workspace', 'workspace',
    CASE WHEN r.rol_name IN ('CEO', 'COO', 'General Coordinator', 'Project Coordinator') THEN true ELSE false END,  -- can_view
    CASE WHEN r.rol_name IN ('CEO', 'COO', 'General Coordinator', 'Project Coordinator') THEN true ELSE false END,  -- can_edit
    CASE WHEN r.rol_name IN ('CEO', 'COO') THEN true ELSE false END                                                 -- can_delete
FROM rols r
WHERE NOT EXISTS (
    SELECT 1 FROM role_permissions rp
    WHERE rp.rol_id = r.rol_id AND rp.module_key = 'workspace'
);

-- 2) menu item (General) -----------------------------------------------------
INSERT INTO menu_items (slug, item_name, icon_type, icon_text, category_id, "order")
SELECT 'workspace', 'Workspace', 'material', 'workspaces',
       (SELECT id FROM public.menu_categories WHERE name = 'General' LIMIT 1),
       90
ON CONFLICT (slug) DO NOTHING;

-- 3) link role_permissions -> menu_item --------------------------------------
UPDATE role_permissions rp
SET menu_item_id = mi.id
FROM menu_items mi
WHERE mi.slug = 'workspace'
  AND rp.module_key = 'workspace'
  AND (rp.menu_item_id IS NULL OR rp.menu_item_id <> mi.id);


-- =============================================================================
-- VERIFICATION
-- =============================================================================
-- select mi.slug, mi.item_name, mc.name as category, mi."order"
--   from public.menu_items mi
--   left join public.menu_categories mc on mc.id = mi.category_id
--  where mi.slug in ('clients','workspace') order by mi.slug;
--
-- select r.rol_name, rp.module_key, rp.can_view, rp.can_edit, rp.can_delete, mi.slug
--   from public.role_permissions rp
--   join public.rols r on r.rol_id = rp.rol_id
--   left join public.menu_items mi on mi.id = rp.menu_item_id
--  where rp.module_key in ('clients','workspace')
--  order by rp.module_key, r.rol_name;
