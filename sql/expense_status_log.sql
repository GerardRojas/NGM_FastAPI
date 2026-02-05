-- =============================================
-- EXPENSE STATUS LOG - Audit trail for status changes
-- =============================================
-- Tracks all status changes for expenses to analyze
-- categorization error rates and approval workflows
-- =============================================

-- Status log table
CREATE TABLE IF NOT EXISTS public.expense_status_log (
    id uuid DEFAULT gen_random_uuid() PRIMARY KEY,
    expense_id uuid NOT NULL REFERENCES public."expenses_manual_COGS"(expense_id) ON DELETE CASCADE,
    old_status text,
    new_status text NOT NULL,
    changed_by uuid REFERENCES public.users(user_id),
    changed_at timestamp with time zone DEFAULT now(),
    reason text,
    metadata jsonb DEFAULT '{}'
);

-- Indexes for fast lookups
CREATE INDEX IF NOT EXISTS idx_expense_status_log_expense ON public.expense_status_log(expense_id);
CREATE INDEX IF NOT EXISTS idx_expense_status_log_status ON public.expense_status_log(new_status);
CREATE INDEX IF NOT EXISTS idx_expense_status_log_date ON public.expense_status_log(changed_at DESC);
CREATE INDEX IF NOT EXISTS idx_expense_status_log_user ON public.expense_status_log(changed_by);

-- RLS Policies
ALTER TABLE public.expense_status_log ENABLE ROW LEVEL SECURITY;

-- Policy: Anyone authenticated can read
CREATE POLICY "expense_status_log_select" ON public.expense_status_log
    FOR SELECT TO authenticated USING (true);

-- Policy: Only authenticated users can insert (logs are created via app)
CREATE POLICY "expense_status_log_insert" ON public.expense_status_log
    FOR INSERT TO authenticated WITH CHECK (true);

-- Comments
COMMENT ON TABLE public.expense_status_log IS 'Audit trail of expense status changes for metrics and analysis';
COMMENT ON COLUMN public.expense_status_log.old_status IS 'Previous status: pending, auth, review';
COMMENT ON COLUMN public.expense_status_log.new_status IS 'New status: pending, auth, review';
COMMENT ON COLUMN public.expense_status_log.reason IS 'Optional reason for status change';
COMMENT ON COLUMN public.expense_status_log.metadata IS 'Additional data: field_changes, categorization_error, deleted, etc.';

-- =============================================
-- EXPENSE CHANGE LOG - Detailed field changes
-- =============================================
-- Tracks all field-level changes to expenses in review status
CREATE TABLE IF NOT EXISTS public.expense_change_log (
    id uuid DEFAULT gen_random_uuid() PRIMARY KEY,
    expense_id uuid NOT NULL REFERENCES public."expenses_manual_COGS"(expense_id) ON DELETE CASCADE,
    field_name text NOT NULL,
    old_value text,
    new_value text,
    changed_by uuid REFERENCES public.users(user_id),
    changed_at timestamp with time zone DEFAULT now(),
    expense_status text NOT NULL,
    change_reason text
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_expense_change_log_expense ON public.expense_change_log(expense_id);
CREATE INDEX IF NOT EXISTS idx_expense_change_log_field ON public.expense_change_log(field_name);
CREATE INDEX IF NOT EXISTS idx_expense_change_log_date ON public.expense_change_log(changed_at DESC);

-- RLS
ALTER TABLE public.expense_change_log ENABLE ROW LEVEL SECURITY;

CREATE POLICY "expense_change_log_select" ON public.expense_change_log
    FOR SELECT TO authenticated USING (true);

CREATE POLICY "expense_change_log_insert" ON public.expense_change_log
    FOR INSERT TO authenticated WITH CHECK (true);

-- Comments
COMMENT ON TABLE public.expense_change_log IS 'Detailed audit trail of field-level changes to expenses';
COMMENT ON COLUMN public.expense_change_log.field_name IS 'Name of the field that changed (e.g., account_id, Amount, LineDescription)';
COMMENT ON COLUMN public.expense_change_log.old_value IS 'Previous value as string';
COMMENT ON COLUMN public.expense_change_log.new_value IS 'New value as string';
COMMENT ON COLUMN public.expense_change_log.expense_status IS 'Status of expense when change occurred (pending, auth, review)';
COMMENT ON COLUMN public.expense_change_log.change_reason IS 'Reason for change (client correction, categorization error, etc.)';

-- =============================================
-- ALTER expenses_manual_COGS table
-- =============================================
-- Add status column (transition from auth_status boolean)
ALTER TABLE public."expenses_manual_COGS"
ADD COLUMN IF NOT EXISTS status text DEFAULT 'pending';

-- Create index on status for fast filtering
CREATE INDEX IF NOT EXISTS idx_expenses_status ON public."expenses_manual_COGS"(status);

-- Add constraint to ensure valid status values
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'check_expense_status'
    ) THEN
        ALTER TABLE public."expenses_manual_COGS"
        ADD CONSTRAINT check_expense_status
        CHECK (status IN ('pending', 'auth', 'review'));
    END IF;
END $$;

-- Update existing records: migrate auth_status boolean to status text
UPDATE public."expenses_manual_COGS"
SET status = CASE
    WHEN auth_status = true THEN 'auth'
    WHEN auth_status = false OR auth_status IS NULL THEN 'pending'
    ELSE 'pending'
END
WHERE status IS NULL OR status = 'pending';

-- Comments
COMMENT ON COLUMN public."expenses_manual_COGS".status IS 'Status: pending (initial), auth (approved by manager), review (flagged for review by manager/COO/CEO)';
COMMENT ON COLUMN public."expenses_manual_COGS".auth_status IS 'DEPRECATED: Use status column instead. Kept for backwards compatibility.';
