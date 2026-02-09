-- Add is_external boolean column to users table (defaults to false)
-- All existing users remain internal
ALTER TABLE public.users
  ADD COLUMN IF NOT EXISTS is_external boolean NOT NULL DEFAULT false;
