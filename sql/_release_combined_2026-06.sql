-- #############################################################################
-- COMBINED RELEASE MIGRATION — Calendar + Workspace company scoping + Ops views
-- #############################################################################
-- Pega este script completo en el SQL editor de Supabase (staging primero,
-- luego prod). Todas las secciones son idempotentes (IF NOT EXISTS / guards),
-- así que re-correrlo no rompe nada. Orden con dependencias respetadas:
--   1-5  Calendar (tablas base -> fases -> menu item)
--   6-9  company_id en clients / vendors / build_manifests / sheet_templates
--   10   provision de presets por company (DESPUÉS de #9)
--   11   operations_dashboard_views (arregla el "Not Found" del Ops Dashboard)
-- (calendar_phase5_webhooks_and_conflict.sql está vacío — se omite.)
-- #############################################################################


-- #############################################################################
-- 1/11  create_calendar_events.sql
-- #############################################################################

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

CREATE INDEX IF NOT EXISTS idx_cea_user
    ON calendar_event_attendees (user_id);

-- updated_at trigger (reuses shared portal_update_timestamp; inline fallback).
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

-- RLS — service_role full access, authenticated read-only.
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


-- #############################################################################
-- 2/11  calendar_phase2_recurrence_reminders.sql
-- #############################################################################

ALTER TABLE calendar_events ADD COLUMN IF NOT EXISTS rrule text;
ALTER TABLE calendar_events ADD COLUMN IF NOT EXISTS rrule_until timestamptz;
ALTER TABLE calendar_events ADD COLUMN IF NOT EXISTS reminder_minutes integer;

CREATE INDEX IF NOT EXISTS idx_calendar_events_recurring
    ON calendar_events (rrule_until)
    WHERE rrule IS NOT NULL;


-- #############################################################################
-- 3/11  calendar_phase3_ics_and_reminders.sql
-- #############################################################################

-- 1. user_calendar_tokens — ICS feed subscription tokens
CREATE TABLE IF NOT EXISTS user_calendar_tokens (
    token        text PRIMARY KEY,                 -- url-safe random, set by API
    user_id      uuid NOT NULL,                    -- soft ref -> users.user_id
    label        text NOT NULL DEFAULT 'default',  -- human label (e.g. "Phone", "Outlook")
    created_at   timestamptz NOT NULL DEFAULT now(),
    last_used_at timestamptz
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_uct_user_label
    ON user_calendar_tokens (user_id, lower(label));
CREATE INDEX IF NOT EXISTS idx_uct_user
    ON user_calendar_tokens (user_id);

-- 2. calendar_reminder_log — cron dispatch idempotency
CREATE TABLE IF NOT EXISTS calendar_reminder_log (
    event_id        uuid NOT NULL REFERENCES calendar_events(event_id) ON DELETE CASCADE,
    occurrence_at   timestamptz NOT NULL,
    dispatched_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (event_id, occurrence_at)
);

CREATE INDEX IF NOT EXISTS idx_crl_dispatched
    ON calendar_reminder_log (dispatched_at);

ALTER TABLE user_calendar_tokens    ENABLE ROW LEVEL SECURITY;
ALTER TABLE calendar_reminder_log   ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies
                   WHERE tablename='user_calendar_tokens' AND policyname='uct_service_all') THEN
        CREATE POLICY uct_service_all ON user_calendar_tokens
            FOR ALL TO service_role USING (true) WITH CHECK (true);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies
                   WHERE tablename='calendar_reminder_log' AND policyname='crl_service_all') THEN
        CREATE POLICY crl_service_all ON calendar_reminder_log
            FOR ALL TO service_role USING (true) WITH CHECK (true);
    END IF;
END $$;


-- #############################################################################
-- 4/11  calendar_phase4_google_sync.sql
-- #############################################################################

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = 'public' AND table_name = 'calendar_events'
    ) THEN
        RAISE EXCEPTION 'Run create_calendar_events.sql first (Phase 1) — calendar_events must exist before Phase 4.';
    END IF;
