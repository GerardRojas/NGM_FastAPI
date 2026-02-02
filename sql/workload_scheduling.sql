-- ============================================
-- WORKLOAD SCHEDULING SCHEMA
-- ============================================
-- Adds auto-scheduling capabilities based on user workload
-- Enables automatic task linking when users are overloaded

-- ============================================
-- 1. ADD SCHEDULING FIELDS TO TASKS TABLE
-- ============================================

-- Scheduled start date (auto-calculated based on availability)
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS scheduled_start_date TIMESTAMP WITH TIME ZONE;

-- Scheduled end date (auto-calculated: start + estimated_hours)
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS scheduled_end_date TIMESTAMP WITH TIME ZONE;

-- Queue position for a user (lower = higher priority in queue)
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS queue_position INTEGER;

-- Flag to indicate this task was auto-linked due to workload
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS auto_linked BOOLEAN DEFAULT FALSE;

-- The task that is blocking this one (auto-set when queued)
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS blocked_by_task_id UUID REFERENCES tasks(task_id);

-- Scheduling status
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS scheduling_status VARCHAR(20) DEFAULT 'unscheduled';
-- Values: 'unscheduled', 'scheduled', 'in_progress', 'completed', 'blocked'

-- Index for faster scheduling queries
CREATE INDEX IF NOT EXISTS idx_tasks_scheduled_start ON tasks(scheduled_start_date) WHERE scheduled_start_date IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_tasks_owner_queue ON tasks("Owner_id", queue_position) WHERE queue_position IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_tasks_scheduling_status ON tasks(scheduling_status);

-- ============================================
-- 2. USER CAPACITY SETTINGS TABLE
-- ============================================
-- Per-user work capacity configuration

CREATE TABLE IF NOT EXISTS user_capacity_settings (
    setting_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- The user this setting applies to
    user_id UUID NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,

    -- Daily work capacity in hours (default 8)
    hours_per_day DECIMAL(4,2) DEFAULT 8.0,

    -- Working days per week (default 5 for Mon-Fri)
    days_per_week INTEGER DEFAULT 5,

    -- Working days (0=Sunday, 1=Monday, etc.)
    -- Default: Monday to Friday [1,2,3,4,5]
    working_days INTEGER[] DEFAULT ARRAY[1,2,3,4,5],

    -- Work start time (for more precise scheduling)
    work_start_time TIME DEFAULT '09:00:00',

    -- Work end time
    work_end_time TIME DEFAULT '18:00:00',

    -- Buffer percentage (extra time to account for meetings, interruptions)
    -- E.g., 20 means only 80% of capacity is available for tasks
    buffer_percent INTEGER DEFAULT 20,

    -- Metadata
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),

    -- One setting per user
    CONSTRAINT unique_user_capacity UNIQUE (user_id)
);

-- ============================================
-- 3. TIME OFF / UNAVAILABILITY TABLE
-- ============================================
-- Track when users are unavailable (vacation, sick, etc.)

CREATE TABLE IF NOT EXISTS user_time_off (
    time_off_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    user_id UUID NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,

    -- Start of unavailability
    start_date DATE NOT NULL,

    -- End of unavailability (inclusive)
    end_date DATE NOT NULL,

    -- Type: 'vacation', 'sick', 'holiday', 'other'
    time_off_type VARCHAR(20) DEFAULT 'vacation',

    -- Optional notes
    notes TEXT,

    -- Metadata
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    created_by UUID REFERENCES users(user_id),

    -- Ensure end >= start
    CONSTRAINT valid_date_range CHECK (end_date >= start_date)
);

CREATE INDEX IF NOT EXISTS idx_user_time_off_dates ON user_time_off(user_id, start_date, end_date);

-- ============================================
-- 4. WORKLOAD SNAPSHOT TABLE
-- ============================================
-- Historical tracking of workload for analytics

