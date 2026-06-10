-- ============================================================
-- expenses_manual_COGS — soft delete.
--
-- A bookkeeper (can_delete) "deletes" an expense -> it's soft-deleted: hidden from
-- the ledger and every report, but recoverable. Only Accounting Manager / CEO /
-- COO can delete permanently (hard delete). Pairs with api/routers/expenses.py.
--
--   is_deleted     -> hidden everywhere except the Deleted/trash view
--   deleted_at     -> when it was soft-deleted
--   deleted_by     -> who soft-deleted it (users.user_id, best-effort)
--   delete_reason  -> optional note
--
-- Idempotent. Run on staging, then prod.
-- ============================================================

alter table "expenses_manual_COGS" add column if not exists is_deleted    boolean not null default false;
alter table "expenses_manual_COGS" add column if not exists deleted_at    timestamptz;
alter table "expenses_manual_COGS" add column if not exists deleted_by    uuid;
alter table "expenses_manual_COGS" add column if not exists delete_reason text;

-- Existing rows are live (not deleted) — the NOT NULL DEFAULT already backfills
-- them to false, but be explicit in case the column pre-existed as nullable.
update "expenses_manual_COGS" set is_deleted = false where is_deleted is null;

-- Hot path: list/report reads filter is_deleted = false, scoped by project.
create index if not exists idx_expenses_cogs_live
  on "expenses_manual_COGS" (project)
  where is_deleted = false;
