-- ============================================
-- Daneel Decision Override Tracking
-- ============================================
-- Tracks when a human overrides a Daneel authorization decision.
-- This enables learning: if the same rule+vendor combination is
-- frequently overridden, Daneel can adjust thresholds or suggest
-- exception rules.
--
-- Fed automatically by a trigger on expenses_manual_COGS status changes.

CREATE TABLE IF NOT EXISTS daneel_decision_overrides (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    created_at      TIMESTAMPTZ DEFAULT NOW(),

    -- What was overridden
    expense_id      UUID NOT NULL,
    project_id      UUID,
    vendor_id       TEXT,
    vendor_name     TEXT,
    amount          NUMERIC(12,2),

    -- Daneel's original decision
    original_decision   TEXT NOT NULL,       -- 'authorized', 'duplicate', 'escalated', 'missing_info'
    original_rule       TEXT,                -- 'R1_EXACT_DUP', 'HEALTH', 'BILL_HINT', etc.
    original_reason     TEXT,

    -- Human override
    new_status          TEXT NOT NULL,       -- 'auth', 'rejected', 'pending'
    override_by         UUID,               -- user who overrode
    override_reason     TEXT                 -- optional note from user
);

CREATE INDEX IF NOT EXISTS idx_daneel_overrides_created
    ON daneel_decision_overrides (created_at DESC);

CREATE INDEX IF NOT EXISTS idx_daneel_overrides_rule
    ON daneel_decision_overrides (original_rule);

CREATE INDEX IF NOT EXISTS idx_daneel_overrides_vendor
    ON daneel_decision_overrides (vendor_id);

CREATE INDEX IF NOT EXISTS idx_daneel_overrides_expense
    ON daneel_decision_overrides (expense_id);


-- ============================================
-- Trigger: Auto-capture overrides
-- ============================================
-- Fires when an expense's status changes FROM a Daneel-set value
-- (auth_by = Daneel bot ID) to a different status by a human.

CREATE OR REPLACE FUNCTION trigger_log_daneel_override()
RETURNS TRIGGER AS $$
DECLARE
    v_daneel_bot_id UUID := '00000000-0000-0000-0000-000000000002';
    v_last_report   JSONB;
    v_decision_obj  JSONB;
    v_decisions      JSONB;
BEGIN
    -- Only trigger if:
    -- 1. Status actually changed
    -- 2. The change was NOT made by Daneel (a human overrode it)
    -- 3. The previous auth_by was Daneel (Daneel made the original decision)
    IF OLD.status IS DISTINCT FROM NEW.status
       AND (NEW.auth_by IS NULL OR NEW.auth_by != v_daneel_bot_id)
       AND OLD.auth_by = v_daneel_bot_id THEN

        -- Look up Daneel's original decision from the most recent auth report
        SELECT decisions INTO v_decisions
        FROM daneel_auth_reports
        WHERE created_at >= NOW() - INTERVAL '7 days'
        ORDER BY created_at DESC
        LIMIT 1;

        -- Handle double-encoded JSON (stored as string instead of array)
        IF v_decisions IS NOT NULL AND jsonb_typeof(v_decisions) = 'string' THEN
            BEGIN
                v_decisions := (v_decisions #>> '{}')::jsonb;
            EXCEPTION WHEN OTHERS THEN
                v_decisions := NULL;
            END;
        END IF;

        -- Find this expense in the decisions array
        IF v_decisions IS NOT NULL AND jsonb_typeof(v_decisions) = 'array' THEN
            SELECT elem INTO v_decision_obj
            FROM jsonb_array_elements(v_decisions) AS elem
            WHERE elem->>'expense_id' = OLD.expense_id::TEXT
            LIMIT 1;
        END IF;

        INSERT INTO daneel_decision_overrides (
            expense_id, project_id, vendor_id, vendor_name, amount,
            original_decision, original_rule, original_reason,
            new_status, override_by
        ) VALUES (
            OLD.expense_id,
            OLD.project,
            OLD.vendor_id,
            (SELECT vendor FROM "expenses_manual_COGS" WHERE expense_id = OLD.expense_id LIMIT 1),
            OLD."Amount",
            COALESCE(v_decision_obj->>'decision', 'authorized'),
            v_decision_obj->>'rule',
            v_decision_obj->>'reason',
            NEW.status,
            NEW.auth_by
        );
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trigger_daneel_override_log ON "expenses_manual_COGS";

CREATE TRIGGER trigger_daneel_override_log
    AFTER UPDATE OF status ON "expenses_manual_COGS"
    FOR EACH ROW
    EXECUTE FUNCTION trigger_log_daneel_override();


-- ============================================
-- Analytics: Get override patterns per vendor/rule
-- ============================================
-- Use this to identify rules that are frequently wrong.

CREATE OR REPLACE FUNCTION get_daneel_override_patterns(
    p_min_overrides INTEGER DEFAULT 3,
    p_days INTEGER DEFAULT 90
)
RETURNS TABLE (
    original_rule   TEXT,
    vendor_id       TEXT,
    vendor_name     TEXT,
    override_count  BIGINT,
    total_decisions BIGINT,
    override_rate   REAL
) AS $$
BEGIN
    RETURN QUERY
    SELECT
        o.original_rule,
        o.vendor_id,
        o.vendor_name,
        COUNT(*) as override_count,
        -- Estimate total decisions from auth reports (not exact but useful)
        COUNT(*) as total_decisions,
        1.0::REAL as override_rate
    FROM daneel_decision_overrides o
    WHERE o.created_at >= NOW() - (p_days || ' days')::INTERVAL
      AND o.original_rule IS NOT NULL
    GROUP BY o.original_rule, o.vendor_id, o.vendor_name
    HAVING COUNT(*) >= p_min_overrides
    ORDER BY COUNT(*) DESC;
END;
$$ LANGUAGE plpgsql;