CREATE TABLE IF NOT EXISTS workload_snapshots (
    snapshot_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    user_id UUID NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,

    -- Snapshot date
    snapshot_date DATE NOT NULL DEFAULT CURRENT_DATE,

    -- Metrics at this point in time
    total_tasks INTEGER DEFAULT 0,
    total_estimated_hours DECIMAL(10,2) DEFAULT 0,
    completed_tasks INTEGER DEFAULT 0,
    completed_hours DECIMAL(10,2) DEFAULT 0,
    overdue_tasks INTEGER DEFAULT 0,

    -- Capacity at this time
    weekly_capacity_hours DECIMAL(10,2),
    utilization_percent DECIMAL(5,2),

    -- Workload status
    status VARCHAR(20), -- critical, overloaded, optimal, normal, underloaded

    -- Burnout risk score (0-100)
    burnout_risk_score INTEGER DEFAULT 0,

    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),

    -- One snapshot per user per day
    CONSTRAINT unique_user_daily_snapshot UNIQUE (user_id, snapshot_date)
);

CREATE INDEX IF NOT EXISTS idx_workload_snapshots_user_date ON workload_snapshots(user_id, snapshot_date DESC);

-- ============================================
-- 5. VIEW: User Workload Summary
-- ============================================

CREATE OR REPLACE VIEW v_user_workload AS
WITH task_stats AS (
    SELECT
        t."Owner_id" as user_id,
        COUNT(*) as total_tasks,
        COUNT(CASE WHEN ts.task_status IN ('Not Started', 'Working on It') THEN 1 END) as active_tasks,
        SUM(CASE WHEN ts.task_status IN ('Not Started', 'Working on It')
            THEN COALESCE(t.estimated_hours, 2) ELSE 0 END) as pending_hours,
        COUNT(CASE WHEN t.deadline < CURRENT_DATE AND ts.task_status NOT IN ('Done', 'Completed') THEN 1 END) as overdue_count,
        COUNT(CASE WHEN t.deadline BETWEEN CURRENT_DATE AND CURRENT_DATE + 7
            AND ts.task_status NOT IN ('Done', 'Completed') THEN 1 END) as due_soon_count,
        MIN(CASE WHEN ts.task_status = 'Working on It' THEN t.task_id END) as current_task_id,
        MIN(t.scheduled_end_date) FILTER (WHERE ts.task_status = 'Working on It') as current_task_ends
    FROM tasks t
    LEFT JOIN tasks_status ts ON ts.task_status_id = t.task_status
    WHERE t."Owner_id" IS NOT NULL
    GROUP BY t."Owner_id"
),
capacity AS (
    SELECT
        u.user_id,
        u.username,
        COALESCE(ucs.hours_per_day, 8) as hours_per_day,
        COALESCE(ucs.days_per_week, 5) as days_per_week,
        COALESCE(ucs.buffer_percent, 20) as buffer_percent,
        (COALESCE(ucs.hours_per_day, 8) * COALESCE(ucs.days_per_week, 5)) as weekly_capacity,
        (COALESCE(ucs.hours_per_day, 8) * COALESCE(ucs.days_per_week, 5) *
         (100 - COALESCE(ucs.buffer_percent, 20)) / 100.0) as effective_weekly_capacity
    FROM users u
    LEFT JOIN user_capacity_settings ucs ON ucs.user_id = u.user_id
)
SELECT
    c.user_id,
    c.username,
    c.hours_per_day,
    c.days_per_week,
    c.weekly_capacity,
    c.effective_weekly_capacity,
    COALESCE(ts.total_tasks, 0) as total_tasks,
    COALESCE(ts.active_tasks, 0) as active_tasks,
    COALESCE(ts.pending_hours, 0) as pending_hours,
    COALESCE(ts.overdue_count, 0) as overdue_count,
    COALESCE(ts.due_soon_count, 0) as due_soon_count,
    ts.current_task_id,
    ts.current_task_ends,
    CASE
        WHEN c.effective_weekly_capacity > 0
        THEN ROUND((COALESCE(ts.pending_hours, 0) / c.effective_weekly_capacity * 100)::numeric, 1)
        ELSE 0
    END as utilization_percent,
    CASE
        WHEN c.effective_weekly_capacity > 0 THEN
            CASE
                WHEN (COALESCE(ts.pending_hours, 0) / c.effective_weekly_capacity * 100) > 120 THEN 'critical'
                WHEN (COALESCE(ts.pending_hours, 0) / c.effective_weekly_capacity * 100) > 100 THEN 'overloaded'
                WHEN (COALESCE(ts.pending_hours, 0) / c.effective_weekly_capacity * 100) > 80 THEN 'optimal'
                WHEN (COALESCE(ts.pending_hours, 0) / c.effective_weekly_capacity * 100) > 50 THEN 'normal'
                ELSE 'underloaded'
            END
        ELSE 'underloaded'
    END as workload_status,
    -- Calculate days until backlog is cleared
    CASE
        WHEN c.hours_per_day > 0
        THEN CEIL(COALESCE(ts.pending_hours, 0) / (c.hours_per_day * (100 - c.buffer_percent) / 100.0))
        ELSE 0
    END as days_to_clear_backlog,
    -- Earliest available date (when current backlog would be done)
    CURRENT_DATE + (
        CASE
            WHEN c.hours_per_day > 0
            THEN CEIL(COALESCE(ts.pending_hours, 0) / (c.hours_per_day * (100 - c.buffer_percent) / 100.0))
            ELSE 0
        END
    )::integer as earliest_available_date
