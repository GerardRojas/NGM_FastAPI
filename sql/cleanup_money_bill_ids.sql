-- ============================================================================
-- CLEANUP: garbage "money-like" bill_ids  (SAFE, preview-first)
-- ----------------------------------------------------------------------------
-- Some bills were created with a bill_id that is really an AMOUNT typed by
-- mistake on the Bill # field ("$21.99", "155.00", "$306.45"). They pollute the
-- bill view (each price becomes its own one-item "bill") and can't be mirrored
-- to Vault (no real invoice). The frontend now guards new ones (looksLikeMoneyBillId)
-- but old data remains.
--
-- "Money-like" matches the SAME rule the frontend uses (bill-view.ts MONEY_BILL_ID):
--   starts with '$' + digit   OR   a plain number with 1-2 decimals.
--
-- This script DELETES only the 100%-safe subset (Section B): money-like bills
-- with NO expenses referencing them (orphans) -> nothing points at them, so
-- removing them breaks nothing. Sections A and C are REVIEW-ONLY (read/optional).
--
-- Run Section A first, eyeball it, then run Section B. Idempotent. Staging first.
-- ============================================================================

-- ── Section A — PREVIEW (read-only). What is money-like, and is it referenced? ──
SELECT
    b.bill_id,
    b.receipt_url IS NOT NULL AS has_receipt,
    COUNT(e.expense_id)       AS expense_refs   -- 0 = orphan (safe to delete)
FROM public.bills b
LEFT JOIN public.expenses_manual_COGS e ON e.bill_id = b.bill_id
WHERE b.bill_id ~ '^\$\s*\d'
   OR b.bill_id ~ '^\d+\.\d{1,2}$'
GROUP BY b.bill_id, b.receipt_url
ORDER BY expense_refs DESC, b.bill_id;


-- ── Section B — SAFE DELETE: orphan money-like bills (no expenses point at them) ──
-- Wrapped so it only ever touches rows with zero references.
DELETE FROM public.bills b
WHERE (b.bill_id ~ '^\$\s*\d' OR b.bill_id ~ '^\d+\.\d{1,2}$')
  AND NOT EXISTS (
      SELECT 1 FROM public.expenses_manual_COGS e WHERE e.bill_id = b.bill_id
  );
-- Verify nothing money-like + orphan remains:
-- SELECT COUNT(*) FROM public.bills b
--  WHERE (b.bill_id ~ '^\$\s*\d' OR b.bill_id ~ '^\d+\.\d{1,2}$')
--    AND NOT EXISTS (SELECT 1 FROM public.expenses_manual_COGS e WHERE e.bill_id = b.bill_id);


-- ── Section C — OPTIONAL (review before running): clear the bad bill_id on the
--    EXPENSES that caused it, turning them into correct "no-bill" rows. This
--    MUTATES expense rows, so it is left commented — preview first, then uncomment.
--
-- Preview the affected expenses:
-- SELECT expense_id, project, bill_id, "LineDescription", "Amount"
--   FROM public.expenses_manual_COGS
--  WHERE bill_id ~ '^\$\s*\d' OR bill_id ~ '^\d+\.\d{1,2}$'
--  ORDER BY "TxnDate" DESC;
--
-- Then, if it looks right, clear them:
-- UPDATE public.expenses_manual_COGS
--    SET bill_id = NULL
--  WHERE bill_id ~ '^\$\s*\d' OR bill_id ~ '^\d+\.\d{1,2}$';
--
-- (After Section C you can re-run Section B to sweep any bills left orphaned.)
