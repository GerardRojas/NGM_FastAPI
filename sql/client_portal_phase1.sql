-- =============================================================================
-- Client Portal — Phase 1 schema  (NGM Connect + Clients)
-- =============================================================================
-- Adds the tables that power the client portal:
--   1. portal_shares          — the publish registry (default-deny: nothing is
--                               client-visible unless an active row exists here)
--   2. project_client_access  — which client sees which project, and which
--                               portal modules are enabled for that pairing
--   3. client_invites         — magic-link onboarding for client accounts
--   4. users.account_type / users.client_id — distinguishes internal staff from
--                               external client accounts
--
-- Conventions follow the existing schema (invoice_links.sql, vault_schema.sql):
--   * uuid PKs via gen_random_uuid()
--   * SOFT references only — plain uuid columns, NO hard FK constraints to
--     externally-created tables (projects / clients / users live in Supabase,
--     not in this folder). App-level integrity, same as vault_files.project_id.
--   * RLS enabled: service_role full access (backend API) + authenticated read.
--
-- Idempotent. Run on staging, then prod (Supabase SQL editor).
-- NOTE: assumes clients.client_id and users.user_id are uuid. If client_id is
--       bigint/text in your DB, adjust the users.client_id column type below.
-- =============================================================================


-- =============================================================================
-- 1. portal_shares — the publish registry (heart of default-deny control)
-- =============================================================================
-- One active row = one item the team has explicitly published to the client
-- portal for a given project. Unpublish = set is_active=false (keep the audit
-- trail). item_id points at a row in the source module (photo/plan/file/...).

CREATE TABLE IF NOT EXISTS portal_shares (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id      uuid NOT NULL,                    -- soft ref -> projects.project_id
    item_type       text NOT NULL,                    -- 'photo'|'plan_revision'|'vault_file'|'milestone'|'phase'
    item_id         uuid NOT NULL,                    -- id of the published resource
    client_caption  text,                             -- optional client-facing label/override
    shared_by       uuid NOT NULL,                    -- soft ref -> users.user_id
    shared_at       timestamptz NOT NULL DEFAULT now(),
    is_active       boolean NOT NULL DEFAULT true,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now()
);

-- Fast "what is published for this project" lookups (the portal read path).
CREATE INDEX IF NOT EXISTS idx_portal_shares_project
    ON portal_shares (project_id, item_type)
    WHERE is_active = true;

CREATE INDEX IF NOT EXISTS idx_portal_shares_item
    ON portal_shares (item_type, item_id)
    WHERE is_active = true;

-- An item can only be actively shared once per project (re-publishing reuses it).
CREATE UNIQUE INDEX IF NOT EXISTS uq_portal_shares_active_item
    ON portal_shares (project_id, item_type, item_id)
    WHERE is_active = true;


-- =============================================================================
-- 2. project_client_access — client <-> project + enabled portal modules
-- =============================================================================
-- Configured from the Clients profile. Feeds both the portal data scope and the
-- workspace dropdown in NGM Connect. `modules` is a flag bag, e.g.
--   {"overview":true,"photos":true,"plans":true,"timeline":true,
--    "documents":false,"messages":false}

CREATE TABLE IF NOT EXISTS project_client_access (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id       uuid NOT NULL,                    -- soft ref -> clients.client_id
    project_id      uuid NOT NULL,                    -- soft ref -> projects.project_id
    modules         jsonb NOT NULL DEFAULT '{}'::jsonb,
    granted_by      uuid NOT NULL,                    -- soft ref -> users.user_id
    granted_at      timestamptz NOT NULL DEFAULT now(),
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now()
);

-- One access row per (client, project).
CREATE UNIQUE INDEX IF NOT EXISTS uq_project_client_access
    ON project_client_access (client_id, project_id);

-- "Which projects can this client see" — the portal entry query.
CREATE INDEX IF NOT EXISTS idx_pca_client
    ON project_client_access (client_id);


-- =============================================================================
-- 3. client_invites — magic-link onboarding for client accounts
-- =============================================================================
-- Mirrors the invoice_links token discipline: a signed JWT (short TTL) stored
-- with status tracking. Accepting an invite provisions/links a client account.

