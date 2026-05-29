-- =============================================================================
-- Categories re-arch · RESYNC OVERLAY RUNBOOK
-- =============================================================================
-- Propagates account_category_map changes to historic expenses_manual_COGS
-- AND budgets_qbo rows. New rows pick up overlay changes automatically via
-- dual-write; historic rows keep the classification they had when first
-- inserted, so a remap in /categories doesn't reflect retroactively without
-- running this script.
--
-- WHEN TO RUN
--   - After changing the account -> subcategory mapping for an account in /categories
--   - After changing the cost_type of a mapping
--   - After renaming accounts."Name" (the BVA name-bridge depends on it)
--   - NOT NEEDED for: renaming a subcategory, moving a subcategory between
--     categories — those reflect automatically because BVA reads
--     subcategories.name and subcategories.category_id via JOIN.
--
-- HOW TO RUN
--   1) Run block A (PREVIEW). It writes nothing; shows what would change.
--   2) If the from/to columns look right, run block B (APPLY). Transactional.
--   3) Re-run block A. Should return 0 rows.
--
-- SCOPE
--   - Single account: replace '<ACCOUNT_ID>' with the UUID you remapped.
--   - Bulk sweep across all drift: delete the two lines marked "-- BULK".
--
-- Verified clean against prod on 2026-05-29 after Phase B migration
-- (0 drift). Re-run after every catalog change.
-- =============================================================================

-- ----------------------------------------------------------------------------
-- A) PREVIEW — diff overlay vs historic data (no writes)
-- ----------------------------------------------------------------------------
SELECT
  'expenses'                                AS source,
  e.account_id::text                        AS account_id,
  a."Name"                                  AS account_name,
  COUNT(*)                                  AS rows_to_update,
  SUM(e."Amount")::numeric(14,2)            AS total_amount,
  STRING_AGG(DISTINCT s_old.name, ', ')     AS current_subcat,
  STRING_AGG(DISTINCT s_new.name, ', ')     AS new_subcat,
  STRING_AGG(DISTINCT e.cost_type::text, ', ') AS current_costtype,
  STRING_AGG(DISTINCT m.cost_type::text, ', ') AS new_costtype
FROM "expenses_manual_COGS" e
JOIN account_category_map  m  ON m.account_id = e.account_id
JOIN accounts              a  ON a.account_id = e.account_id
LEFT JOIN subcategories s_old ON s_old.id = e.subcategory_id
LEFT JOIN subcategories s_new ON s_new.id = m.subcategory_id
WHERE e.account_id IS NOT NULL
  AND (e.subcategory_id IS DISTINCT FROM m.subcategory_id
       OR e.cost_type   IS DISTINCT FROM m.cost_type)
  AND e.account_id = '<ACCOUNT_ID>'  -- BULK: delete this line to sweep all accounts
GROUP BY e.account_id, a."Name"

UNION ALL

SELECT
  'budgets',
  m.account_id::text,
  a."Name",
  COUNT(*),
  SUM(b.amount_sum)::numeric(14,2),
  STRING_AGG(DISTINCT s_old.name, ', '),
  STRING_AGG(DISTINCT s_new.name, ', '),
  STRING_AGG(DISTINCT b.cost_type::text, ', '),
  STRING_AGG(DISTINCT m.cost_type::text, ', ')
FROM budgets_qbo b
JOIN account_category_map  m  ON m.account_id::text = b.account_id
JOIN accounts              a  ON a.account_id = m.account_id
LEFT JOIN subcategories s_old ON s_old.id = b.subcategory_id
LEFT JOIN subcategories s_new ON s_new.id = m.subcategory_id
WHERE b.account_id IS NOT NULL
  AND (b.subcategory_id IS DISTINCT FROM m.subcategory_id
       OR b.cost_type   IS DISTINCT FROM m.cost_type)
  AND b.account_id = '<ACCOUNT_ID>'  -- BULK: delete this line to sweep all accounts
GROUP BY m.account_id, a."Name";


-- ----------------------------------------------------------------------------
-- B) APPLY — only run after the preview looks right
-- ----------------------------------------------------------------------------
BEGIN;

-- B.1) Expenses
UPDATE "expenses_manual_COGS" e
SET subcategory_id = m.subcategory_id,
    cost_type      = m.cost_type
FROM account_category_map m
WHERE e.account_id IS NOT NULL
  AND e.account_id = m.account_id
  AND (e.subcategory_id IS DISTINCT FROM m.subcategory_id
       OR e.cost_type   IS DISTINCT FROM m.cost_type)
  AND e.account_id = '<ACCOUNT_ID>';  -- BULK: delete this line to sweep all accounts

-- B.2) Budgets (keeps BVA reconciling on both sides)
UPDATE budgets_qbo b
SET subcategory_id = m.subcategory_id,
    cost_type      = m.cost_type,
    category_id    = sub.category_id
FROM account_category_map m
JOIN subcategories sub ON sub.id = m.subcategory_id
WHERE b.account_id IS NOT NULL
  AND b.account_id = m.account_id::text
  AND (b.subcategory_id IS DISTINCT FROM m.subcategory_id
       OR b.cost_type   IS DISTINCT FROM m.cost_type)
  AND b.account_id = '<ACCOUNT_ID>';  -- BULK: delete this line to sweep all accounts

COMMIT;

-- ----------------------------------------------------------------------------
-- C) VERIFY — re-run block A. Should return 0 rows.
-- ----------------------------------------------------------------------------
