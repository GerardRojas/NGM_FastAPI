-- ============================================================================
-- RECEIPTS -> VAULT : Phase 0 (schema foundations)
-- ----------------------------------------------------------------------------
-- Lets the backend sync a bill's receipt into the project's Vault "Receipts"
-- folder with exactly ONE file per bill number. Adds the per-bill dedup key and
-- makes sure every project has a Receipts folder to land them in.
--
-- Idempotent. Run on staging first, then prod (Supabase SQL editor). No downtime.
-- ============================================================================

-- 1. Per-bill dedup key on vault_files.
--    Which bill (invoice number) a vault receipt came from. NULL = not a
--    bill-sourced receipt (manual upload, Andrew, etc.).
ALTER TABLE public.vault_files
  ADD COLUMN IF NOT EXISTS source_bill_id text;

-- 2. One vault file per (project, bill). Partial + scoped to live, bill-sourced
--    rows so it never clashes with regular files (source_bill_id NULL) or with
--    soft-deleted history.
CREATE UNIQUE INDEX IF NOT EXISTS uq_vault_files_project_bill
  ON public.vault_files (project_id, source_bill_id)
  WHERE source_bill_id IS NOT NULL AND is_deleted = false;

-- 3. Ensure every project has a top-level "Receipts" folder for the sync to use.
--    (Mirrors add_project_vault_folders.sql, scoped to just Receipts. Idempotent.)
DO $$
DECLARE
  proj RECORD;
BEGIN
  FOR proj IN SELECT project_id, project_name FROM public.projects LOOP
    IF NOT EXISTS (
      SELECT 1 FROM public.vault_files
       WHERE project_id = proj.project_id
         AND name = 'Receipts'
         AND is_folder = true
         AND parent_id IS NULL
         AND is_deleted = false
    ) THEN
      INSERT INTO public.vault_files (name, is_folder, project_id, parent_id, size_bytes)
      VALUES ('Receipts', true, proj.project_id, NULL, 0);
      RAISE NOTICE 'Created Receipts folder for project "%"', proj.project_name;
    END IF;
  END LOOP;
END $$;

-- VERIFICATION ---------------------------------------------------------------
-- Column + index present:
-- select column_name from information_schema.columns
--   where table_name = 'vault_files' and column_name = 'source_bill_id';
-- select indexname from pg_indexes where indexname = 'uq_vault_files_project_bill';
--
-- Every project has a Receipts folder (should be 0 missing):
-- select count(*) as projects_without_receipts
--   from public.projects p
--  where not exists (
--    select 1 from public.vault_files v
--     where v.project_id = p.project_id and v.name = 'Receipts'
--       and v.is_folder = true and v.parent_id is null and v.is_deleted = false);
