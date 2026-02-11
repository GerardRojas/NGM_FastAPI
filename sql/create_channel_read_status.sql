-- ========================================
-- Channel Read Status - Per-user unread tracking
-- ========================================
-- Tracks when each user last read each channel so we can
-- compute unread message counts efficiently.

CREATE TABLE IF NOT EXISTS channel_read_status (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    channel_key TEXT NOT NULL,          -- e.g. "project_general:uuid" or "direct:uuid"
    last_read_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(user_id, channel_key)
);

-- Index for: get all channels a user has read
CREATE INDEX IF NOT EXISTS idx_channel_read_status_user
    ON channel_read_status(user_id);

-- Index for: efficient unread count join on channel_key
CREATE INDEX IF NOT EXISTS idx_channel_read_status_key_time
    ON channel_read_status(channel_key, last_read_at);

-- RLS
ALTER TABLE channel_read_status ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Service role full access" ON channel_read_status;
CREATE POLICY "Service role full access" ON channel_read_status
    FOR ALL
    USING (auth.role() = 'service_role')
    WITH CHECK (auth.role() = 'service_role');

-- ========================================
-- Postgres function: get unread counts for all channels
-- ========================================
-- Single query: counts messages newer than last_read_at per channel.
-- Excludes own messages, deleted messages, and thread replies.

CREATE OR REPLACE FUNCTION get_unread_counts(p_user_id UUID)
RETURNS TABLE(channel_key TEXT, unread_count BIGINT) AS $$
BEGIN
    RETURN QUERY
    SELECT
        m.channel_key,
        COUNT(*)::BIGINT AS unread_count
    FROM messages m
    LEFT JOIN channel_read_status crs
        ON crs.channel_key = m.channel_key
        AND crs.user_id = p_user_id
    WHERE m.created_at > COALESCE(crs.last_read_at, '1970-01-01'::timestamptz)
      AND m.user_id != p_user_id
      AND m.is_deleted = false
      AND m.reply_to_id IS NULL
    GROUP BY m.channel_key
    HAVING COUNT(*) > 0;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;
