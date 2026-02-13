-- ============================================
-- Arturito Intent Log
-- ============================================
-- Tracks every intent detection for analytics, learning, and NLU improvement.
-- Key metrics: which intents fail (GPT fallback), which are most used,
-- confidence distribution, and delegation patterns.

CREATE TABLE IF NOT EXISTS arturito_intent_log (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    created_at      TIMESTAMPTZ DEFAULT NOW(),

    -- Who & where
    user_email      TEXT,
    user_role       TEXT,
    space_id        TEXT,
    current_page    TEXT,

    -- What the user said
    raw_text        TEXT NOT NULL,

    -- NLU result
    detected_intent TEXT NOT NULL,
    confidence      REAL DEFAULT 0.0,
    source          TEXT DEFAULT 'local',      -- 'local' (regex) or 'gpt'
    entities        JSONB DEFAULT '{}',

    -- Outcome
    action_result   TEXT,                      -- 'success', 'low_confidence', 'unknown_intent',
                                               -- 'permission_denied', 'handler_error', 'delegated'
    delegated_to    TEXT,                      -- 'andrew', 'daneel', or NULL

    -- Performance
    processing_ms   INTEGER DEFAULT 0
);

-- Index for analytics queries
CREATE INDEX IF NOT EXISTS idx_arturito_intent_log_created
    ON arturito_intent_log (created_at DESC);

CREATE INDEX IF NOT EXISTS idx_arturito_intent_log_intent
    ON arturito_intent_log (detected_intent);

CREATE INDEX IF NOT EXISTS idx_arturito_intent_log_source
    ON arturito_intent_log (source);

CREATE INDEX IF NOT EXISTS idx_arturito_intent_log_result
    ON arturito_intent_log (action_result);


-- ============================================
-- Arturito User Patterns (navigation memory)
-- ============================================
-- Tracks per-user intent frequency for proactive suggestions.

CREATE TABLE IF NOT EXISTS arturito_user_patterns (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_email      TEXT NOT NULL,
    intent          TEXT NOT NULL,
    hit_count       INTEGER DEFAULT 1,
    last_used       TIMESTAMPTZ DEFAULT NOW(),

    UNIQUE (user_email, intent)
);

CREATE INDEX IF NOT EXISTS idx_arturito_user_patterns_user
    ON arturito_user_patterns (user_email);


-- ============================================
-- Helper: Log an intent (called from Python)
-- ============================================

CREATE OR REPLACE FUNCTION log_arturito_intent(
    p_user_email    TEXT,
    p_user_role     TEXT,
    p_space_id      TEXT,
    p_current_page  TEXT,
    p_raw_text      TEXT,
    p_intent        TEXT,
    p_confidence    REAL,
    p_source        TEXT,
    p_entities      JSONB,
    p_action_result TEXT,
    p_delegated_to  TEXT,
    p_processing_ms INTEGER
) RETURNS VOID AS $$
BEGIN
    -- Log the intent
    INSERT INTO arturito_intent_log (
        user_email, user_role, space_id, current_page,
        raw_text, detected_intent, confidence, source, entities,
        action_result, delegated_to, processing_ms
    ) VALUES (
        p_user_email, p_user_role, p_space_id, p_current_page,
        p_raw_text, p_intent, p_confidence, p_source, p_entities,
        p_action_result, p_delegated_to, p_processing_ms
    );

    -- Update user patterns (upsert)
    INSERT INTO arturito_user_patterns (user_email, intent, hit_count, last_used)
    VALUES (p_user_email, p_intent, 1, NOW())
    ON CONFLICT (user_email, intent)
    DO UPDATE SET
        hit_count = arturito_user_patterns.hit_count + 1,
        last_used = NOW();
END;
$$ LANGUAGE plpgsql;
