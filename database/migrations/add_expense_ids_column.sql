-- ================================
-- Migration: Add expense_ids JSONB column to pending_receipts
-- ================================
-- Tracks ALL expense IDs created from a single receipt (not just the first one).
-- The existing expense_id FK column is kept for backwards compatibility.
--
-- Run this in your Supabase SQL editor or via psql.

ALTER TABLE pending_receipts
ADD COLUMN IF NOT EXISTS expense_ids JSONB DEFAULT '[]'::jsonb;

COMMENT ON COLUMN pending_receipts.expense_ids IS
  'JSON array of ALL expense_id UUIDs created from this receipt. '
  'Complements the legacy expense_id FK which only tracks the first.';

-- Backfill: copy existing expense_id into expense_ids where non-null
UPDATE pending_receipts
SET expense_ids = jsonb_build_array(expense_id::text)
WHERE expense_id IS NOT NULL
  AND (expense_ids IS NULL OR expense_ids = '[]'::jsonb);
