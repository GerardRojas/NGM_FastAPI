-- =============================================
-- CREATE TABLE: expenses_manual_general
-- General / Overhead expenses (non-COGS)
-- Same structure as expenses_manual_COGS but
-- project is OPTIONAL (allows "General" entries)
-- =============================================

CREATE TABLE IF NOT EXISTS expenses_manual_general (
    expense_id    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project       UUID REFERENCES projects(project_id),  -- nullable for "General" entries
    txn_type      UUID,
    "TxnDate"     DATE,
    "TxnId_QBO"   TEXT,
    "LineUID"     TEXT,
    "Amount"      NUMERIC,
    vendor_id     UUID,
    payment_type  UUID,
    account_id    UUID,
    "LineDescription" TEXT,
    bill_id       TEXT,
    show_on_reports   BOOLEAN DEFAULT TRUE,
    coinciliation_status BOOLEAN DEFAULT FALSE,
    created_by    UUID,
    status        TEXT DEFAULT 'pending',   -- 'pending' | 'auth' | 'review'
    status_reason TEXT,
    auth_status   BOOLEAN DEFAULT FALSE,
    auth_by       UUID,
    receipt_url   TEXT,
    categorization_confidence INTEGER,
    categorization_source     TEXT,
    created_at    TIMESTAMPTZ DEFAULT NOW(),
    updated_at    TIMESTAMPTZ DEFAULT NOW()
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_gen_expenses_project
    ON expenses_manual_general(project);
CREATE INDEX IF NOT EXISTS idx_gen_expenses_date
    ON expenses_manual_general("TxnDate" DESC);
CREATE INDEX IF NOT EXISTS idx_gen_expenses_status
    ON expenses_manual_general(status);
CREATE INDEX IF NOT EXISTS idx_gen_expenses_vendor
    ON expenses_manual_general(vendor_id);
CREATE INDEX IF NOT EXISTS idx_gen_expenses_account
    ON expenses_manual_general(account_id);
CREATE INDEX IF NOT EXISTS idx_gen_expenses_general_only
    ON expenses_manual_general(created_at DESC)
    WHERE project IS NULL;

-- Auto-update updated_at
CREATE OR REPLACE FUNCTION update_gen_expense_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_gen_expense_updated_at ON expenses_manual_general;
CREATE TRIGGER trg_gen_expense_updated_at
    BEFORE UPDATE ON expenses_manual_general
    FOR EACH ROW
    EXECUTE FUNCTION update_gen_expense_updated_at();

-- Enable RLS
ALTER TABLE expenses_manual_general ENABLE ROW LEVEL SECURITY;

-- Allow all authenticated users (matches COGS table policy)
CREATE POLICY "Allow all for authenticated users"
    ON expenses_manual_general
    FOR ALL
    USING (true)
    WITH CHECK (true);
