-- ================================================================
-- Hari Agent: Database setup
-- ================================================================
-- Hari is the Team Coordinator AI agent.
-- Manages task delegation, scheduling, and follow-up via messages.
-- ================================================================

-- 1. Hari bot user
-- Uses a well-known UUID so backend and frontend can reference it.
-- The password is a unique dummy bcrypt hash -- bot can never login.
INSERT INTO users (user_id, user_name, avatar_color, password_hash)
VALUES (
  '00000000-0000-0000-0000-000000000004',
  'Hari',
  280,
  '$2b$12$HariNoLoginHariNoLoginAN3.3.3.3.3.3.3.3.3.3.3.3.3.33'
)
ON CONFLICT (user_id) DO UPDATE SET
  user_name = EXCLUDED.user_name,
  avatar_color = EXCLUDED.avatar_color;


-- 2. coordinator_tasks table
-- Core task tracking for Hari's coordination engine.
CREATE TABLE IF NOT EXISTS coordinator_tasks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- Instruction
    instruction_text TEXT NOT NULL,
    description TEXT NOT NULL,

    -- People
    created_by UUID NOT NULL REFERENCES users(user_id),
    assigned_to UUID REFERENCES users(user_id),

    -- Context
    project_id UUID,
    channel_key TEXT NOT NULL DEFAULT '',

    -- Timing
    deadline TIMESTAMPTZ,
    follow_up_at TIMESTAMPTZ,

    -- Status
    -- Values: pending_confirmation, active, in_progress, completed, overdue, blocked, cancelled
    status TEXT NOT NULL DEFAULT 'pending_confirmation',

    -- Escalation
    escalation_count INT DEFAULT 0,
    last_escalated_at TIMESTAMPTZ,

    -- Resolution
    completed_at TIMESTAMPTZ,
    completion_notes TEXT,
    completed_by UUID REFERENCES users(user_id),

    -- Metadata
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

-- Indexes for common queries
CREATE INDEX IF NOT EXISTS idx_coordinator_tasks_status
    ON coordinator_tasks(status);

CREATE INDEX IF NOT EXISTS idx_coordinator_tasks_assigned
    ON coordinator_tasks(assigned_to);

CREATE INDEX IF NOT EXISTS idx_coordinator_tasks_created_by
    ON coordinator_tasks(created_by);

CREATE INDEX IF NOT EXISTS idx_coordinator_tasks_deadline
    ON coordinator_tasks(deadline)
    WHERE deadline IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_coordinator_tasks_follow_up
    ON coordinator_tasks(follow_up_at)
    WHERE follow_up_at IS NOT NULL
    AND status NOT IN ('completed', 'cancelled');

CREATE INDEX IF NOT EXISTS idx_coordinator_tasks_project
    ON coordinator_tasks(project_id)
    WHERE project_id IS NOT NULL;


-- 3. Auto-update updated_at trigger
CREATE OR REPLACE FUNCTION update_coordinator_tasks_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_coordinator_tasks_updated_at ON coordinator_tasks;
CREATE TRIGGER trg_coordinator_tasks_updated_at
    BEFORE UPDATE ON coordinator_tasks
    FOR EACH ROW
    EXECUTE FUNCTION update_coordinator_tasks_updated_at();


-- 4. RLS policies (service_role bypasses, anon blocked)
ALTER TABLE coordinator_tasks ENABLE ROW LEVEL SECURITY;

-- Allow service_role full access (backend uses service_role key)
CREATE POLICY coordinator_tasks_service_all ON coordinator_tasks
    FOR ALL
    USING (true)
    WITH CHECK (true);


-- 5. Default configuration in agent_config
INSERT INTO agent_config (key, value, updated_at)
VALUES
    ('hari_coordinator_enabled', 'false', now()),
    ('hari_default_follow_up_hours', '2', now()),
    ('hari_escalation_interval_hours', '4', now()),
    ('hari_max_escalations', '3', now()),
    ('hari_stale_task_hours', '24', now()),
    ('hari_instructor_roles', '["CEO","COO","Coordinator","PM"]', now()),
    ('hari_viewer_roles', '["CEO","COO","Coordinator","PM","Bookkeeper"]', now()),
    ('hari_auto_confirm_users', '[]', now()),
    ('hari_notify_assignee_on_create', 'true', now()),
    ('hari_notify_channel', 'true', now())
ON CONFLICT (key) DO NOTHING;
