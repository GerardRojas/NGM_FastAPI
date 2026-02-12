-- ============================================
-- Vault Module - Database Schema
-- ============================================
-- File storage system with virtual folder hierarchy and versioning
-- Run this migration against Supabase SQL editor

-- ============================================
-- Table: vault_files
-- ============================================
-- Stores both folders and files in a single table.
-- Folders: is_folder=true, bucket_path=NULL
-- Files: is_folder=false, bucket_path points to Supabase Storage

CREATE TABLE IF NOT EXISTS vault_files (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name            text NOT NULL,
    is_folder       boolean NOT NULL DEFAULT false,
    parent_id       uuid REFERENCES vault_files(id) ON DELETE SET NULL,
    project_id      uuid,  -- NULL = global vault
    bucket_path     text,  -- NULL for folders, storage path for files
    mime_type       text,
    size_bytes      bigint DEFAULT 0,
    file_hash       text,  -- SHA-256 for duplicate detection
    uploaded_by     uuid,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    is_deleted      boolean NOT NULL DEFAULT false
);

-- Indexes for common queries
CREATE INDEX IF NOT EXISTS idx_vault_files_parent
    ON vault_files (parent_id, is_deleted)
    WHERE is_deleted = false;

CREATE INDEX IF NOT EXISTS idx_vault_files_project
    ON vault_files (project_id, is_deleted)
    WHERE is_deleted = false;

CREATE INDEX IF NOT EXISTS idx_vault_files_hash
    ON vault_files (file_hash)
    WHERE file_hash IS NOT NULL AND is_deleted = false;

CREATE INDEX IF NOT EXISTS idx_vault_files_name_search
    ON vault_files USING gin (to_tsvector('english', name))
    WHERE is_deleted = false;

CREATE INDEX IF NOT EXISTS idx_vault_files_uploaded_by
    ON vault_files (uploaded_by)
    WHERE is_deleted = false;


-- ============================================
-- Table: vault_file_versions
-- ============================================
-- Each file upload creates a version record.
-- Version 1 = original upload. Subsequent uploads increment version_number.

CREATE TABLE IF NOT EXISTS vault_file_versions (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    file_id         uuid NOT NULL REFERENCES vault_files(id) ON DELETE CASCADE,
    version_number  integer NOT NULL DEFAULT 1,
    bucket_path     text NOT NULL,
    size_bytes      bigint DEFAULT 0,
    uploaded_by     uuid,
    comment         text,
    created_at      timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_vault_versions_file
    ON vault_file_versions (file_id, version_number DESC);


-- ============================================
-- Auto-update updated_at trigger
-- ============================================
CREATE OR REPLACE FUNCTION vault_update_timestamp()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_vault_files_updated ON vault_files;
CREATE TRIGGER trg_vault_files_updated
    BEFORE UPDATE ON vault_files
    FOR EACH ROW
    EXECUTE FUNCTION vault_update_timestamp();


-- ============================================
-- RLS Policies (optional, for direct Supabase access)
-- ============================================
ALTER TABLE vault_files ENABLE ROW LEVEL SECURITY;
ALTER TABLE vault_file_versions ENABLE ROW LEVEL SECURITY;

-- Allow full access via service role (backend API)
CREATE POLICY vault_files_service_all ON vault_files
    FOR ALL TO service_role USING (true) WITH CHECK (true);

CREATE POLICY vault_versions_service_all ON vault_file_versions
    FOR ALL TO service_role USING (true) WITH CHECK (true);

-- Allow authenticated users to read non-deleted files
CREATE POLICY vault_files_auth_select ON vault_files
    FOR SELECT TO authenticated
    USING (is_deleted = false);

CREATE POLICY vault_versions_auth_select ON vault_file_versions
    FOR SELECT TO authenticated
    USING (true);
