-- =============================================================================
-- CATEGORIES RE-ARCH — PHASE 2: cost_type on the catalog + expenses (ADDITIVE)
-- =============================================================================
-- Adds the cost_type dimension where spend originates, and the fine
-- classification (subcategory + cost_type) onto expenses. All additive/nullable
-- or defaulted, so nothing existing breaks. account_id stays the source of truth;
-- the new expense columns are dual-written going forward and backfilled here from
-- the parity-proven overlay (account_category_map).
--
-- Depends on categories_rearch_phase1.sql (cost_type enum, subcategories,
-- account_category_map). Idempotent. Run on staging, then prod.
-- =============================================================================

-- 1. Materials: every material is classified (default 'material'; edit in UI). --
ALTER TABLE public.materials
    ADD COLUMN IF NOT EXISTS cost_type cost_type NOT NULL DEFAULT 'material';

-- 2. Concept components: per-line cost_type. NULL => inherit the material's type
--    at read time, so a composite rolls up into material/labor/external_service. --
ALTER TABLE public.concept_materials
    ADD COLUMN IF NOT EXISTS cost_type cost_type;

-- 3. Expenses: the fine classification. account_id is untouched; these are the
--    new keys (dual-written), backfilled below from the overlay. --
ALTER TABLE public."expenses_manual_COGS"
    ADD COLUMN IF NOT EXISTS subcategory_id uuid REFERENCES public.subcategories(id) ON DELETE SET NULL;
ALTER TABLE public."expenses_manual_COGS"
    ADD COLUMN IF NOT EXISTS cost_type cost_type;

CREATE INDEX IF NOT EXISTS idx_expenses_subcategory
    ON public."expenses_manual_COGS" (subcategory_id)
    WHERE subcategory_id IS NOT NULL;

-- 4. Backfill expenses from the overlay (account_id -> subcategory + cost_type).
--    Only fills rows not already classified, so re-runs and future dual-writes
--    are never overwritten. Expenses with a null/unmapped account stay NULL
--    (they were unclassified before too — no regression).
UPDATE public."expenses_manual_COGS" e
   SET subcategory_id = m.subcategory_id,
       cost_type      = m.cost_type
  FROM public.account_category_map m
 WHERE e.account_id = m.account_id
   AND e.subcategory_id IS NULL;

-- VERIFICATION ----------------------------------------------------------------
-- select count(*) total,
--        count(subcategory_id) classified,
--        count(*) - count(subcategory_id) unclassified
--   from public."expenses_manual_COGS";
-- select cost_type, count(*) from public.materials group by 1;
