-- ================================
-- Pending Receipts Schema
-- ================================
-- Tracks receipts uploaded to project channels for expense processing
-- Enables automation: upload receipt → auto-create expense

-- ================================
-- Main Table: pending_receipts
-- ================================

CREATE TABLE IF NOT EXISTS pending_receipts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- Project & Channel Context
    project_id UUID NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
    message_id UUID REFERENCES messages(id) ON DELETE SET NULL,  -- Optional link to message

    -- File Information
    file_name TEXT NOT NULL,
    file_url TEXT NOT NULL,                    -- Supabase Storage URL
    file_type TEXT NOT NULL,                   -- MIME type (image/jpeg, application/pdf, etc.)
    file_size INTEGER,                         -- Size in bytes
    thumbnail_url TEXT,                        -- For quick preview

    -- OCR/Parsed Data (from receipt scan)
    parsed_data JSONB DEFAULT '{}',            -- Full OCR response
    vendor_name TEXT,                          -- Extracted vendor
    amount DECIMAL(12,2),                      -- Extracted amount
    receipt_date DATE,                         -- Extracted date
    suggested_category TEXT,                   -- AI-suggested category
    suggested_account_id UUID,                 -- Matched account from accounts table

    -- Processing Status
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN (
        'pending',      -- Uploaded, not processed
        'processing',   -- OCR in progress
        'ready',        -- Parsed, ready to create expense
        'linked',       -- Linked to an expense
        'rejected',     -- Manually rejected/invalid
        'error'         -- Processing error
    )),
    processing_error TEXT,                     -- Error message if failed

    -- Expense Link (once converted)
    expense_id UUID REFERENCES "expenses_manual_COGS"(expense_id) ON DELETE SET NULL,

    -- Audit Fields
    uploaded_by UUID NOT NULL REFERENCES users(user_id),
    processed_at TIMESTAMPTZ,
    linked_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- ================================
-- Indexes
-- ================================

-- Fast lookup by project
CREATE INDEX IF NOT EXISTS idx_pending_receipts_project
ON pending_receipts(project_id);

-- Fast lookup by status for processing queue
CREATE INDEX IF NOT EXISTS idx_pending_receipts_status
ON pending_receipts(status) WHERE status IN ('pending', 'processing', 'ready');

-- Fast lookup by uploader
CREATE INDEX IF NOT EXISTS idx_pending_receipts_uploader
ON pending_receipts(uploaded_by);

-- Fast lookup of unlinked receipts
CREATE INDEX IF NOT EXISTS idx_pending_receipts_unlinked
ON pending_receipts(project_id, status) WHERE expense_id IS NULL;

-- ================================
-- Updated_at Trigger
-- ================================

CREATE OR REPLACE FUNCTION update_pending_receipts_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trigger_pending_receipts_updated_at ON pending_receipts;
CREATE TRIGGER trigger_pending_receipts_updated_at
    BEFORE UPDATE ON pending_receipts
    FOR EACH ROW
    EXECUTE FUNCTION update_pending_receipts_updated_at();

-- ================================
-- RLS Policies
-- ================================

ALTER TABLE pending_receipts ENABLE ROW LEVEL SECURITY;

-- Service role has full access (API uses service role key)
CREATE POLICY "Service role has full access"
ON pending_receipts FOR ALL
USING (auth.role() = 'service_role')
WITH CHECK (auth.role() = 'service_role');

-- Authenticated users can view all receipts (simplified - no project_members table)
CREATE POLICY "Authenticated users can view receipts"
ON pending_receipts FOR SELECT
USING (auth.role() = 'authenticated');

-- Users can insert receipts
CREATE POLICY "Authenticated users can upload receipts"
ON pending_receipts FOR INSERT
WITH CHECK (auth.role() = 'authenticated');

-- Users can update their own receipts
CREATE POLICY "Users can update own receipts"
ON pending_receipts FOR UPDATE
USING (uploaded_by = auth.uid());

-- ================================
-- Storage Bucket Setup (run in Supabase Dashboard or via API)
-- ================================

-- Create bucket for pending expenses receipts
-- Note: Run this via Supabase Dashboard > Storage > New Bucket
-- Or via supabase.storage.createBucket() in code

/*
Bucket Configuration:
- Name: pending-expenses
- Public: true (for displaying images)
- File size limit: 20MB
- Allowed MIME types: image/jpeg, image/png, image/webp, image/gif, application/pdf

Path structure:
pending-expenses/
  └── {project_id}/
      └── {receipt_id}_{original_filename}

Example:
pending-expenses/abc123/rec_456_invoice.pdf
*/

-- ================================
-- Helper View: Pending Receipts with Project Info
-- ================================

CREATE OR REPLACE VIEW pending_receipts_view AS
SELECT
    pr.*,
    p.project_name,
    u.user_name AS uploader_name,
    u.avatar_color AS uploader_avatar_color,
    a."Name" AS suggested_account_name
FROM pending_receipts pr
LEFT JOIN projects p ON pr.project_id = p.project_id
LEFT JOIN users u ON pr.uploaded_by = u.user_id
LEFT JOIN accounts a ON pr.suggested_account_id = a.account_id;

-- ================================
-- Function: Get pending receipts count per project
-- ================================

CREATE OR REPLACE FUNCTION get_pending_receipts_count(p_project_id UUID)
RETURNS TABLE(
    total INTEGER,
    pending INTEGER,
    ready INTEGER,
    processing INTEGER
) AS $$
BEGIN
    RETURN QUERY
    SELECT
        COUNT(*)::INTEGER as total,
        COUNT(*) FILTER (WHERE status = 'pending')::INTEGER as pending,
        COUNT(*) FILTER (WHERE status = 'ready')::INTEGER as ready,
        COUNT(*) FILTER (WHERE status = 'processing')::INTEGER as processing
    FROM pending_receipts
    WHERE project_id = p_project_id
    AND expense_id IS NULL;
END;
$$ LANGUAGE plpgsql;
