-- =============================================================================
-- Calendar — Phase 4 schema (Google Calendar bidirectional sync)
-- =============================================================================
-- Two tables back the per-user Google Calendar integration:
--
--   1. google_calendar_tokens   — one row per connected user (OAuth refresh +
--                                  access tokens, plus the syncToken cursor for
--                                  incremental pull-sync)
--   2. calendar_sync_mappings   — one row per (event_id, google_event_id)
--                                  pairing. Carries the Google etag so push
--                                  updates use If-Match for optimistic
--                                  concurrency, and last_synced_at so the
--                                  background reconciler can spot drift.
--
-- All access mediated by service_role; tokens never reach the authenticated
-- client (no RLS select policy on tokens).
--
-- DEPENDENCIES — run this first if you haven't already:
--   * sql/create_calendar_events.sql        (Phase 1 — creates calendar_events;
--                                            Phase 4's calendar_sync_mappings has
--                                            a FK to it).
--
-- Idempotent. Run on staging first, then prod (Supabase SQL editor).
-- Path: C:\Users\germa\Desktop\NGM_API\sql\calendar_phase4_google_sync.sql
-- =============================================================================

-- Fail fast with a clear message if Phase 1 hasn't been applied yet.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = 'public' AND table_name = 'calendar_events'
    ) THEN
        RAISE EXCEPTION 'Run sql/create_calendar_events.sql first (Phase 1) — calendar_events must exist before Phase 4 can reference it.';
    END IF;
END $$;


-- =============================================================================
-- 1. google_calendar_tokens — per-user Google OAuth tokens + sync cursor
-- =============================================================================
-- access_token expires in ~1h; refresh_token is long-lived and used to mint
-- new access tokens. sync_token is Google's opaque incremental cursor for
-- list-events; we store it after every successful pull so the next pull is
-- a delta (per https://developers.google.com/calendar/api/guides/sync).

CREATE TABLE IF NOT EXISTS google_calendar_tokens (
    user_id           uuid PRIMARY KEY,                -- soft ref -> users.user_id
    google_user_email text,                             -- the Google account email (for display)
    calendar_id       text NOT NULL DEFAULT 'primary',  -- which Google calendar to sync
    access_token      text NOT NULL,
    refresh_token     text NOT NULL,
    token_expires_at  timestamptz NOT NULL,
    scope             text,
    sync_token        text,                             -- opaque Google cursor; NULL until first full sync
    last_synced_at    timestamptz,
    connected_at      timestamptz NOT NULL DEFAULT now(),
    updated_at        timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_gct_email
    ON google_calendar_tokens (google_user_email);


-- =============================================================================
-- 2. calendar_sync_mappings — event_id <-> google_event_id pairing
-- =============================================================================
-- One row per (local event, Google event). Two callers care about the etag:
--   * push:  PATCH /events/{id}  with If-Match: <etag>  (optimistic concurrency)
--   * pull:  if Google's returned etag differs from ours, the row changed
--
-- last_local_update_at vs last_synced_at lets the reconciler tell stale rows
-- (event was edited locally after the last push) from clean rows.

CREATE TABLE IF NOT EXISTS calendar_sync_mappings (
    event_id              uuid PRIMARY KEY REFERENCES calendar_events(event_id) ON DELETE CASCADE,
    google_event_id       text NOT NULL,
    google_calendar_id    text NOT NULL DEFAULT 'primary',
    google_etag           text,
    sync_source           text NOT NULL DEFAULT 'local'    -- 'local'|'google'; who originated the event
        CHECK (sync_source IN ('local', 'google')),
    last_synced_at        timestamptz NOT NULL DEFAULT now(),
    last_local_update_at  timestamptz
);

-- Reverse lookup (Google webhook / pull-sync needs google_event_id -> local).
CREATE UNIQUE INDEX IF NOT EXISTS uq_csm_google_event
    ON calendar_sync_mappings (google_calendar_id, google_event_id);

CREATE INDEX IF NOT EXISTS idx_csm_synced
    ON calendar_sync_mappings (last_synced_at);


-- =============================================================================
-- updated_at trigger for google_calendar_tokens (reuses portal_update_timestamp
-- defined by client_portal_phase1.sql; fall back to inline def for idempotency).
-- =============================================================================
CREATE OR REPLACE FUNCTION portal_update_timestamp()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_gct_updated ON google_calendar_tokens;
CREATE TRIGGER trg_gct_updated
    BEFORE UPDATE ON google_calendar_tokens
    FOR EACH ROW EXECUTE FUNCTION portal_update_timestamp();


-- =============================================================================
-- RLS — service_role full access (backend API). No authenticated select on
-- tokens; the API mediates "is connected?" via /calendar/google/status.
-- =============================================================================
ALTER TABLE google_calendar_tokens     ENABLE ROW LEVEL SECURITY;
ALTER TABLE calendar_sync_mappings     ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies
                   WHERE tablename='google_calendar_tokens' AND policyname='gct_service_all') THEN
        CREATE POLICY gct_service_all ON google_calendar_tokens
            FOR ALL TO service_role USING (true) WITH CHECK (true);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies
                   WHERE tablename='calendar_sync_mappings' AND policyname='csm_service_all') THEN
        CREATE POLICY csm_service_all ON calendar_sync_mappings
            FOR ALL TO service_role USING (true) WITH CHECK (true);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies
                   WHERE tablename='calendar_sync_mappings' AND policyname='csm_auth_select') THEN
        CREATE POLICY csm_auth_select ON calendar_sync_mappings
            FOR SELECT TO authenticated USING (true);
    END IF;
END $$;


-- =============================================================================
-- VERIFICATION
-- =============================================================================
-- select count(*) from google_calendar_tokens;
-- select count(*) from calendar_sync_mappings;
-- select user_id, google_user_email, calendar_id, last_synced_at
--   from google_calendar_tokens order by connected_at desc;