END $$;

-- 1. google_calendar_tokens — per-user Google OAuth tokens + sync cursor
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

-- 2. calendar_sync_mappings — event_id <-> google_event_id pairing
CREATE TABLE IF NOT EXISTS calendar_sync_mappings (
    event_id              uuid PRIMARY KEY REFERENCES calendar_events(event_id) ON DELETE CASCADE,
    google_event_id       text NOT NULL,
    google_calendar_id    text NOT NULL DEFAULT 'primary',
    google_etag           text,
    sync_source           text NOT NULL DEFAULT 'local'    -- 'local'|'google'
        CHECK (sync_source IN ('local', 'google')),
    last_synced_at        timestamptz NOT NULL DEFAULT now(),
    last_local_update_at  timestamptz
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_csm_google_event
    ON calendar_sync_mappings (google_calendar_id, google_event_id);

CREATE INDEX IF NOT EXISTS idx_csm_synced
    ON calendar_sync_mappings (last_synced_at);

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


-- #############################################################################
-- 5/11  calendar_menu_item.sql  (sidebar + role permissions)
-- #############################################################################

-- 1) per-role permissions
INSERT INTO role_permissions (rol_id, module_key, module_name, module_url, can_view, can_edit, can_delete)
SELECT r.rol_id, 'calendar', 'Calendar', 'calendar',
    true,
    CASE WHEN r.rol_name IN ('CEO', 'COO', 'General Coordinator', 'Project Coordinator') THEN true ELSE false END,
    CASE WHEN r.rol_name IN ('CEO', 'COO') THEN true ELSE false END
FROM rols r
WHERE NOT EXISTS (
    SELECT 1 FROM role_permissions rp
    WHERE rp.rol_id = r.rol_id AND rp.module_key = 'calendar'
);

-- 2) menu item (General)
INSERT INTO menu_items (slug, item_name, icon_type, icon_text, category_id, "order")
SELECT 'calendar', 'Calendar', 'material', 'event',
       (SELECT id FROM public.menu_categories WHERE name = 'General' LIMIT 1),
       50
ON CONFLICT (slug) DO NOTHING;

-- 3) link role_permissions -> menu_item
UPDATE role_permissions rp
SET menu_item_id = mi.id
FROM menu_items mi
WHERE mi.slug = 'calendar'
  AND rp.module_key = 'calendar'
  AND (rp.menu_item_id IS NULL OR rp.menu_item_id <> mi.id);


-- #############################################################################
-- 6/11  add_company_id_to_clients.sql
-- #############################################################################

DO $$
BEGIN
    IF to_regclass('public.clients') IS NULL THEN
        RAISE NOTICE 'Skipping: public.clients does not exist.';
        RETURN;
    END IF;

    ALTER TABLE public.clients
        ADD COLUMN IF NOT EXISTS company_id UUID REFERENCES public.companies(id) ON DELETE SET NULL;

    CREATE INDEX IF NOT EXISTS idx_clients_company
        ON public.clients (company_id);

    COMMENT ON COLUMN public.clients.company_id
        IS 'Owning organization. NULL = shared / visible in all companies.';
END $$;


-- #############################################################################
-- 7/11  add_company_id_to_vendors.sql   (table is "Vendors" — capital V)
-- #############################################################################

DO $$
BEGIN
    IF to_regclass('public."Vendors"') IS NULL THEN
        RAISE NOTICE 'Skipping: public."Vendors" does not exist.';
        RETURN;
    END IF;

    ALTER TABLE public."Vendors"
        ADD COLUMN IF NOT EXISTS company_id UUID REFERENCES public.companies(id) ON DELETE SET NULL;

    CREATE INDEX IF NOT EXISTS idx_vendors_company
        ON public."Vendors" (company_id);

    COMMENT ON COLUMN public."Vendors".company_id
        IS 'Owning organization. NULL = shared / visible in all companies.';
