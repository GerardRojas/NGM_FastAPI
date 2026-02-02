-- =============================================
-- PROCESS MANAGER - Shared State Tables
-- =============================================
-- Stores the visual state of the process manager
-- Shared across all authorized users
-- =============================================

-- Main state table for process manager
CREATE TABLE IF NOT EXISTS public.process_manager_state (
    id uuid DEFAULT gen_random_uuid() PRIMARY KEY,
    state_key text UNIQUE NOT NULL,  -- 'node_positions', 'custom_modules', 'flow_positions', 'draft_states'
    state_data jsonb NOT NULL DEFAULT '{}',
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    updated_by uuid REFERENCES auth.users(id)
);

-- Index for fast lookups
CREATE INDEX IF NOT EXISTS idx_process_manager_state_key ON public.process_manager_state(state_key);

-- Trigger to auto-update updated_at
CREATE OR REPLACE FUNCTION update_process_manager_timestamp()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trigger_process_manager_updated_at ON public.process_manager_state;
CREATE TRIGGER trigger_process_manager_updated_at
    BEFORE UPDATE ON public.process_manager_state
    FOR EACH ROW
    EXECUTE FUNCTION update_process_manager_timestamp();

-- History table for audit trail and backup
CREATE TABLE IF NOT EXISTS public.process_manager_history (
    id uuid DEFAULT gen_random_uuid() PRIMARY KEY,
    state_key text NOT NULL,
    state_data jsonb NOT NULL,
    changed_by uuid REFERENCES auth.users(id),
    changed_at timestamp with time zone DEFAULT now(),
    change_type text NOT NULL  -- 'create', 'update', 'delete'
);

-- Index for history lookups
CREATE INDEX IF NOT EXISTS idx_process_manager_history_key ON public.process_manager_history(state_key);
CREATE INDEX IF NOT EXISTS idx_process_manager_history_date ON public.process_manager_history(changed_at DESC);

-- Trigger to log changes to history
CREATE OR REPLACE FUNCTION log_process_manager_changes()
RETURNS TRIGGER AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        INSERT INTO public.process_manager_history (state_key, state_data, changed_by, change_type)
        VALUES (NEW.state_key, NEW.state_data, NEW.updated_by, 'create');
        RETURN NEW;
    ELSIF TG_OP = 'UPDATE' THEN
        INSERT INTO public.process_manager_history (state_key, state_data, changed_by, change_type)
        VALUES (NEW.state_key, NEW.state_data, NEW.updated_by, 'update');
        RETURN NEW;
    ELSIF TG_OP = 'DELETE' THEN
        INSERT INTO public.process_manager_history (state_key, state_data, changed_by, change_type)
        VALUES (OLD.state_key, OLD.state_data, OLD.updated_by, 'delete');
        RETURN OLD;
    END IF;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trigger_process_manager_history ON public.process_manager_state;
CREATE TRIGGER trigger_process_manager_history
    AFTER INSERT OR UPDATE OR DELETE ON public.process_manager_state
    FOR EACH ROW
    EXECUTE FUNCTION log_process_manager_changes();

-- RLS Policies (Row Level Security)
ALTER TABLE public.process_manager_state ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.process_manager_history ENABLE ROW LEVEL SECURITY;

-- Policy: Anyone authenticated can read
CREATE POLICY "process_manager_state_select" ON public.process_manager_state
    FOR SELECT TO authenticated USING (true);

-- Policy: Anyone authenticated can insert/update
CREATE POLICY "process_manager_state_insert" ON public.process_manager_state
    FOR INSERT TO authenticated WITH CHECK (true);

CREATE POLICY "process_manager_state_update" ON public.process_manager_state
    FOR UPDATE TO authenticated USING (true) WITH CHECK (true);

-- Policy: History is read-only for authenticated users
CREATE POLICY "process_manager_history_select" ON public.process_manager_history
    FOR SELECT TO authenticated USING (true);

-- Insert default state keys
INSERT INTO public.process_manager_state (state_key, state_data) VALUES
    ('node_positions', '{}'),
    ('custom_modules', '[]'),
    ('flow_positions', '{}'),
    ('draft_states', '{}'),
    ('module_connections', '{}')
ON CONFLICT (state_key) DO NOTHING;

-- =============================================
-- COMMENTS
-- =============================================
COMMENT ON TABLE public.process_manager_state IS 'Stores shared visual state for process manager - positions, modules, connections';
COMMENT ON TABLE public.process_manager_history IS 'Audit trail of all changes to process manager state for backup/recovery';
COMMENT ON COLUMN public.process_manager_state.state_key IS 'Type of state: node_positions, custom_modules, flow_positions, draft_states, module_connections';
COMMENT ON COLUMN public.process_manager_state.state_data IS 'JSON data for the state';
