-- =====================================================
-- CREATE PROCESS DRAFTS TABLE
-- For storing proposed processes not yet implemented in code
-- =====================================================

-- Create process_drafts table
CREATE TABLE IF NOT EXISTS public.process_drafts (
    id uuid DEFAULT gen_random_uuid() PRIMARY KEY,
    process_id text NOT NULL UNIQUE,  -- Unique identifier matching code annotations
    name text NOT NULL,
    category text NOT NULL DEFAULT 'operations',
    trigger_type text NOT NULL DEFAULT 'manual',
    description text,
    owner text,
    steps jsonb DEFAULT '[]'::jsonb,
    position jsonb,  -- {x, y} for canvas positioning
    status text NOT NULL DEFAULT 'draft',  -- draft, proposed, approved, rejected
    created_by uuid REFERENCES public.users(user_id),
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),

    -- Constraints
    CONSTRAINT valid_category CHECK (category IN ('coordination', 'bookkeeping', 'operations', 'finance', 'hr', 'sales', 'other')),
    CONSTRAINT valid_trigger CHECK (trigger_type IN ('manual', 'scheduled', 'event', 'webhook')),
    CONSTRAINT valid_status CHECK (status IN ('draft', 'proposed', 'approved', 'rejected'))
);

-- Create index on process_id for fast lookups
CREATE INDEX IF NOT EXISTS idx_process_drafts_process_id ON public.process_drafts(process_id);

-- Create index on category for filtering
CREATE INDEX IF NOT EXISTS idx_process_drafts_category ON public.process_drafts(category);

-- Create index on status for filtering
CREATE INDEX IF NOT EXISTS idx_process_drafts_status ON public.process_drafts(status);

-- Add comments
COMMENT ON TABLE public.process_drafts IS 'Stores draft/proposed processes that are not yet implemented in code';
COMMENT ON COLUMN public.process_drafts.process_id IS 'Unique identifier matching @process: annotation in code';
COMMENT ON COLUMN public.process_drafts.steps IS 'JSON array of process steps with number, name, type, description, connects_to';
COMMENT ON COLUMN public.process_drafts.position IS 'Canvas position {x, y} for visual layout';
COMMENT ON COLUMN public.process_drafts.status IS 'draft=working, proposed=submitted for review, approved=ready for implementation, rejected=not approved';

-- Enable RLS
ALTER TABLE public.process_drafts ENABLE ROW LEVEL SECURITY;

-- Policy: Anyone authenticated can read
CREATE POLICY "Allow read for authenticated users"
    ON public.process_drafts
    FOR SELECT
    TO authenticated
    USING (true);

-- Policy: Anyone authenticated can insert
CREATE POLICY "Allow insert for authenticated users"
    ON public.process_drafts
    FOR INSERT
    TO authenticated
    WITH CHECK (true);

-- Policy: Creator or admin can update
CREATE POLICY "Allow update for creator or admin"
    ON public.process_drafts
    FOR UPDATE
    TO authenticated
    USING (true);

-- Policy: Creator or admin can delete
CREATE POLICY "Allow delete for creator or admin"
    ON public.process_drafts
    FOR DELETE
    TO authenticated
    USING (true);

-- Function to auto-update updated_at
CREATE OR REPLACE FUNCTION update_process_drafts_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Trigger for updated_at
DROP TRIGGER IF EXISTS trigger_process_drafts_updated_at ON public.process_drafts;
CREATE TRIGGER trigger_process_drafts_updated_at
    BEFORE UPDATE ON public.process_drafts
    FOR EACH ROW
    EXECUTE FUNCTION update_process_drafts_updated_at();

-- =====================================================
-- SAMPLE DATA (optional - for testing)
-- =====================================================

-- Insert a sample draft process
-- INSERT INTO public.process_drafts (process_id, name, category, trigger_type, description, steps, status)
-- VALUES (
--     'DRAFT_invoice_automation',
--     'Automated Invoice Processing',
--     'bookkeeping',
--     'event',
--     'Proposed workflow for automatically processing incoming invoices from email attachments',
--     '[
--         {"number": 1, "name": "Receive Email", "type": "event", "description": "Email with invoice attachment received", "connects_to": [2]},
--         {"number": 2, "name": "Extract Invoice Data", "type": "action", "description": "OCR and parse invoice details", "connects_to": [3]},
--         {"number": 3, "name": "Validate Data", "type": "condition", "description": "Check if all required fields are present", "connects_to": [4, 5]},
--         {"number": 4, "name": "Create Expense Entry", "type": "action", "description": "Create entry in expenses_manual_COGS", "connects_to": [6]},
--         {"number": 5, "name": "Flag for Review", "type": "notification", "description": "Notify accountant of validation issues", "connects_to": []},
--         {"number": 6, "name": "Notify Accountant", "type": "notification", "description": "Send notification for authorization", "connects_to": []}
--     ]'::jsonb,
--     'proposed'
-- );
