-- =============================================
-- EXPENSES - Separate tax from total cost
-- =============================================
-- Adds a dedicated tax column to expense lines.
--
-- Model (decided with the team):
--   Amount      = TOTAL cost (base + tax) — stays canonical. Everything that
--                 already sums Amount (budgets, bills, reconciliation, QBO)
--                 keeps working unchanged.
--   tax_amount  = the sales-tax portion of Amount (default 0).
--   base cost   = DERIVED in the app as (Amount - tax_amount); NOT stored.
--
-- The receipt scanner already returns a per-line tax (tax_included); the app now
-- surfaces it here instead of folding it invisibly into Amount.
--
-- Backward compatible: existing rows get tax_amount = 0, so base = Amount.
-- Idempotent. Run on staging, then prod.
-- =============================================

ALTER TABLE public."expenses_manual_COGS"
    ADD COLUMN IF NOT EXISTS tax_amount NUMERIC(12, 2) NOT NULL DEFAULT 0;

COMMENT ON COLUMN public."expenses_manual_COGS".tax_amount
    IS 'Sales-tax portion of Amount. Base cost = Amount - tax_amount (derived in app). Amount stays the total.';

-- VERIFICATION ------------------------------------------------
-- select expense_id, "Amount", tax_amount, ("Amount" - tax_amount) as base_cost
-- from public."expenses_manual_COGS"
-- limit 20;
