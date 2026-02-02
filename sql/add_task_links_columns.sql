-- Add links columns to tasks table
-- Run this in Supabase SQL Editor

-- Add docs_link column (for documentation links like Google Docs, Notion, etc.)
ALTER TABLE tasks
ADD COLUMN IF NOT EXISTS docs_link TEXT;

-- Add result_link column (for result/deliverable links)
ALTER TABLE tasks
ADD COLUMN IF NOT EXISTS result_link TEXT;

-- Add comment for documentation
COMMENT ON COLUMN tasks.docs_link IS 'URL to documentation (Google Docs, Notion, etc.)';
COMMENT ON COLUMN tasks.result_link IS 'URL to task result/deliverable';

-- Create index for tasks with links (optional, for faster queries)
CREATE INDEX IF NOT EXISTS idx_tasks_has_links
ON tasks ((docs_link IS NOT NULL OR result_link IS NOT NULL));
