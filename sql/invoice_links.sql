-- ============================================================
-- Invoice Links - Shareable payment links for clients
-- ============================================================

CREATE TABLE IF NOT EXISTS invoice_links (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    invoice_ref     text NOT NULL,
    client_name     text NOT NULL,
    client_email    text NOT NULL,
    description     text NOT NULL,
    amount_cents    integer,                          -- NULL = open-amount (client enters)
    link_type       text NOT NULL DEFAULT 'fixed',    -- 'fixed' | 'open'
    status          text NOT NULL DEFAULT 'active',   -- 'active' | 'paid' | 'expired' | 'cancelled'
    token           text NOT NULL,                    -- signed JWT
    created_by      uuid NOT NULL,                    -- staff user_id
    expires_at      timestamptz NOT NULL,
    paid_at         timestamptz,
    paid_amount     integer,                          -- actual amount paid (for open-amount links)
    stripe_session_id   text,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now()
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_invoice_links_status ON invoice_links (status) WHERE status = 'active';
CREATE INDEX IF NOT EXISTS idx_invoice_links_ref ON invoice_links (invoice_ref);
CREATE INDEX IF NOT EXISTS idx_invoice_links_created_by ON invoice_links (created_by);
CREATE INDEX IF NOT EXISTS idx_invoice_links_token ON invoice_links (token);

-- Auto-update updated_at
CREATE OR REPLACE FUNCTION update_invoice_links_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_invoice_links_updated_at ON invoice_links;
CREATE TRIGGER trg_invoice_links_updated_at
    BEFORE UPDATE ON invoice_links
    FOR EACH ROW
    EXECUTE FUNCTION update_invoice_links_updated_at();

-- RLS
ALTER TABLE invoice_links ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Allow authenticated full access" ON invoice_links
    FOR ALL
    TO authenticated
    USING (true)
    WITH CHECK (true);

CREATE POLICY "Allow service_role full access" ON invoice_links
    FOR ALL
    TO service_role
    USING (true)
    WITH CHECK (true);
