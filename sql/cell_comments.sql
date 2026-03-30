-- ============================================================
-- CELL COMMENTS — cross-module commenting system
-- Supports comments on any cell/row in any page/module
-- with @mentions and creator-only deletion
-- ============================================================

CREATE TABLE IF NOT EXISTS public.cell_comments (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    -- Location keys (what page, what record, what column)
    module          VARCHAR(50)  NOT NULL,          -- e.g. 'expenses', 'pipeline', 'budgets'
    record_id       VARCHAR(255) NOT NULL,          -- row/entity ID (expense_id, task_id, etc.)
    column_key      VARCHAR(100) DEFAULT NULL,      -- column name (null = row-level comment)
    -- Content
    body            TEXT         NOT NULL,
    mentions        UUID[]       DEFAULT '{}',      -- array of mentioned user_ids
    -- Ownership
    created_by      UUID         NOT NULL REFERENCES public.users(user_id) ON DELETE CASCADE,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    is_resolved     BOOLEAN      NOT NULL DEFAULT FALSE,
    resolved_by     UUID         REFERENCES public.users(user_id),
    resolved_at     TIMESTAMPTZ
);

-- Fast lookups: all comments for a given cell
CREATE INDEX IF NOT EXISTS idx_cell_comments_lookup
    ON public.cell_comments (module, record_id, column_key);

-- Fast lookups: comments mentioning a user
CREATE INDEX IF NOT EXISTS idx_cell_comments_mentions
    ON public.cell_comments USING GIN (mentions);

-- Fast lookups: comments by creator
CREATE INDEX IF NOT EXISTS idx_cell_comments_creator
    ON public.cell_comments (created_by);

-- Auto-update updated_at
CREATE OR REPLACE FUNCTION update_cell_comments_timestamp()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_cell_comments_updated ON public.cell_comments;
CREATE TRIGGER trg_cell_comments_updated
    BEFORE UPDATE ON public.cell_comments
    FOR EACH ROW
    EXECUTE FUNCTION update_cell_comments_timestamp();

-- RLS
ALTER TABLE public.cell_comments ENABLE ROW LEVEL SECURITY;

CREATE POLICY cell_comments_select ON public.cell_comments
    FOR SELECT TO authenticated USING (true);

CREATE POLICY cell_comments_insert ON public.cell_comments
    FOR INSERT TO authenticated WITH CHECK (true);

CREATE POLICY cell_comments_update ON public.cell_comments
    FOR UPDATE TO authenticated USING (created_by = auth.uid());

CREATE POLICY cell_comments_delete ON public.cell_comments
    FOR DELETE TO authenticated USING (created_by = auth.uid());

-- Documentation
COMMENT ON TABLE  public.cell_comments IS 'Cross-module cell-level commenting with @mentions';
COMMENT ON COLUMN public.cell_comments.module IS 'Page/module identifier: expenses, pipeline, budgets, etc.';
COMMENT ON COLUMN public.cell_comments.record_id IS 'ID of the row/entity being commented on';
COMMENT ON COLUMN public.cell_comments.column_key IS 'Column name for cell-level comments; NULL for row-level';
COMMENT ON COLUMN public.cell_comments.mentions IS 'Array of user_ids mentioned with @ in the comment';
COMMENT ON COLUMN public.cell_comments.is_resolved IS 'Whether the comment thread has been resolved';
