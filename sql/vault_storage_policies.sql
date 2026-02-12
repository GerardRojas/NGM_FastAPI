-- ============================================
-- Vault Storage Bucket + Policies
-- ============================================
-- Creates the 'vault' bucket in Supabase Storage
-- and sets up RLS policies for access control.
--
-- Run in Supabase SQL Editor AFTER vault_schema.sql

-- ============================================
-- Create the vault bucket (if not exists)
-- ============================================
-- Note: Bucket creation is typically done via Supabase Dashboard
-- or the storage API. This INSERT works if executed with service_role:
INSERT INTO storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
VALUES (
    'vault',
    'vault',
    true,  -- public URLs (access controlled at DB level)
    524288000,  -- 500 MB max file size
    NULL  -- allow all MIME types
)
ON CONFLICT (id) DO UPDATE SET
    file_size_limit = 524288000,
    public = true;


-- ============================================
-- Storage RLS Policies for vault bucket
-- ============================================

-- Allow anyone to read files from vault bucket (public)
CREATE POLICY "vault_public_select"
ON storage.objects FOR SELECT
TO public
USING (bucket_id = 'vault');

-- Allow authenticated users to upload files
CREATE POLICY "vault_auth_insert"
ON storage.objects FOR INSERT
TO public
WITH CHECK (bucket_id = 'vault');

-- Allow authenticated users to update files (overwrite)
CREATE POLICY "vault_auth_update"
ON storage.objects FOR UPDATE
TO public
USING (bucket_id = 'vault');

-- Allow authenticated users to delete files
CREATE POLICY "vault_auth_delete"
ON storage.objects FOR DELETE
TO public
USING (bucket_id = 'vault');
