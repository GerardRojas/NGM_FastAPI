-- ============================================================================
-- RD MODEL (Residential Development — Rent & Hold / Build-to-Sell): sidebar entry
-- ----------------------------------------------------------------------------
-- The page already exists (RDCalculatorReactPage at /rd-calculator) but was only
-- reachable via the "RD Model" button inside the Fix & Flip Calculator. This puts
-- it in the sidebar as its own module, in the SAME "Development" group as Fix &
-- Flip Calculator (it sits right after it).
--
-- The React sidebar = menu_items JOINed to role_permissions via
-- role_permissions.menu_item_id (NULL link rows are dropped), so all three pieces
-- are wired: 1) per-role permissions, 2) the menu_items row under "Development",
-- 3) the link. Mirrors add_fix_flip_calculator_permission.sql exactly (same roles).
--
-- Default: CEO/COO/Estimator view+edit; everyone else off (admins toggle on).
-- Idempotent. Run on staging first, then prod (Supabase SQL editor). Users must
-- refresh their session for the cached menu to pick it up.
-- Path: C:\Users\germa\Desktop\NGM_API\sql\add_rd_calculator_permission.sql
-- ============================================================================

-- 1) PER-ROLE PERMISSIONS ----------------------------------------------------
INSERT INTO role_permissions (rol_id, module_key, module_name, module_url, can_view, can_edit, can_delete)
SELECT r.rol_id, 'rd_calculator', 'RD Model', 'rd-calculator',
    CASE WHEN r.rol_name IN ('CEO', 'COO', 'Estimator') THEN true ELSE false END,  -- can_view
    CASE WHEN r.rol_name IN ('CEO', 'COO', 'Estimator') THEN true ELSE false END,  -- can_edit
    false                                                                          -- can_delete
FROM rols r
WHERE NOT EXISTS (
    SELECT 1 FROM role_permissions rp
    WHERE rp.rol_id = r.rol_id AND rp.module_key = 'rd_calculator'
);

-- 2) MENU ITEM (Development, right after Fix & Flip Calculator = 3) -----------
INSERT INTO menu_items (slug, item_name, icon_type, icon_text, category_id, "order")
SELECT 'rd-calculator', 'RD Model', NULL, 'apartment', c.id, 4
FROM menu_categories c
WHERE c.name = 'Development'
ON CONFLICT (slug) DO NOTHING;

-- 3) LINK role_permissions -> menu_item --------------------------------------
UPDATE role_permissions rp
SET menu_item_id = mi.id
FROM menu_items mi
WHERE mi.slug = 'rd-calculator'
  AND rp.module_key = 'rd_calculator';

-- 4) VERIFICATION ------------------------------------------------------------
SELECT r.rol_name, rp.module_key, rp.can_view, rp.can_edit, rp.can_delete, mi.slug, mi."order"
FROM role_permissions rp
JOIN rols r ON r.rol_id = rp.rol_id
LEFT JOIN menu_items mi ON mi.id = rp.menu_item_id
WHERE rp.module_key = 'rd_calculator'
ORDER BY r.rol_name;
