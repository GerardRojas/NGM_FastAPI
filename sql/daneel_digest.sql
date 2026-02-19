-- ============================================================================
-- Daneel Digest: schema changes for periodic consolidated messages
-- ============================================================================

-- Track which auth reports have been included in a digest message
ALTER TABLE daneel_auth_reports
    ADD COLUMN IF NOT EXISTS digest_sent_at TIMESTAMPTZ;

-- Fast lookup of un-digested reports
CREATE INDEX IF NOT EXISTS idx_daneel_reports_undigested
    ON daneel_auth_reports (created_at DESC)
    WHERE digest_sent_at IS NULL;

-- Default config: digest enabled, flush every 4 hours
INSERT INTO agent_config (key, value) VALUES
    ('daneel_digest_enabled', 'true'),
    ('daneel_digest_interval_hours', '4')
ON CONFLICT (key) DO NOTHING;
