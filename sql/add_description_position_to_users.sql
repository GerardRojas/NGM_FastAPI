-- Add description and position fields to users table
ALTER TABLE users ADD COLUMN IF NOT EXISTS user_description TEXT;
ALTER TABLE users ADD COLUMN IF NOT EXISTS user_position TEXT;
