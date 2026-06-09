-- =============================================================================
-- NGM — pending migrations (unified). Run top-to-bottom in the Supabase SQL
-- editor. Idempotent: safe to run more than once. Run on STAGING first, verify,
-- then PROD.
--
-- Contains:
--   1. tasks multi-person columns      (fixes the 500 on /pipeline/tasks/my-tasks)
--   2. project_user_access             (external-user workspace access)
--   3. client_notification_prefs       (Connect email prefs — Phase 2)
--   4. portal_invoices                 (Connect billing — Phase 3)
--
-- Dependency: sections 2-4 assume client_portal_phase1.sql is already applied
-- (it created the portal_update_timestamp() trigger function + the clients table).
-- That migration is live in prod, so this holds; if a fresh DB errors on the
-- trigger in section 2, run client_portal_phase1.sql first.
-- =============================================================================


-- 1) ─────────────────────────────────────────────────────────────────────────
--    tasks: multi-person collaborators/managers (UUID[] alongside legacy cols)
-- -----------------------------------------------------------------------------
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS collaborators_ids UUID[];
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS managers_ids UUID[];

UPDATE tasks
SET collaborators_ids = ARRAY["Colaborators_id"]
WHERE "Colaborators_id" IS NOT NULL AND collaborators_ids IS NULL;

UPDATE tasks
SET managers_ids = ARRAY[manager::UUID]
WHERE manager IS NOT NULL AND managers_ids IS NULL;

COMMENT ON COLUMN tasks.collaborators_ids IS 'Array of collaborator user UUIDs';
COMMENT ON COLUMN tasks.managers_ids IS 'Array of manager user UUIDs';


-- 2) ─────────────────────────────────────────────────────────────────────────
--    project_user_access — workspace access for external users (Team Mgmt)
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS project_user_access (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         uuid NOT NULL,
    project_id      uuid NOT NULL,
    modules         jsonb NOT NULL DEFAULT '{}'::jsonb,
    granted_by      uuid NOT NULL,
    granted_at      timestamptz NOT NULL DEFAULT now(),
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_project_user_access ON project_user_access (user_id, project_id);
CREATE INDEX IF NOT EXISTS idx_pua_user ON project_user_access (user_id);

DROP TRIGGER IF EXISTS trg_pua_updated ON project_user_access;
CREATE TRIGGER trg_pua_updated
    BEFORE UPDATE ON project_user_access
    FOR EACH ROW EXECUTE FUNCTION portal_update_timestamp();

ALTER TABLE project_user_access ENABLE ROW LEVEL SECURITY;
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename='project_user_access' AND policyname='pua_service_all') THEN
        CREATE POLICY pua_service_all ON project_user_access FOR ALL TO service_role USING (true) WITH CHECK (true);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename='project_user_access' AND policyname='pua_auth_select') THEN
        CREATE POLICY pua_auth_select ON project_user_access FOR SELECT TO authenticated USING (true);
    END IF;
END $$;


-- 3) ─────────────────────────────────────────────────────────────────────────
--    client_notification_prefs — per-client portal email toggles (default-on)
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.client_notification_prefs (
    client_id     uuid PRIMARY KEY REFERENCES public.clients(client_id) ON DELETE CASCADE,
    new_message   boolean NOT NULL DEFAULT true,
    new_invoice   boolean NOT NULL DEFAULT true,
    share_digest  boolean NOT NULL DEFAULT true,
    weekly_update boolean NOT NULL DEFAULT true,
    updated_at    timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE public.client_notification_prefs ENABLE ROW LEVEL SECURITY;
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename='client_notification_prefs' AND policyname='cnp_service_all') THEN
        CREATE POLICY cnp_service_all ON public.client_notification_prefs FOR ALL TO service_role USING (true) WITH CHECK (true);
    END IF;
END $$;


-- 4) ─────────────────────────────────────────────────────────────────────────
--    portal_invoices — invoices shared with a client (wraps invoice_links)
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.portal_invoices (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id       uuid NOT NULL,
    client_id        uuid NOT NULL,
    invoice_link_id  uuid NOT NULL,
    caption          text,
    created_by       uuid,
    created_at       timestamptz NOT NULL DEFAULT now(),
    viewed_at        timestamptz
);

CREATE INDEX IF NOT EXISTS idx_portal_invoices_project ON public.portal_invoices (project_id);
CREATE INDEX IF NOT EXISTS idx_portal_invoices_client  ON public.portal_invoices (client_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_portal_invoices_link ON public.portal_invoices (invoice_link_id);

ALTER TABLE public.portal_invoices ENABLE ROW LEVEL SECURITY;
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename='portal_invoices' AND policyname='portal_invoices_service_all') THEN
        CREATE POLICY portal_invoices_service_all ON public.portal_invoices FOR ALL TO service_role USING (true) WITH CHECK (true);
    END IF;
END $$;

-- =============================================================================
-- Verification (optional):
--   select column_name from information_schema.columns
--     where table_name='tasks' and column_name in ('collaborators_ids','managers_ids');
--   select to_regclass('public.project_user_access'),
--          to_regclass('public.client_notification_prefs'),
--          to_regclass('public.portal_invoices');
-- =============================================================================
