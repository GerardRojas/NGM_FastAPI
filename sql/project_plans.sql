-- =============================================================================
-- PROJECT PLANS — git-style plan/submittal tracking (planos)
-- -----------------------------------------------------------------------------
-- A "plan" is a named plan set for a project (e.g. "Architectural Set"). Each
-- plan has one or more BRANCHES (git-style: a default "main" plus optional
-- parallel branches for design options / review rounds). Each branch holds an
-- ordered list of REVISIONS (submittals) — one uploaded PDF per revision, with
-- a status and timeline dates. The PDF itself lives in the project's Vault
-- "Plans" folder; plan_revisions.file_id points at that vault file.
--
-- Vault badge: any vault file referenced by a plan_revision is "a plan" and is
-- badged in the Vault browser (lookup via plan_revisions.file_id).
--
-- Idempotent. Run on staging first, then prod.
-- =============================================================================

-- 1. Plan set (level 1) -------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.project_plans (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    -- The owning project (no FK: projects live in a separate table and we avoid
    -- coupling to its lifecycle, same pattern as photo_annotations.project_id).
    project_id  UUID NOT NULL,
    name        TEXT NOT NULL,
    discipline  TEXT,                                   -- optional: Architectural / Structural / MEP / Civil
    created_by  UUID REFERENCES public.users(user_id) ON DELETE SET NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 2. Branches (git-style; parent_branch_id = the branch it forked from) -------
CREATE TABLE IF NOT EXISTS public.plan_branches (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    plan_id          UUID NOT NULL REFERENCES public.project_plans(id) ON DELETE CASCADE,
    name             TEXT NOT NULL,                     -- e.g. "main", "City Round 2", "Option B"
    parent_branch_id UUID REFERENCES public.plan_branches(id) ON DELETE SET NULL,
    is_default       BOOLEAN NOT NULL DEFAULT FALSE,    -- the "main" branch
    created_by       UUID REFERENCES public.users(user_id) ON DELETE SET NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 3. Revisions / submittals (one Vault PDF per revision) ----------------------
CREATE TABLE IF NOT EXISTS public.plan_revisions (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    branch_id    UUID NOT NULL REFERENCES public.plan_branches(id) ON DELETE CASCADE,
    -- Vault file (the PDF) for this revision. No FK (vault soft-deletes); orphan
    -- links are harmless. This is what the Vault badge looks up.
    file_id      UUID NOT NULL,
    label        TEXT NOT NULL DEFAULT 'Rev 0',
    status       TEXT NOT NULL DEFAULT 'submitted'
                 CHECK (status IN ('draft','submitted','under_review','approved','revise_resubmit','superseded')),
    submitted_at TIMESTAMPTZ,
    reviewed_at  TIMESTAMPTZ,
    due_at       TIMESTAMPTZ,
    notes        TEXT,
    created_by   UUID REFERENCES public.users(user_id) ON DELETE SET NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Indexes ---------------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_project_plans_project ON public.project_plans(project_id);
CREATE INDEX IF NOT EXISTS idx_plan_branches_plan    ON public.plan_branches(plan_id);
CREATE INDEX IF NOT EXISTS idx_plan_branches_parent  ON public.plan_branches(parent_branch_id);
CREATE INDEX IF NOT EXISTS idx_plan_revisions_branch ON public.plan_revisions(branch_id);
CREATE INDEX IF NOT EXISTS idx_plan_revisions_file   ON public.plan_revisions(file_id);  -- Vault badge lookup

-- Auto-update updated_at on all three -----------------------------------------
CREATE OR REPLACE FUNCTION public.touch_project_plans_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DO $$
DECLARE t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY['project_plans','plan_branches','plan_revisions']
    LOOP
        EXECUTE format('DROP TRIGGER IF EXISTS trg_%s_updated ON public.%I', t, t);
        EXECUTE format(
            'CREATE TRIGGER trg_%s_updated BEFORE UPDATE ON public.%I '
            'FOR EACH ROW EXECUTE FUNCTION public.touch_project_plans_updated_at()', t, t);
    END LOOP;
END$$;

-- RLS: backend uses the service role; keep these off the anon key --------------
ALTER TABLE public.project_plans  ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.plan_branches  ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.plan_revisions ENABLE ROW LEVEL SECURITY;

DO $$
DECLARE t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY['project_plans','plan_branches','plan_revisions']
    LOOP
        EXECUTE format('DROP POLICY IF EXISTS "Service role full access" ON public.%I', t);
        EXECUTE format(
            'CREATE POLICY "Service role full access" ON public.%I FOR ALL '
            'USING (auth.role() = ''service_role'') WITH CHECK (auth.role() = ''service_role'')', t);
    END LOOP;
END$$;

COMMENT ON TABLE public.project_plans  IS 'A named plan set for a project (planos). Has branches.';
COMMENT ON TABLE public.plan_branches  IS 'Git-style branch of a plan (default main + parallel branches).';
COMMENT ON TABLE public.plan_revisions IS 'Submittal/revision in a branch; file_id -> Vault PDF.';

-- VERIFICATION ----------------------------------------------------------------
-- select p.name plan, b.name branch, r.label, r.status, r.file_id
--   from public.plan_revisions r
--   join public.plan_branches b on b.id = r.branch_id
--   join public.project_plans p on p.id = b.plan_id
--   order by p.name, b.name, r.created_at;
