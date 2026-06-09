-- =============================================================================
-- portal_invoices — invoices shared with a client in the NGM Connect portal.
-- =============================================================================
-- Thin join between a Stripe payment link (invoice_links) and a (project, client)
-- so the Billing module can list/curate invoices. The authoritative amount and
-- payment status always come from invoice_links at read time — this table only
-- records "this link is shared with this client on this project" (+ a caption and
-- a viewed timestamp). Idempotent. Run on staging, then prod.
-- Path: C:\Users\germa\Desktop\NGM_API\sql\portal_invoices.sql
-- =============================================================================

CREATE TABLE IF NOT EXISTS public.portal_invoices (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id       uuid NOT NULL,                 -- soft ref -> projects.project_id
    client_id        uuid NOT NULL,                 -- soft ref -> clients.client_id
    invoice_link_id  uuid NOT NULL,                 -- soft ref -> invoice_links.id
    caption          text,
    created_by       uuid,                          -- soft ref -> users.user_id
    created_at       timestamptz NOT NULL DEFAULT now(),
    viewed_at        timestamptz
);

-- "Invoices for this project" / "for this client" — the list queries.
CREATE INDEX IF NOT EXISTS idx_portal_invoices_project ON public.portal_invoices (project_id);
CREATE INDEX IF NOT EXISTS idx_portal_invoices_client  ON public.portal_invoices (client_id);
-- One portal record per payment link.
CREATE UNIQUE INDEX IF NOT EXISTS uq_portal_invoices_link ON public.portal_invoices (invoice_link_id);

-- Service-role only (the API mediates everything); hard scoping lives in the API.
ALTER TABLE public.portal_invoices ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename='portal_invoices' AND policyname='portal_invoices_service_all') THEN
        CREATE POLICY portal_invoices_service_all ON public.portal_invoices FOR ALL TO service_role USING (true) WITH CHECK (true);
    END IF;
END $$;
