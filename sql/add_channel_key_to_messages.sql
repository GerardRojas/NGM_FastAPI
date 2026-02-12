-- ================================================================
-- Messages: Add channel_key generated column
-- ================================================================
-- This column is required by get_unread_counts() RPC function.
-- It's a computed column that combines channel_type with either
-- channel_id (for custom/direct/group) or project_id (for projects).
-- ================================================================

-- Add channel_key as a GENERATED ALWAYS column
ALTER TABLE messages
ADD COLUMN IF NOT EXISTS channel_key TEXT
GENERATED ALWAYS AS (
    CASE
        WHEN channel_type IN ('custom', 'direct', 'group')
            THEN channel_type || ':' || COALESCE(channel_id::text, '')
        ELSE
            channel_type || ':' || COALESCE(project_id::text, '')
    END
) STORED;

-- Create index for efficient joins in get_unread_counts
CREATE INDEX IF NOT EXISTS idx_messages_channel_key
    ON messages(channel_key);

-- Create composite index for the unread query
CREATE INDEX IF NOT EXISTS idx_messages_channel_key_created
    ON messages(channel_key, created_at);
