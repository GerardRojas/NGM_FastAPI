-- =============================================================================
-- Sidebar entry for the Task Automations page (route /operations-automations)
-- =============================================================================
-- The React sidebar is built from role_permissions -> menu_items -> menu_categories.
-- This page (system automations + their owners, e.g. estimate_to_budget,
-- overdue_tasks — the ones that create/resolve Pipeline tasks) already works by
-- URL and was only reachable from a button inside the Operations Dashboard.
--
-- This seed surfaces it as its own sidebar module, placed in the SAME group that
-- already holds Operations Dashboard / Pipeline (resolved dynamically, so we
-- don't hard-code the group name), and granted to every role that can already
-- see the Operations Dashboard (mirroring its view/edit/delete). After running,
-- users refresh their session for the cached menu to pick it up.
--
-- Idempotent. Run on STAGING first, verify, then PROD.
-- Path: C:\Users\germa\Desktop\NGM_API\sql\operations_automations_menu_item.sql
-- =============================================================================

-- 1) Menu item, dropped into the same category as Operations Dashboard / Pipeline
--    and appended after the existing items in that group. The EXISTS(target_cat)
--    guard avoids inserting an uncategorized row if neither anchor item is found.
WITH target_cat AS (
    SELECT category_id
    FROM public.menu_items
    WHERE slug IN (
        'operations-dashboard', 'operations_dashboard', 'operation-manager',
        'pipeline', 'pipeline-manager', 'pipeline_manager'
    )
      AND category_id IS NOT NULL
    ORDER BY CASE
                 WHEN slug LIKE 'operations%' THEN 0   -- prefer Operations Dashboard's group
                 WHEN slug LIKE 'operation-%' THEN 1
                 ELSE 2                                  -- fall back to Pipeline's group
             END
    LIMIT 1
)
INSERT INTO public.menu_items (id, slug, item_name, icon_type, icon_text, category_id, "order")
SELECT gen_random_uuid(), 'operations-automations', 'Automations', 'material', 'bolt',
       (SELECT category_id FROM target_cat),
       COALESCE((SELECT MAX(mi."order") FROM public.menu_items mi
                  WHERE mi.category_id = (SELECT category_id FROM target_cat)), 0) + 1
WHERE NOT EXISTS (SELECT 1 FROM public.menu_items WHERE slug = 'operations-automations')
  AND EXISTS (SELECT 1 FROM target_cat);

-- 2) Grant it to every role that already has an Operations Dashboard permission,
--    carrying over view/edit/delete. Aggregated per role (bool_or) so a role with
--    several ops-dashboard key variants only gets one row.
INSERT INTO public.role_permissions
       (id, rol_id, menu_item_id, module_key, module_name, module_url, can_view, can_edit, can_delete)
SELECT gen_random_uuid(), src.rol_id,
       (SELECT id FROM public.menu_items WHERE slug = 'operations-automations' LIMIT 1),
       'operations-automations', 'Automations', 'operations-automations',
       src.can_view, src.can_edit, src.can_delete
FROM (
    SELECT rp.rol_id,
           bool_or(rp.can_view)   AS can_view,
           bool_or(rp.can_edit)   AS can_edit,
           bool_or(rp.can_delete) AS can_delete
    FROM public.role_permissions rp
    WHERE rp.module_key IN (
        'operations_dashboard', 'operations-dashboard',
        'operation_manager', 'operation-manager'
    )
    GROUP BY rp.rol_id
) src
WHERE NOT EXISTS (
    SELECT 1 FROM public.role_permissions x
    WHERE x.rol_id = src.rol_id AND x.module_key = 'operations-automations'
);

-- VERIFICATION ----------------------------------------------------------------
-- Confirm it landed in the same group as Operations Dashboard / Pipeline:
-- select mi.slug, mi.item_name, mc.name as category
--   from public.menu_items mi join public.menu_categories mc on mc.id = mi.category_id
--  where mi.slug in ('operations-automations','operations-dashboard','pipeline')
--  order by mc.name, mi."order";
-- select r.rol_name, rp.can_view, rp.can_edit, rp.can_delete
--   from public.role_permissions rp join public.rols r on r.rol_id = rp.rol_id
--  where rp.module_key = 'operations-automations' order by r.rol_name;
-- =============================================================================
