-- =============================================================================
-- Calendar — Phase 1 schema (calendar_events + calendar_event_attendees)
-- =============================================================================
-- Backing tables for the in-house calendar module. Phase 0 surfaces existing
-- pipeline tasks + project milestones as a read-only overlay; Phase 1 adds a
-- dedicated event store so the team can schedule meetings, site visits, etc.
--
-- Conventions follow client_portal_phase1.sql / project_user_access.sql:
--   * uuid PKs via gen_random_uuid()
--   * SOFT references only — plain uuid columns (NO hard FK constraints to the
--     externally-managed projects/companies/users tables). App-level integrity.
--   * Reuse the shared `portal_update_timestamp()` trigger function for
--     updated_at maintenance (created in client_portal_phase1.sql).
--   * RLS enabled — service_role full access, authenticated read-only.
--   * Hard scoping (visibility=project / private) is enforced in the API layer.
--
-- Idempotent. Run on staging first, then prod (Supabase SQL editor).
-- Path: C:\Users\germa\Desktop\NGM_API\sql\create_calendar_events.sql
-- =============================================================================


-- =============================================================================
-- 1. calendar_events — the event store
-- =============================================================================
CREATE TABLE IF NOT EXISTS calendar_events (
    event_id      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    title         text NOT NULL,
    description   text,
    location      text,
    start_at      timestamptz NOT NULL,
    end_at        timestamptz NOT NULL,
    all_day       boolean NOT NULL DEFAULT false,
    color         text,                                 -- optional hex e.g. '#3b82f6'
    project_id    uuid,                                 -- soft ref -> projects.project_id
    company_id    uuid,                                 -- soft ref -> companies.company_id
    created_by    uuid NOT NULL,                        -- soft ref -> users.user_id
    visibility    text NOT NULL DEFAULT 'team'          -- 'team' | 'private' | 'project'
        CHECK (visibility IN ('team', 'private', 'project')),
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT calendar_events_range CHECK (end_at >= start_at)
);

-- Range scan: "give me events overlapping [from, to]".
CREATE INDEX IF NOT EXISTS idx_calendar_events_range
    ON calendar_events (start_at, end_at);

CREATE INDEX IF NOT EXISTS idx_calendar_events_project
    ON calendar_events (project_id)
    WHERE project_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_calendar_events_creator
    ON calendar_events (created_by);


-- =============================================================================
-- 2. calendar_event_attendees — invitation list + RSVP status
-- =============================================================================
CREATE TABLE IF NOT EXISTS calendar_event_attendees (
    event_id     uuid NOT NULL REFERENCES calendar_events(event_id) ON DELETE CASCADE,
    user_id      uuid NOT NULL,                         -- soft ref -> users.user_id
    status       text NOT NULL DEFAULT 'invited'        -- invited|accepted|declined|tentative
        CHECK (status IN ('invited', 'accepted', 'declined', 'tentative')),
    responded_at timestamptz,
    created_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (event_id, user_id)
);

-- "Which events is this user in" — the per-user calendar query.
CREATE INDEX IF NOT EXISTS idx_cea_user
    ON calendar_event_attendees (user_id);


-- =============================================================================
-- updated_at trigger — reuses the shared portal_update_timestamp() function
-- defined by client_portal_phase1.sql. Fall back to inline definition in case
-- this migration runs before that one (defensive idempotency).
-- =============================================================================
CREATE OR REPLACE FUNCTION portal_update_timestamp()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_calendar_events_updated ON calendar_events;
CREATE TRIGGER trg_calendar_events_updated
    BEFORE UPDATE ON calendar_events
    FOR EACH ROW EXECUTE FUNCTION portal_update_timestamp();


-- =============================================================================
-- RLS — service_role full access (backend API), authenticated read-only.
-- =============================================================================
ALTER TABLE calendar_events           ENABLE ROW LEVEL SECURITY;
ALTER TABLE calendar_event_attendees  ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename='calendar_events' AND policyname='calendar_events_service_all') THEN
        CREATE POLICY calendar_events_service_all ON calendar_events FOR ALL TO service_role USING (true) WITH CHECK (true);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename='calendar_events' AND policyname='calendar_events_auth_select') THEN
        CREATE POLICY calendar_events_auth_select ON calendar_events FOR SELECT TO authenticated USING (true);
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename='calendar_event_attendees' AND policyname='cea_service_all') THEN
        CREATE POLICY cea_service_all ON calendar_event_attendees FOR ALL TO service_role USING (true) WITH CHECK (true);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename='calendar_event_attendees' AND policyname='cea_auth_select') THEN
        CREATE POLICY cea_auth_select ON calendar_event_attendees FOR SELECT TO authenticated USING (true);
    END IF;
END $$;


-- =============================================================================
-- VERIFICATION
-- =============================================================================
-- select count(*) from calendar_events;
-- select count(*) from calendar_event_attendees;
-- select column_name, data_type from information_schema.columns
--   where table_name='calendar_events' order by ordinal_position;
