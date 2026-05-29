-- ============================================================================
-- CONCEPT BUILDER v2 — concept_lines (Opus-style typed composition)
-- ----------------------------------------------------------------------------
-- Replaces, going forward, "concept_materials + header scalars (base/labor/
-- waste%/overhead%)" with one ordered list of TYPED lines per concept:
--   line_type = 'material'   → catalog material (qty × unit_cost or material price)
--   line_type = 'labor'      → MANUAL labor (label + qty × unit_cost; no material_id)
--   line_type = 'percentage' → percent applied to a base (applies_to: material|labor|both)
--
-- Concept cost (computed in api/routers/concepts.py recalculate_concept_cost_from_lines):
--   materialSubtotal = Σ material lines (qty × COALESCE(unit_cost, material.price_numeric, 0))
--   laborSubtotal    = Σ labor lines   (qty × COALESCE(unit_cost, 0))
--   each % line       = (percent/100) × (material | labor | material+labor)   -- NON-compounding
--   calculated_cost   = materialSubtotal + laborSubtotal + Σ(% lines)
--
-- ADDITIVE + REVERSIBLE: concept_materials and the concepts.* scalar columns are
-- left intact (source of truth until the Phase 4 cutover). Idempotent.
-- Run on staging first, then prod (Supabase SQL editor).
-- Path: C:\Users\germa\Desktop\NGM_API\sql\create_concept_lines.sql
-- ============================================================================

CREATE TABLE IF NOT EXISTS public.concept_lines (
    id           uuid NOT NULL DEFAULT gen_random_uuid(),
    concept_id   uuid NOT NULL REFERENCES public.concepts(id) ON DELETE CASCADE,
    line_type    text NOT NULL,                 -- 'material' | 'labor' | 'percentage'
    sort_order   integer NOT NULL DEFAULT 0,
    label        text,                           -- shown name (material: optional snapshot; labor/% : required)

    -- material / labor lines
    material_id  text,                           -- material lines only (app-level integrity; materials."ID" is text)
    unit         text,                           -- unit label (material unit snapshot or manual labor unit)
    quantity     numeric(12,4),
    unit_cost    numeric(12,2),                  -- material: override (NULL → use material price); labor: manual cost

    -- percentage lines
    percent      numeric(7,4),                   -- e.g. 5.0 = 5%
    applies_to   text,                           -- 'material' | 'labor' | 'both'

    -- classification (categories-rearch) for material/labor lines
    cost_type    text,
    notes        text,
    created_at   timestamp with time zone NOT NULL DEFAULT now(),

    CONSTRAINT concept_lines_pkey PRIMARY KEY (id),
    CONSTRAINT concept_lines_line_type_chk CHECK (line_type IN ('material', 'labor', 'percentage')),
    CONSTRAINT concept_lines_applies_to_chk CHECK (applies_to IS NULL OR applies_to IN ('material', 'labor', 'both'))
);

CREATE INDEX IF NOT EXISTS idx_concept_lines_concept ON public.concept_lines(concept_id);
CREATE INDEX IF NOT EXISTS idx_concept_lines_order ON public.concept_lines(concept_id, sort_order);

COMMENT ON TABLE public.concept_lines IS 'Typed composition lines of a concept (material | labor | percentage). Replaces concept_materials + header scalars at the Phase 4 cutover.';
COMMENT ON COLUMN public.concept_lines.line_type IS 'material = catalog material; labor = manual labor; percentage = % of a base';
COMMENT ON COLUMN public.concept_lines.applies_to IS 'percentage lines: material | labor | both (material+labor subtotals)';
COMMENT ON COLUMN public.concept_lines.unit_cost IS 'material: override of the material price (NULL = use materials.price_numeric); labor: the manual unit cost';
