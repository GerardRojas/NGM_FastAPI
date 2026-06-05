-- =============================================
-- SHEET TEMPLATES - Provision per-company copies of the default presets
-- =============================================
-- Gives every existing organization its own editable copies of the shared
-- preset templates, stamping the company's name into the export/report header
-- (branding.companyName + branding.companyInfo). Each copy is company-scoped
-- (company_id = company.id) and kept is_preset = true so the UI marks it
-- built-in (editable, not deletable).
--
-- New companies get this automatically on creation (companies router). This
-- script backfills the companies that already existed.
--
-- Idempotent: a company that already has a copy with the same name is skipped,
-- so re-running never duplicates. Guarded: skips if tables are absent.
-- Run AFTER add_company_id_to_sheet_templates.sql. Staging, then prod.
-- =============================================

DO $$
BEGIN
    IF to_regclass('public.sheet_templates') IS NULL OR to_regclass('public.companies') IS NULL THEN
        RAISE NOTICE 'Skipping: sheet_templates or companies table does not exist.';
        RETURN;
    END IF;

    INSERT INTO public.sheet_templates (name, theme, branding, view_config, is_default, is_preset, company_id)
    SELECT
        t.name,
        t.theme,
        jsonb_set(
            jsonb_set(coalesce(t.branding, '{}'::jsonb), '{companyName}', to_jsonb(c.name)),
            '{companyInfo}', to_jsonb(c.name)
        ),
        t.view_config,
        t.is_default,
        true,
        c.id
    FROM public.sheet_templates t
    CROSS JOIN public.companies c
    WHERE t.is_preset = true
      AND t.company_id IS NULL
      AND NOT EXISTS (
          SELECT 1 FROM public.sheet_templates x
          WHERE x.company_id = c.id AND x.name = t.name
      );
END $$;

-- VERIFICATION ------------------------------------------------
-- select c.name as company, st.name as template, st.branding->>'companyName' as header_name
-- from public.sheet_templates st
-- join public.companies c on c.id = st.company_id
-- order by c.name, st.name;
