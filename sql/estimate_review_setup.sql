-- =============================================================================
-- ESTIMATE REVIEW — unified setup for the "send a branch to review" feature.
-- Run this whole file in the Supabase SQL editor: STAGING first, verify, then
-- PROD. Everything is IDEMPOTENT and additive, so it is safe to re-run.
--
-- Run BEFORE deploying the backend (the new code reads
-- role_permissions.can_review_estimates). Reviewers must refresh their session
-- (log out / in) so the cached permissions map picks up the new flag.
--
-- Contains:
--   1. role_permissions.can_review_estimates  (+ seed CEO/COO)   — the role permission
--   2. role_permission_audit                  (shared audit; created if missing)
--   3. tasks.managers_ids / collaborators_ids (review-task assignment dependency)
--   4. automation_settings 'estimate_review'  (duty config: enable + person reviewer)
--
-- Supersedes the split files estimate_reviewer_setup.sql + seed_duty_estimate_review.sql.
-- Path: C:\Users\germa\Desktop\NGM_API\sql\estimate_review_setup.sql
-- =============================================================================


-- 1) ─────────────────────────────────────────────────────────────────────────
--    can_review_estimates — per-role permission (meaningful on the 'estimator'
--    module row only). CEO/COO are always reviewers (locked on in the backend).
-- -----------------------------------------------------------------------------
ALTER TABLE public.role_permissions
    ADD COLUMN IF NOT EXISTS can_review_estimates boolean NOT NULL DEFAULT false;

UPDATE public.role_permissions rp
SET can_review_estimates = true
FROM public.rols r
WHERE rp.rol_id = r.rol_id
  AND rp.module_key = 'estimator'
  AND r.rol_name IN ('CEO', 'COO')
  AND rp.can_review_estimates IS DISTINCT FROM true;


-- 2) ─────────────────────────────────────────────────────────────────────────
--    role_permission_audit — who/when/what for permission changes outside the
--    generic grid (shared with can_authorize). Created if a prior migration
--    hasn't already. Writes are best-effort in code.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.role_permission_audit (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    rol_id        text,
    rol_name      text,
    module_key    text NOT NULL,
    field         text NOT NULL,              -- e.g. 'can_review_estimates'
    old_value     boolean,
    new_value     boolean,
    actor_user_id text,
    actor_name    text,
    actor_role    text,
    source        text NOT NULL DEFAULT 'roles_ui',
    created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_role_permission_audit_rol
    ON public.role_permission_audit (rol_id);
CREATE INDEX IF NOT EXISTS idx_role_permission_audit_created
    ON public.role_permission_audit (created_at DESC);


-- 3) ─────────────────────────────────────────────────────────────────────────
--    tasks multi-person columns — the review task is assigned via managers_ids[].
--    Safe no-op if a prior migration (_run_pending_2026_06.sql §1) already added
--    them. The legacy-column backfill lives in that migration, not here.
-- -----------------------------------------------------------------------------
ALTER TABLE public.tasks ADD COLUMN IF NOT EXISTS managers_ids UUID[];
ALTER TABLE public.tasks ADD COLUMN IF NOT EXISTS collaborators_ids UUID[];


-- 4) ─────────────────────────────────────────────────────────────────────────
--    automation_settings 'estimate_review' — registers the duty so it shows in
--    the Responsibilities catalog, where an admin enables it and assigns a
--    specific PERSON reviewer (default_manager_id) or a role (responsible_role_id),
--    on top of the role-based reviewers from part 1. Event-driven (not scheduled):
--    the task is created by create_estimate_review_task() in pipeline.py.
-- -----------------------------------------------------------------------------
INSERT INTO public.automation_settings
    (automation_type, display_name, is_enabled, default_priority, config)
VALUES
    ('estimate_review', 'Estimate Review', true, 3, '{"reviewer_user_ids": []}'::jsonb)
ON CONFLICT (automation_type) DO NOTHING;


-- =============================================================================
-- VERIFICATION (optional)
-- -----------------------------------------------------------------------------
-- -- Roles that can review estimates:
-- select r.rol_name, rp.can_review_estimates
--   from public.role_permissions rp
--   join public.rols r on r.rol_id = rp.rol_id
--  where rp.module_key = 'estimator'
--  order by r.rol_name;
--
-- -- The duty row:
-- select automation_type, display_name, is_enabled, default_manager_id, config
--   from public.automation_settings where automation_type = 'estimate_review';
--
-- -- Task columns:
-- select column_name from information_schema.columns
--  where table_name = 'tasks' and column_name in ('managers_ids','collaborators_ids');
-- =============================================================================
