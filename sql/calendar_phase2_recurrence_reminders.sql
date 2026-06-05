-- =============================================================================
-- Calendar — Phase 2 schema additions (recurrence + reminders)
-- =============================================================================
-- Adds three columns to calendar_events:
--   * rrule            — recurrence rule, RRULE-lite subset (see below)
--   * rrule_until      — last possible occurrence, indexed for fast filtering
--   * reminder_minutes — minutes before start_at to fire a reminder (NULL = off)
--
-- RRULE-lite supported by our in-house expander (no third-party lib):
--   FREQ=DAILY|WEEKLY|MONTHLY
--   INTERVAL=N            (defaults to 1)
--   BYDAY=MO,TU,WE,...    (WEEKLY only; comma-separated)
--   UNTIL=YYYYMMDD'T'HHMMSS'Z'   (mirrored to rrule_until on the row)
--   COUNT=N               (alternative to UNTIL; not used by the UI yet)
--
-- Reminders in Phase 2: the value is stored, and attendees get an immediate
-- "invited to event" notification on create. Scheduled "X minutes before"
-- dispatch is deferred to Phase 3 (needs a cron worker; see CALENDAR_PLAN.md).
--
-- Idempotent. Run on staging first, then prod (Supabase SQL editor).
-- Path: C:\Users\germa\Desktop\NGM_API\sql\calendar_phase2_recurrence_reminders.sql
-- =============================================================================

ALTER TABLE calendar_events ADD COLUMN IF NOT EXISTS rrule text;
ALTER TABLE calendar_events ADD COLUMN IF NOT EXISTS rrule_until timestamptz;
ALTER TABLE calendar_events ADD COLUMN IF NOT EXISTS reminder_minutes integer;

-- Range filter for recurring events: "which series might fire in [from, to]".
-- One-off events still use idx_calendar_events_range (start_at, end_at).
CREATE INDEX IF NOT EXISTS idx_calendar_events_recurring
    ON calendar_events (rrule_until)
    WHERE rrule IS NOT NULL;


-- =============================================================================
-- VERIFICATION
-- =============================================================================
-- select column_name, data_type from information_schema.columns
--   where table_name='calendar_events' and column_name in ('rrule','rrule_until','reminder_minutes')
--   order by column_name;
--
-- select event_id, title, rrule, rrule_until, reminder_minutes
--   from calendar_events where rrule is not null order by start_at desc limit 20;
