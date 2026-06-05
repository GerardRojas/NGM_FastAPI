-- =============================================================================
-- Sidebar cleanup — collapse the "Workspace" + legacy "NGM Connect" duplicates
-- into a single entry labeled "NGM Connect" pointing at the canonical /workspace.
-- =============================================================================
-- Context: client_portal_menu_items.sql inserted a `workspace` menu item, but
-- an older migration left a `connect` (a.k.a. ngm-connect / ngm_connect) row
-- alive, so both show in the sidebar. The React app already normalizes the
-- legacy /connect slug to /workspace (module-config.ts), so the legacy row is
-- pure dead weight. We keep the `workspace` row (direct /workspace route +
-- already-wired role_permissions for module_key='workspace') and just relabel
-- it; then we remove the legacy `connect*` rows and their permissions.
--
-- Idempotent. Run on staging first, then prod. Users must refresh their session
-- for the cached menu to pick up the change.
-- Path: C:\Users\germa\Desktop\NGM_API\sql\rename_workspace_to_ngm_connect.sql
-- =============================================================================

-- 1) Relabel the surviving Workspace menu item -> "NGM Connect" --------------
UPDATE public.menu_items
   SET item_name = 'NGM Connect'
 WHERE slug = 'workspace';

-- Keep role_permissions.module_name in sync (display only; module_key stays
-- 'workspace' so all the existing wiring keeps working).
UPDATE public.role_permissions
   SET module_name = 'NGM Connect'
 WHERE module_key = 'workspace';

-- 2) Remove the legacy connect duplicates ------------------------------------
DO $$
DECLARE
    legacy_ids uuid[];
BEGIN
    SELECT array_agg(id) INTO legacy_ids
      FROM public.menu_items
     WHERE slug IN ('connect', 'ngm-connect', 'ngm_connect');

    IF legacy_ids IS NULL THEN
        RAISE NOTICE 'No legacy connect menu_items found — nothing to remove.';
        RETURN;
    END IF;

    -- Drop role_permissions that point at the legacy rows (by link or by the
    -- old module_key), so the FK to menu_items is clear before we delete.
    DELETE FROM public.role_permissions
     WHERE menu_item_id = ANY(legacy_ids)
        OR module_key IN ('connect', 'ngm-connect', 'ngm_connect');

    DELETE FROM public.menu_items
     WHERE id = ANY(legacy_ids);

    RAISE NOTICE 'Removed % legacy connect menu_item(s).', array_length(legacy_ids, 1);
END $$;

-- =============================================================================
-- VERIFICATION (uncomment to confirm — should return exactly one row: workspace / NGM Connect)
-- =============================================================================
-- select mi.slug, mi.item_name, mc.name as category, mi."order"
--   from public.menu_items mi
--   left join public.menu_categories mc on mc.id = mi.category_id
--  where mi.slug in ('workspace','connect','ngm-connect','ngm_connect')
--  order by mi.slug;
