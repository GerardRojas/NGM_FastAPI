-- ============================================================
-- Unified in-app notifications feed
-- ============================================================
-- One row per "user X was tagged/mentioned in thing Y". Powers the dashboard
-- Mentions widget across ALL modules (messages, board/cell comments, estimator,
-- vault, ngm cam, ...). Each tagging point writes here via
-- api/services/notifications_feed.create_notification.
--
-- Read model:
--   * read_at NULL  -> unread (shown highlighted)
--   * read_at set   -> read (shown dimmed for a short window, then dropped)
-- Cleanup (see cleanup_notifications): delete unread older than 30 days and
-- read older than 7 days so the feed stays bounded.

CREATE TABLE IF NOT EXISTS notifications (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at     TIMESTAMPTZ DEFAULT now(),

    -- Recipient (the tagged/mentioned user)
    user_id        UUID NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,

    -- What kind of tag and where it came from
    type           TEXT NOT NULL,          -- 'mention_message' | 'mention_comment' | ...
    module         TEXT NOT NULL,          -- 'messages' | 'board' | 'expenses' | 'estimator' | 'vault' | ...

    -- Pointer back to the tagged thing + how to open it
    reference_type TEXT,                   -- 'message' | 'comment' | ...
    reference_id   TEXT,                   -- message_id / comment_id / record_id
    deep_link      TEXT,                   -- route to navigate to on click

    -- Who tagged the user + a short preview
    actor_id       UUID REFERENCES users(user_id) ON DELETE SET NULL,
    actor_name     TEXT,
    preview        TEXT,
    context        JSONB DEFAULT '{}'::jsonb,

    -- Read tracking
    read_at        TIMESTAMPTZ
);

-- Feed query: recipient's unread + recently-read, newest first.
CREATE INDEX IF NOT EXISTS idx_notifications_user_created
    ON notifications (user_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_notifications_user_unread
    ON notifications (user_id) WHERE read_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_notifications_created
    ON notifications (created_at DESC);


-- ============================================================
-- Cleanup helper: drop stale rows so the feed stays bounded.
--   * unread older than 30 days (nobody ever saw them)
--   * read older than 7 days (already actioned)
-- Call periodically (cron) via POST /notifications/inbox/cleanup.
-- ============================================================
CREATE OR REPLACE FUNCTION cleanup_notifications()
RETURNS integer AS $$
DECLARE
    deleted integer;
BEGIN
    WITH gone AS (
        DELETE FROM notifications
        WHERE (read_at IS NULL AND created_at < now() - interval '30 days')
           OR (read_at IS NOT NULL AND read_at < now() - interval '7 days')
        RETURNING 1
    )
    SELECT count(*) INTO deleted FROM gone;
    RETURN deleted;
END;
$$ LANGUAGE plpgsql;
