-- ================================================================
-- Message Mentions: Track read status per user per message
-- ================================================================
-- Used by GET /messages/mentions (is_read) and
-- PATCH /messages/mentions/{message_id}/read
-- ================================================================

CREATE TABLE IF NOT EXISTS message_mentions (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    message_id UUID NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    read_at TIMESTAMPTZ DEFAULT NULL,
    created_at TIMESTAMPTZ DEFAULT now(),
    UNIQUE(message_id, user_id)
);

-- Index for fast lookups by user
CREATE INDEX IF NOT EXISTS idx_message_mentions_user_id ON message_mentions(user_id);
CREATE INDEX IF NOT EXISTS idx_message_mentions_message_id ON message_mentions(message_id);

-- RLS
ALTER TABLE message_mentions ENABLE ROW LEVEL SECURITY;

-- Service role full access (backend uses service_role key)
DROP POLICY IF EXISTS "Service role full access" ON message_mentions;
CREATE POLICY "Service role full access" ON message_mentions
    FOR ALL
    USING (auth.role() = 'service_role')
    WITH CHECK (auth.role() = 'service_role');
