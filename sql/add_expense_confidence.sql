-- ================================================================
-- Expense Categorization Confidence & Source Tracking
-- ================================================================
-- Adds per-expense confidence metadata to track HOW and HOW WELL
-- each expense was categorized. This enables:
--   1. Querying low-confidence items for human review
--   2. Tracking categorization source (cache, ML, GPT, manual, etc.)
--   3. Analytics on categorization pipeline accuracy over time
--
-- NULL confidence = legacy data (no tracking existed at creation time)
-- 100 confidence + source='manual' = human-verified categorization
-- ================================================================


-- ================================================================
-- 1. ADD COLUMNS
-- ================================================================

-- Confidence score: 0-100, NULL for legacy rows
ALTER TABLE "expenses_manual_COGS"
ADD COLUMN IF NOT EXISTS categorization_confidence INTEGER DEFAULT NULL;

-- Source of the categorization decision
ALTER TABLE "expenses_manual_COGS"
ADD COLUMN IF NOT EXISTS categorization_source TEXT DEFAULT NULL;


-- ================================================================
-- 2. CONSTRAINTS
-- ================================================================
-- Add CHECK constraints via DO block to be idempotent

DO $$
BEGIN
    -- Confidence must be 0-100 if set
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'check_categorization_confidence_range'
    ) THEN
        ALTER TABLE "expenses_manual_COGS"
        ADD CONSTRAINT check_categorization_confidence_range
        CHECK (categorization_confidence >= 0 AND categorization_confidence <= 100);
    END IF;

    -- Source must be one of the known values if set
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'check_categorization_source_values'
    ) THEN
        ALTER TABLE "expenses_manual_COGS"
        ADD CONSTRAINT check_categorization_source_values
        CHECK (categorization_source IN ('ml', 'cache', 'affinity', 'gpt', 'gpt_heavy', 'manual'));
    END IF;
END $$;


-- ================================================================
-- 3. INDEXES
-- ================================================================

-- Index for querying low-confidence items (e.g., WHERE confidence < 70)
CREATE INDEX IF NOT EXISTS idx_expenses_categorization_confidence
ON "expenses_manual_COGS"(categorization_confidence);

-- Partial index for items that still need review (low confidence, not NULL)
CREATE INDEX IF NOT EXISTS idx_expenses_low_confidence
ON "expenses_manual_COGS"(categorization_confidence)
WHERE categorization_confidence IS NOT NULL AND categorization_confidence < 70;

-- Index on source for analytics (e.g., "how many were from cache vs GPT?")
CREATE INDEX IF NOT EXISTS idx_expenses_categorization_source
ON "expenses_manual_COGS"(categorization_source)
WHERE categorization_source IS NOT NULL;


-- ================================================================
-- 4. BACKFILL: Human-corrected expenses -> confidence=100, source='manual'
-- ================================================================
-- Expenses that appear in categorization_corrections were manually fixed
-- by a user, so they are definitively correct (confidence=100).

UPDATE "expenses_manual_COGS" e
SET
    categorization_confidence = 100,
    categorization_source = 'manual'
FROM categorization_corrections cc
WHERE cc.expense_id = e.expense_id
  AND e.categorization_confidence IS NULL;


-- ================================================================
-- 5. BACKFILL: Cached categorizations -> use cached confidence, source='cache'
-- ================================================================
-- Expenses that match a cache entry (by description hash) get the
-- cache's confidence score. We join on the MD5 hash of the description.
-- Only backfill rows that weren't already set by step 4 (manual).

UPDATE "expenses_manual_COGS" e
SET
    categorization_confidence = cc.confidence,
    categorization_source = 'cache'
FROM categorization_cache cc
WHERE cc.description_hash = md5(lower(trim(e."LineDescription")))
  AND e.categorization_confidence IS NULL
  AND e."LineDescription" IS NOT NULL;


-- ================================================================
-- 6. COMMENTS
-- ================================================================

COMMENT ON COLUMN "expenses_manual_COGS".categorization_confidence IS
'Confidence score (0-100) for the auto-categorization. NULL = legacy data (pre-tracking). 100 = human-verified or manual entry.';

COMMENT ON COLUMN "expenses_manual_COGS".categorization_source IS
'How the categorization was determined: ml, cache, affinity, gpt, gpt_heavy, manual. NULL = legacy/unknown.';
