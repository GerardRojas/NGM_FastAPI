-- =============================================================================
-- TAKEOFF PLANS — plan_type (Floor Plan, Footings, Title 24…) + status.
--
-- plan_type: a free-text label (with UI suggestions) to organize plans.
-- status:    'draft'  = loaded in the estimator (not yet official)
--            'official' = promoted to the project on approval (versioned record).
-- See estimator/PLANS_TAKEOFF_PLAN.md.
--
-- Idempotent and additive. Run on STAGING first, verify, then PROD.
-- Run BEFORE deploying the backend (create/list/patch now use these columns).
-- Path: C:\Users\germa\Desktop\NGM_API\sql\takeoff_plan_type_status.sql
-- =============================================================================

ALTER TABLE public.takeoff_plans
    ADD COLUMN IF NOT EXISTS plan_type text;

ALTER TABLE public.takeoff_plans
    ADD COLUMN IF NOT EXISTS status text NOT NULL DEFAULT 'draft';

-- VERIFICATION
-- select id, filename, plan_type, status from public.takeoff_plans order by created_at desc limit 20;
-- =============================================================================
