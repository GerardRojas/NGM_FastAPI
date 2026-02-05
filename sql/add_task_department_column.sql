-- =====================================================
-- Add task_department column to tasks table
-- =====================================================
-- This column was missing - the code references it but it was never created.
-- The column stores a UUID reference to the task_departments table.

-- Add the column if it doesn't exist
ALTER TABLE public.tasks
ADD COLUMN IF NOT EXISTS task_department UUID REFERENCES public.task_departments(department_id);

-- Create index for better query performance
CREATE INDEX IF NOT EXISTS idx_tasks_task_department
ON public.tasks(task_department);

-- Notify PostgREST to reload schema cache
NOTIFY pgrst, 'reload schema';

-- Verification query (run this to confirm the column exists)
-- SELECT column_name, data_type, is_nullable
-- FROM information_schema.columns
-- WHERE table_name = 'tasks' AND column_name = 'task_department';
