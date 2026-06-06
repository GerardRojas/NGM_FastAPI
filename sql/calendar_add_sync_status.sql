-- Calendar fix: add the `sync_status` column the API writes/reads.
--
-- The API (api/services/google_calendar.py + api/routers/calendar.py) sets and
-- selects calendar_sync_mappings.sync_status ('synced'|'pending'|'conflict'|
-- 'push_failed'), but Phase 4's table never created the column. With it missing,
-- _fetch_sync_mappings' SELECT errors and list_events fails once any event
-- exists. This migration aligns the schema with the code.
--
-- Idempotent — safe to run multiple times.

ALTER TABLE calendar_sync_mappings
    ADD COLUMN IF NOT EXISTS sync_status text NOT NULL DEFAULT 'synced';

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'csm_sync_status_chk'
    ) THEN
        ALTER TABLE calendar_sync_mappings
            ADD CONSTRAINT csm_sync_status_chk
            CHECK (sync_status IN ('synced', 'pending', 'conflict', 'push_failed'));
    END IF;
END $$;

-- Verify:
-- select event_id, sync_status, sync_source from calendar_sync_mappings limit 5;
