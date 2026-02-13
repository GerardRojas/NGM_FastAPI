-- ========================================
-- Verification script for unread counts functionality
-- ========================================
-- Run this after executing add_channel_key_to_messages.sql
-- and create_channel_read_status.sql

-- 1. Check if channel_key column exists in messages table
SELECT column_name, data_type
FROM information_schema.columns
WHERE table_name = 'messages'
  AND column_name = 'channel_key';
-- Expected: 1 row with column_name='channel_key', data_type='text'

-- 2. Check if channel_read_status table exists
SELECT table_name
FROM information_schema.tables
WHERE table_name = 'channel_read_status';
-- Expected: 1 row

-- 3. Check if get_unread_counts function exists
SELECT routine_name, routine_type
FROM information_schema.routines
WHERE routine_name = 'get_unread_counts'
  AND routine_schema = 'public';
-- Expected: 1 row with routine_type='FUNCTION'

-- 4. Test the function (replace 'your-user-id-here' with a real user_id)
-- SELECT * FROM get_unread_counts('your-user-id-here');
-- Expected: Should return rows with channel_key and unread_count (no error)
