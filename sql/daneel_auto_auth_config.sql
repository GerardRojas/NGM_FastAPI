-- ================================================================
-- Daneel Auto-Authorization: Database setup
-- ================================================================
-- Adds config keys and tracking table for Daneel's expense
-- auto-authorization feature.
-- ================================================================

-- 1. Config keys in agent_config (same table Arturito uses)
INSERT INTO agent_config (key, value) VALUES
    ('daneel_auto_auth_enabled',        'false'::jsonb),
    ('daneel_auto_auth_require_bill',   'true'::jsonb),
    ('daneel_auto_auth_require_receipt','true'::jsonb),
    ('daneel_fuzzy_threshold',          '85'::jsonb),
    ('daneel_amount_tolerance',         '0.05'::jsonb),
    ('daneel_labor_keywords',           '"labor"'::jsonb),
    ('daneel_bookkeeping_role',         'null'::jsonb),
    ('daneel_accounting_mgr_role',      'null'::jsonb),
    ('daneel_auto_auth_last_run',       'null'::jsonb),
    ('daneel_gpt_fallback_enabled',    'false'::jsonb),
    ('daneel_gpt_fallback_confidence', '75'::jsonb),
    ('daneel_bookkeeping_users',       '[]'::jsonb),
    ('daneel_accounting_mgr_users',    '[]'::jsonb)
ON CONFLICT (key) DO NOTHING;

-- 2. Tracking table for expenses awaiting missing info
CREATE TABLE IF NOT EXISTS daneel_pending_info (
    expense_id   UUID PRIMARY KEY REFERENCES "expenses_manual_COGS"(expense_id) ON DELETE CASCADE,
    project_id   UUID,
    missing_fields TEXT[] NOT NULL,
    requested_at TIMESTAMPTZ DEFAULT NOW(),
    resolved_at  TIMESTAMPTZ,
    message_id   UUID
);

CREATE INDEX IF NOT EXISTS idx_daneel_pending_unresolved
    ON daneel_pending_info (resolved_at) WHERE resolved_at IS NULL;

-- RLS: service role only (backend manages this table)
ALTER TABLE daneel_pending_info ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Service role full access on daneel_pending_info" ON daneel_pending_info;
CREATE POLICY "Service role full access on daneel_pending_info" ON daneel_pending_info
    FOR ALL
    USING (auth.role() = 'service_role')
    WITH CHECK (auth.role() = 'service_role');

-- 3. Auth reports: decision log for each run session
CREATE TABLE IF NOT EXISTS daneel_auth_reports (
    report_id   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    report_type TEXT NOT NULL,          -- 'project_run', 'backlog', 'realtime_batch'
    project_id  UUID,
    project_name TEXT,
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    summary     JSONB NOT NULL DEFAULT '{}',
    decisions   JSONB NOT NULL DEFAULT '[]'
);

CREATE INDEX IF NOT EXISTS idx_daneel_reports_created
    ON daneel_auth_reports (created_at DESC);

ALTER TABLE daneel_auth_reports ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Service role full access on daneel_auth_reports" ON daneel_auth_reports;
CREATE POLICY "Service role full access on daneel_auth_reports" ON daneel_auth_reports
    FOR ALL
    USING (auth.role() = 'service_role')
    WITH CHECK (auth.role() = 'service_role');
