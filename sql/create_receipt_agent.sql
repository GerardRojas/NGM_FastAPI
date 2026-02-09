-- ================================================================
-- Receipt Agent: Database migrations
-- ================================================================
-- 1. Arturito bot user (for posting messages in channels)
-- 2. file_hash column on pending_receipts (duplicate detection)
-- 3. 'duplicate' status support
-- ================================================================

-- 1. Arturito bot user
-- Uses a well-known UUID so backend and frontend can reference it
INSERT INTO users (user_id, user_name, avatar_color)
VALUES (
  '00000000-0000-0000-0000-000000000001',
  'Arturito',
  35
)
ON CONFLICT (user_id) DO NOTHING;

-- 2. file_hash column for duplicate detection (SHA-256)
ALTER TABLE pending_receipts ADD COLUMN IF NOT EXISTS file_hash TEXT;

CREATE INDEX IF NOT EXISTS idx_pending_receipts_file_hash
  ON pending_receipts(file_hash)
  WHERE file_hash IS NOT NULL;

-- 3. Composite index for data-based duplicate detection (vendor+amount+date)
CREATE INDEX IF NOT EXISTS idx_pending_receipts_dedup
  ON pending_receipts(project_id, vendor_name, amount, receipt_date)
  WHERE status IN ('ready', 'linked');

-- 4. Expand status CHECK constraint to include 'duplicate' and 'check_review'
ALTER TABLE pending_receipts DROP CONSTRAINT IF EXISTS pending_receipts_status_check;
ALTER TABLE pending_receipts ADD CONSTRAINT pending_receipts_status_check
  CHECK (status IN (
    'pending',
    'processing',
    'ready',
    'linked',
    'rejected',
    'error',
    'duplicate',
    'check_review'
  ));
