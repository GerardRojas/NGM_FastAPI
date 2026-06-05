-- =============================================
-- CLIENTS - Organization scoping
-- =============================================
-- Adds a nullable company_id so clients can belong to a single organization.
-- NULL = shared / visible in every organization (existing rows stay visible
-- everywhere — no backfill).
-- NOTE: there is currently no clients CRUD router, so this column only powers the
-- read-side filter on the clients dropdown (GET /projects/meta). Create-wiring is
-- deferred until a real clients CRUD exists.
-- ON DELETE SET NULL: removing a company reverts its clients to shared.
--
-- Guarded: SKIPS silently if the table is absent (instead of erroring).
-- Backward compatible. Idempotent. Run on staging, then prod.
-- =============================================

DO $$
BEGIN
    IF to_regclass('public.clients') IS NULL THEN
        RAISE NOTICE 'Skipping: public.clients does not exist.';
        RETURN;
    END IF;

    ALTER TABLE public.clients
        ADD COLUMN IF NOT EXISTS company_id UUID REFERENCES public.companies(id) ON DELETE SET NULL;

    CREATE INDEX IF NOT EXISTS idx_clients_company
        ON public.clients (company_id);

    COMMENT ON COLUMN public.clients.company_id
        IS 'Owning organization. NULL = shared / visible in all companies.';
END $$;

-- VERIFICATION ------------------------------------------------
-- select client_id, client_name, company_id from public.clients limit 20;
