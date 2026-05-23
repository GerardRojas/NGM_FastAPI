-- ============================================================
-- PHOTO ANNOTATIONS — NGM Cam non-destructive markup
-- Editable vector overlays (arrows, lines, rectangles, ellipses,
-- freehand, text) drawn on top of a vault photo. The original
-- image is never modified; shapes are stored as JSON and rendered
-- as an SVG overlay. One shared doc per photo (file_id).
-- ============================================================

CREATE TABLE IF NOT EXISTS public.photo_annotations (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    -- The vault file (photo) these annotations belong to. One doc per photo.
    file_id         UUID         NOT NULL UNIQUE,
    -- Optional project context (NULL for standalone NGM Cam projects).
    project_id      UUID         DEFAULT NULL,
    -- Array of shape objects:
    --   { id, type: 'arrow'|'line'|'rect'|'ellipse'|'freehand'|'text',
    --     color, strokeWidth, points: [[x,y],...] (normalized 0..1), text? }
    shapes          JSONB        NOT NULL DEFAULT '[]',
    -- Last editor (collaborative last-write-wins).
    updated_by      UUID         REFERENCES public.users(user_id) ON DELETE SET NULL,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- Fast lookup by file
CREATE INDEX IF NOT EXISTS idx_photo_annotations_file
    ON public.photo_annotations (file_id);

-- Fast lookup by project (badge counts across a project's photos)
CREATE INDEX IF NOT EXISTS idx_photo_annotations_project
    ON public.photo_annotations (project_id);

-- Auto-update updated_at
CREATE OR REPLACE FUNCTION update_photo_annotations_timestamp()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_photo_annotations_updated ON public.photo_annotations;
CREATE TRIGGER trg_photo_annotations_updated
    BEFORE UPDATE ON public.photo_annotations
    FOR EACH ROW
    EXECUTE FUNCTION update_photo_annotations_timestamp();

-- RLS: authenticated users can read/write (same model as cell_comments)
ALTER TABLE public.photo_annotations ENABLE ROW LEVEL SECURITY;

CREATE POLICY photo_annotations_select ON public.photo_annotations
    FOR SELECT TO authenticated USING (true);

CREATE POLICY photo_annotations_insert ON public.photo_annotations
    FOR INSERT TO authenticated WITH CHECK (true);

CREATE POLICY photo_annotations_update ON public.photo_annotations
    FOR UPDATE TO authenticated USING (true);

CREATE POLICY photo_annotations_delete ON public.photo_annotations
    FOR DELETE TO authenticated USING (true);

-- Documentation
COMMENT ON TABLE  public.photo_annotations IS 'NGM Cam editable vector overlays on vault photos (non-destructive)';
COMMENT ON COLUMN public.photo_annotations.file_id IS 'Vault file id of the photo (one annotation doc per photo)';
COMMENT ON COLUMN public.photo_annotations.shapes IS 'Array of shape objects with normalized 0..1 coordinates';
COMMENT ON COLUMN public.photo_annotations.updated_by IS 'Last user who edited the annotations';
