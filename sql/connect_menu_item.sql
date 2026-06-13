-- =============================================================================
-- Connect — sidebar menu_item + role permissions
-- =============================================================================
-- Surfaces the internal Connect builder in the React sidebar:
--   * Connect -> /connect (build/curate external workspaces: who sees what) → "Admin"
--
-- Connect is the team-facing control center for the client portal: it lists every
-- curated workspace (one per audience+project), with inline module toggles and
-- preview/invite/remove. It complements Clients (CRM) and Workspace (the shared
-- preview shell). Companion to client_portal_menu_items.sql.
--
-- The React sidebar is built from menu_items JOINed to role_permissions via
-- role_permissions.menu_item_id (NULL menu_item_id rows are silently dropped),
-- so the module needs all three pieces wired:
--   1) per-role permissions
--   2) menu_items row (slug = React route token, no leading slash, no .html)
--   3) role_permissions.menu_item_id linked to that menu_items row
--
-- Default access: CEO, COO, General Coordinator, Project Coordinator get
-- view+edit; everyone else off (admins toggle on from Roles Management).
--
-- Idempotent. Run on staging first, then prod (Supabase SQL editor). After
-- running, users must refresh their session for the cached menu to pick it up.
-- The page already works by URL (/connect); this just exposes it in the sidebar.
-- Path: C:\Users\germa\Desktop\NGM_API\sql\connect_menu_item.sql
-- =============================================================================

-- 1) per-role permissions ----------------------------------------------------
INSERT INTO role_permissions (rol_id, module_key, module_name, module_url, can_view, can_edit, can_delete)
SELECT r.rol_id, 'connect', 'Connect', 'connect',
    CASE WHEN r.rol_name IN ('CEO', 'COO', 'General Coordinator', 'Project Coordinator') THEN true ELSE false END,  -- can_view
    CASE WHEN r.rol_name IN ('CEO', 'COO', 'General Coordinator', 'Project Coordinator') THEN true ELSE false END,  -- can_edit
    CASE WHEN r.rol_name IN ('CEO', 'COO') THEN true ELSE false END                                                 -- can_delete
FROM rols r
WHERE NOT EXISTS (
    SELECT 1 FROM role_permissions rp
    WHERE rp.rol_id = r.rol_id AND rp.module_key = 'connect'
);

-- 2) menu item (Admin) -------------------------------------------------------
INSERT INTO menu_items (slug, item_name, icon_type, icon_text, category_id, "order")
SELECT 'connect', 'Connect', 'material', 'hub',
       (SELECT id FROM public.menu_categories WHERE name = 'Admin' LIMIT 1),
       3
ON CONFLICT (slug) DO NOTHING;

-- 3) link role_permissions -> menu_item --------------------------------------
UPDATE role_permissions rp
SET menu_item_id = mi.id
FROM menu_items mi
WHERE mi.slug = 'connect'
  AND rp.module_key = 'connect'
  AND (rp.menu_item_id IS NULL OR rp.menu_item_id <> mi.id);

-- =============================================================================
-- VERIFICATION
-- =============================================================================
-- select mi.slug, mi.item_name, mc.name as category, mi."order"
--   from public.menu_items mi
--   left join public.menu_categories mc on mc.id = mi.category_id
--  where mi.slug = 'connect';
--
-- select r.rol_name, rp.can_view, rp.can_edit, rp.can_delete, mi.slug
--   from public.role_permissions rp
--   join public.rols r on r.rol_id = rp.rol_id
--   left join public.menu_items mi on mi.id = rp.menu_item_id
--  where rp.module_key = 'connect'
--  order by r.rol_name;
