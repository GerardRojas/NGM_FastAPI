-- =============================================================================
-- external_contacts — unified directory for all external parties (NGM Connect)
-- =============================================================================
-- Replaces the split between the `clients` page and Team Management's "external
-- users" section with ONE directory. Two tiers:
--   * 'team_member' — interconnected: uses the hub itself (modules, role
--     permissions, receives tasks). These are LOGIN accounts and stay in `users`
--     (heavily FK'd); we only add a directory entry + link via users.contact_id.
--   * 'client' — recurring/informational: read-only NGM Connect workspace with
--     curated modules (payments, timeline, budget carátula, invoices) + comms.
--
-- Migration strategy (low risk): refs TO `clients` are mostly SOFT (no hard FK),
-- so we seed external_contacts PRESERVING the original id (id = client_id, and
-- id = user_id for externals). Existing project_client_access.client_id /
-- project_user_access.user_id / projects.client_id / portal_invoices.client_id /
-- client_invites.client_id keep resolving unchanged.
--
-- Idempotent (IF NOT EXISTS + ON CONFLICT DO NOTHING). Run on prod BEFORE the
-- backend that consumes it.
-- Path: C:\Users\germa\Desktop\NGM_API\sql\create_external_contacts.sql
-- =============================================================================

-- 1) Directory table ---------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.external_contacts (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tier         text NOT NULL DEFAULT 'client' CHECK (tier IN ('team_member', 'client')),
    category     text,                       -- vendor | contractor | company | client (descriptive)
    name         text NOT NULL,
    contact_name text,
    email        text,
    phone        text,
    address      text,
    notes        text,
    status       text DEFAULT 'Active',
    company_id   uuid REFERENCES public.companies(id) ON DELETE SET NULL,
    created_at   timestamptz DEFAULT now(),
    updated_at   timestamptz DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_external_contacts_tier    ON public.external_contacts (tier);
CREATE INDEX IF NOT EXISTS idx_external_contacts_company ON public.external_contacts (company_id);

-- 2) Link a login account (users) to its directory entry ---------------------
ALTER TABLE public.users
    ADD COLUMN IF NOT EXISTS contact_id uuid;

CREATE INDEX IF NOT EXISTS idx_users_contact ON public.users (contact_id) WHERE contact_id IS NOT NULL;

-- 3) Seed 'client' contacts from the existing clients table -------------------
--    id = client_id keeps every soft-ref to clients valid.
INSERT INTO public.external_contacts
    (id, tier, category, name, contact_name, email, phone, address, notes, status, company_id, created_at, updated_at)
SELECT c.client_id, 'client', 'client',
       c.client_name, c.contact_name, c.email, c.phone, c.address, c.notes,
       COALESCE(c.status, 'Active'), c.company_id,
       COALESCE(c.created_at, now()), COALESCE(c.updated_at, now())
  FROM public.clients c
 WHERE c.client_name IS NOT NULL
ON CONFLICT (id) DO NOTHING;

-- 4) Seed 'team_member' contacts from external login accounts -----------------
--    id = user_id; `users` has no email column (login lives in user_name).
INSERT INTO public.external_contacts
    (id, tier, category, name, phone, address, notes, status, created_at, updated_at)
SELECT u.user_id, 'team_member', 'contractor',
       COALESCE(NULLIF(u.user_name, ''), 'External team member'),
       u.user_phone_number, u.user_address, u.user_description,
       'Active', COALESCE(u.created_at, now()), now()
  FROM public.users u
 WHERE u.is_external = true
ON CONFLICT (id) DO NOTHING;

-- 5) Link each external login to its (just-seeded) directory entry ------------
UPDATE public.users
   SET contact_id = user_id
 WHERE is_external = true
   AND contact_id IS NULL;

-- =============================================================================
-- VERIFICATION (uncomment to confirm)
-- =============================================================================
-- select tier, count(*) from public.external_contacts group by tier;
-- select count(*) as linked_logins from public.users where is_external and contact_id is not null;
-- =============================================================================
