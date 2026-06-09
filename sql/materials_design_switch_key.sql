-- =============================================================================
-- MATERIALS — design_switch_key (the package SLOT/position).
--
-- Design packages are switched by POSITION (Switch ID), not by category: the same
-- switch key across finishes (Brushed Nickel / Matte Black / Gold Finish) marks
-- the same role (e.g. "cabinet pulls", "front door hardware") with a different
-- product. Pairs with the existing materials.design_package_option (the finish).
-- See estimator-database/DESIGN_PACKAGES_PLAN.md.
--
-- Idempotent and additive. Run on STAGING first, verify, then PROD.
-- Path: C:\Users\germa\Desktop\NGM_API\sql\materials_design_switch_key.sql
-- =============================================================================

ALTER TABLE public.materials
    ADD COLUMN IF NOT EXISTS design_switch_key text;

CREATE INDEX IF NOT EXISTS idx_materials_design_switch_key
    ON public.materials (design_switch_key);

-- VERIFICATION
-- select count(*) from public.materials where design_switch_key is not null;
-- select design_package_option, count(*) from public.materials
--   where design_switch_key is not null group by 1 order by 1;
-- =============================================================================
