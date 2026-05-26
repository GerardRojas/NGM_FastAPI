-- ============================================================================
-- BUDGET VS ACTUALS — close the category coverage gap for prior projects
-- ----------------------------------------------------------------------------
-- After wiring budgets into the new Category -> Subcategory hierarchy, 98.6% of
-- the imported QBO budget ($) already bridged to a real category by account
-- name. This migration closes the remaining ~1.4% (4 budget account names that
-- did not reach a category), so prior projects contrast fully.
--
-- The gap, and how each is handled:
--   1. "Handles"                          — account exists, just unmapped → map it.
--   2. "Custom Shower Glass Door Material" — missing from accounts → create
--      "Custom Shower Glass Door Labor"      subcategory under Doors + 2 accounts.
--   3. "retaining wall labor"             — a TYPO in existing data: the account
--                                            "Retaning Wall Labor" (subcat
--                                            "Retaning Wall") never matched the
--                                            correctly-spelled budget line. Fix
--                                            the typo and unify under the correct
--                                            "Retaining Wall" subcategory.
--
-- The name-bridge in budget-vs-actuals matches budgets_qbo.account_name ->
-- accounts."Name" -> account_category_map, so each budget name needs an accounts
-- row + a map row to land in a category.
--
-- Idempotent. Run on staging first, then prod (Supabase SQL editor).
-- ============================================================================


-- 1. Handles -----------------------------------------------------------------
-- Account already exists; the "Handles" subcategory already lives under Cabinets.
-- Just add the overlay row (material — cabinet hardware).
INSERT INTO public.account_category_map (account_id, subcategory_id, cost_type, reviewed, source)
SELECT a.account_id, s.id, 'material'::cost_type, true, 'manual'
  FROM public.accounts a
  JOIN public.subcategories s ON s.name = 'Handles'
 WHERE a."Name" = 'Handles'
ON CONFLICT (account_id) DO NOTHING;


-- 2. Custom Shower Glass Door (Material + Labor) -----------------------------
-- Not in accounts at all. Sits with the other glass doors under "Doors".

-- 2a. New subcategory under Doors.
INSERT INTO public.subcategories (category_id, name)
SELECT c.id, 'Custom Shower Glass Door'
  FROM public.categories c
 WHERE c.name = 'Doors'
ON CONFLICT (category_id, name) DO NOTHING;

-- 2b. The two accounts (one per cost_type), AcctNum = current max + 1.
INSERT INTO public.accounts (account_id, "AcctNum", "Name", "AccountCategory", is_cogs)
SELECT gen_random_uuid(),
       (SELECT COALESCE(MAX("AcctNum"), 0) + 1 FROM public.accounts),
       'Custom Shower Glass Door Material', 'Doors', false
 WHERE NOT EXISTS (SELECT 1 FROM public.accounts WHERE "Name" = 'Custom Shower Glass Door Material');

INSERT INTO public.accounts (account_id, "AcctNum", "Name", "AccountCategory", is_cogs)
SELECT gen_random_uuid(),
       (SELECT COALESCE(MAX("AcctNum"), 0) + 1 FROM public.accounts),
       'Custom Shower Glass Door Labor', 'Doors', false
 WHERE NOT EXISTS (SELECT 1 FROM public.accounts WHERE "Name" = 'Custom Shower Glass Door Labor');

-- 2c. Map both accounts to the new subcategory.
INSERT INTO public.account_category_map (account_id, subcategory_id, cost_type, reviewed, source)
SELECT a.account_id, s.id, 'material'::cost_type, true, 'manual'
  FROM public.accounts a
  JOIN public.subcategories s ON s.name = 'Custom Shower Glass Door'
  JOIN public.categories    c ON c.id = s.category_id AND c.name = 'Doors'
 WHERE a."Name" = 'Custom Shower Glass Door Material'
ON CONFLICT (account_id) DO NOTHING;

INSERT INTO public.account_category_map (account_id, subcategory_id, cost_type, reviewed, source)
SELECT a.account_id, s.id, 'labor'::cost_type, true, 'manual'
  FROM public.accounts a
  JOIN public.subcategories s ON s.name = 'Custom Shower Glass Door'
  JOIN public.categories    c ON c.id = s.category_id AND c.name = 'Doors'
 WHERE a."Name" = 'Custom Shower Glass Door Labor'
ON CONFLICT (account_id) DO NOTHING;


-- 3. Retaining Wall — fix the typo and unify --------------------------------
-- 3a. Correct the misspelled account name so QBO budgets ("Retaining Wall
--     Labor") match it.
UPDATE public.accounts
   SET "Name" = 'Retaining Wall Labor'
 WHERE "Name" = 'Retaning Wall Labor';

-- 3b. Re-point anything mapped to the misspelled subcategory "Retaning Wall"
--     onto the correctly-spelled "Retaining Wall" (both under Rough Structure),
--     so Material + Labor live under one subcategory. No-op once already fixed.
UPDATE public.account_category_map m
   SET subcategory_id = (
         SELECT s.id FROM public.subcategories s
           JOIN public.categories c ON c.id = s.category_id
          WHERE s.name = 'Retaining Wall' AND c.name = 'Rough Structure'
          LIMIT 1),
       updated_at = now()
 WHERE m.subcategory_id = (
         SELECT s.id FROM public.subcategories s
           JOIN public.categories c ON c.id = s.category_id
          WHERE s.name = 'Retaning Wall' AND c.name = 'Rough Structure'
          LIMIT 1);

-- 3c. Drop the now-orphan misspelled subcategory (only if nothing maps to it).
DELETE FROM public.subcategories s
 USING public.categories c
 WHERE s.category_id = c.id
   AND c.name = 'Rough Structure'
   AND s.name = 'Retaning Wall'
   AND NOT EXISTS (SELECT 1 FROM public.account_category_map m WHERE m.subcategory_id = s.id);


-- VERIFICATION ---------------------------------------------------------------
-- All four names should now resolve to a category (zero rows = fully closed):
-- select b.account_name, sum(b.amount_sum) amt
--   from public.budgets_qbo b
--   where b.active = true
--     and lower(regexp_replace(b.account_name, '\s+', ' ', 'g')) not in (
--       select lower(regexp_replace(a."Name", '\s+', ' ', 'g'))
--         from public.accounts a
--         join public.account_category_map m on m.account_id = a.account_id)
--   group by 1 order by 2 desc;
--
-- Confirm Retaining Wall is unified (Material + Labor under one subcategory):
-- select s.name, m.cost_type, a."Name"
--   from public.account_category_map m
--   join public.accounts a on a.account_id = m.account_id
--   join public.subcategories s on s.id = m.subcategory_id
--   where s.name = 'Retaining Wall' order by m.cost_type;
