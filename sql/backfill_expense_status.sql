-- ============================================================================
-- BACKFILL: expenses_manual_COGS.status  (align legacy auth_status -> status)
-- ----------------------------------------------------------------------------
-- The reports now define "authorized" canonically as status = 'auth' (P&L and
-- Budget vs Actuals, on-screen and PDF). Legacy rows were authorized via the
-- boolean auth_status=true while status stayed NULL. Without this backfill those
-- rows would silently drop out of EVERY report once the code change deploys.
--
-- This sets status='auth' for rows authorized by the old boolean that have no
-- status yet, so historical totals are preserved exactly. Rows already in
-- 'pending'/'review'/'auth' are left untouched (status is the source of truth).
--
-- RUN THIS BEFORE deploying the fetch_expenses / isAuthorized change.
-- Run on staging first, verify totals match, then prod (Supabase SQL editor).
-- Path: C:\Users\germa\Desktop\NGM_API\sql\backfill_expense_status.sql
-- ============================================================================

-- 0) PREVIEW — how many rows will change, and their summed amount -------------
SELECT count(*)                                   AS rows_to_backfill,
       round(SUM(COALESCE("Amount", 0))::numeric, 2) AS amount_to_backfill
FROM "expenses_manual_COGS"
WHERE auth_status IS TRUE
  AND (status IS NULL OR status = '');

-- 1) BACKFILL ----------------------------------------------------------------
UPDATE "expenses_manual_COGS"
SET status = 'auth'
WHERE auth_status IS TRUE
  AND (status IS NULL OR status = '');

-- 2) VERIFY — no authorized-but-statusless rows remain -----------------------
-- SELECT count(*) AS leftover_null_status_authorized
--   FROM "expenses_manual_COGS"
--  WHERE auth_status IS TRUE AND (status IS NULL OR status = '');
--
-- Optional sanity check: rows where the two fields still disagree (review/decide
-- case by case — these are genuine data inconsistencies, not legacy nulls):
-- SELECT status, auth_status, count(*)
--   FROM "expenses_manual_COGS"
--  WHERE (status = 'auth') <> (auth_status IS TRUE)
--  GROUP BY status, auth_status ORDER BY 3 DESC;
