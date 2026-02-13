-- ============================================
-- Vendor-Account Affinity Table
-- ============================================
-- Tracks which accounts are most commonly used for each vendor.
-- When a vendor has a strong affinity (ratio >= 90%, count >= 5),
-- the categorization system can skip GPT and assign directly.
--
-- Fed automatically by a trigger on expenses_manual_COGS updates.

CREATE TABLE IF NOT EXISTS vendor_account_affinity (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    vendor_id       TEXT NOT NULL,
    vendor_name     TEXT,
    account_id      TEXT NOT NULL,
    account_name    TEXT,
    hit_count       INTEGER DEFAULT 1,
    total_for_vendor INTEGER DEFAULT 1,     -- total expenses for this vendor (across all accounts)
    ratio           REAL GENERATED ALWAYS AS (
                        CASE WHEN total_for_vendor > 0
                             THEN hit_count::REAL / total_for_vendor::REAL
                             ELSE 0.0
                        END
                    ) STORED,
    last_used       TIMESTAMPTZ DEFAULT NOW(),
    created_at      TIMESTAMPTZ DEFAULT NOW(),

    UNIQUE (vendor_id, account_id)
);

CREATE INDEX IF NOT EXISTS idx_vendor_account_affinity_vendor
    ON vendor_account_affinity (vendor_id);

CREATE INDEX IF NOT EXISTS idx_vendor_account_affinity_ratio
    ON vendor_account_affinity (ratio DESC);


-- ============================================
-- Function: Refresh affinity for a vendor
-- ============================================
-- Recalculates affinity counts from actual expense data.
-- Called by the trigger after INSERT/UPDATE on expenses.

CREATE OR REPLACE FUNCTION refresh_vendor_affinity(p_vendor_id TEXT)
RETURNS VOID AS $$
DECLARE
    v_total INTEGER;
    v_vendor_name TEXT;
BEGIN
    -- Get vendor name
    SELECT vendor INTO v_vendor_name
    FROM "expenses_manual_COGS"
    WHERE vendor_id = p_vendor_id
    LIMIT 1;

    -- Count total expenses for this vendor
    SELECT COUNT(*) INTO v_total
    FROM "expenses_manual_COGS"
    WHERE vendor_id = p_vendor_id
      AND account_id IS NOT NULL
      AND account_id != '';

    IF v_total = 0 THEN
        RETURN;
    END IF;

    -- Upsert affinity per account
    INSERT INTO vendor_account_affinity (vendor_id, vendor_name, account_id, account_name, hit_count, total_for_vendor, last_used)
    SELECT
        p_vendor_id,
        v_vendor_name,
        e.account_id,
        a."Name",
        COUNT(*),
        v_total,
        NOW()
    FROM "expenses_manual_COGS" e
    LEFT JOIN accounts a ON a.id::TEXT = e.account_id
    WHERE e.vendor_id = p_vendor_id
      AND e.account_id IS NOT NULL
      AND e.account_id != ''
    GROUP BY e.account_id, a."Name"
    ON CONFLICT (vendor_id, account_id)
    DO UPDATE SET
        vendor_name = EXCLUDED.vendor_name,
        account_name = EXCLUDED.account_name,
        hit_count = EXCLUDED.hit_count,
        total_for_vendor = EXCLUDED.total_for_vendor,
        last_used = NOW();
END;
$$ LANGUAGE plpgsql;


-- ============================================
-- Function: Get top affinity for a vendor
-- ============================================
-- Returns the best account match if affinity is strong enough.

CREATE OR REPLACE FUNCTION get_vendor_affinity(
    p_vendor_id TEXT,
    p_min_count INTEGER DEFAULT 5,
    p_min_ratio REAL DEFAULT 0.90
)
RETURNS TABLE (
    account_id TEXT,
    account_name TEXT,
    hit_count INTEGER,
    ratio REAL
) AS $$
BEGIN
    RETURN QUERY
    SELECT
        va.account_id,
        va.account_name,
        va.hit_count,
        va.ratio
    FROM vendor_account_affinity va
    WHERE va.vendor_id = p_vendor_id
      AND va.hit_count >= p_min_count
      AND va.ratio >= p_min_ratio
    ORDER BY va.ratio DESC, va.hit_count DESC
    LIMIT 1;
END;
$$ LANGUAGE plpgsql;


-- ============================================
-- Trigger: Auto-refresh affinity on expense changes
-- ============================================

CREATE OR REPLACE FUNCTION trigger_refresh_vendor_affinity()
RETURNS TRIGGER AS $$
BEGIN
    -- Only refresh if vendor_id and account_id are present
    IF NEW.vendor_id IS NOT NULL AND NEW.vendor_id != ''
       AND NEW.account_id IS NOT NULL AND NEW.account_id != '' THEN
        PERFORM refresh_vendor_affinity(NEW.vendor_id);
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Drop if exists to avoid duplicates
DROP TRIGGER IF EXISTS trigger_vendor_affinity_refresh ON "expenses_manual_COGS";

CREATE TRIGGER trigger_vendor_affinity_refresh
    AFTER INSERT OR UPDATE OF account_id ON "expenses_manual_COGS"
    FOR EACH ROW
    EXECUTE FUNCTION trigger_refresh_vendor_affinity();
