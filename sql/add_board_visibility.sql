-- =============================================
-- NGM BOARD ITEMS - Visibility & Collaborators
-- =============================================
-- Adds visibility control to board items.
-- Creator controls who can see private boards.
-- =============================================

-- 1. Add visibility column (public = everyone, private = creator + collaborators)
ALTER TABLE public.ngm_board_items
    ADD COLUMN IF NOT EXISTS visibility TEXT NOT NULL DEFAULT 'public'
    CHECK (visibility IN ('public', 'private'));

-- 2. Add collaborators array (UUIDs of users who can see private items)
ALTER TABLE public.ngm_board_items
    ADD COLUMN IF NOT EXISTS collaborators UUID[] DEFAULT '{}';

-- 3. Index for filtering visible items efficiently
CREATE INDEX IF NOT EXISTS idx_ngm_board_items_visibility
    ON public.ngm_board_items(visibility);

-- 4. GIN index for collaborators array lookups (WHERE user_id = ANY(collaborators))
CREATE INDEX IF NOT EXISTS idx_ngm_board_items_collaborators
    ON public.ngm_board_items USING GIN (collaborators);

-- =============================================
-- COMMENTS
-- =============================================
COMMENT ON COLUMN public.ngm_board_items.visibility IS 'public = visible to all, private = only created_by + collaborators';
COMMENT ON COLUMN public.ngm_board_items.collaborators IS 'Array of user UUIDs who can view this item when private';
