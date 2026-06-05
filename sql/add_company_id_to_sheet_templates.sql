-- =============================================
-- SHEET TEMPLATES - Organization scoping
-- =============================================
-- Adds a nullable company_id so templates can belong to a single organization
-- (company). NULL = shared / visible in every organization, which keeps all
-- existing rows (including seeded presets) visible everywhere — no backfill.
-- ON DELETE SET NULL: removing a company reverts its templates to shared
-- instead of destroying them.
--
-- Guarded: SKIPS silently if the table is absent (instead of erroring).
-- Backward compatible. Idempotent. Run on staging, then prod.
-- =============================================

DO $$
BEGIN
    IF to_regclass('public.sheet_templates') IS NULL THEN
        RAISE NOTICE 'Skipping: public.sheet_templates does not exist.';
        RETURN;
    END IF;

    ALTER TABLE public.sheet_templates
        ADD COLUMN IF NOT EXISTS company_id UUID REFERENCES public.companies(id) ON DELETE SET NULL;

    CREATE INDEX IF NOT EXISTS idx_sheet_templates_company
        ON public.sheet_templates (company_id);

    COMMENT ON COLUMN public.sheet_templates.company_id
        IS 'Owning organization. NULL = shared / visible in all companies.';
END $$;

-- VERIFICATION ------------------------------------------------
-- select id, name, is_preset, company_id from public.sheet_templates limit 20;
