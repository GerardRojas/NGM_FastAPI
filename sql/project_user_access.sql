-- =============================================================================
-- project_user_access — workspace access for EXTERNAL USERS (Team Management)
-- =============================================================================
-- Parallel to project_client_access, but keyed by users.user_id instead of
-- clients.client_id. External users (users.is_external=true) like
-- subcontractors, architects, or vendor reps live in Team Management; this
-- table is how the staff grants them per-project workspace access and decides
-- which workspace modules they can see.
--
-- Two parallel tables (vs one generalized project_external_access) keeps each
-- domain's integrity clean: the existing client flow is unchanged, the user
-- flow has its own join surface, and Phase 3 can decide whether to merge them.
--
-- Idempotent. Run on staging, then prod (Supabase SQL editor).
-- Path: C:\Users\germa\Desktop\NGM_API\sql\project_user_access.sql
-- =============================================================================

CREATE TABLE IF NOT EXISTS project_user_access (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         uuid NOT NULL,                    -- soft ref -> users.user_id
    project_id      uuid NOT NULL,                    -- soft ref -> projects.project_id
    modules         jsonb NOT NULL DEFAULT '{}'::jsonb,
    granted_by      uuid NOT NULL,                    -- soft ref -> users.user_id
    granted_at      timestamptz NOT NULL DEFAULT now(),
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now()
);

-- One access row per (user, project).
CREATE UNIQUE INDEX IF NOT EXISTS uq_project_user_access
    ON project_user_access (user_id, project_id);

-- "Which projects can this external user see" — the workspace entry query.
CREATE INDEX IF NOT EXISTS idx_pua_user
    ON project_user_access (user_id);

-- Reuse the shared portal_update_timestamp trigger function created by
-- client_portal_phase1.sql so updated_at stays in sync on edits.
DROP TRIGGER IF EXISTS trg_pua_updated ON project_user_access;
CREATE TRIGGER trg_pua_updated
    BEFORE UPDATE ON project_user_access
    FOR EACH ROW EXECUTE FUNCTION portal_update_timestamp();

-- RLS — service_role full access (the backend API); authenticated read-only.
-- Hard scoping is enforced in the API layer, not here.
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

-- VERIFICATION ----------------------------------------------------------------
-- select count(*) from project_user_access;
-- select pua.user_id, u.user_name, pua.project_id, p.project_name, pua.modules
--   from project_user_access pua
--   left join users u on u.user_id = pua.user_id
--   left join projects p on p.project_id = pua.project_id
--   order by u.user_name;
