-- ============================================================================
-- FIX & FLIP CALCULATOR: sidebar menu entry + role permissions
-- ----------------------------------------------------------------------------
-- The React sidebar is built from menu_items JOINed to role_permissions via
-- role_permissions.menu_item_id (see NGM_API api/routers/permissions.py
-- _build_user_menu). A permission row with a NULL menu_item_id is silently
-- dropped, so inserting only into role_permissions is NOT enough — the module
-- also needs a menu_items row, and the permission rows must point at it.
--
-- This does the full 3-step wiring (mirrors sql/add_sheet_templates_menu.sql):
--   1) per-role permissions, 2) the menu_items row under "Development",
--   3) link role_permissions.menu_item_id -> that menu item.
--
-- Default: CEO/COO/Estimator can view+edit; everyone else off (admins toggle on),
-- mirroring how allowance_adu_calculator was seeded.
-- Idempotent. Run on staging first, then prod (Supabase SQL editor).
-- Path: C:\Users\germa\Desktop\NGM_API\sql\add_fix_flip_calculator_permission.sql
-- ============================================================================

-- 1) PER-ROLE PERMISSIONS ----------------------------------------------------
INSERT INTO role_permissions (rol_id, module_key, module_name, module_url, can_view, can_edit, can_delete)
SELECT r.rol_id, 'fix_flip_calculator', 'Fix & Flip Calculator', 'fix-flip-calculator',
    CASE WHEN r.rol_name IN ('CEO', 'COO', 'Estimator') THEN true ELSE false END,  -- can_view
    CASE WHEN r.rol_name IN ('CEO', 'COO', 'Estimator') THEN true ELSE false END,  -- can_edit
    false                                                                          -- can_delete
FROM rols r
WHERE NOT EXISTS (
    SELECT 1 FROM role_permissions rp
    WHERE rp.rol_id = r.rol_id AND rp.module_key = 'fix_flip_calculator'
);

-- 2) MENU ITEM (Development, after Feasibility=1 and Allowance ADU=2) ---------
INSERT INTO menu_items (slug, item_name, icon_type, icon_text, category_id, "order")
SELECT 'fix-flip-calculator', 'Fix & Flip Calculator', NULL, 'real_estate_agent', c.id, 3
FROM menu_categories c
WHERE c.name = 'Development'
ON CONFLICT (slug) DO NOTHING;

-- 3) LINK role_permissions -> menu_item --------------------------------------
UPDATE role_permissions rp
SET menu_item_id = mi.id
FROM menu_items mi
WHERE mi.slug = 'fix-flip-calculator'
  AND rp.module_key = 'fix_flip_calculator';

-- 4) VERIFICATION ------------------------------------------------------------
SELECT r.rol_name, rp.module_key, rp.can_view, rp.can_edit, rp.can_delete, mi.slug, mi."order"
FROM role_permissions rp
JOIN rols r ON r.rol_id = rp.rol_id
LEFT JOIN menu_items mi ON mi.id = rp.menu_item_id
WHERE rp.module_key = 'fix_flip_calculator'
ORDER BY r.rol_name;
