-- ============================================
-- TASK AUTOMATIONS SCHEMA
-- ============================================
-- Extends the tasks table to support automated task clusters
-- Allows grouping of related automated tasks in the UI

-- ============================================
-- 1. ADD AUTOMATION FIELDS TO TASKS TABLE
-- ============================================

-- automation_type: Identifies the type of automation that created this task
-- This allows grouping related tasks in the UI as expandable clusters
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS automation_type VARCHAR(50);

-- automation_source_id: Reference to the source that triggered this task
-- For expenses: could be the expense_id or a batch reference
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS automation_source_id UUID;

-- automation_metadata: Additional data about the automation
-- Stores project-specific info, counts, etc.
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS automation_metadata JSONB DEFAULT '{}';

-- is_automated: Quick flag to identify automated tasks
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS is_automated BOOLEAN DEFAULT FALSE;

-- Index for faster automation queries
CREATE INDEX IF NOT EXISTS idx_tasks_automation_type ON tasks(automation_type) WHERE automation_type IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_tasks_is_automated ON tasks(is_automated) WHERE is_automated = TRUE;

-- ============================================
-- 2. AUTOMATION SETTINGS TABLE
-- ============================================
-- Stores configuration for each automation type including default owners

CREATE TABLE IF NOT EXISTS automation_settings (
    setting_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- Automation identifier (matches automation_type in tasks)
    automation_type VARCHAR(50) UNIQUE NOT NULL,

    -- Display name for the automation
    display_name VARCHAR(100) NOT NULL,

    -- Is this automation enabled?
    is_enabled BOOLEAN DEFAULT FALSE,

    -- Default department for tasks created by this automation
    default_department_id UUID REFERENCES task_departments(department_id),

    -- Default owner (can be overridden per-project)
    default_owner_id UUID REFERENCES users(user_id),

    -- Default manager/reviewer for the tasks
    default_manager_id UUID REFERENCES users(user_id),

    -- Priority level for automated tasks (1=highest, 5=lowest)
    default_priority INTEGER DEFAULT 3,

    -- Additional configuration as JSON
    config JSONB DEFAULT '{}',

    -- Metadata
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    created_by UUID REFERENCES users(user_id),
    updated_by UUID REFERENCES users(user_id)
);

-- ============================================
-- 3. AUTOMATION OWNER OVERRIDES TABLE
-- ============================================
-- Allows setting different default owners per project for each automation

CREATE TABLE IF NOT EXISTS automation_owner_overrides (
    override_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- Which automation this override applies to
    automation_type VARCHAR(50) NOT NULL,

    -- Which project this override applies to (NULL = all projects)
    project_id UUID REFERENCES projects(project_id) ON DELETE CASCADE,

    -- The owner to assign for this automation + project combo
    owner_id UUID REFERENCES users(user_id),

    -- Optional: different manager for this project
    manager_id UUID REFERENCES users(user_id),

    -- Metadata
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),

    -- Unique constraint: one override per automation+project combo
    CONSTRAINT unique_automation_project_override UNIQUE (automation_type, project_id)
);

-- Index for faster lookups
CREATE INDEX IF NOT EXISTS idx_automation_overrides_type ON automation_owner_overrides(automation_type);
CREATE INDEX IF NOT EXISTS idx_automation_overrides_project ON automation_owner_overrides(project_id);

-- ============================================
-- 4. INSERT DEFAULT AUTOMATION SETTINGS
-- ============================================

INSERT INTO automation_settings (automation_type, display_name, is_enabled, default_priority, config)
VALUES
    ('pending_expenses_auth', 'Pending Expenses to Authorize', false, 2,
     '{"description": "Creates tasks for projects with unauthorized expenses", "department_hint": "bookkeeping"}'),
    ('pending_expenses_categorize', 'Pending Expenses to Categorize', false, 3,
     '{"description": "Creates tasks for expenses that need categorization", "department_hint": "bookkeeping"}'),
    ('pending_health_check', 'Pending Health Check', false, 3,
     '{"description": "Creates periodic health check tasks for active projects", "department_hint": "bookkeeping"}')
ON CONFLICT (automation_type) DO NOTHING;

-- ============================================
-- 5. TRIGGER: Update timestamp on settings change
-- ============================================

CREATE OR REPLACE FUNCTION update_automation_settings_timestamp()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trigger_update_automation_settings_timestamp ON automation_settings;
CREATE TRIGGER trigger_update_automation_settings_timestamp
    BEFORE UPDATE ON automation_settings
    FOR EACH ROW
    EXECUTE FUNCTION update_automation_settings_timestamp();

-- ============================================
-- 6. VIEW: Automation tasks with cluster info
-- ============================================

