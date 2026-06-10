-- =============================================================================
-- FIX — tasks.Owner_id / tasks.task_finished_status carry a broken column
-- DEFAULT of gen_random_uuid(). Any INSERT that OMITS these columns gets a
-- random UUID that is not present in their referenced tables, so the insert
-- fails its foreign key (tasks_Owner_id_fkey / tasks_task_finished_status_fkey).
--
-- The normal create-task endpoint dodges this by always passing both columns
-- explicitly (Owner_id = the form owner, task_finished_status = NULL). But any
-- owner-less automated insert — e.g. create_estimate_review_task() for the
-- "send a branch to review" feature — omits them and silently fails, so the
-- reviewer's task never appears in My Work.
--
-- Drop the nonsensical defaults so an omitted value becomes NULL (both columns
-- are nullable). Normal inserts that set the columns are unaffected.
--
-- Idempotent. Run on STAGING first, verify, then PROD.
-- Path: C:\Users\germa\Desktop\NGM_API\sql\fix_tasks_broken_defaults.sql
-- =============================================================================

ALTER TABLE public.tasks ALTER COLUMN "Owner_id" DROP DEFAULT;
ALTER TABLE public.tasks ALTER COLUMN task_finished_status DROP DEFAULT;

-- VERIFICATION — both column_default values should come back NULL:
-- select column_name, column_default
--   from information_schema.columns
--  where table_name = 'tasks'
--    and column_name in ('Owner_id', 'task_finished_status');
-- =============================================================================
