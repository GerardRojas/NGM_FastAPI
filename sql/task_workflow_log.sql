-- =============================================
-- TASK WORKFLOW LOG - Audit trail for task status changes
-- =============================================
-- Tracks all workflow events: start, submit, approve, reject, etc.
-- Used by administration and coordination for auditing
-- =============================================

-- 1. Create the workflow log table
CREATE TABLE IF NOT EXISTS public.task_workflow_log (
    log_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- Task reference
    task_id UUID NOT NULL REFERENCES public.tasks(task_id) ON DELETE CASCADE,

    -- Event information
    event_type VARCHAR(50) NOT NULL,
    -- Event types: 'started', 'submitted_for_review', 'approved', 'rejected',
    --              'converted_to_coordination', 'reassigned', 'status_changed'

    -- Status tracking
    old_status UUID REFERENCES public.tasks_status(task_status_id),
    new_status UUID REFERENCES public.tasks_status(task_status_id),

    -- Actor (who performed this action)
    performed_by UUID REFERENCES public.users(user_id),

    -- Timing
    performed_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),

    -- Related task (for review workflows)
    related_task_id UUID REFERENCES public.tasks(task_id),

    -- Additional data
    notes TEXT,
    attachments TEXT[], -- Array of file URLs/links
    metadata JSONB DEFAULT '{}'
);

-- 2. Create indexes for fast lookups
CREATE INDEX IF NOT EXISTS idx_task_workflow_log_task ON public.task_workflow_log(task_id);
CREATE INDEX IF NOT EXISTS idx_task_workflow_log_event ON public.task_workflow_log(event_type);
CREATE INDEX IF NOT EXISTS idx_task_workflow_log_date ON public.task_workflow_log(performed_at DESC);
CREATE INDEX IF NOT EXISTS idx_task_workflow_log_user ON public.task_workflow_log(performed_by);
CREATE INDEX IF NOT EXISTS idx_task_workflow_log_related ON public.task_workflow_log(related_task_id) WHERE related_task_id IS NOT NULL;

-- 3. RLS Policies
ALTER TABLE public.task_workflow_log ENABLE ROW LEVEL SECURITY;

-- Policy: Anyone authenticated can read logs
CREATE POLICY "task_workflow_log_select" ON public.task_workflow_log
    FOR SELECT TO authenticated USING (true);

-- Policy: Only authenticated users can insert (logs are created via app)
CREATE POLICY "task_workflow_log_insert" ON public.task_workflow_log
    FOR INSERT TO authenticated WITH CHECK (true);

-- 4. Add new columns to tasks table for review workflow
ALTER TABLE public.tasks ADD COLUMN IF NOT EXISTS parent_task_id UUID REFERENCES public.tasks(task_id);
ALTER TABLE public.tasks ADD COLUMN IF NOT EXISTS review_task_id UUID REFERENCES public.tasks(task_id);
ALTER TABLE public.tasks ADD COLUMN IF NOT EXISTS reviewer_notes TEXT;
ALTER TABLE public.tasks ADD COLUMN IF NOT EXISTS rejection_count INTEGER DEFAULT 0;
ALTER TABLE public.tasks ADD COLUMN IF NOT EXISTS is_coordination_task BOOLEAN DEFAULT FALSE;
ALTER TABLE public.tasks ADD COLUMN IF NOT EXISTS converted_from_task_id UUID REFERENCES public.tasks(task_id);
ALTER TABLE public.tasks ADD COLUMN IF NOT EXISTS workflow_state VARCHAR(30) DEFAULT 'active';
-- workflow_state: 'active', 'in_review', 'completed', 'converted'

-- 5. Index for workflow queries
CREATE INDEX IF NOT EXISTS idx_tasks_parent ON public.tasks(parent_task_id) WHERE parent_task_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_tasks_review ON public.tasks(review_task_id) WHERE review_task_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_tasks_coordination ON public.tasks(is_coordination_task) WHERE is_coordination_task = TRUE;
CREATE INDEX IF NOT EXISTS idx_tasks_workflow_state ON public.tasks(workflow_state);

-- 6. Comments
COMMENT ON TABLE public.task_workflow_log IS 'Audit trail for all task workflow events - start, review, approve, reject, etc.';
COMMENT ON COLUMN public.task_workflow_log.event_type IS 'Type: started, submitted_for_review, approved, rejected, converted_to_coordination, reassigned, status_changed';
COMMENT ON COLUMN public.task_workflow_log.related_task_id IS 'For review events: links to the reviewer task or original task';
COMMENT ON COLUMN public.task_workflow_log.attachments IS 'Array of file URLs or links submitted with the event';
COMMENT ON COLUMN public.task_workflow_log.metadata IS 'Additional context: elapsed_time, rejection_reason, conversion_details, etc.';

COMMENT ON COLUMN public.tasks.parent_task_id IS 'For review tasks: points to the original task being reviewed';
COMMENT ON COLUMN public.tasks.review_task_id IS 'For original tasks: points to the current review task if in review';
COMMENT ON COLUMN public.tasks.reviewer_notes IS 'Notes from reviewer (approval comments or rejection reasons)';
COMMENT ON COLUMN public.tasks.rejection_count IS 'Number of times this task has been rejected';
COMMENT ON COLUMN public.tasks.is_coordination_task IS 'TRUE if this task was converted from approved task for coordination';
COMMENT ON COLUMN public.tasks.converted_from_task_id IS 'For coordination tasks: reference to original approved task';
COMMENT ON COLUMN public.tasks.workflow_state IS 'Current workflow state: active, in_review, completed, converted';

-- 7. View for workflow audit report
CREATE OR REPLACE VIEW v_task_workflow_audit AS
SELECT
    l.log_id,
    l.task_id,
    t.task_description,
    l.event_type,
    os.task_status as old_status_name,
    ns.task_status as new_status_name,
    l.performed_by,
    u.user_name as performed_by_name,
    l.performed_at,
    l.notes,
    l.attachments,
    l.related_task_id,
    rt.task_description as related_task_description,
    p.project_name,
    l.metadata
FROM task_workflow_log l
JOIN tasks t ON t.task_id = l.task_id
LEFT JOIN tasks_status os ON os.task_status_id = l.old_status
LEFT JOIN tasks_status ns ON ns.task_status_id = l.new_status
LEFT JOIN users u ON u.user_id = l.performed_by
LEFT JOIN tasks rt ON rt.task_id = l.related_task_id
LEFT JOIN projects p ON p.project_id = t.project_id
ORDER BY l.performed_at DESC;

COMMENT ON VIEW v_task_workflow_audit IS 'Formatted audit log view for administration reports';
