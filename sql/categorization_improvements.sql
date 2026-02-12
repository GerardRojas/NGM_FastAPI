-- ================================================================
-- Categorization System Improvements
-- ================================================================
-- Adds caching, feedback loop, and metrics for Andrew's
-- auto-categorization system
-- ================================================================

-- ================================================================
-- 1. CATEGORIZATION CACHE
-- ================================================================
-- Cache successful categorizations to avoid redundant GPT calls
-- Hash key = md5(description + construction_stage)
-- TTL: 30 days (implicit via created_at filter)

CREATE TABLE IF NOT EXISTS categorization_cache (
    cache_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    description_hash TEXT NOT NULL,
    description_raw TEXT NOT NULL,
    construction_stage TEXT NOT NULL,
    account_id UUID NOT NULL,
    account_name TEXT NOT NULL,
    confidence INTEGER NOT NULL CHECK (confidence >= 0 AND confidence <= 100),
    reasoning TEXT,
    warning TEXT,
    hit_count INTEGER DEFAULT 1,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    last_used_at TIMESTAMPTZ DEFAULT NOW()
);

-- Index for fast hash lookups
CREATE INDEX IF NOT EXISTS idx_cat_cache_hash
ON categorization_cache(description_hash, construction_stage);

-- Index for cleanup of old entries
CREATE INDEX IF NOT EXISTS idx_cat_cache_created
ON categorization_cache(created_at);

COMMENT ON TABLE categorization_cache IS
'Caches auto-categorization results to reduce GPT API calls and improve performance';

COMMENT ON COLUMN categorization_cache.description_hash IS
'MD5 hash of lowercase trimmed description for fast lookups';

COMMENT ON COLUMN categorization_cache.hit_count IS
'Number of times this cache entry was reused';


-- ================================================================
-- 2. CATEGORIZATION CORRECTIONS (Feedback Loop)
-- ================================================================
-- Stores user corrections to learn project-specific patterns
-- Used as context in GPT prompts to improve future categorizations

