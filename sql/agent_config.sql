-- ================================================================
-- Agent Config: Key-value settings for Arturito receipt agent
-- ================================================================
-- Run this in Supabase SQL Editor to create the agent_config table.
-- Used by: arturito-settings.html (Agent Manager section)
-- ================================================================

CREATE TABLE IF NOT EXISTS agent_config (
    key TEXT PRIMARY KEY,
    value JSONB NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Default values
INSERT INTO agent_config (key, value) VALUES
    ('auto_create_expense', 'true'::jsonb),
    ('min_confidence', '70'::jsonb),
    ('auto_skip_duplicates', 'false'::jsonb),
    ('receipt_reminder_hours', '4'::jsonb),
    ('receipt_max_reminders', '3'::jsonb)
ON CONFLICT (key) DO NOTHING;

-- RLS: service role only (backend manages this table)
ALTER TABLE agent_config ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Service role full access" ON agent_config;
CREATE POLICY "Service role full access" ON agent_config
  FOR ALL
  USING (auth.role() = 'service_role')
  WITH CHECK (auth.role() = 'service_role');
