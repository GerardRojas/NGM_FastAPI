-- =============================================================================
-- ESTIMATE → BUDGET LINK — provenance columns on budgets_qbo so a budget row
-- knows which estimate/branch produced it (the reverse of the branch's
-- promoted_* stamp, which lives in the estimate manifest JSON in Storage).
--
-- Run in the Supabase SQL editor: STAGING first, verify, then PROD.
-- Idempotent and additive — safe to re-run. Run BEFORE deploying the backend
-- (budgets.py /import now writes source_estimate_id / source_branch_id).
--
-- Pairs with the estimator's "Push to Budget" action (approved branch ->
-- pick project -> POST /budgets/import -> POST .../branches/{id}/mark-promoted).
-- Path: C:\Users\germa\Desktop\NGM_API\sql\estimate_budget_link.sql
-- =============================================================================

ALTER TABLE public.budgets_qbo
    ADD COLUMN IF NOT EXISTS source_estimate_id text;

ALTER TABLE public.budgets_qbo
    ADD COLUMN IF NOT EXISTS source_branch_id text;

-- Find a project's budget rows by their source estimate (e.g. "show the budget
-- that came from estimate X", or to re-sync on a new revision).
CREATE INDEX IF NOT EXISTS idx_budgets_qbo_source_estimate
    ON public.budgets_qbo (source_estimate_id);


-- =============================================================================
-- VERIFICATION (optional)
-- -----------------------------------------------------------------------------
-- select column_name from information_schema.columns
--  where table_name = 'budgets_qbo'
--    and column_name in ('source_estimate_id','source_branch_id');
--
-- -- Budgets produced by the estimator:
-- select ngm_project_id, source_estimate_id, source_branch_id, count(*)
--   from public.budgets_qbo
--  where source_estimate_id is not null
--  group by 1,2,3;
-- =============================================================================
