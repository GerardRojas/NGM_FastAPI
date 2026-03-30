-- ================================================================
-- Add 'broadcast' channel type + write_roles/read_roles columns
-- ================================================================
-- Broadcast channels are announcement channels where only specific
-- roles can write, and specific roles can read. CEO/COO always
-- have full access regardless of configuration.
-- ================================================================

-- 1. Drop old CHECK constraint on type
DO $$
DECLARE
    constraint_name TEXT;
BEGIN
    SELECT conname INTO constraint_name
    FROM pg_constraint
    WHERE conrelid = 'channels'::regclass
      AND contype = 'c'
      AND pg_get_constraintdef(oid) LIKE '%type%';

    IF constraint_name IS NOT NULL THEN
        EXECUTE format('ALTER TABLE channels DROP CONSTRAINT %I', constraint_name);
        RAISE NOTICE 'Dropped constraint: %', constraint_name;
    ELSE
        RAISE NOTICE 'No CHECK constraint on type column found';
    END IF;
END $$;

-- 2. Add updated CHECK constraint that includes 'broadcast'
ALTER TABLE channels
ADD CONSTRAINT channels_type_check
CHECK (type IN ('custom', 'direct', 'group', 'broadcast'));

-- 3. Add write_roles and read_roles columns (JSONB arrays of role names)
--    write_roles: which roles can send messages (CEO/COO always can)
--    read_roles: which roles can see the channel (CEO/COO always can, empty = everyone)
ALTER TABLE channels ADD COLUMN IF NOT EXISTS write_roles JSONB DEFAULT '[]'::jsonb;
ALTER TABLE channels ADD COLUMN IF NOT EXISTS read_roles JSONB DEFAULT '[]'::jsonb;

-- 4. Update messages.channel_key generated column to include 'broadcast'
--    Must DROP and re-ADD since generated columns can't be altered in-place.
--    The view messages_with_details depends on channel_key, so we save its
--    definition, drop with CASCADE, recreate column, then restore the view.

-- 4a. Save the view definition before dropping
DO $$
DECLARE
    _view_def TEXT;
BEGIN
    SELECT pg_get_viewdef('messages_with_details', true) INTO _view_def;
    IF _view_def IS NOT NULL THEN
        -- Store temporarily in a temp table so we can restore after
        CREATE TEMP TABLE IF NOT EXISTS _saved_view_def (def TEXT);
        DELETE FROM _saved_view_def;
        INSERT INTO _saved_view_def VALUES (_view_def);
        RAISE NOTICE 'Saved messages_with_details view definition';
    ELSE
        RAISE NOTICE 'View messages_with_details not found, skipping save';
    END IF;
EXCEPTION WHEN undefined_table THEN
    RAISE NOTICE 'View messages_with_details does not exist, skipping';
END $$;

-- 4b. Drop column with CASCADE (drops the dependent view)
ALTER TABLE messages DROP COLUMN IF EXISTS channel_key CASCADE;

-- 4c. Recreate the generated column with broadcast included
ALTER TABLE messages
ADD COLUMN channel_key TEXT
GENERATED ALWAYS AS (
    CASE
        WHEN channel_type IN ('custom', 'direct', 'group', 'broadcast')
            THEN channel_type || ':' || COALESCE(channel_id::text, '')
        ELSE
            channel_type || ':' || COALESCE(project_id::text, '')
    END
) STORED;

-- 4d. Restore the view from saved definition
DO $$
DECLARE
    _view_def TEXT;
BEGIN
    SELECT def INTO _view_def FROM _saved_view_def LIMIT 1;
    IF _view_def IS NOT NULL THEN
        EXECUTE 'CREATE OR REPLACE VIEW messages_with_details AS ' || _view_def;
        RAISE NOTICE 'Restored messages_with_details view';
    END IF;
EXCEPTION WHEN undefined_table THEN
    RAISE NOTICE 'No saved view definition found, skipping restore';
END $$;

-- Cleanup temp table
DROP TABLE IF EXISTS _saved_view_def;

-- Recreate indexes
CREATE INDEX IF NOT EXISTS idx_messages_channel_key
    ON messages(channel_key);
CREATE INDEX IF NOT EXISTS idx_messages_channel_key_created
    ON messages(channel_key, created_at);

-- 5. Verify
SELECT conname, pg_get_constraintdef(oid)
FROM pg_constraint
WHERE conrelid = 'channels'::regclass
  AND contype = 'c';

SELECT column_name, data_type, column_default
FROM information_schema.columns
WHERE table_name = 'channels' AND column_name IN ('write_roles', 'read_roles');
