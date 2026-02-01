-- =============================================
-- Table: qbo_budget_mapping
-- Maps QuickBooks Budgets to NGM Hub Projects
-- =============================================
--
-- Similar to qbo_project_mapping for expenses
-- Allows users to map QBO budgets to NGM projects

CREATE TABLE IF NOT EXISTS qbo_budget_mapping (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- QBO Budget Info
    qbo_budget_id TEXT NOT NULL UNIQUE,   -- Budget ID in QuickBooks
    qbo_budget_name TEXT,                  -- Budget name in QBO
    qbo_fiscal_year INTEGER,               -- Fiscal year of the budget

    -- NGM Project Link
    ngm_project_id UUID,                   -- Mapped NGM Hub project ID

    -- Matching metadata
    auto_matched BOOLEAN DEFAULT false,    -- Was this auto-matched?
    match_confidence NUMERIC(5, 2),        -- Confidence score if auto-matched

    -- Timestamps
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- =============================================
-- Indices
-- =============================================

CREATE INDEX IF NOT EXISTS idx_qbo_budget_mapping_budget_id ON qbo_budget_mapping(qbo_budget_id);
CREATE INDEX IF NOT EXISTS idx_qbo_budget_mapping_project ON qbo_budget_mapping(ngm_project_id);
CREATE INDEX IF NOT EXISTS idx_qbo_budget_mapping_year ON qbo_budget_mapping(qbo_fiscal_year DESC);
CREATE INDEX IF NOT EXISTS idx_qbo_budget_mapping_unmapped ON qbo_budget_mapping(ngm_project_id) WHERE ngm_project_id IS NULL;

-- =============================================
-- Trigger for updated_at
-- =============================================

CREATE OR REPLACE FUNCTION update_qbo_budget_mapping_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trigger_qbo_budget_mapping_updated_at
    BEFORE UPDATE ON qbo_budget_mapping
    FOR EACH ROW
    EXECUTE FUNCTION update_qbo_budget_mapping_updated_at();

-- =============================================
-- RLS Policies
-- =============================================

ALTER TABLE qbo_budget_mapping ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Users can view budget mappings" ON qbo_budget_mapping
    FOR SELECT
    USING (auth.role() = 'authenticated');

CREATE POLICY "Only service role can modify budget mappings" ON qbo_budget_mapping
    FOR ALL
    USING (auth.role() = 'service_role');

-- =============================================
-- Comments
-- =============================================

COMMENT ON TABLE qbo_budget_mapping IS 'Maps QuickBooks Online budgets to NGM Hub projects';
COMMENT ON COLUMN qbo_budget_mapping.qbo_budget_id IS 'QuickBooks Budget ID - unique identifier';
COMMENT ON COLUMN qbo_budget_mapping.ngm_project_id IS 'NGM Hub project UUID - null if unmapped';
COMMENT ON COLUMN qbo_budget_mapping.auto_matched IS 'True if matched automatically by name similarity';
