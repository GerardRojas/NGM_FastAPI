-- =============================================================================
-- workspace_shares — member-scoped, PROJECT-INDEPENDENT shares (NGM Connect, C)
-- =============================================================================
-- portal_shares is keyed on project_id NOT NULL. But some things shared with a
-- client/member aren't tied to a project — e.g. an Estimate Budget (carátula),
-- whose estimate may or may not be promoted to a project. This table shares an
-- item DIRECTLY to a workspace member (external_contacts), no project required.
-- When a member has an active share of a given item_type, that module surfaces
-- in their workspace showing only the shared item(s).
--
--   external_type 'client' -> external_contacts (tier client)
--   external_type 'user'   -> external_contacts (tier team_member)
--   external_id  == external_contacts.id (== client_id / user_id)
--   item_type 'caratula'   -> an estimate budget: (estimate_id, branch_id)
--
-- estimate_id / branch_id are TEXT (estimates live as storage folders, not uuids).
-- Idempotent. Run on prod before the backend that consumes it.
-- Path: C:\Users\germa\Desktop\NGM_API\sql\create_workspace_shares.sql
-- =============================================================================

CREATE TABLE IF NOT EXISTS public.workspace_shares (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    external_type text NOT NULL CHECK (external_type IN ('client', 'user')),
    external_id   uuid NOT NULL,
    item_type     text NOT NULL,            -- 'caratula' (extensible)
    estimate_id   text,                     -- carátula ref
    branch_id     text,                     -- carátula ref
    caption       text,
    shared_by     uuid,
    is_active     boolean NOT NULL DEFAULT true,
    shared_at     timestamptz DEFAULT now(),
    created_at    timestamptz DEFAULT now()
);

-- One active share per (member, item_type, estimate, branch). COALESCE keeps the
-- partial unique index valid when estimate_id/branch_id are null (other types).
CREATE UNIQUE INDEX IF NOT EXISTS uq_workspace_shares_active
    ON public.workspace_shares (
        external_type, external_id, item_type,
        COALESCE(estimate_id, ''), COALESCE(branch_id, '')
    )
    WHERE is_active = true;

CREATE INDEX IF NOT EXISTS idx_workspace_shares_member
    ON public.workspace_shares (external_type, external_id) WHERE is_active = true;
CREATE INDEX IF NOT EXISTS idx_workspace_shares_item
    ON public.workspace_shares (item_type, estimate_id, branch_id) WHERE is_active = true;

-- =============================================================================
-- VERIFICATION (uncomment)
-- =============================================================================
-- select item_type, count(*) from public.workspace_shares where is_active group by item_type;
-- =============================================================================