CREATE TABLE IF NOT EXISTS categorization_corrections (
    correction_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id UUID REFERENCES projects(project_id) ON DELETE CASCADE,
    user_id UUID REFERENCES users(user_id) ON DELETE SET NULL,
    expense_id UUID,
    description TEXT NOT NULL,
    construction_stage TEXT NOT NULL,
    original_account_id UUID,
    original_account_name TEXT,
    original_confidence INTEGER,
    corrected_account_id UUID NOT NULL,
    corrected_account_name TEXT NOT NULL,
    correction_reason TEXT,
    applied_count INTEGER DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Index for fetching recent corrections by project/stage
CREATE INDEX IF NOT EXISTS idx_cat_corrections_project_stage
ON categorization_corrections(project_id, construction_stage, created_at DESC);

-- Index for user activity tracking
CREATE INDEX IF NOT EXISTS idx_cat_corrections_user
ON categorization_corrections(user_id, created_at DESC);

COMMENT ON TABLE categorization_corrections IS
'User corrections to auto-categorization used to improve future predictions via feedback loop';

COMMENT ON COLUMN categorization_corrections.applied_count IS
'Number of times this correction pattern was applied to new categorizations';


-- ================================================================
-- 3. CATEGORIZATION METRICS
-- ================================================================
-- Logs confidence distribution and accuracy per categorization run
-- Enables data-driven tuning of min_confidence threshold

CREATE TABLE IF NOT EXISTS categorization_metrics (
    metric_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id UUID REFERENCES projects(project_id) ON DELETE CASCADE,
    receipt_id UUID,
    construction_stage TEXT NOT NULL,
    total_items INTEGER NOT NULL,
    avg_confidence NUMERIC(5,2),
    min_confidence INTEGER,
    max_confidence INTEGER,
    items_below_70 INTEGER,
    items_below_60 INTEGER,
    items_below_50 INTEGER,
    cache_hits INTEGER DEFAULT 0,
    cache_misses INTEGER DEFAULT 0,
    gpt_tokens_used INTEGER,
    processing_time_ms INTEGER,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Index for analytics queries by project
CREATE INDEX IF NOT EXISTS idx_cat_metrics_project
ON categorization_metrics(project_id, created_at DESC);

-- Index for performance analysis
CREATE INDEX IF NOT EXISTS idx_cat_metrics_stage
ON categorization_metrics(construction_stage, created_at DESC);

COMMENT ON TABLE categorization_metrics IS
'Performance and accuracy metrics for auto-categorization runs';

COMMENT ON COLUMN categorization_metrics.cache_hits IS
'Number of items that were served from cache instead of GPT';


-- ================================================================
-- 4. HELPER FUNCTIONS
-- ================================================================

-- Function to clean up old cache entries (30+ days)
CREATE OR REPLACE FUNCTION cleanup_old_categorization_cache()
RETURNS INTEGER AS $$
DECLARE
    deleted_count INTEGER;
BEGIN
    DELETE FROM categorization_cache
    WHERE created_at < NOW() - INTERVAL '30 days'
    AND last_used_at < NOW() - INTERVAL '30 days';

    GET DIAGNOSTICS deleted_count = ROW_COUNT;
    RETURN deleted_count;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION cleanup_old_categorization_cache() IS
'Removes cache entries older than 30 days that havent been used recently';


-- Function to get top corrections for a project/stage (for GPT context)
CREATE OR REPLACE FUNCTION get_recent_corrections(
    p_project_id UUID,
    p_stage TEXT,
    p_limit INTEGER DEFAULT 5
)
RETURNS TABLE (
    description TEXT,
    original_account TEXT,
    corrected_account TEXT,
    times_applied INTEGER
) AS $$
BEGIN
    RETURN QUERY
    SELECT
        cc.description,
        cc.original_account_name,
        cc.corrected_account_name,
        cc.applied_count
    FROM categorization_corrections cc
    WHERE cc.project_id = p_project_id
    AND cc.construction_stage = p_stage
    ORDER BY cc.created_at DESC
    LIMIT p_limit;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION get_recent_corrections IS
'Fetches recent user corrections for a project/stage to use as GPT context';


-- ================================================================
-- 5. RLS POLICIES (if needed)
-- ================================================================

-- Enable RLS on new tables
ALTER TABLE categorization_cache ENABLE ROW LEVEL SECURITY;
ALTER TABLE categorization_corrections ENABLE ROW LEVEL SECURITY;
ALTER TABLE categorization_metrics ENABLE ROW LEVEL SECURITY;

-- Service role has full access (backend operations)
CREATE POLICY "Service role full access on cache"
ON categorization_cache FOR ALL
TO service_role
USING (true)
WITH CHECK (true);

CREATE POLICY "Service role full access on corrections"
ON categorization_corrections FOR ALL
TO service_role
USING (true)
WITH CHECK (true);

CREATE POLICY "Service role full access on metrics"
ON categorization_metrics FOR ALL
TO service_role
USING (true)
WITH CHECK (true);

-- Authenticated users can read their project's corrections
CREATE POLICY "Users can read project corrections"
ON categorization_corrections FOR SELECT
TO authenticated
USING (
    project_id IN (
        SELECT DISTINCT project FROM "expenses_manual_COGS"
        WHERE created_by = auth.uid()
    )
);

-- ================================================================
-- GRANTS
-- ================================================================

GRANT ALL ON categorization_cache TO service_role;
GRANT ALL ON categorization_corrections TO service_role;
GRANT ALL ON categorization_metrics TO service_role;

GRANT SELECT ON categorization_corrections TO authenticated;
GRANT SELECT ON categorization_metrics TO authenticated;


-- ================================================================
-- 6. AUTOMATIC CORRECTION CAPTURE (Trigger)
-- ================================================================
-- When an expense's account_id is updated, log it as a correction
-- if it was originally auto-categorized

CREATE OR REPLACE FUNCTION log_category_correction()
RETURNS TRIGGER AS $$
DECLARE
    proj_id UUID;
    stage TEXT;
    orig_account_name TEXT;
    new_account_name TEXT;
BEGIN
    -- Only log if account_id changed
    IF OLD.account_id IS DISTINCT FROM NEW.account_id THEN

        -- Get project_id and construction stage
        -- Note: Using quoted identifier to preserve case-sensitivity
        SELECT project INTO proj_id FROM "expenses_manual_COGS" WHERE expense_id = NEW.expense_id;

        IF proj_id IS NOT NULL THEN
            SELECT project_stage INTO stage FROM projects WHERE project_id = proj_id;

            -- Get account names (using "Name" with capital N)
            SELECT "Name" INTO orig_account_name FROM accounts WHERE account_id = OLD.account_id;
            SELECT "Name" INTO new_account_name FROM accounts WHERE account_id = NEW.account_id;

            -- Insert correction record
            INSERT INTO categorization_corrections (
                project_id,
                expense_id,
                description,
                construction_stage,
                original_account_id,
                original_account_name,
                corrected_account_id,
                corrected_account_name,
                user_id
            ) VALUES (
                proj_id,
                NEW.expense_id,
                NEW."LineDescription",
                COALESCE(stage, 'General'),
                OLD.account_id,
                orig_account_name,
                NEW.account_id,
                new_account_name,
                NEW.updated_by  -- Assumes updated_by field exists
            );
        END IF;
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Create trigger on expenses_manual_COGS table (quoted to preserve case)
DROP TRIGGER IF EXISTS trigger_log_category_correction ON "expenses_manual_COGS";
CREATE TRIGGER trigger_log_category_correction
AFTER UPDATE ON "expenses_manual_COGS"
FOR EACH ROW
WHEN (OLD.account_id IS DISTINCT FROM NEW.account_id)
EXECUTE FUNCTION log_category_correction();

COMMENT ON FUNCTION log_category_correction() IS
'Automatically logs user corrections to expense categories for feedback loop';
