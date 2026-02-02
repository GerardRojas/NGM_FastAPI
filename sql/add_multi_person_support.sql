-- Add multi-person support for collaborators and managers
-- Run this in Supabase SQL Editor

-- IMPORTANT: This migration changes single UUID columns to UUID arrays
-- Existing data will be preserved as single-element arrays

-- Step 1: Add new array columns (temporary)
ALTER TABLE tasks
ADD COLUMN IF NOT EXISTS collaborators_ids UUID[];

ALTER TABLE tasks
ADD COLUMN IF NOT EXISTS managers_ids UUID[];

-- Step 2: Migrate existing data to arrays
UPDATE tasks
SET collaborators_ids = ARRAY["Colaborators_id"]
WHERE "Colaborators_id" IS NOT NULL AND collaborators_ids IS NULL;

UPDATE tasks
SET managers_ids = ARRAY[manager::UUID]
WHERE manager IS NOT NULL AND managers_ids IS NULL;

-- Step 3: Add comments for documentation
COMMENT ON COLUMN tasks.collaborators_ids IS 'Array of collaborator user UUIDs';
COMMENT ON COLUMN tasks.managers_ids IS 'Array of manager user UUIDs';

-- Note: We keep the old columns (Colaborators_id, manager) for backward compatibility
-- The backend will read from both and write to the new array columns
-- After verifying everything works, you can drop the old columns:
-- ALTER TABLE tasks DROP COLUMN IF EXISTS "Colaborators_id";
-- ALTER TABLE tasks DROP COLUMN IF EXISTS manager;
