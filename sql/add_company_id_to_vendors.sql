-- =============================================
-- VENDORS - Organization scoping
-- =============================================
-- Adds a nullable company_id so vendors can belong to a single organization.
-- NULL = shared / visible in every organization (existing rows, e.g. those synced
-- from QuickBooks, stay visible everywhere — no backfill).
-- NOTE: the table is "Vendors" (capital V).
-- ON DELETE SET NULL: removing a company reverts its vendors to shared.
--
-- Guarded: SKIPS silently if the table is absent (instead of erroring).
-- Backward compatible. Idempotent. Run on staging, then prod.
-- =============================================

DO $$
BEGIN
    IF to_regclass('public."Vendors"') IS NULL THEN
        RAISE NOTICE 'Skipping: public."Vendors" does not exist.';
        RETURN;
    END IF;

    ALTER TABLE public."Vendors"
        ADD COLUMN IF NOT EXISTS company_id UUID REFERENCES public.companies(id) ON DELETE SET NULL;

    CREATE INDEX IF NOT EXISTS idx_vendors_company
        ON public."Vendors" (company_id);

    COMMENT ON COLUMN public."Vendors".company_id
        IS 'Owning organization. NULL = shared / visible in all companies.';
END $$;

-- VERIFICATION ------------------------------------------------
-- select id, vendor_name, company_id from public."Vendors" limit 20;
