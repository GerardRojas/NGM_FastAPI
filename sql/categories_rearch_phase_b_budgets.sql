-- Categories re-arch · Phase B (budgets side)
--
-- Adds the same (subcategory_id, cost_type) classification we already dual-write
-- on expenses to the budgets table, so Budget vs Actuals can reconcile by the
-- new axis when both sides have it AND keep working by account-name match when
-- one (or both) sides don't — strictly additive, fully reversible.
--
-- Run order: idempotent. Safe to re-run; backfill only touches rows whose
-- subcategory_id is still NULL.
--
-- Path: NGM_API/sql/categories_rearch_phase_b_budgets.sql

BEGIN;

-- 1. Additive columns on budgets_qbo. cost_type uses the enum created in
--    categories_rearch_phase1.sql. category_id is denormalized for fast joins
--    (the canonical FK is subcategory_id -> categories via subcategories).
ALTER TABLE budgets_qbo
  ADD COLUMN IF NOT EXISTS category_id    uuid REFERENCES categories(id) ON DELETE SET NULL,
  ADD COLUMN IF NOT EXISTS subcategory_id uuid REFERENCES subcategories(id) ON DELETE SET NULL,
  ADD COLUMN IF NOT EXISTS cost_type      cost_type;

-- Index the JOIN axis BVA will use most often (project_id + classification).
CREATE INDEX IF NOT EXISTS idx_budgets_qbo_subcat_costtype
  ON budgets_qbo(ngm_project_id, subcategory_id, cost_type)
  WHERE subcategory_id IS NOT NULL;

-- 2. Backfill from existing internal account_id via the overlay. QBO-imported
--    budgets carry the QuickBooks account_id (different id space) — those stay
--    NULL until a future QBO-mapping pass; estimator-saved budgets that already
--    persist account_id can be classified now.
UPDATE budgets_qbo b
SET
  subcategory_id = m.subcategory_id,
  cost_type      = m.cost_type,
  category_id    = sub.category_id
FROM account_category_map m
JOIN subcategories sub ON sub.id = m.subcategory_id
WHERE b.account_id IS NOT NULL
  AND b.account_id = m.account_id
  AND b.subcategory_id IS NULL;

-- 3. Sanity counts (for the operator running this script).
DO $$
DECLARE
  total int;
  classified int;
BEGIN
  SELECT COUNT(*) INTO total FROM budgets_qbo;
  SELECT COUNT(*) INTO classified FROM budgets_qbo WHERE subcategory_id IS NOT NULL;
  RAISE NOTICE 'budgets_qbo: % total, % classified after backfill (delta from previous run).', total, classified;
END $$;

COMMIT;

-- Rollback (manual, if ever needed):
--   ALTER TABLE budgets_qbo DROP COLUMN IF EXISTS category_id;
--   ALTER TABLE budgets_qbo DROP COLUMN IF EXISTS subcategory_id;
--   ALTER TABLE budgets_qbo DROP COLUMN IF EXISTS cost_type;
--   DROP INDEX IF EXISTS idx_budgets_qbo_subcat_costtype;
