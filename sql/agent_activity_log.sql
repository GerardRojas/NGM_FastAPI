-- ============================================
-- Agent Activity Log
-- ============================================
-- One row per agent COMMAND execution (a registered function from
-- agent_registry: run_auto_auth, check_budget, process_receipt, ...).
-- Powers the Agent Hub analytics: global usage across all agents, plus
-- per-user breakdown of who runs which commands.
--
-- Written from the brain's _execute_function_call (api/services/agent_brain.py)
-- and, for scheduled runs, from the service entry points.

CREATE TABLE IF NOT EXISTS agent_activity_log (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    created_at      TIMESTAMPTZ DEFAULT NOW(),

    -- Who & where
    user_id         UUID,                       -- NULL for scheduled/system runs
    user_name       TEXT,                       -- denormalized for fast display
    agent           TEXT NOT NULL,              -- 'daneel' | 'andrew' | 'hari' | 'art'
    project_id      UUID,
    source          TEXT DEFAULT 'chat',        -- 'modal' | 'chat' | 'scheduled' | 'api'

    -- What ran
    function        TEXT NOT NULL,              -- registry function name

    -- Outcome & performance
    status          TEXT DEFAULT 'ok',          -- 'ok' | 'error'
    latency_ms      INTEGER DEFAULT 0,
    error           TEXT
);

CREATE INDEX IF NOT EXISTS idx_agent_activity_created
    ON agent_activity_log (created_at DESC);

CREATE INDEX IF NOT EXISTS idx_agent_activity_agent
    ON agent_activity_log (agent);

CREATE INDEX IF NOT EXISTS idx_agent_activity_user
    ON agent_activity_log (user_id);

CREATE INDEX IF NOT EXISTS idx_agent_activity_function
    ON agent_activity_log (function);
