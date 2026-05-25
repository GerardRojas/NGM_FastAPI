-- ================================================================
-- Enable Supabase Realtime on messages
-- ================================================================
-- Lets the Messages view subscribe to live message inserts/updates/deletes
-- instead of polling every few seconds. Clients subscribe with no server-side
-- filter (the channel_key is a generated column Realtime can't filter on) and
-- derive the channel from each row, refetching the active thread to pick up the
-- joined author/avatar fields the WAL payload doesn't carry.
--
-- REPLICA IDENTITY FULL is required so DELETE events carry the row's
-- channel_type/channel_id/project_id in payload.old (clients need them to know
-- which channel a delete belongs to).
--
-- Mirrors enable_realtime_message_reactions.sql. Idempotent. Run on staging,
-- then prod.
-- ================================================================

ALTER TABLE public.messages REPLICA IDENTITY FULL;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_publication_tables
        WHERE pubname = 'supabase_realtime'
          AND schemaname = 'public'
          AND tablename = 'messages'
    ) THEN
        ALTER PUBLICATION supabase_realtime ADD TABLE public.messages;
        RAISE NOTICE 'Added public.messages to supabase_realtime';
    ELSE
        RAISE NOTICE 'public.messages already in supabase_realtime';
    END IF;
END $$;

-- Verification
SELECT schemaname, tablename
FROM pg_publication_tables
WHERE pubname = 'supabase_realtime' AND tablename = 'messages';