FROM capacity c
LEFT JOIN task_stats ts ON ts.user_id = c.user_id;

-- ============================================
-- 6. FUNCTION: Calculate Next Available Slot
-- ============================================
-- Given a user and estimated hours, find the next available time slot

CREATE OR REPLACE FUNCTION get_next_available_slot(
    p_user_id UUID,
    p_estimated_hours DECIMAL DEFAULT 2
) RETURNS JSON AS $$
DECLARE
    v_capacity RECORD;
    v_pending_hours DECIMAL;
    v_current_task_ends TIMESTAMP WITH TIME ZONE;
    v_available_date DATE;
    v_hours_per_day DECIMAL;
    v_buffer_factor DECIMAL;
    v_days_needed INTEGER;
BEGIN
    -- Get user capacity settings
    SELECT
        COALESCE(ucs.hours_per_day, 8) as hours_per_day,
        COALESCE(ucs.days_per_week, 5) as days_per_week,
        COALESCE(ucs.buffer_percent, 20) as buffer_percent,
        COALESCE(ucs.working_days, ARRAY[1,2,3,4,5]) as working_days
    INTO v_capacity
    FROM users u
    LEFT JOIN user_capacity_settings ucs ON ucs.user_id = u.user_id
    WHERE u.user_id = p_user_id;

    IF NOT FOUND THEN
        RETURN json_build_object('error', 'User not found');
    END IF;

    v_hours_per_day := v_capacity.hours_per_day;
    v_buffer_factor := (100 - v_capacity.buffer_percent) / 100.0;

    -- Get pending hours and current task end
    SELECT
        COALESCE(SUM(CASE WHEN ts.task_status IN ('Not Started', 'Working on It')
            THEN COALESCE(t.estimated_hours, 2) ELSE 0 END), 0),
        MAX(t.scheduled_end_date) FILTER (WHERE ts.task_status IN ('Not Started', 'Working on It'))
    INTO v_pending_hours, v_current_task_ends
    FROM tasks t
    LEFT JOIN tasks_status ts ON ts.task_status_id = t.task_status
    WHERE t."Owner_id" = p_user_id;

    -- Calculate days needed to clear current backlog
    v_days_needed := CEIL(v_pending_hours / (v_hours_per_day * v_buffer_factor));

    -- Calculate available date (skip weekends if not working days)
    v_available_date := CURRENT_DATE;
    WHILE v_days_needed > 0 LOOP
        v_available_date := v_available_date + 1;
        -- Check if this is a working day
        IF EXTRACT(DOW FROM v_available_date)::integer = ANY(v_capacity.working_days) THEN
            -- Check for time off
            IF NOT EXISTS (
                SELECT 1 FROM user_time_off
                WHERE user_id = p_user_id
                AND v_available_date BETWEEN start_date AND end_date
            ) THEN
                v_days_needed := v_days_needed - 1;
            END IF;
        END IF;
    END LOOP;

    -- Calculate end date for the new task
    DECLARE
        v_task_days INTEGER := CEIL(p_estimated_hours / (v_hours_per_day * v_buffer_factor));
        v_end_date DATE := v_available_date;
    BEGIN
        WHILE v_task_days > 0 LOOP
            v_end_date := v_end_date + 1;
            IF EXTRACT(DOW FROM v_end_date)::integer = ANY(v_capacity.working_days) THEN
                IF NOT EXISTS (
                    SELECT 1 FROM user_time_off
                    WHERE user_id = p_user_id
                    AND v_end_date BETWEEN start_date AND end_date
                ) THEN
                    v_task_days := v_task_days - 1;
                END IF;
            END IF;
        END LOOP;

        RETURN json_build_object(
            'user_id', p_user_id,
            'current_pending_hours', v_pending_hours,
            'estimated_hours', p_estimated_hours,
            'available_start_date', v_available_date,
            'estimated_end_date', v_end_date,
            'days_until_available', v_available_date - CURRENT_DATE,
            'hours_per_day', v_hours_per_day,
            'effective_hours_per_day', v_hours_per_day * v_buffer_factor
        );
    END;
