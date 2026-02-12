-- ================================================================
-- Vault: Add predefined folders to each project
-- ================================================================
-- Creates standard folder structure for each project:
-- - Receipts (for expenses and bills)
-- - Plans (drawings, blueprints)
-- - Contracts (agreements, POs)
-- - Approvals (permits, sign-offs)
-- - Documents (general docs)
-- - Photos (site photos, progress pics)
-- ================================================================

DO $$
DECLARE
  proj RECORD;
  folder_names TEXT[] := ARRAY['Receipts', 'Plans', 'Contracts', 'Approvals', 'Documents', 'Photos'];
  folder_name TEXT;
  existing_count INT;
BEGIN
  -- Loop through all projects
  FOR proj IN
    SELECT project_id, project_name
    FROM projects
  LOOP
    -- Create each standard folder if it doesn't exist
    FOREACH folder_name IN ARRAY folder_names
    LOOP
      -- Check if folder already exists for this project
      SELECT COUNT(*) INTO existing_count
      FROM vault_files
      WHERE project_id = proj.project_id
        AND name = folder_name
        AND is_folder = true
        AND parent_id IS NULL;

      -- Create folder if it doesn't exist
      IF existing_count = 0 THEN
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
          proj.project_id,
          NULL,
          0,
          now(),
          now()
        );

        RAISE NOTICE 'Created folder "%" in project "%"', folder_name, proj.project_name;
      END IF;
    END LOOP;
  END LOOP;
END $$;
