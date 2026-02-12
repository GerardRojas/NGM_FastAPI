-- ================================================================
-- Vault: Auto-create project folders on new project creation
-- ================================================================
-- Trigger that automatically creates standard folder structure
-- when a new project is created
-- ================================================================

-- Function to create standard project folders
CREATE OR REPLACE FUNCTION create_project_vault_folders()
RETURNS TRIGGER AS $$
DECLARE
  folder_names TEXT[] := ARRAY['Receipts', 'Plans', 'Contracts', 'Approvals', 'Documents', 'Photos'];
  folder_name TEXT;
BEGIN
  -- Only proceed if this is a new project
  IF TG_OP = 'INSERT' THEN
    -- Create each standard folder
    FOREACH folder_name IN ARRAY folder_names
    LOOP
      INSERT INTO vault_files (
        name,
        is_folder,
        project_id,
        parent_id,
        size_bytes,
        created_at,
        updated_at
      ) VALUES (
        folder_name,
        true,
        NEW.project_id,
        NULL,
        0,
        now(),
        now()
      );
    END LOOP;

    RAISE NOTICE 'Created standard folders for project "%"', NEW.project_name;
  END IF;

  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Drop existing trigger if it exists
DROP TRIGGER IF EXISTS trigger_create_project_vault_folders ON projects;

-- Create trigger that fires after project insert
CREATE TRIGGER trigger_create_project_vault_folders
  AFTER INSERT ON projects
  FOR EACH ROW
  EXECUTE FUNCTION create_project_vault_folders();

-- Grant execute permission to authenticated role (if using RLS)
GRANT EXECUTE ON FUNCTION create_project_vault_folders() TO authenticated;
