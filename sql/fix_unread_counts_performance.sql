-- ================================================================
-- Fix: unread-counts performance + ensure all dependencies exist
-- ================================================================
-- Run this in Supabase SQL Editor to fix the /messages/unread-counts 500 errors.
-- All statements are idempotent (safe to re-run).

-- 1. Ensure is_deleted column exists
ALTER TABLE messages ADD COLUMN IF NOT EXISTS is_deleted BOOLEAN DEFAULT FALSE;

-- 2. Ensure channel_key generated column exists
-- NOTE: If this fails with "column already exists", that's fine - skip it.
-- Generated columns can't use IF NOT EXISTS, so wrap in DO block:
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'messages' AND column_name = 'channel_key'
    ) THEN
        ALTER TABLE messages
        ADD COLUMN channel_key TEXT
        GENERATED ALWAYS AS (
            CASE
                WHEN channel_type IN ('custom', 'direct', 'group')
                    THEN channel_type || ':' || COALESCE(channel_id::text, '')
                ELSE
                    channel_type || ':' || COALESCE(project_id::text, '')
            END
        ) STORED;
    END IF;
END $$;

-- 3. Create indexes for efficient unread count queries
CREATE INDEX IF NOT EXISTS idx_messages_channel_key
    ON messages(channel_key);

CREATE INDEX IF NOT EXISTS idx_messages_channel_key_created
    ON messages(channel_key, created_at);

-- Composite index covering the WHERE clause filters
CREATE INDEX IF NOT EXISTS idx_messages_unread_scan
    ON messages(channel_key, created_at, user_id)
    WHERE is_deleted = false AND reply_to_id IS NULL;

-- 4. Ensure channel_read_status table exists
CREATE TABLE IF NOT EXISTS channel_read_status (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    channel_key TEXT NOT NULL,
    last_read_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(user_id, channel_key)
);

CREATE INDEX IF NOT EXISTS idx_channel_read_status_user
    ON channel_read_status(user_id);

CREATE INDEX IF NOT EXISTS idx_channel_read_status_key_time
    ON channel_read_status(channel_key, last_read_at);

-- 5. Recreate the RPC function (optimized version)
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

-- 6. RLS for channel_read_status
ALTER TABLE channel_read_status ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Service role full access" ON channel_read_status;
CREATE POLICY "Service role full access" ON channel_read_status
    FOR ALL
    USING (auth.role() = 'service_role')
    WITH CHECK (auth.role() = 'service_role');
