-- Create project_phases and project_milestones tables
-- Run in Supabase SQL editor

CREATE TABLE IF NOT EXISTS project_phases (
  phase_id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  project_id UUID NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
  phase_name TEXT NOT NULL,
  phase_order INT DEFAULT 0,
  start_date DATE,
  end_date DATE,
  actual_start DATE,
  actual_end DATE,
  status TEXT DEFAULT 'pending' CHECK (status IN ('pending', 'in_progress', 'completed', 'delayed')),
  progress_pct NUMERIC(5,2) DEFAULT 0 CHECK (progress_pct >= 0 AND progress_pct <= 100),
  color TEXT DEFAULT '#3ecf8e',
  notes TEXT,
  created_at TIMESTAMPTZ DEFAULT now(),
  updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS project_milestones (
  milestone_id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  project_id UUID NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
  phase_id UUID REFERENCES project_phases(phase_id) ON DELETE SET NULL,
  milestone_name TEXT NOT NULL,
  due_date DATE,
  completed_date DATE,
  status TEXT DEFAULT 'pending' CHECK (status IN ('pending', 'completed', 'overdue')),
  notes TEXT,
  created_at TIMESTAMPTZ DEFAULT now(),
  updated_at TIMESTAMPTZ DEFAULT now()
);

-- Indexes for common queries
CREATE INDEX IF NOT EXISTS idx_phases_project ON project_phases(project_id);
CREATE INDEX IF NOT EXISTS idx_milestones_project ON project_milestones(project_id);
CREATE INDEX IF NOT EXISTS idx_milestones_phase ON project_milestones(phase_id);

-- Enable RLS
ALTER TABLE project_phases ENABLE ROW LEVEL SECURITY;
ALTER TABLE project_milestones ENABLE ROW LEVEL SECURITY;

-- Permissive policies (matching existing pattern — service role bypass)
CREATE POLICY "Allow all for authenticated" ON project_phases FOR ALL USING (true);
CREATE POLICY "Allow all for authenticated" ON project_milestones FOR ALL USING (true);