END;
$$ LANGUAGE plpgsql;

-- ============================================
-- 7. FUNCTION: Auto-Schedule Task
-- ============================================
-- Automatically schedules a task and creates dependencies if needed

CREATE OR REPLACE FUNCTION auto_schedule_task(
    p_task_id UUID
) RETURNS JSON AS $$
DECLARE
    v_task RECORD;
    v_owner_id UUID;
    v_estimated_hours DECIMAL;
    v_blocking_task_id UUID;
    v_slot JSON;
    v_dependency_created BOOLEAN := FALSE;
BEGIN
    -- Get task info
    SELECT t.*, ts.task_status
    INTO v_task
    FROM tasks t
    LEFT JOIN tasks_status ts ON ts.task_status_id = t.task_status
    WHERE t.task_id = p_task_id;

    IF NOT FOUND THEN
        RETURN json_build_object('success', false, 'error', 'Task not found');
    END IF;

    v_owner_id := v_task."Owner_id";
    v_estimated_hours := COALESCE(v_task.estimated_hours, 2);

    IF v_owner_id IS NULL THEN
        RETURN json_build_object('success', false, 'error', 'Task has no owner assigned');
    END IF;

    -- Find the last task in the user's queue (the one that would block this one)
    SELECT t.task_id INTO v_blocking_task_id
    FROM tasks t
    LEFT JOIN tasks_status ts ON ts.task_status_id = t.task_status
    WHERE t."Owner_id" = v_owner_id
      AND t.task_id != p_task_id
      AND ts.task_status IN ('Not Started', 'Working on It')
    ORDER BY t.queue_position DESC NULLS FIRST, t.scheduled_end_date DESC NULLS FIRST
    LIMIT 1;

    -- Get next available slot
    v_slot := get_next_available_slot(v_owner_id, v_estimated_hours);

    -- Update task with scheduled dates
    UPDATE tasks SET
        scheduled_start_date = (v_slot->>'available_start_date')::timestamp with time zone,
        scheduled_end_date = (v_slot->>'estimated_end_date')::timestamp with time zone,
        scheduling_status = 'scheduled',
        queue_position = COALESCE((
            SELECT MAX(queue_position) + 1
            FROM tasks
            WHERE "Owner_id" = v_owner_id AND queue_position IS NOT NULL
        ), 1)
    WHERE task_id = p_task_id;

    -- If there's a blocking task, create auto-dependency
    IF v_blocking_task_id IS NOT NULL THEN
        -- Check if dependency already exists
        IF NOT EXISTS (
            SELECT 1 FROM task_dependencies
            WHERE predecessor_task_id = v_blocking_task_id
              AND successor_task_id = p_task_id
        ) THEN
            INSERT INTO task_dependencies (predecessor_task_id, successor_task_id, dependency_type, is_auto_generated)
            VALUES (v_blocking_task_id, p_task_id, 'finish_to_start', TRUE)
            ON CONFLICT DO NOTHING;

            v_dependency_created := TRUE;

            -- Mark task as auto-linked
            UPDATE tasks SET
                auto_linked = TRUE,
                blocked_by_task_id = v_blocking_task_id
            WHERE task_id = p_task_id;
        END IF;
    END IF;

    RETURN json_build_object(
        'success', true,
        'task_id', p_task_id,
        'scheduled_start', v_slot->>'available_start_date',
        'scheduled_end', v_slot->>'estimated_end_date',
        'blocking_task_id', v_blocking_task_id,
        'dependency_created', v_dependency_created,
        'queue_position', (SELECT queue_position FROM tasks WHERE task_id = p_task_id)
    );
