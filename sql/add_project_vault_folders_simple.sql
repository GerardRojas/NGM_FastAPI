-- ================================================================
-- Vault: Add predefined folders to each project (SIMPLE VERSION)
-- ================================================================
-- Run this in Supabase SQL Editor
-- ================================================================

-- First, let's check if vault_files table exists and has data
SELECT 'vault_files table check:' as info, COUNT(*) as total_files,
       COUNT(*) FILTER (WHERE is_folder = true) as folders,
       COUNT(*) FILTER (WHERE project_id IS NOT NULL) as project_files
FROM vault_files;

-- Check how many projects we have
SELECT 'projects table check:' as info, COUNT(*) as total_projects
FROM projects;

-- Now insert folders for each project
INSERT INTO vault_files (name, is_folder, project_id, parent_id, size_bytes)
SELECT folder_name, true, p.project_id, NULL, 0
FROM projects p
CROSS JOIN (
  SELECT unnest(ARRAY['Receipts', 'Plans', 'Contracts', 'Approvals', 'Documents', 'Photos']) as folder_name
) folders
WHERE NOT EXISTS (
  -- Only create if folder doesn't already exist for this project
  SELECT 1 FROM vault_files vf
  WHERE vf.project_id = p.project_id
    AND vf.name = folder_name
    AND vf.is_folder = true
    AND vf.parent_id IS NULL
);

-- Show results
SELECT 'Result:' as info, COUNT(*) as folders_created
FROM vault_files
WHERE is_folder = true AND project_id IS NOT NULL;

-- Show folders per project
SELECT
  p.project_name,
  COUNT(vf.id) as folder_count,
  string_agg(vf.name, ', ' ORDER BY vf.name) as folders
FROM projects p
LEFT JOIN vault_files vf ON vf.project_id = p.project_id AND vf.is_folder = true AND vf.parent_id IS NULL
GROUP BY p.project_id, p.project_name
ORDER BY p.project_name;
