-- ================================================================
-- Allow 'group' channel type in channels table
-- ================================================================
-- The channels.type column may have a CHECK constraint that only
-- allows 'custom' and 'direct'. This migration adds 'group' support
-- needed for the Payroll group channel feature.
-- ================================================================

-- 1. Check current constraints on channels table (diagnostic)
SELECT conname, pg_get_constraintdef(oid)
FROM pg_constraint
WHERE conrelid = 'channels'::regclass
  AND contype = 'c';

-- 2. Drop the old CHECK constraint on type (name may vary)
--    Common names: channels_type_check, channels_type_constraint
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

-- 3. Add updated CHECK constraint that includes 'group'
ALTER TABLE channels
ADD CONSTRAINT channels_type_check
CHECK (type IN ('custom', 'direct', 'group'));

-- 4. Verify
SELECT conname, pg_get_constraintdef(oid)
FROM pg_constraint
WHERE conrelid = 'channels'::regclass
  AND contype = 'c';
