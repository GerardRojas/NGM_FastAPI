-- =============================================================================
-- Workspace scoping — add company_id to the entity tables that lack it
-- =============================================================================
-- Part of the "full pass" migrating every company-scoped module to the active
-- workspace model. These tables had NO company_id column (verified against the
-- live schema 2026-06-05), so the backend could not filter by company.
--
-- Convention (matches clients / Vendors / sheet_templates):
--   company_id uuid NULL  -> "shared / visible in all workspaces"
--   ON DELETE SET NULL    -> deleting a company unshares its rows, never deletes
-- Existing rows stay NULL (shared) so nothing disappears until reassigned.
--
-- Idempotent (ADD COLUMN IF NOT EXISTS + guards). Run on staging first, prod
-- second. Pair each table with its backend filter (.or_(company_id.eq.X,
-- company_id.is.null)) + create persisting company_id, and the frontend
-- withActiveOrg/stampOrg/useOrgChange.
-- Path: C:\Users\germa\Desktop\NGM_API\sql\add_company_id_workspace_scoping.sql
-- =============================================================================

DO $$
DECLARE
    t text;
    -- Only tables filtered DIRECTLY by company_id. Budgets are project-scoped
    -- and budget alerts scope via their project_id -> projects.source_company,
    -- so budgets_qbo / budget_alert_* intentionally do NOT get a company_id col.
    tables text[] := ARRAY[
        'accounts',
        'bills',
        'paymet_methods',          -- (sic) existing table name is misspelled
        'invoice_links',
        'feasibility_deals',
        'fix_flip_deals'
    ];
BEGIN
    FOREACH t IN ARRAY tables LOOP
        IF to_regclass('public.' || quote_ident(t)) IS NULL THEN
            RAISE NOTICE 'Skipping %: table does not exist.', t;
            CONTINUE;
        END IF;

        EXECUTE format(
            'ALTER TABLE public.%I ADD COLUMN IF NOT EXISTS company_id uuid REFERENCES public.companies(id) ON DELETE SET NULL',
            t
        );
        EXECUTE format(
            'CREATE INDEX IF NOT EXISTS %I ON public.%I (company_id)',
            'idx_' || t || '_company', t
        );
        EXECUTE format(
            'COMMENT ON COLUMN public.%I.company_id IS %L',
            t, 'Owning organization. NULL = shared / visible in all workspaces.'
        );
        RAISE NOTICE 'Ensured company_id on %.', t;
    END LOOP;
END $$;

-- =============================================================================
-- VERIFICATION (uncomment): every table below should return one row.
-- =============================================================================
-- select table_name, column_name
--   from information_schema.columns
--  where table_schema = 'public' and column_name = 'company_id'
--    and table_name in ('accounts','bills','paymet_methods','invoice_links',
--                       'feasibility_deals','fix_flip_deals')
--  order by table_name;
