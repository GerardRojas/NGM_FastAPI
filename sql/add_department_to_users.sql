-- ========================================
-- Add department_id column to users table
-- ========================================
-- Replaces the old text-based user_position field with a proper FK
-- to task_departments, so users can be assigned a department from
-- the same list used by pipeline tasks.

ALTER TABLE public.users
ADD COLUMN IF NOT EXISTS department_id UUID REFERENCES public.task_departments(department_id);

-- Index for lookups by department
CREATE INDEX IF NOT EXISTS idx_users_department_id
ON public.users(department_id);

-- Optional: drop the old text column once migration is confirmed
-- ALTER TABLE public.users DROP COLUMN IF EXISTS user_position;