END;
$$ LANGUAGE plpgsql;

-- ============================================
-- 8. ADD is_auto_generated TO DEPENDENCIES
-- ============================================

ALTER TABLE task_dependencies ADD COLUMN IF NOT EXISTS is_auto_generated BOOLEAN DEFAULT FALSE;

-- ============================================
-- 9. FUNCTION: Recalculate User Schedule
-- ============================================
-- Recalculates all scheduled dates for a user's tasks

CREATE OR REPLACE FUNCTION recalculate_user_schedule(
    p_user_id UUID
) RETURNS JSON AS $$
DECLARE
    v_task RECORD;
    v_current_date DATE := CURRENT_DATE;
    v_capacity RECORD;
    v_hours_remaining DECIMAL;
    v_task_count INTEGER := 0;
BEGIN
    -- Get user capacity
    SELECT
        COALESCE(ucs.hours_per_day, 8) as hours_per_day,
        COALESCE(ucs.buffer_percent, 20) as buffer_percent,
        COALESCE(ucs.working_days, ARRAY[1,2,3,4,5]) as working_days
    INTO v_capacity
    FROM users u
    LEFT JOIN user_capacity_settings ucs ON ucs.user_id = u.user_id
    WHERE u.user_id = p_user_id;

    v_hours_remaining := v_capacity.hours_per_day * (100 - v_capacity.buffer_percent) / 100.0;

    -- Process tasks in order (by priority, deadline, then queue position)
    FOR v_task IN
        SELECT t.task_id, COALESCE(t.estimated_hours, 2) as estimated_hours
        FROM tasks t
        LEFT JOIN tasks_status ts ON ts.task_status_id = t.task_status
        WHERE t."Owner_id" = p_user_id
          AND ts.task_status IN ('Not Started', 'Working on It')
        ORDER BY
            CASE WHEN t.deadline IS NOT NULL THEN 0 ELSE 1 END,
            t.deadline ASC NULLS LAST,
            t.queue_position ASC NULLS LAST,
            t.created_at ASC
    LOOP
        -- Find next working day if needed
        WHILE NOT (EXTRACT(DOW FROM v_current_date)::integer = ANY(v_capacity.working_days))
              OR EXISTS (SELECT 1 FROM user_time_off WHERE user_id = p_user_id
                        AND v_current_date BETWEEN start_date AND end_date)
        LOOP
            v_current_date := v_current_date + 1;
            v_hours_remaining := v_capacity.hours_per_day * (100 - v_capacity.buffer_percent) / 100.0;
        END LOOP;

        -- Calculate task duration in days
        DECLARE
            v_start_date DATE := v_current_date;
            v_hours_needed DECIMAL := v_task.estimated_hours;
            v_end_date DATE;
        BEGIN
            -- Consume hours from current day
            IF v_hours_needed <= v_hours_remaining THEN
                v_hours_remaining := v_hours_remaining - v_hours_needed;
                v_end_date := v_current_date;
            ELSE
                v_hours_needed := v_hours_needed - v_hours_remaining;
                v_current_date := v_current_date + 1;
                v_hours_remaining := v_capacity.hours_per_day * (100 - v_capacity.buffer_percent) / 100.0;

                -- Continue adding days until task fits
                WHILE v_hours_needed > 0 LOOP
                    -- Skip non-working days
                    WHILE NOT (EXTRACT(DOW FROM v_current_date)::integer = ANY(v_capacity.working_days))
                          OR EXISTS (SELECT 1 FROM user_time_off WHERE user_id = p_user_id
                                    AND v_current_date BETWEEN start_date AND end_date)
                    LOOP
                        v_current_date := v_current_date + 1;
                    END LOOP;

                    IF v_hours_needed <= v_hours_remaining THEN
                        v_hours_remaining := v_hours_remaining - v_hours_needed;
                        v_hours_needed := 0;
                    ELSE
                        v_hours_needed := v_hours_needed - v_hours_remaining;
                        v_current_date := v_current_date + 1;
                        v_hours_remaining := v_capacity.hours_per_day * (100 - v_capacity.buffer_percent) / 100.0;
                    END IF;
                END LOOP;

                v_end_date := v_current_date;
            END IF;

            -- Update task
            UPDATE tasks SET
                scheduled_start_date = v_start_date,
                scheduled_end_date = v_end_date,
                scheduling_status = 'scheduled',
                queue_position = v_task_count + 1
            WHERE task_id = v_task.task_id;

            v_task_count := v_task_count + 1;
        END;
    END LOOP;

    RETURN json_build_object(
        'success', true,
        'user_id', p_user_id,
        'tasks_scheduled', v_task_count,
        'schedule_ends', v_current_date
    );
