-- ================================================================
-- Add a `color` column to channels
-- ================================================================
-- Lets the team pick a color when creating a channel. Stored as an
-- integer HUE (0-359), matching the convention used by users.avatar_color
-- so the frontend resolves channel and user colors the same way
-- (assets lib/avatar-color: hsl(<hue> 70% 45%)). NULL = no explicit
-- color, the UI falls back to a deterministic hash of the channel id.
-- Idempotent. Run on staging, then prod (Supabase SQL editor).
-- ================================================================

ALTER TABLE channels ADD COLUMN IF NOT EXISTS color INTEGER;

-- Keep it a valid hue when set (NULL stays allowed).
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'channels_color_hue_check'
    ) THEN
        ALTER TABLE channels
            ADD CONSTRAINT channels_color_hue_check
            CHECK (color IS NULL OR (color >= 0 AND color <= 360));
    END IF;
END $$;

-- VERIFICATION
-- select column_name, data_type from information_schema.columns
--   where table_name='channels' and column_name='color';
