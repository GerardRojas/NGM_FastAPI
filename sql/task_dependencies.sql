-- ============================================
-- TASK DEPENDENCIES TABLE
-- ============================================
-- Stores relationships between tasks for the Operation Manager
-- Allows defining task dependencies (predecessor/successor relationships)

-- Drop existing table if needed (comment out in production)
-- DROP TABLE IF EXISTS task_dependencies CASCADE;

CREATE TABLE IF NOT EXISTS task_dependencies (
    dependency_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- The predecessor task (must be completed first)
    predecessor_task_id UUID NOT NULL REFERENCES tasks(task_id) ON DELETE CASCADE,

    -- The successor task (depends on predecessor)
    successor_task_id UUID NOT NULL REFERENCES tasks(task_id) ON DELETE CASCADE,

    -- Type of dependency:
    -- 'finish_to_start' (FS): Successor can't start until predecessor finishes (DEFAULT, most common)
    -- 'start_to_start' (SS): Successor can't start until predecessor starts
    -- 'finish_to_finish' (FF): Successor can't finish until predecessor finishes
    -- 'start_to_finish' (SF): Successor can't finish until predecessor starts (rare)
    dependency_type VARCHAR(20) NOT NULL DEFAULT 'finish_to_start',

    -- Optional lag time (in hours) - positive = delay, negative = overlap
    lag_hours DECIMAL(10,2) DEFAULT 0,

    -- Visual properties for the connection line (stored as JSON)
    -- Can include: color, style, label, etc.
    visual_properties JSONB DEFAULT '{}',

    -- Metadata
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    created_by UUID REFERENCES users(user_id),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),

    -- Prevent duplicate dependencies
    CONSTRAINT unique_dependency UNIQUE (predecessor_task_id, successor_task_id),

    -- Prevent self-referencing
    CONSTRAINT no_self_dependency CHECK (predecessor_task_id != successor_task_id)
);

-- Index for faster lookups
CREATE INDEX IF NOT EXISTS idx_task_dependencies_predecessor ON task_dependencies(predecessor_task_id);
CREATE INDEX IF NOT EXISTS idx_task_dependencies_successor ON task_dependencies(successor_task_id);

-- Composite index for finding all dependencies of a task
CREATE INDEX IF NOT EXISTS idx_task_dependencies_both ON task_dependencies(predecessor_task_id, successor_task_id);

-- ============================================
-- TRIGGER: Update updated_at timestamp
-- ============================================
CREATE OR REPLACE FUNCTION update_task_dependency_timestamp()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trigger_update_task_dependency_timestamp ON task_dependencies;
CREATE TRIGGER trigger_update_task_dependency_timestamp
    BEFORE UPDATE ON task_dependencies
    FOR EACH ROW
    EXECUTE FUNCTION update_task_dependency_timestamp();

-- ============================================
-- VIEW: Task with dependencies info
-- ============================================
CREATE OR REPLACE VIEW v_task_dependencies AS
SELECT
    t.task_id,
    t.task_description,
    t.project_id,
    t.task_status,

    -- Predecessors (tasks that must complete before this one)
    (
        SELECT COALESCE(json_agg(json_build_object(
            'dependency_id', td.dependency_id,
            'task_id', pred.task_id,
            'task_description', pred.task_description,
            'dependency_type', td.dependency_type,
            'lag_hours', td.lag_hours,
            'task_status', pred.task_status
        )), '[]'::json)
        FROM task_dependencies td
        JOIN tasks pred ON pred.task_id = td.predecessor_task_id
        WHERE td.successor_task_id = t.task_id
    ) AS predecessors,

    -- Successors (tasks that depend on this one)
    (
        SELECT COALESCE(json_agg(json_build_object(
            'dependency_id', td.dependency_id,
            'task_id', succ.task_id,
            'task_description', succ.task_description,
            'dependency_type', td.dependency_type,
            'lag_hours', td.lag_hours,
            'task_status', succ.task_status
        )), '[]'::json)
        FROM task_dependencies td
        JOIN tasks succ ON succ.task_id = td.successor_task_id
        WHERE td.predecessor_task_id = t.task_id
    ) AS successors,

    -- Count of blocking tasks (predecessors not yet completed)
    (
        SELECT COUNT(*)
        FROM task_dependencies td
        JOIN tasks pred ON pred.task_id = td.predecessor_task_id
        JOIN tasks_status ts ON ts.task_status_id = pred.task_status
        WHERE td.successor_task_id = t.task_id
        AND ts.task_status NOT IN ('Done', 'Completed', 'Closed')
    ) AS blocking_count,

    -- Is this task blocked?
    (
        SELECT COUNT(*) > 0
        FROM task_dependencies td
        JOIN tasks pred ON pred.task_id = td.predecessor_task_id
        JOIN tasks_status ts ON ts.task_status_id = pred.task_status
        WHERE td.successor_task_id = t.task_id
        AND ts.task_status NOT IN ('Done', 'Completed', 'Closed')
    ) AS is_blocked

FROM tasks t;

-- ============================================
-- FUNCTION: Check for circular dependencies
-- ============================================
CREATE OR REPLACE FUNCTION check_circular_dependency(
    p_predecessor_id UUID,
    p_successor_id UUID
) RETURNS BOOLEAN AS $$
DECLARE
    v_has_cycle BOOLEAN := FALSE;