END;
$$ LANGUAGE plpgsql;

-- ============================================
-- 10. TRIGGER: Auto-schedule on task assignment
-- ============================================

CREATE OR REPLACE FUNCTION trigger_auto_schedule_task()
RETURNS TRIGGER AS $$
BEGIN
    -- Only auto-schedule if owner changed and new owner is set
    IF (TG_OP = 'INSERT' AND NEW."Owner_id" IS NOT NULL) OR
       (TG_OP = 'UPDATE' AND NEW."Owner_id" IS DISTINCT FROM OLD."Owner_id" AND NEW."Owner_id" IS NOT NULL) THEN
        -- Schedule the task asynchronously (just mark for scheduling)
        NEW.scheduling_status := 'pending_schedule';
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Note: Enable this trigger only if you want fully automatic scheduling
-- DROP TRIGGER IF EXISTS trigger_task_auto_schedule ON tasks;
-- CREATE TRIGGER trigger_task_auto_schedule
--     BEFORE INSERT OR UPDATE OF "Owner_id" ON tasks
--     FOR EACH ROW
--     EXECUTE FUNCTION trigger_auto_schedule_task();

-- ============================================
-- 11. RLS POLICIES
-- ============================================

ALTER TABLE user_capacity_settings ENABLE ROW LEVEL SECURITY;
ALTER TABLE user_time_off ENABLE ROW LEVEL SECURITY;
ALTER TABLE workload_snapshots ENABLE ROW LEVEL SECURITY;

-- Allow all authenticated users to read capacity settings
CREATE POLICY "Users can view capacity settings"
    ON user_capacity_settings FOR SELECT USING (true);

CREATE POLICY "Users can manage own capacity"
    ON user_capacity_settings FOR ALL USING (true);

CREATE POLICY "Users can view time off"
    ON user_time_off FOR SELECT USING (true);

CREATE POLICY "Users can manage time off"
    ON user_time_off FOR ALL USING (true);

CREATE POLICY "Users can view workload snapshots"
    ON workload_snapshots FOR SELECT USING (true);

CREATE POLICY "System can insert workload snapshots"
    ON workload_snapshots FOR INSERT WITH CHECK (true);

-- ============================================
-- COMMENTS
-- ============================================

COMMENT ON TABLE user_capacity_settings IS 'Per-user work capacity configuration for scheduling';
COMMENT ON TABLE user_time_off IS 'User unavailability periods (vacation, sick, etc.)';
COMMENT ON TABLE workload_snapshots IS 'Historical workload tracking for analytics and burnout detection';
COMMENT ON COLUMN tasks.scheduled_start_date IS 'Auto-calculated start date based on user availability';
COMMENT ON COLUMN tasks.scheduled_end_date IS 'Auto-calculated end date based on estimated hours';
COMMENT ON COLUMN tasks.queue_position IS 'Position in user task queue (lower = higher priority)';
COMMENT ON COLUMN tasks.auto_linked IS 'True if this task was auto-linked due to workload constraints';
COMMENT ON FUNCTION get_next_available_slot IS 'Calculates next available time slot for a user';
COMMENT ON FUNCTION auto_schedule_task IS 'Schedules a task and creates auto-dependencies if needed';
COMMENT ON FUNCTION recalculate_user_schedule IS 'Recalculates all scheduled dates for a user';
