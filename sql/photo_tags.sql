-- ============================================================
-- PHOTO TAGS — NGM Cam global tag catalog + per-photo assignments
-- ------------------------------------------------------------
-- A single company-wide list of tags (managed from "Manage Tags" on web),
-- each with a display color. Tags are attached to vault photos through a join
-- table so a photo can have many tags and a tag many photos. Used to filter and
-- sort the NGM Cam gallery on web and mobile.
--
--   photo_tags        : the managed catalog (the list shown in Manage Tags)
--   photo_file_tags   : links a vault file (photo) to a tag
--
-- Idempotent. Run on staging, then prod.
-- ============================================================

-- ---- Catalog: the global list of tags ----------------------
CREATE TABLE IF NOT EXISTS public.photo_tags (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name        TEXT         NOT NULL,
    -- Hex color for the tag chip (UI). Defaults to a neutral gray.
    color       TEXT         NOT NULL DEFAULT '#6b7280',
    created_by  UUID         REFERENCES public.users(user_id) ON DELETE SET NULL,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- Case-insensitive unique name (so "Trenching" and "trenching" can't both exist).
CREATE UNIQUE INDEX IF NOT EXISTS idx_photo_tags_name_lower
    ON public.photo_tags (lower(name));

-- ---- Join: photo <-> tag -----------------------------------
CREATE TABLE IF NOT EXISTS public.photo_file_tags (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    -- Vault file (photo) id. No FK to vault_files to avoid coupling to its
    -- soft-delete lifecycle; orphan links are cheap and harmless.
    file_id     UUID         NOT NULL,
    tag_id      UUID         NOT NULL REFERENCES public.photo_tags(id) ON DELETE CASCADE,
    created_by  UUID         REFERENCES public.users(user_id) ON DELETE SET NULL,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    UNIQUE (file_id, tag_id)
);

-- Fast lookups: tags for a set of files, and files for a tag.
CREATE INDEX IF NOT EXISTS idx_photo_file_tags_file ON public.photo_file_tags (file_id);
CREATE INDEX IF NOT EXISTS idx_photo_file_tags_tag  ON public.photo_file_tags (tag_id);

-- Auto-update photo_tags.updated_at
CREATE OR REPLACE FUNCTION update_photo_tags_timestamp()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_photo_tags_updated ON public.photo_tags;
CREATE TRIGGER trg_photo_tags_updated
    BEFORE UPDATE ON public.photo_tags
    FOR EACH ROW
    EXECUTE FUNCTION update_photo_tags_timestamp();

-- RLS: authenticated users can read/write (same model as photo_annotations).
ALTER TABLE public.photo_tags ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.photo_file_tags ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS photo_tags_select ON public.photo_tags;
DROP POLICY IF EXISTS photo_tags_insert ON public.photo_tags;
DROP POLICY IF EXISTS photo_tags_update ON public.photo_tags;
DROP POLICY IF EXISTS photo_tags_delete ON public.photo_tags;
CREATE POLICY photo_tags_select ON public.photo_tags FOR SELECT TO authenticated USING (true);
CREATE POLICY photo_tags_insert ON public.photo_tags FOR INSERT TO authenticated WITH CHECK (true);
CREATE POLICY photo_tags_update ON public.photo_tags FOR UPDATE TO authenticated USING (true);
CREATE POLICY photo_tags_delete ON public.photo_tags FOR DELETE TO authenticated USING (true);

DROP POLICY IF EXISTS photo_file_tags_select ON public.photo_file_tags;
DROP POLICY IF EXISTS photo_file_tags_insert ON public.photo_file_tags;
DROP POLICY IF EXISTS photo_file_tags_delete ON public.photo_file_tags;
CREATE POLICY photo_file_tags_select ON public.photo_file_tags FOR SELECT TO authenticated USING (true);
CREATE POLICY photo_file_tags_insert ON public.photo_file_tags FOR INSERT TO authenticated WITH CHECK (true);
CREATE POLICY photo_file_tags_delete ON public.photo_file_tags FOR DELETE TO authenticated USING (true);

-- Documentation
COMMENT ON TABLE  public.photo_tags        IS 'NGM Cam global tag catalog (the Manage Tags list)';
COMMENT ON COLUMN public.photo_tags.color  IS 'Hex color for the tag chip in the UI';
COMMENT ON TABLE  public.photo_file_tags   IS 'Links a vault photo (file_id) to a photo_tags entry';

-- VERIFICATION ------------------------------------------------
-- select t.name, t.color, count(ft.id) as photos
-- from public.photo_tags t
-- left join public.photo_file_tags ft on ft.tag_id = t.id
-- group by t.id order by t.name;
