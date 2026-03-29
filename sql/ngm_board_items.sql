-- =============================================
-- NGM BOARD ITEMS - Dedicated tables for boards, tables, folders
-- =============================================
-- Replaces the generic key-value storage for board/table/folder registry
-- with proper structured tables including full audit trail.
-- =============================================

-- 1. Unified registry for boards, tables, and folders
CREATE TABLE IF NOT EXISTS public.ngm_board_items (
    id TEXT PRIMARY KEY,                -- board_xxx, table_xxx, folder_xxx
    item_type TEXT NOT NULL CHECK (item_type IN ('board', 'table', 'folder')),
    name TEXT NOT NULL,
    folder_id TEXT REFERENCES public.ngm_board_items(id) ON DELETE SET NULL,

    -- Board-specific fields
    board_type TEXT CHECK (board_type IN ('process', 'freeform')),

    -- Table-specific fields
    cols INTEGER DEFAULT 5,
    rows INTEGER DEFAULT 10,

    -- Audit
    created_by UUID REFERENCES public.users(user_id),
    updated_by UUID REFERENCES public.users(user_id),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_ngm_board_items_type ON public.ngm_board_items(item_type);
CREATE INDEX IF NOT EXISTS idx_ngm_board_items_folder ON public.ngm_board_items(folder_id);

-- Auto-update updated_at trigger
CREATE OR REPLACE FUNCTION update_ngm_board_items_timestamp()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trigger_ngm_board_items_updated_at ON public.ngm_board_items;
CREATE TRIGGER trigger_ngm_board_items_updated_at
    BEFORE UPDATE ON public.ngm_board_items
    FOR EACH ROW
    EXECUTE FUNCTION update_ngm_board_items_timestamp();


-- 2. Table cell data (one row per table)
CREATE TABLE IF NOT EXISTS public.ngm_board_table_data (
    table_id TEXT PRIMARY KEY REFERENCES public.ngm_board_items(id) ON DELETE CASCADE,
    cell_data JSONB NOT NULL DEFAULT '{}',
    column_headers JSONB NOT NULL DEFAULT '{}',
    updated_by UUID REFERENCES public.users(user_id),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Auto-update updated_at trigger for table data
DROP TRIGGER IF EXISTS trigger_ngm_board_table_data_updated_at ON public.ngm_board_table_data;
CREATE TRIGGER trigger_ngm_board_table_data_updated_at
    BEFORE UPDATE ON public.ngm_board_table_data
    FOR EACH ROW
    EXECUTE FUNCTION update_ngm_board_items_timestamp();


-- 3. History / audit trail
CREATE TABLE IF NOT EXISTS public.ngm_board_history (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    item_id TEXT NOT NULL,
    action TEXT NOT NULL CHECK (action IN ('create', 'update', 'delete', 'cell_edit')),
    changed_by UUID REFERENCES public.users(user_id),
    changed_at TIMESTAMPTZ DEFAULT NOW(),
    metadata JSONB DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_ngm_board_history_item ON public.ngm_board_history(item_id);
CREATE INDEX IF NOT EXISTS idx_ngm_board_history_date ON public.ngm_board_history(changed_at DESC);

-- Trigger: log item changes to history
CREATE OR REPLACE FUNCTION log_ngm_board_item_changes()
RETURNS TRIGGER AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        INSERT INTO public.ngm_board_history (item_id, action, changed_by, metadata)
        VALUES (NEW.id, 'create', NEW.created_by, jsonb_build_object('name', NEW.name, 'item_type', NEW.item_type));
        RETURN NEW;
    ELSIF TG_OP = 'UPDATE' THEN
        INSERT INTO public.ngm_board_history (item_id, action, changed_by, metadata)
        VALUES (NEW.id, 'update', NEW.updated_by, jsonb_build_object('name', NEW.name));
        RETURN NEW;
    ELSIF TG_OP = 'DELETE' THEN
        INSERT INTO public.ngm_board_history (item_id, action, changed_by, metadata)
        VALUES (OLD.id, 'delete', OLD.updated_by, jsonb_build_object('name', OLD.name, 'item_type', OLD.item_type));
        RETURN OLD;
    END IF;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trigger_ngm_board_item_history ON public.ngm_board_items;
CREATE TRIGGER trigger_ngm_board_item_history
    AFTER INSERT OR UPDATE OR DELETE ON public.ngm_board_items
    FOR EACH ROW
    EXECUTE FUNCTION log_ngm_board_item_changes();


-- =============================================
-- RLS Policies
-- =============================================
ALTER TABLE public.ngm_board_items ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.ngm_board_table_data ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.ngm_board_history ENABLE ROW LEVEL SECURITY;

-- Items: authenticated can CRUD
CREATE POLICY "ngm_board_items_select" ON public.ngm_board_items
    FOR SELECT TO authenticated USING (true);
CREATE POLICY "ngm_board_items_insert" ON public.ngm_board_items
    FOR INSERT TO authenticated WITH CHECK (true);
CREATE POLICY "ngm_board_items_update" ON public.ngm_board_items
    FOR UPDATE TO authenticated USING (true) WITH CHECK (true);
CREATE POLICY "ngm_board_items_delete" ON public.ngm_board_items
    FOR DELETE TO authenticated USING (true);

-- Table data: authenticated can CRUD
CREATE POLICY "ngm_board_table_data_select" ON public.ngm_board_table_data
    FOR SELECT TO authenticated USING (true);
CREATE POLICY "ngm_board_table_data_insert" ON public.ngm_board_table_data
    FOR INSERT TO authenticated WITH CHECK (true);
CREATE POLICY "ngm_board_table_data_update" ON public.ngm_board_table_data
    FOR UPDATE TO authenticated USING (true) WITH CHECK (true);
CREATE POLICY "ngm_board_table_data_delete" ON public.ngm_board_table_data
    FOR DELETE TO authenticated USING (true);

-- History: read-only
CREATE POLICY "ngm_board_history_select" ON public.ngm_board_history
    FOR SELECT TO authenticated USING (true);


-- =============================================
-- COMMENTS
-- =============================================
COMMENT ON TABLE public.ngm_board_items IS 'Unified registry for NGM Board items: boards, tables, and folders with full audit trail';
COMMENT ON TABLE public.ngm_board_table_data IS 'Cell data and column headers for spreadsheet tables';
COMMENT ON TABLE public.ngm_board_history IS 'Audit trail of all create/update/delete actions on board items';
COMMENT ON COLUMN public.ngm_board_items.id IS 'Prefixed ID: board_xxx, table_xxx, folder_xxx';
COMMENT ON COLUMN public.ngm_board_items.folder_id IS 'Parent folder ID for nesting. NULL = root level';
COMMENT ON COLUMN public.ngm_board_table_data.cell_data IS 'JSONB: { "A1": { "raw": "=SUM(A2:A5)", "computed": 42 }, ... }';