END $$;


-- #############################################################################
-- 8/11  add_company_id_to_build_manifests.sql  (guarded — skips if absent)
-- #############################################################################

DO $$
BEGIN
    IF to_regclass('public.build_manifests') IS NULL THEN
        RAISE NOTICE 'Skipping: public.build_manifests does not exist (run build_manifests.sql first).';
        RETURN;
    END IF;

    ALTER TABLE public.build_manifests
        ADD COLUMN IF NOT EXISTS company_id UUID REFERENCES public.companies(id) ON DELETE SET NULL;

    CREATE INDEX IF NOT EXISTS idx_build_manifests_company
        ON public.build_manifests (company_id);

    COMMENT ON COLUMN public.build_manifests.company_id
        IS 'Owning organization. NULL = shared / visible in all companies.';
END $$;


-- #############################################################################
-- 9/11  add_company_id_to_sheet_templates.sql
-- #############################################################################

DO $$
BEGIN
    IF to_regclass('public.sheet_templates') IS NULL THEN
        RAISE NOTICE 'Skipping: public.sheet_templates does not exist.';
        RETURN;
    END IF;

    ALTER TABLE public.sheet_templates
        ADD COLUMN IF NOT EXISTS company_id UUID REFERENCES public.companies(id) ON DELETE SET NULL;

    CREATE INDEX IF NOT EXISTS idx_sheet_templates_company
        ON public.sheet_templates (company_id);

    COMMENT ON COLUMN public.sheet_templates.company_id
        IS 'Owning organization. NULL = shared / visible in all companies.';
END $$;


-- #############################################################################
-- 10/11  provision_company_sheet_templates.sql   (AFTER #9)
-- #############################################################################

DO $$
BEGIN
    IF to_regclass('public.sheet_templates') IS NULL OR to_regclass('public.companies') IS NULL THEN
        RAISE NOTICE 'Skipping: sheet_templates or companies table does not exist.';
        RETURN;
    END IF;

    INSERT INTO public.sheet_templates (name, theme, branding, view_config, is_default, is_preset, company_id)
    SELECT
        t.name,
        t.theme,
        jsonb_set(
            jsonb_set(coalesce(t.branding, '{}'::jsonb), '{companyName}', to_jsonb(c.name)),
            '{companyInfo}', to_jsonb(c.name)
        ),
        t.view_config,
        t.is_default,
        true,
        c.id
    FROM public.sheet_templates t
    CROSS JOIN public.companies c
    WHERE t.is_preset = true
      AND t.company_id IS NULL
      AND NOT EXISTS (
          SELECT 1 FROM public.sheet_templates x
          WHERE x.company_id = c.id AND x.name = t.name
      );
END $$;


-- #############################################################################
-- 11/11  operations_dashboard_views.sql   (fixes the Ops Dashboard "Not Found")
-- #############################################################################

CREATE TABLE IF NOT EXISTS operations_dashboard_views (
    view_id     uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     uuid NOT NULL,                    -- soft ref -> users.user_id
    name        text NOT NULL,
    filters     jsonb NOT NULL DEFAULT '{}'::jsonb,
    is_default  boolean NOT NULL DEFAULT false,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_odv_user_name
    ON operations_dashboard_views (user_id, lower(name));

CREATE UNIQUE INDEX IF NOT EXISTS uq_odv_user_default
    ON operations_dashboard_views (user_id)
    WHERE is_default;

CREATE INDEX IF NOT EXISTS idx_odv_user
    ON operations_dashboard_views (user_id, updated_at DESC);

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

-- #############################################################################
-- FIN — todas las secciones idempotentes. Verifica con los SELECTs comentados
-- en cada archivo original si quieres confirmar tablas/columnas creadas.
-- #############################################################################
