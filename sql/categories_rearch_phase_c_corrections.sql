-- Categories re-arch · Phase C (corrections side)
--
-- Andrew's correction-learning loop currently stores account_id only, so when
-- two distinct accounts map to the same (subcategory_id, cost_type) the system
-- can't consolidate user feedback. This migration adds the same classification
-- triple we already carry on expenses and budgets to the corrections table,
-- backfills via the overlay, and updates the auto-capture trigger so future
-- corrections record both axes natively.
--
-- Strictly additive, idempotent, fully reversible.
-- Path: NGM_API/sql/categories_rearch_phase_c_corrections.sql

BEGIN;

-- 1. Additive columns. Cost_type uses the enum from phase 1; subcategory FKs
--    SET NULL on delete so deleting a subcategory doesn't blow away history.
ALTER TABLE categorization_corrections
  ADD COLUMN IF NOT EXISTS original_subcategory_id  uuid REFERENCES subcategories(id) ON DELETE SET NULL,
  ADD COLUMN IF NOT EXISTS original_cost_type       cost_type,
  ADD COLUMN IF NOT EXISTS corrected_subcategory_id uuid REFERENCES subcategories(id) ON DELETE SET NULL,
  ADD COLUMN IF NOT EXISTS corrected_cost_type      cost_type;

CREATE INDEX IF NOT EXISTS idx_cat_corrections_corrected_subcat
  ON categorization_corrections(corrected_subcategory_id, corrected_cost_type)
  WHERE corrected_subcategory_id IS NOT NULL;

-- 2. Backfill from the overlay (idempotent — only touches NULL rows).
--    Two passes so original_* and corrected_* are independent (a row may have
--    overlay for one side but not the other if a mapping was deleted later).
UPDATE categorization_corrections cc
SET original_subcategory_id = m.subcategory_id,
    original_cost_type      = m.cost_type
FROM account_category_map m
WHERE cc.original_account_id IS NOT NULL
  AND cc.original_account_id = m.account_id
  AND cc.original_subcategory_id IS NULL;

UPDATE categorization_corrections cc
SET corrected_subcategory_id = m.subcategory_id,
    corrected_cost_type      = m.cost_type
FROM account_category_map m
WHERE cc.corrected_account_id IS NOT NULL
  AND cc.corrected_account_id = m.account_id
  AND cc.corrected_subcategory_id IS NULL;

-- 3. Rewrite the auto-capture trigger so newly-logged corrections include the
--    triple natively. Replaces the function (overrides without dropping the
--    trigger that uses it).
CREATE OR REPLACE FUNCTION log_category_correction()
RETURNS TRIGGER AS $$
DECLARE
    proj_id UUID;
    stage TEXT;
    orig_account_name TEXT;
    new_account_name TEXT;
    orig_sub UUID;
    orig_ct  cost_type;
    new_sub  UUID;
    new_ct   cost_type;
BEGIN
    IF OLD.account_id IS DISTINCT FROM NEW.account_id THEN
        SELECT project INTO proj_id FROM "expenses_manual_COGS" WHERE expense_id = NEW.expense_id;

        IF proj_id IS NOT NULL THEN
            stage := 'General';

            SELECT "Name" INTO orig_account_name FROM accounts WHERE account_id = OLD.account_id;
            SELECT "Name" INTO new_account_name  FROM accounts WHERE account_id = NEW.account_id;

            -- Phase C: also resolve the classification triple via the overlay
            -- so future learning can run on (subcategory_id, cost_type).
            SELECT subcategory_id, cost_type INTO orig_sub, orig_ct
              FROM account_category_map WHERE account_id = OLD.account_id;
            SELECT subcategory_id, cost_type INTO new_sub, new_ct
              FROM account_category_map WHERE account_id = NEW.account_id;

            INSERT INTO categorization_corrections (
                project_id, expense_id, description, construction_stage,
                original_account_id, original_account_name,
                original_subcategory_id, original_cost_type,
                corrected_account_id, corrected_account_name,
                corrected_subcategory_id, corrected_cost_type,
                user_id
            ) VALUES (
                proj_id, NEW.expense_id, NEW."LineDescription",
                COALESCE(stage, 'General'),
                OLD.account_id, orig_account_name, orig_sub, orig_ct,
                NEW.account_id, new_account_name, new_sub, new_ct,
                NEW.updated_by
            );
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- 4. Sanity counts.
DO $$
DECLARE
  total int;
  with_corrected_triple int;
  with_original_triple int;
BEGIN
  SELECT COUNT(*) INTO total FROM categorization_corrections;
  SELECT COUNT(*) INTO with_corrected_triple FROM categorization_corrections WHERE corrected_subcategory_id IS NOT NULL;
  SELECT COUNT(*) INTO with_original_triple FROM categorization_corrections WHERE original_subcategory_id IS NOT NULL;
  RAISE NOTICE 'categorization_corrections: % total, % w/ corrected triple, % w/ original triple.', total, with_corrected_triple, with_original_triple;
END $$;

COMMIT;

-- Rollback (manual, if ever needed):
--   ALTER TABLE categorization_corrections DROP COLUMN IF EXISTS original_subcategory_id;
--   ALTER TABLE categorization_corrections DROP COLUMN IF EXISTS original_cost_type;
--   ALTER TABLE categorization_corrections DROP COLUMN IF EXISTS corrected_subcategory_id;
--   ALTER TABLE categorization_corrections DROP COLUMN IF EXISTS corrected_cost_type;
--   DROP INDEX IF EXISTS idx_cat_corrections_corrected_subcat;
--   -- Old trigger function is replaced; to restore prior behavior re-run
--   -- sql/categorization_improvements.sql section 6.
