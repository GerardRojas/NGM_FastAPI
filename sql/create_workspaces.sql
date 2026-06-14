-- =============================================================================
-- workspaces — first-class grouping layer for NGM Connect (Pillar B, additive)
-- =============================================================================
-- A workspace is a NAMED grouping of members (external_contacts) + projects.
-- It can be created EMPTY and filled progressively. This is ADDITIVE: the actual
-- per-(member, project) module grants stay in project_client_access /
-- project_user_access (the live portal read plane is untouched). Workspaces just
-- organize who + which projects belong together, so the team manages grants in
-- one place instead of a flat (entity, project) list.
--
--   workspace_members.external_type  'client' -> external_contacts (tier client)
--                                    'user'   -> external_contacts (tier team_member)
--   workspace_members.external_id == external_contacts.id (== client_id / user_id)
--
-- Idempotent. Run on prod (no dependency on the backend; safe to run first).
-- Path: C:\Users\germa\Desktop\NGM_API\sql\create_workspaces.sql
-- =============================================================================

CREATE TABLE IF NOT EXISTS public.workspaces (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name        text NOT NULL,
    company_id  uuid REFERENCES public.companies(id) ON DELETE SET NULL,
    created_by  uuid,
    created_at  timestamptz DEFAULT now(),
    updated_at  timestamptz DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_workspaces_company ON public.workspaces (company_id);

CREATE TABLE IF NOT EXISTS public.workspace_members (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id  uuid NOT NULL REFERENCES public.workspaces(id) ON DELETE CASCADE,
    external_type text NOT NULL CHECK (external_type IN ('client', 'user')),
    external_id   uuid NOT NULL,
    added_at      timestamptz DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_workspace_members
    ON public.workspace_members (workspace_id, external_type, external_id);
CREATE INDEX IF NOT EXISTS idx_workspace_members_ws ON public.workspace_members (workspace_id);

CREATE TABLE IF NOT EXISTS public.workspace_projects (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id  uuid NOT NULL REFERENCES public.workspaces(id) ON DELETE CASCADE,
    project_id    uuid NOT NULL,
    added_at      timestamptz DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_workspace_projects
    ON public.workspace_projects (workspace_id, project_id);
CREATE INDEX IF NOT EXISTS idx_workspace_projects_ws ON public.workspace_projects (workspace_id);

-- =============================================================================
-- VERIFICATION (uncomment)
-- =============================================================================
-- select count(*) from public.workspaces;
-- =============================================================================