BEGIN
    -- Check if adding this dependency would create a cycle
    -- Uses recursive CTE to traverse the dependency graph
    WITH RECURSIVE dependency_chain AS (
        -- Start from the successor
        SELECT successor_task_id, predecessor_task_id, 1 as depth
        FROM task_dependencies
        WHERE predecessor_task_id = p_successor_id

        UNION ALL

        -- Follow the chain
        SELECT td.successor_task_id, td.predecessor_task_id, dc.depth + 1
        FROM task_dependencies td
        JOIN dependency_chain dc ON td.predecessor_task_id = dc.successor_task_id
        WHERE dc.depth < 100 -- Prevent infinite loops
    )
    SELECT EXISTS (
        SELECT 1 FROM dependency_chain
        WHERE successor_task_id = p_predecessor_id
    ) INTO v_has_cycle;

    RETURN v_has_cycle;
END;
$$ LANGUAGE plpgsql;

-- ============================================
-- FUNCTION: Create dependency with validation
-- ============================================
CREATE OR REPLACE FUNCTION create_task_dependency(
    p_predecessor_id UUID,
    p_successor_id UUID,
    p_dependency_type VARCHAR(20) DEFAULT 'finish_to_start',
    p_lag_hours DECIMAL DEFAULT 0,
    p_created_by UUID DEFAULT NULL
) RETURNS JSON AS $$
DECLARE
    v_dependency_id UUID;
    v_result JSON;
BEGIN
    -- Check for self-reference
    IF p_predecessor_id = p_successor_id THEN
        RETURN json_build_object(
            'success', false,
            'error', 'Cannot create dependency: task cannot depend on itself'
        );
    END IF;

    -- Check for existing dependency
    IF EXISTS (
        SELECT 1 FROM task_dependencies
        WHERE predecessor_task_id = p_predecessor_id
        AND successor_task_id = p_successor_id
    ) THEN
        RETURN json_build_object(
            'success', false,
            'error', 'Dependency already exists'
        );
    END IF;

    -- Check for circular dependency
    IF check_circular_dependency(p_predecessor_id, p_successor_id) THEN
        RETURN json_build_object(
            'success', false,
            'error', 'Cannot create dependency: would create a circular dependency'
        );
    END IF;

    -- Create the dependency
    INSERT INTO task_dependencies (
        predecessor_task_id,
        successor_task_id,
        dependency_type,
        lag_hours,
        created_by
    ) VALUES (
        p_predecessor_id,
        p_successor_id,
        p_dependency_type,
        p_lag_hours,
        p_created_by
    ) RETURNING dependency_id INTO v_dependency_id;

    RETURN json_build_object(
        'success', true,
        'dependency_id', v_dependency_id,
        'message', 'Dependency created successfully'
    );
END;
$$ LANGUAGE plpgsql;

-- ============================================
-- FUNCTION: Get all dependencies for a project
-- ============================================
CREATE OR REPLACE FUNCTION get_project_dependencies(p_project_id UUID)
RETURNS JSON AS $$
BEGIN
    RETURN (
        SELECT COALESCE(json_agg(json_build_object(
            'dependency_id', td.dependency_id,
            'predecessor_task_id', td.predecessor_task_id,
            'predecessor_title', pred.task_description,
            'successor_task_id', td.successor_task_id,
            'successor_title', succ.task_description,
            'dependency_type', td.dependency_type,
            'lag_hours', td.lag_hours,
            'visual_properties', td.visual_properties
        )), '[]'::json)
        FROM task_dependencies td
        JOIN tasks pred ON pred.task_id = td.predecessor_task_id
        JOIN tasks succ ON succ.task_id = td.successor_task_id
        WHERE pred.project_id = p_project_id
           OR succ.project_id = p_project_id
    );
END;
$$ LANGUAGE plpgsql;

-- ============================================
-- RLS POLICIES (Row Level Security)
-- ============================================
ALTER TABLE task_dependencies ENABLE ROW LEVEL SECURITY;

-- Allow read access to authenticated users
CREATE POLICY "Users can view task dependencies"
    ON task_dependencies FOR SELECT
    USING (true);

-- Allow insert for authenticated users
CREATE POLICY "Users can create task dependencies"
    ON task_dependencies FOR INSERT
    WITH CHECK (true);

-- Allow update for authenticated users
CREATE POLICY "Users can update task dependencies"
    ON task_dependencies FOR UPDATE
    USING (true);

-- Allow delete for authenticated users
CREATE POLICY "Users can delete task dependencies"
    ON task_dependencies FOR DELETE
    USING (true);

-- ============================================
-- SAMPLE DATA (for testing)
-- ============================================
-- Uncomment to add sample dependencies
/*
INSERT INTO task_dependencies (predecessor_task_id, successor_task_id, dependency_type)
SELECT
    t1.task_id,
    t2.task_id,
    'finish_to_start'
FROM tasks t1
JOIN tasks t2 ON t1.project_id = t2.project_id AND t1.task_id != t2.task_id
WHERE t1.deadline < t2.deadline
LIMIT 5;
*/

COMMENT ON TABLE task_dependencies IS 'Stores task dependency relationships for the Operation Manager workflow visualization';
COMMENT ON COLUMN task_dependencies.dependency_type IS 'Type of dependency: finish_to_start (default), start_to_start, finish_to_finish, start_to_finish';
COMMENT ON COLUMN task_dependencies.lag_hours IS 'Lag time in hours. Positive = delay after predecessor, Negative = overlap allowed';
