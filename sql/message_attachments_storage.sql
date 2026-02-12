-- ============================================
-- Message Attachments Storage Bucket + Policies
-- ============================================
-- Creates the 'message-attachments' bucket in Supabase Storage
-- for non-receipt file attachments sent in chat messages.
--
-- Storage path convention:
--   {project_id_or_channel_id}/{timestamp}_{random}.{ext}
--
-- Run in Supabase SQL Editor.

-- ============================================
-- Create the bucket (if not exists)
-- ============================================
INSERT INTO storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
VALUES (
    'message-attachments',
    'message-attachments',
    true,          -- public URLs (simplifies read access)
    10485760,      -- 10 MB max file size
    ARRAY[
        'image/jpeg', 'image/png', 'image/gif', 'image/webp', 'image/svg+xml',
        'application/pdf',
        'application/msword',
        'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        'application/vnd.ms-excel',
        'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        'text/plain', 'text/csv'
    ]
)
ON CONFLICT (id) DO UPDATE SET
    file_size_limit = 10485760,
    public = true,
    allowed_mime_types = EXCLUDED.allowed_mime_types;


-- ============================================
-- Storage RLS Policies
-- ============================================

-- Public read access
DROP POLICY IF EXISTS "msg_attachments_public_select" ON storage.objects;
CREATE POLICY "msg_attachments_public_select"
ON storage.objects FOR SELECT
TO public
USING (bucket_id = 'message-attachments');

-- Upload (insert)
DROP POLICY IF EXISTS "msg_attachments_public_insert" ON storage.objects;
CREATE POLICY "msg_attachments_public_insert"
ON storage.objects FOR INSERT
TO public
WITH CHECK (bucket_id = 'message-attachments');

-- Update (overwrite)
DROP POLICY IF EXISTS "msg_attachments_public_update" ON storage.objects;
CREATE POLICY "msg_attachments_public_update"
ON storage.objects FOR UPDATE
TO public
USING (bucket_id = 'message-attachments');

-- Delete
DROP POLICY IF EXISTS "msg_attachments_public_delete" ON storage.objects;
CREATE POLICY "msg_attachments_public_delete"
ON storage.objects FOR DELETE
TO public
USING (bucket_id = 'message-attachments');
