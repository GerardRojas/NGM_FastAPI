-- ================================================================
-- Enable Supabase Realtime on message_reactions
-- ================================================================
-- Lets clients subscribe to live reaction changes so a reaction made in
-- the dashboard News widget shows up in the Messages view (and vice versa)
-- without a reload. Mirrors how public.tasks is already published for the
-- Pipeline realtime feed.
--
-- REPLICA IDENTITY FULL is required so DELETE events carry the row's
-- message_id in payload.old (clients refetch reactions for that message).
--
-- Idempotent. Run on staging, then prod.
-- ================================================================

ALTER TABLE public.message_reactions REPLICA IDENTITY FULL;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_publication_tables
        WHERE pubname = 'supabase_realtime'
          AND schemaname = 'public'
          AND tablename = 'message_reactions'
    ) THEN
        ALTER PUBLICATION supabase_realtime ADD TABLE public.message_reactions;
        RAISE NOTICE 'Added public.message_reactions to supabase_realtime';
    ELSE
        RAISE NOTICE 'public.message_reactions already in supabase_realtime';
    END IF;
END $$;

-- Verification
SELECT schemaname, tablename
FROM pg_publication_tables
WHERE pubname = 'supabase_realtime' AND tablename = 'message_reactions';