CREATE TABLE IF NOT EXISTS client_invites (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id       uuid NOT NULL,                    -- soft ref -> clients.client_id
    email           text NOT NULL,
    token           text NOT NULL,                    -- signed JWT
    status          text NOT NULL DEFAULT 'pending',  -- 'pending'|'accepted'|'expired'|'revoked'
    created_by      uuid NOT NULL,                    -- soft ref -> users.user_id (staff)
    expires_at      timestamptz NOT NULL,
    accepted_at     timestamptz,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_client_invites_token
    ON client_invites (token);

CREATE INDEX IF NOT EXISTS idx_client_invites_client
    ON client_invites (client_id);

CREATE INDEX IF NOT EXISTS idx_client_invites_pending
    ON client_invites (status)
    WHERE status = 'pending';


-- =============================================================================
-- 4. users — external client account support
-- =============================================================================
-- account_type distinguishes staff from external clients. client_id links a
-- client account back to its clients row (NULL for internal staff). The JWT
-- carries both so the /portal router can resolve scope from the token alone.

ALTER TABLE users ADD COLUMN IF NOT EXISTS account_type text NOT NULL DEFAULT 'internal';
ALTER TABLE users ADD COLUMN IF NOT EXISTS client_id uuid;  -- soft ref -> clients.client_id

-- account_type must be one of the known values (guarded for idempotency).
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'users_account_type_check'
    ) THEN
        ALTER TABLE users
            ADD CONSTRAINT users_account_type_check
            CHECK (account_type IN ('internal', 'client'));
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_users_client
    ON users (client_id)
    WHERE client_id IS NOT NULL;


-- =============================================================================
-- Shared updated_at trigger for the new tables
-- =============================================================================
CREATE OR REPLACE FUNCTION portal_update_timestamp()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_portal_shares_updated ON portal_shares;
CREATE TRIGGER trg_portal_shares_updated
    BEFORE UPDATE ON portal_shares
    FOR EACH ROW EXECUTE FUNCTION portal_update_timestamp();

DROP TRIGGER IF EXISTS trg_pca_updated ON project_client_access;
CREATE TRIGGER trg_pca_updated
    BEFORE UPDATE ON project_client_access
    FOR EACH ROW EXECUTE FUNCTION portal_update_timestamp();

DROP TRIGGER IF EXISTS trg_client_invites_updated ON client_invites;
CREATE TRIGGER trg_client_invites_updated
    BEFORE UPDATE ON client_invites
    FOR EACH ROW EXECUTE FUNCTION portal_update_timestamp();


-- =============================================================================
-- RLS — service_role full access (backend API), authenticated read-only.
-- The hardened /portal scoping is enforced in the API layer, not via RLS.
-- =============================================================================
ALTER TABLE portal_shares          ENABLE ROW LEVEL SECURITY;
ALTER TABLE project_client_access  ENABLE ROW LEVEL SECURITY;
ALTER TABLE client_invites         ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename='portal_shares' AND policyname='portal_shares_service_all') THEN
        CREATE POLICY portal_shares_service_all ON portal_shares FOR ALL TO service_role USING (true) WITH CHECK (true);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename='portal_shares' AND policyname='portal_shares_auth_select') THEN
        CREATE POLICY portal_shares_auth_select ON portal_shares FOR SELECT TO authenticated USING (true);
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename='project_client_access' AND policyname='pca_service_all') THEN
        CREATE POLICY pca_service_all ON project_client_access FOR ALL TO service_role USING (true) WITH CHECK (true);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename='project_client_access' AND policyname='pca_auth_select') THEN
        CREATE POLICY pca_auth_select ON project_client_access FOR SELECT TO authenticated USING (true);
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename='client_invites' AND policyname='client_invites_service_all') THEN
        CREATE POLICY client_invites_service_all ON client_invites FOR ALL TO service_role USING (true) WITH CHECK (true);
    END IF;
END $$;


-- =============================================================================
-- VERIFICATION
-- =============================================================================
-- select column_name, data_type from information_schema.columns
--   where table_name='users' and column_name in ('account_type','client_id');
-- select count(*) from portal_shares;
-- select count(*) from project_client_access;
-- select count(*) from client_invites;
