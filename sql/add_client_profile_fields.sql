-- =============================================================================
-- Client profile fields — enable full client CRUD from the hub Clients page.
-- =============================================================================
-- The `clients` table historically only carried client_id / client_name (+ the
-- company_id added by add_company_id_to_clients.sql). This adds the profile
-- columns the new Clients UI manages (contact, email, phone, address, status,
-- notes) plus audit timestamps. Every column is additive and guarded with
-- IF NOT EXISTS, so the existing read-side (dropdowns, /projects/meta) is
-- untouched.
--
-- Idempotent. Run on STAGING first, verify, then PROD.
-- Path: C:\Users\germa\Desktop\NGM_API\sql\add_client_profile_fields.sql
-- =============================================================================

ALTER TABLE public.clients
    ADD COLUMN IF NOT EXISTS company_id   uuid REFERENCES public.companies(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS contact_name text,
    ADD COLUMN IF NOT EXISTS email        text,
    ADD COLUMN IF NOT EXISTS phone        text,
    ADD COLUMN IF NOT EXISTS address      text,
    ADD COLUMN IF NOT EXISTS notes        text,
    ADD COLUMN IF NOT EXISTS status       text DEFAULT 'Active',
    ADD COLUMN IF NOT EXISTS created_at   timestamptz DEFAULT now(),
    ADD COLUMN IF NOT EXISTS updated_at   timestamptz DEFAULT now();

CREATE INDEX IF NOT EXISTS idx_clients_company ON public.clients (company_id);

-- VERIFICATION ----------------------------------------------------------------
-- select column_name, data_type
--   from information_schema.columns
--  where table_name = 'clients' order by ordinal_position;
-- =============================================================================
