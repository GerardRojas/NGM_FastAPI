-- =============================================================================
-- operations_dashboard_views — saved filter presets for Operations Dashboard
-- =============================================================================
-- Each user can save named combinations of the Operations Dashboard filters
-- (project_ids, owner_ids, date_from/to, project_status, task_status) and mark
-- one of them as the default that auto-loads when they open the page.
--
-- filters is jsonb so the shape can evolve without a migration; the backend
-- normalizes it before persisting.
--
-- Idempotent. Run on staging, then prod (Supabase SQL editor).
-- Path: C:\Users\germa\Desktop\NGM_API\sql\operations_dashboard_views.sql
-- =============================================================================

CREATE TABLE IF NOT EXISTS operations_dashboard_views (
    view_id     uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     uuid NOT NULL,                    -- soft ref -> users.user_id
    name        text NOT NULL,
    filters     jsonb NOT NULL DEFAULT '{}'::jsonb,
    is_default  boolean NOT NULL DEFAULT false,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);

-- Case-insensitive uniqueness so "Weekly Review" and "weekly review" collide.
CREATE UNIQUE INDEX IF NOT EXISTS uq_odv_user_name
    ON operations_dashboard_views (user_id, lower(name));

-- At most one default view per user.
CREATE UNIQUE INDEX IF NOT EXISTS uq_odv_user_default
    ON operations_dashboard_views (user_id)
    WHERE is_default;

-- Fast "list my views" query.
CREATE INDEX IF NOT EXISTS idx_odv_user
    ON operations_dashboard_views (user_id, updated_at DESC);

-- updated_at touch trigger (local fn so we don't depend on portal_update_timestamp).
CREATE OR REPLACE FUNCTION odv_touch_updated_at() RETURNS trigger AS $$
BEGIN
    NEW.updated_at := now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_odv_updated ON operations_dashboard_views;
CREATE TRIGGER trg_odv_updated
    BEFORE UPDATE ON operations_dashboard_views
    FOR EACH ROW EXECUTE FUNCTION odv_touch_updated_at();

-- RLS — service_role full access (backend API); authenticated select-only.
-- Per-user scoping is enforced in the API layer (current_user.user_id),
-- not at the row level here.
ALTER TABLE operations_dashboard_views ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE tablename='operations_dashboard_views' AND policyname='odv_service_all'
    ) THEN
        CREATE POLICY odv_service_all ON operations_dashboard_views
            FOR ALL TO service_role USING (true) WITH CHECK (true);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE tablename='operations_dashboard_views' AND policyname='odv_auth_select'
    ) THEN
        CREATE POLICY odv_auth_select ON operations_dashboard_views
            FOR SELECT TO authenticated USING (true);
    END IF;
END $$;

-- VERIFICATION ----------------------------------------------------------------
-- select count(*) from operations_dashboard_views;
-- select view_id, user_id, name, is_default, filters, updated_at
--   from operations_dashboard_views
--   order by user_id, updated_at desc;
