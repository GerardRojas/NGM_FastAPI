-- =============================================================================
-- Link demo/beta Requests -> Leads inbox.
-- Run in the Supabase SQL editor (STAGING first, then PROD). IDEMPOTENT.
-- Path: C:\Users\germa\Desktop\NGM_API\sql\link_demo_requests_to_leads.sql
--
-- Why: "Requests" (beta_access_requests, IT side) and "Leads" (contact_messages,
-- coordination side) are separate inboxes owned by different teams. A demo/beta
-- request should now ALSO surface as a Lead so coordination can follow up, while
-- IT still handles the request technically. The two rows stay linked via
-- contact_messages.linked_request_id so each side can trace the other.
-- =============================================================================

-- 1) Link column on the Leads table ------------------------------------------
ALTER TABLE public.contact_messages
    ADD COLUMN IF NOT EXISTS linked_request_id uuid;

-- FK to the originating request (guarded — Postgres has no ADD CONSTRAINT IF NOT
-- EXISTS). ON DELETE SET NULL: deleting a request keeps the lead, just unlinks it.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'contact_messages_linked_request_fk'
    ) THEN
        ALTER TABLE public.contact_messages
            ADD CONSTRAINT contact_messages_linked_request_fk
            FOREIGN KEY (linked_request_id)
            REFERENCES public.beta_access_requests(id) ON DELETE SET NULL;
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_contact_messages_linked_request
    ON public.contact_messages (linked_request_id);


-- 2) Backfill: create a linked Lead for every existing Request that doesn't have
--    one yet. Mirrors the synthesis the API does for new requests going forward.
INSERT INTO public.contact_messages (name, email, message, source, lang, status, linked_request_id, submitted_at)
SELECT
    b.name,
    b.email,
    'Demo/beta access request.'
        || COALESCE(' Company: '       || NULLIF(b.company, ''),         '')
        || COALESCE(' / Role: '        || NULLIF(b.role, ''),            '')
        || COALESCE(' / Industry: '    || NULLIF(b.industry, ''),        '')
        || COALESCE(' / Team size: '   || NULLIF(b.team_size, ''),       '')
        || COALESCE(' / Active proj: ' || NULLIF(b.active_projects, ''), '')
        || COALESCE(' / Plan: '        || NULLIF(b.plan_interest, ''),   '')
        || COALESCE(' / Billing: '     || NULLIF(b.billing_period, ''),  '')
        || COALESCE(E'\n\nMessage: '   || NULLIF(b.message, ''),         '') AS message,
    'demo-request' AS source,
    b.lang,
    'new' AS status,
    b.id AS linked_request_id,
    COALESCE(b.requested_at, now()) AS submitted_at
FROM public.beta_access_requests b
WHERE NOT EXISTS (
    SELECT 1 FROM public.contact_messages c WHERE c.linked_request_id = b.id
);


-- =============================================================================
-- VERIFICATION (optional)
-- =============================================================================
-- select c.id, c.name, c.source, c.status, c.linked_request_id
--   from public.contact_messages c
--  where c.source = 'demo-request'
--  order by c.submitted_at desc;
-- =============================================================================
