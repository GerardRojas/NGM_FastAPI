-- Categories re-arch · user-configurable cost_types per subcategory
--
-- Today the chips shown on /categories and on the estimator subcategory picker
-- are DERIVED from account_category_map (whichever cost_types currently have
-- accounts mapped to this subcategory). That means you can't say "this
-- subcategory should accept Labor" before mapping a Labor account to it — the
-- chip simply doesn't appear.
--
-- This migration adds an authoritative `allowed_cost_types` column on
-- subcategories so the user can pre-declare which types each subcategory
-- accepts. The picker / categories page then drives chips off this column;
-- chips without a matching account_category_map row are flagged as orphans
-- (the existing "no account linked" amber state).
--
-- Backfill = whatever derived cost_types currently exist for each subcategory,
-- so the migration is loss-free: post-run, every subcategory keeps showing the
-- exact same chips it showed before.
--
-- Strictly additive, idempotent, fully reversible.
-- Path: NGM_API/sql/categories_rearch_subcategory_allowed_types.sql

BEGIN;

-- 1. Additive column — array of cost_type enum values (phase 1 enum).
ALTER TABLE subcategories
  ADD COLUMN IF NOT EXISTS allowed_cost_types cost_type[] NOT NULL DEFAULT '{}';

-- 2. Backfill from the overlay (idempotent — only touches rows whose current
--    array is empty). Sorts the array deterministically.
UPDATE subcategories s
SET allowed_cost_types = derived.arr
FROM (
  SELECT subcategory_id, ARRAY_AGG(DISTINCT cost_type ORDER BY cost_type) AS arr
  FROM account_category_map
  WHERE cost_type IS NOT NULL
  GROUP BY subcategory_id
) derived
WHERE s.id = derived.subcategory_id
  AND (s.allowed_cost_types IS NULL OR cardinality(s.allowed_cost_types) = 0);

-- 3. Sanity counts.
DO $$
DECLARE
  total int;
  with_types int;
  no_types int;
BEGIN
  SELECT COUNT(*) INTO total FROM subcategories;
  SELECT COUNT(*) INTO with_types FROM subcategories WHERE cardinality(allowed_cost_types) > 0;
  no_types := total - with_types;
  RAISE NOTICE 'subcategories: % total, % with allowed_cost_types, % empty (need user curation).', total, with_types, no_types;
END $$;

COMMIT;

-- Rollback (manual, if ever needed):
--   ALTER TABLE subcategories DROP COLUMN IF EXISTS allowed_cost_types;
