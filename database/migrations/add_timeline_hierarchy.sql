-- ============================================
-- TIMELINE HIERARCHY: Add WBS hierarchy + dependencies
-- ============================================
-- Extends project_phases with parent-child, sort order, phase type, etc.
-- Creates phase_dependencies table for FS/SS/FF/SF links.
-- Run in Supabase SQL editor.

-- ── New columns on project_phases ────────────────────────────────

ALTER TABLE project_phases
  ADD COLUMN IF NOT EXISTS parent_phase_id UUID REFERENCES project_phases(phase_id) ON DELETE SET NULL,
  ADD COLUMN IF NOT EXISTS sort_order INTEGER DEFAULT 0,
  ADD COLUMN IF NOT EXISTS phase_type VARCHAR(20) DEFAULT 'task',
  ADD COLUMN IF NOT EXISTS duration_days INTEGER DEFAULT 0,
  ADD COLUMN IF NOT EXISTS assigned_to UUID,
  ADD COLUMN IF NOT EXISTS wbs_number VARCHAR(20) DEFAULT '',
  ADD COLUMN IF NOT EXISTS collapsed BOOLEAN DEFAULT false;

-- Index for parent lookups
CREATE INDEX IF NOT EXISTS idx_phases_parent ON project_phases(parent_phase_id);
-- Index for sort ordering within a project
CREATE INDEX IF NOT EXISTS idx_phases_sort ON project_phases(project_id, sort_order);

-- Backfill duration_days from existing date ranges
-- DATE - DATE in PostgreSQL returns INTEGER (number of days) directly
UPDATE project_phases
SET duration_days = (end_date - start_date)
WHERE start_date IS NOT NULL
  AND end_date IS NOT NULL
  AND (duration_days IS NULL OR duration_days = 0);

-- Backfill sort_order from phase_order where not set
UPDATE project_phases
SET sort_order = phase_order
WHERE sort_order = 0 AND phase_order > 0;


-- ── phase_dependencies table ─────────────────────────────────────

CREATE TABLE IF NOT EXISTS phase_dependencies (
  dependency_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id UUID NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
  predecessor_phase_id UUID NOT NULL REFERENCES project_phases(phase_id) ON DELETE CASCADE,
  successor_phase_id UUID NOT NULL REFERENCES project_phases(phase_id) ON DELETE CASCADE,
  dependency_type VARCHAR(2) DEFAULT 'FS',  -- FS, SS, FF, SF
  lag_days INTEGER DEFAULT 0,
  created_at TIMESTAMPTZ DEFAULT now(),
  updated_at TIMESTAMPTZ DEFAULT now(),

  CONSTRAINT unique_phase_dependency UNIQUE (predecessor_phase_id, successor_phase_id),
  CONSTRAINT no_self_phase_dependency CHECK (predecessor_phase_id != successor_phase_id)
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_phase_dep_project ON phase_dependencies(project_id);
CREATE INDEX IF NOT EXISTS idx_phase_dep_predecessor ON phase_dependencies(predecessor_phase_id);
CREATE INDEX IF NOT EXISTS idx_phase_dep_successor ON phase_dependencies(successor_phase_id);

-- RLS
ALTER TABLE phase_dependencies ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Allow all for authenticated" ON phase_dependencies FOR ALL USING (true);

-- Trigger for updated_at
CREATE OR REPLACE FUNCTION update_phase_dependency_timestamp()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trigger_update_phase_dep_timestamp ON phase_dependencies;
CREATE TRIGGER trigger_update_phase_dep_timestamp
    BEFORE UPDATE ON phase_dependencies
    FOR EACH ROW
    EXECUTE FUNCTION update_phase_dependency_timestamp();


-- ── Circular dependency check for phases ─────────────────────────

CREATE OR REPLACE FUNCTION check_phase_circular_dependency(
    p_predecessor_id UUID,
    p_successor_id UUID
) RETURNS BOOLEAN AS $$
DECLARE
    v_has_cycle BOOLEAN := FALSE;
BEGIN
    WITH RECURSIVE dep_chain AS (
        SELECT successor_phase_id, predecessor_phase_id, 1 AS depth
        FROM phase_dependencies
        WHERE predecessor_phase_id = p_successor_id

        UNION ALL

        SELECT pd.successor_phase_id, pd.predecessor_phase_id, dc.depth + 1
        FROM phase_dependencies pd
        JOIN dep_chain dc ON pd.predecessor_phase_id = dc.successor_phase_id
        WHERE dc.depth < 100
    )
    SELECT EXISTS (
        SELECT 1 FROM dep_chain
        WHERE successor_phase_id = p_predecessor_id
    ) INTO v_has_cycle;

    RETURN v_has_cycle;
END;
$$ LANGUAGE plpgsql;


COMMENT ON TABLE phase_dependencies IS 'Phase dependency relationships for Timeline Manager Gantt chart';
COMMENT ON COLUMN phase_dependencies.dependency_type IS 'FS=Finish-to-Start, SS=Start-to-Start, FF=Finish-to-Finish, SF=Start-to-Finish';
COMMENT ON COLUMN phase_dependencies.lag_days IS 'Lag in days. Positive=delay, Negative=overlap';
