-- =============================================================================
-- Seed typical QBO cost codes + default category links
-- =============================================================================
-- Practically all registered project-purchase expenses are COGS, so we seed a
-- typical COGS-centric set (the one that matters is "Cost of Goods Sold") and
-- default every category's cost_code to it, except the catch-all "Other Expenses".
-- Idempotent. Only fills cost_code_id where it's still NULL (won't overwrite
-- manual choices made in the Accounting page). Run on staging, then prod.
-- =============================================================================

INSERT INTO public.qbo_cost_codes (code, name, is_cogs, sort_order) VALUES
  ('50000', 'Cost of Goods Sold', TRUE,  1),
  ('50100', 'Job Materials',      TRUE,  2),
  ('50200', 'Job Labor',          TRUE,  3),
  ('50300', 'Subcontractors',     TRUE,  4),
  ('50400', 'Equipment Rental',   TRUE,  5),
  ('60000', 'Other Expenses',     FALSE, 6)
ON CONFLICT (code) DO NOTHING;

-- Default: every category -> Cost of Goods Sold (the bulk of project spend).
UPDATE public.categories
   SET cost_code_id = (SELECT id FROM public.qbo_cost_codes WHERE code = '50000')
 WHERE cost_code_id IS NULL
   AND name <> 'Other Expenses';

-- The overhead catch-all category -> Other Expenses.
UPDATE public.categories
   SET cost_code_id = (SELECT id FROM public.qbo_cost_codes WHERE code = '60000')
 WHERE cost_code_id IS NULL
   AND name = 'Other Expenses';

-- VERIFICATION ----------------------------------------------------------------
-- select code, name, is_cogs from public.qbo_cost_codes order by sort_order;
-- select c.name category, q.name cost_code
--   from public.categories c left join public.qbo_cost_codes q on q.id = c.cost_code_id
--  order by 2, 1;