CREATE OR REPLACE VIEW v_automation_task_clusters AS
SELECT
    t.automation_type,
    ast.display_name as cluster_name,
    ast.is_enabled,
    COUNT(t.task_id) as task_count,
    COUNT(CASE WHEN ts.task_status = 'Done' OR ts.task_status = 'Completed' THEN 1 END) as completed_count,
    COUNT(CASE WHEN ts.task_status = 'Not Started' THEN 1 END) as pending_count,
    json_agg(json_build_object(
        'task_id', t.task_id,
        'task_description', t.task_description,
        'project_id', t.project_id,
        'project_name', p.project_name,
        'owner_id', t."Owner_id",
        'task_status', ts.task_status,
        'automation_metadata', t.automation_metadata
    ) ORDER BY p.project_name) as tasks
FROM tasks t
JOIN automation_settings ast ON ast.automation_type = t.automation_type
LEFT JOIN tasks_status ts ON ts.task_status_id = t.task_status
LEFT JOIN projects p ON p.project_id = t.project_id
WHERE t.is_automated = TRUE
  AND t.automation_type IS NOT NULL
GROUP BY t.automation_type, ast.display_name, ast.is_enabled;

-- ============================================
-- 7. FUNCTION: Get or create automation task for project
-- ============================================

CREATE OR REPLACE FUNCTION upsert_automation_task(
    p_automation_type VARCHAR(50),
    p_project_id UUID,
    p_task_description TEXT,
    p_metadata JSONB DEFAULT '{}'
) RETURNS JSON AS $$
DECLARE
    v_task_id UUID;
    v_settings RECORD;
    v_override RECORD;
    v_owner_id UUID;
    v_manager_id UUID;
    v_department_id UUID;
    v_status_id UUID;
    v_is_new BOOLEAN := FALSE;
BEGIN
    -- Get automation settings
    SELECT * INTO v_settings FROM automation_settings WHERE automation_type = p_automation_type;

    IF NOT FOUND THEN
        RETURN json_build_object('success', false, 'error', 'Unknown automation type');
    END IF;

    IF NOT v_settings.is_enabled THEN
        RETURN json_build_object('success', false, 'error', 'Automation is disabled');
    END IF;

    -- Check for project-specific override
    SELECT * INTO v_override
    FROM automation_owner_overrides
    WHERE automation_type = p_automation_type
      AND project_id = p_project_id;

    -- Determine owner and manager
    v_owner_id := COALESCE(v_override.owner_id, v_settings.default_owner_id);
    v_manager_id := COALESCE(v_override.manager_id, v_settings.default_manager_id);
    v_department_id := v_settings.default_department_id;

    -- Get "Not Started" status
    SELECT task_status_id INTO v_status_id
    FROM tasks_status
    WHERE task_status ILIKE '%not started%'
    LIMIT 1;

    -- Check if task already exists for this automation+project
    SELECT task_id INTO v_task_id
    FROM tasks
    WHERE automation_type = p_automation_type
      AND project_id = p_project_id
      AND is_automated = TRUE;

    IF v_task_id IS NULL THEN
        -- Create new task
        INSERT INTO tasks (
            task_description,
            project_id,
            "Owner_id",
            manager,
            task_department,
            task_status,
            automation_type,
            automation_metadata,
            is_automated
        ) VALUES (
            p_task_description,
            p_project_id,
            v_owner_id,
            v_manager_id,
            v_department_id,
            v_status_id,
            p_automation_type,
            p_metadata,
            TRUE
        ) RETURNING task_id INTO v_task_id;

        v_is_new := TRUE;
    ELSE
        -- Update existing task metadata
        UPDATE tasks
        SET automation_metadata = p_metadata,
            updated_at = NOW()
        WHERE task_id = v_task_id;
    END IF;

    RETURN json_build_object(
        'success', true,
        'task_id', v_task_id,
        'is_new', v_is_new,
        'owner_id', v_owner_id,
        'manager_id', v_manager_id
    );
END;
$$ LANGUAGE plpgsql;

-- ============================================
-- 8. RLS POLICIES
-- ============================================

ALTER TABLE automation_settings ENABLE ROW LEVEL SECURITY;
ALTER TABLE automation_owner_overrides ENABLE ROW LEVEL SECURITY;

-- Allow read access to authenticated users
CREATE POLICY "Users can view automation settings"
    ON automation_settings FOR SELECT USING (true);

CREATE POLICY "Users can view automation overrides"
    ON automation_owner_overrides FOR SELECT USING (true);

-- Allow modifications (in production, restrict to admins)
CREATE POLICY "Users can modify automation settings"
    ON automation_settings FOR ALL USING (true);

CREATE POLICY "Users can modify automation overrides"
    ON automation_owner_overrides FOR ALL USING (true);

-- ============================================
-- COMMENTS
-- ============================================

COMMENT ON TABLE automation_settings IS 'Configuration for each automation type including default owners and departments';
COMMENT ON TABLE automation_owner_overrides IS 'Project-specific owner overrides for automations';
COMMENT ON COLUMN tasks.automation_type IS 'Identifier for the automation that created this task (used for clustering)';
COMMENT ON COLUMN tasks.is_automated IS 'Quick flag to identify tasks created by automations';
