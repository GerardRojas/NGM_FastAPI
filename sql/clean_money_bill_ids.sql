-- ============================================================
-- Clean up expenses where bill_id was mistakenly set to an amount
-- (e.g. "$21.99", "$12,134.50", "501.86") instead of an invoice/bill number.
--
-- These come from manual entry on cash / no-invoice expenses. They pollute the
-- bill view (each price becomes its own one-item "bill"). The frontend now
-- guards against new ones (looksLikeMoneyBillId); this fixes the existing rows.
--
-- Run the SELECT first to review, then choose ONE of the UPDATEs. Idempotent.
-- Table: expenses_manual_COGS (Supabase).
-- ============================================================

-- 0) PREVIEW — every money-like bill_id (run this first) -------
select
    e.expense_id,
    e.bill_id,
    e."Amount",
    e."LineDescription",
    p.project_name,
    count(*) over (partition by e.bill_id) as rows_sharing_billid
from expenses_manual_COGS e
left join projects p on p.project_id = e.project
where e.bill_id ~ '^\$\s*[0-9]|^[0-9]+\.[0-9]{1,2}$'
order by e.bill_id;

-- ------------------------------------------------------------
-- OPTION A — null ALL money-like bill_ids (simplest).
-- The multi-line bills whose bill_id is the invoice TOTAL (e.g. "$12,134.50"
-- x3) will split into "No Bill Assigned".
-- ------------------------------------------------------------
-- update expenses_manual_COGS
-- set bill_id = null
-- where bill_id ~ '^\$\s*[0-9]|^[0-9]+\.[0-9]{1,2}$';

-- ------------------------------------------------------------
-- OPTION B — null only the JUNK singletons; keep money-like bill_ids that are
-- shared by 2+ expenses (those at least group a real multi-line bill, e.g.
-- "$12,134.50"). Recommended.
-- ------------------------------------------------------------
-- update expenses_manual_COGS e
-- set bill_id = null
-- where e.bill_id ~ '^\$\s*[0-9]|^[0-9]+\.[0-9]{1,2}$'
--   and (select count(*) from expenses_manual_COGS x where x.bill_id = e.bill_id) = 1;

-- 9) VERIFY (after running an UPDATE) -------------------------
-- select count(*) as remaining_money_bill_ids
-- from expenses_manual_COGS
-- where bill_id ~ '^\$\s*[0-9]|^[0-9]+\.[0-9]{1,2}$';
