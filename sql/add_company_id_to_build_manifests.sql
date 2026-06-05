-- =============================================
-- BUILD MANIFESTS (Project Builder / Revit) - Organization scoping
-- =============================================
-- Adds a nullable company_id so saved manifests/templates can belong to a single
-- organization. NULL = shared / visible in every organization (existing rows stay
-- visible everywhere — no backfill). Manifests already carry an optional
-- project_id; company_id lets template-style manifests (project_id NULL) still be
-- scoped to a company.
-- ON DELETE SET NULL: removing a company reverts its manifests to shared.
--
-- NOTE: build_manifests is created by sql/build_manifests.sql and only exists in
-- environments where the Project Builder feature is deployed. This migration is
-- guarded so it silently SKIPS (instead of erroring) when the table is absent.
-- Run build_manifests.sql first if you need the table.
--
-- Backward compatible. Idempotent. Run on staging, then prod.
-- =============================================

DO $$
BEGIN
    IF to_regclass('public.build_manifests') IS NULL THEN
        RAISE NOTICE 'Skipping: public.build_manifests does not exist (run build_manifests.sql first).';
        RETURN;
    END IF;

    ALTER TABLE public.build_manifests
        ADD COLUMN IF NOT EXISTS company_id UUID REFERENCES public.companies(id) ON DELETE SET NULL;

    CREATE INDEX IF NOT EXISTS idx_build_manifests_company
        ON public.build_manifests (company_id);

    COMMENT ON COLUMN public.build_manifests.company_id
        IS 'Owning organization. NULL = shared / visible in all companies.';
END $$;

-- VERIFICATION ------------------------------------------------
-- select id, name, project_id, company_id from public.build_manifests limit 20;
