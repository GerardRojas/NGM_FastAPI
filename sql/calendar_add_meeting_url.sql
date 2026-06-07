-- =============================================================================
-- Calendar — video meeting link (Option A: Google Meet)
-- =============================================================================
-- Adds the columns the API writes when an event opts into a video call:
--   * meeting_url      — the join URL (Google Meet `hangoutLink`)
--   * meeting_provider — which service produced it (today only 'google_meet')
--
-- The Meet link is created by Google when the event is pushed with
-- conferenceData (see api/services/google_calendar.py push_event create_meet),
-- so this is only ever populated for users who have connected Google Calendar.
-- A plain pasted link still lives in `location`; this column is the
-- system-generated, one-click "Join" target.
--
-- Idempotent — safe to run multiple times. Run on staging first, then prod.
-- Path: C:\Users\germa\Desktop\NGM_API\sql\calendar_add_meeting_url.sql
-- =============================================================================

ALTER TABLE calendar_events
    ADD COLUMN IF NOT EXISTS meeting_url text;

ALTER TABLE calendar_events
    ADD COLUMN IF NOT EXISTS meeting_provider text;

-- Verify:
-- select event_id, title, meeting_provider, meeting_url
--   from calendar_events where meeting_url is not null limit 5;
