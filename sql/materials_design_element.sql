-- =============================================================================
-- MATERIALS — is_design_element flag + retro-analysis seed + "Model" field.
--
-- A "design element" is a finish/selection a client/field cares about (tile, WC,
-- paint, cabinets, fixtures…) — as opposed to structural/labor work. It drives
-- the Design Take Off Form (see hub-vite .../sheet-templates/TAKEOFF_PLAN.md and
-- estimator-database/DESIGN_ELEMENTS_PLAN.md).
--
-- Snake_case column to match the table's newer columns (cost_type, category_id).
-- Idempotent and additive. Run on STAGING first, verify, then PROD.
-- Path: C:\Users\germa\Desktop\NGM_API\sql\materials_design_element.sql
-- =============================================================================

-- 1) ── Columns ───────────────────────────────────────────────────────────────
ALTER TABLE public.materials
    ADD COLUMN IF NOT EXISTS is_design_element boolean NOT NULL DEFAULT false;

-- "Model" (Title Case, next to "Brand"/"SKU"): the manufacturer model/spec shown
-- on the Design Take Off selection schedule. Free text; defaults null.
ALTER TABLE public.materials
    ADD COLUMN IF NOT EXISTS "Model" text;


-- 2) ── Retro-analysis: seed the OBVIOUS design/finish categories ─────────────
--    Only material rows (cost_type defaults to 'material' when null) whose
--    material_categories.name is an unambiguous finish/selection category.
--    Borderline categories are intentionally LEFT OUT for manual review:
--    Interior Trim, Stair Case, Exterior Finishes, Exterior Structures, Roof,
--    HVAC, Solar Panels, Landscaping, Hardscape.
UPDATE public.materials m
SET is_design_element = true
FROM public.material_categories c
WHERE m.category_id = c.id
  AND coalesce(m.cost_type, 'material') = 'material'
  AND c.name IN (
        'Windows',
        'Doors',
        'Flooring',
        'Ceramic Tile',
        'Cabinets',
        'Countertops',
        'Paint',
        'Appliances',
        'Finish Plumbing',
        'Finish Electrical'
      )
  AND m.is_design_element IS DISTINCT FROM true;


-- =============================================================================
-- VERIFICATION (optional)
-- -----------------------------------------------------------------------------
-- -- How many design elements got flagged, by category:
-- select c.name, count(*) filter (where m.is_design_element) as design,
--        count(*) as total
--   from public.materials m
--   join public.material_categories c on c.id = m.category_id
--  where coalesce(m.cost_type,'material') = 'material'
--  group by c.name order by design desc;
--
-- -- Borderline categories to review by hand (add to the IN(...) above if design):
-- select distinct c.name from public.material_categories c
--   join public.materials m on m.category_id = c.id
--  where c.name in ('Interior Trim','Stair Case','Exterior Finishes',
--                   'Exterior Structures','Roof','HVAC','Solar Panels',
--                   'Landscaping','Hardscape');
-- =============================================================================
