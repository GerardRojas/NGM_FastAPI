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

-- 2. Backfill (pass 1): rows whose account_id matches an internal account
--    directly — works for estimator-saved budgets (they ship the internal
--    accounts.account_id from the picker).
--    `budgets_qbo.account_id` is text (it also stores QBO ids in another id
--    space), while `account_category_map.account_id` is uuid — cast both sides
--    to text so the comparison is safe and never errors on non-UUID QBO ids.
UPDATE budgets_qbo b
SET
  subcategory_id = m.subcategory_id,
  cost_type      = m.cost_type,
  category_id    = sub.category_id
FROM account_category_map m
JOIN subcategories sub ON sub.id = m.subcategory_id
WHERE b.account_id IS NOT NULL
  AND b.account_id = m.account_id::text
  AND b.subcategory_id IS NULL;

-- 3. Backfill (pass 2): name-based fallback for QBO-imported budgets, whose
--    account_id lives in QuickBooks's id space (different from internal
--    accounts.account_id). The CTE de-duplicates so a name collision in the
--    accounts table can't make this UPDATE join multiple rows.
WITH name_to_overlay AS (
  SELECT DISTINCT ON (LOWER(TRIM(a."Name")))
    LOWER(TRIM(a."Name")) AS norm_name,
    m.subcategory_id,
    m.cost_type,
    sub.category_id
  FROM accounts a
  JOIN account_category_map m ON m.account_id = a.account_id
  JOIN subcategories sub ON sub.id = m.subcategory_id
  ORDER BY LOWER(TRIM(a."Name")), a.account_id
)
UPDATE budgets_qbo b
SET
  subcategory_id = n.subcategory_id,
  cost_type      = n.cost_type,
  category_id    = n.category_id
FROM name_to_overlay n
WHERE b.subcategory_id IS NULL
  AND b.account_name IS NOT NULL
  AND LOWER(TRIM(b.account_name)) = n.norm_name;

-- 4. Sanity counts (for the operator running this script).
DO $$
DECLARE
  total int;
  classified int;
  by_id int;
  by_name int;
BEGIN
  SELECT COUNT(*) INTO total FROM budgets_qbo;
  SELECT COUNT(*) INTO classified FROM budgets_qbo WHERE subcategory_id IS NOT NULL;
  SELECT COUNT(*) INTO by_id FROM budgets_qbo b JOIN account_category_map m ON m.account_id::text = b.account_id WHERE b.subcategory_id IS NOT NULL;
  by_name := classified - by_id;
  RAISE NOTICE 'budgets_qbo: % total, % classified (% via account_id, % via account_name).', total, classified, by_id, by_name;
END $$;

COMMIT;

-- Rollback (manual, if ever needed):
--   ALTER TABLE budgets_qbo DROP COLUMN IF EXISTS category_id;
--   ALTER TABLE budgets_qbo DROP COLUMN IF EXISTS subcategory_id;
--   ALTER TABLE budgets_qbo DROP COLUMN IF EXISTS cost_type;
--   DROP INDEX IF EXISTS idx_budgets_qbo_subcat_costtype;
