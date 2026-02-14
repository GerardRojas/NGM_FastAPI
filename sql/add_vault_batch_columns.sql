-- Add vault_file_id and batch_id columns to pending_receipts
-- for linking vault files to receipt processing and batch tracking

ALTER TABLE pending_receipts ADD COLUMN IF NOT EXISTS vault_file_id uuid;
ALTER TABLE pending_receipts ADD COLUMN IF NOT EXISTS batch_id uuid;

CREATE INDEX IF NOT EXISTS idx_pending_receipts_vault_file
  ON pending_receipts(vault_file_id)
  WHERE vault_file_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_pending_receipts_batch
  ON pending_receipts(batch_id)
  WHERE batch_id IS NOT NULL;
