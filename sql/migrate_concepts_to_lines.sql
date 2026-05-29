-- ============================================================================
-- CONCEPT BUILDER v2 — backfill concept_lines from concept_materials + scalars
-- ----------------------------------------------------------------------------
-- Populates concept_lines for every concept that has none yet (idempotent — safe
-- to re-run; re-run before the Phase 2 cutover to pick up edits made via the old
-- builder in the meantime). Does NOT touch concept_materials, the concepts.*
-- scalar columns, or concepts.calculated_cost — fully reversible (just
-- TRUNCATE public.concept_lines to undo).
--
-- Mapping per concept:
--   concept_materials row  → 'material' line (material_id, qty, unit_cost=override, cost_type)
--   waste_percent  (>0)    → 'percentage' line, applies_to='material', label 'Waste'
--   labor_cost     (>0)    → 'labor' line (qty 1, unit_cost=labor_cost), label 'Labor'
--   base_cost      (>0)    → 'labor' line label 'Base' — base behaves like labor in the old
--                            formula (added post-waste, pre-overhead), so this preserves the
--                            math; a 'material' line would wrongly get waste applied. Rare field.
--   overhead_percentage(>0)→ 'percentage' line, applies_to='both', label 'Overhead'
--
-- PARITY NOTE: the new model applies percentages to the RAW subtotals (NON-compounding),
-- while the old formula compounded overhead onto materials-with-waste. So concepts that have
-- BOTH waste% > 0 AND overhead% > 0 will show a small delta (intended model change). The
-- report at the end lists deltas so you can review before any cutover.
-- Run on staging first, then prod (Supabase SQL editor).
-- Path: C:\Users\germa\Desktop\NGM_API\sql\migrate_concepts_to_lines.sql
-- ============================================================================

-- Snapshot the concepts to migrate (no lines yet) BEFORE inserting, so the per-type
-- inserts below all target the same set.
DROP TABLE IF EXISTS _concepts_to_migrate;
CREATE TEMP TABLE _concepts_to_migrate AS
SELECT c.id,
       c.waste_percent,
       c.labor_cost,
       c.base_cost,
       c.overhead_percentage,
       COALESCE((SELECT MAX(cm.sort_order) FROM public.concept_materials cm WHERE cm.concept_id = c.id), -1) AS max_mat_sort
FROM public.concepts c
WHERE NOT EXISTS (SELECT 1 FROM public.concept_lines cl WHERE cl.concept_id = c.id);

-- 1) material lines
INSERT INTO public.concept_lines (concept_id, line_type, sort_order, material_id, quantity, unit_cost, cost_type)
SELECT cm.concept_id, 'material', COALESCE(cm.sort_order, 0), cm.material_id, cm.quantity, cm.unit_cost_override, cm.cost_type
FROM public.concept_materials cm
JOIN _concepts_to_migrate t ON t.id = cm.concept_id;

-- 2) waste% → percentage on material
INSERT INTO public.concept_lines (concept_id, line_type, sort_order, label, percent, applies_to)
SELECT t.id, 'percentage', t.max_mat_sort + 1, 'Waste', t.waste_percent, 'material'
FROM _concepts_to_migrate t
WHERE COALESCE(t.waste_percent, 0) > 0;

-- 3) labor_cost → labor line
INSERT INTO public.concept_lines (concept_id, line_type, sort_order, label, quantity, unit_cost)
SELECT t.id, 'labor', t.max_mat_sort + 2, 'Labor', 1, t.labor_cost
FROM _concepts_to_migrate t
WHERE COALESCE(t.labor_cost, 0) > 0;

-- 4) base_cost → labor line "Base" (math-equivalent bucket: post-waste, pre-overhead)
INSERT INTO public.concept_lines (concept_id, line_type, sort_order, label, quantity, unit_cost)
SELECT t.id, 'labor', t.max_mat_sort + 3, 'Base', 1, t.base_cost
FROM _concepts_to_migrate t
WHERE COALESCE(t.base_cost, 0) > 0;

-- 5) overhead% → percentage on both
INSERT INTO public.concept_lines (concept_id, line_type, sort_order, label, percent, applies_to)
SELECT t.id, 'percentage', t.max_mat_sort + 4, 'Overhead', t.overhead_percentage, 'both'
FROM _concepts_to_migrate t
WHERE COALESCE(t.overhead_percentage, 0) > 0;

-- ── PARITY / DELTA REPORT ───────────────────────────────────────────────────
-- new (line-based, non-compounding) vs old (stored calculated_cost). Concepts with
-- both waste% and overhead% set are expected to differ slightly; everything else
-- should be ~0.
WITH mat AS (
    SELECT cl.concept_id, SUM(cl.quantity * COALESCE(cl.unit_cost, m.price_numeric, 0)) AS sub
    FROM public.concept_lines cl
    LEFT JOIN public.materials m ON cl.material_id = m."ID"
    WHERE cl.line_type = 'material'
    GROUP BY cl.concept_id
),
lab AS (
    SELECT concept_id, SUM(quantity * COALESCE(unit_cost, 0)) AS sub
    FROM public.concept_lines WHERE line_type = 'labor' GROUP BY concept_id
),
pct AS (
    SELECT cl.concept_id,
           SUM((cl.percent / 100.0) * CASE cl.applies_to
                WHEN 'material' THEN COALESCE(mat.sub, 0)
                WHEN 'labor'    THEN COALESCE(lab.sub, 0)
                ELSE COALESCE(mat.sub, 0) + COALESCE(lab.sub, 0)
           END) AS amt
    FROM public.concept_lines cl
    LEFT JOIN mat ON mat.concept_id = cl.concept_id
    LEFT JOIN lab ON lab.concept_id = cl.concept_id
    WHERE cl.line_type = 'percentage'
    GROUP BY cl.concept_id
),
newcost AS (
    SELECT c.id, c.code,
           c.calculated_cost AS old_cost,
           ROUND(COALESCE(mat.sub, 0) + COALESCE(lab.sub, 0) + COALESCE(pct.amt, 0), 2) AS new_cost
    FROM public.concepts c
    LEFT JOIN mat ON mat.concept_id = c.id
    LEFT JOIN lab ON lab.concept_id = c.id
    LEFT JOIN pct ON pct.concept_id = c.id
    WHERE EXISTS (SELECT 1 FROM public.concept_lines cl WHERE cl.concept_id = c.id)
)
SELECT code,
       old_cost,
       new_cost,
       ROUND(new_cost - COALESCE(old_cost, 0), 2) AS delta
FROM newcost
ORDER BY ABS(ROUND(new_cost - COALESCE(old_cost, 0), 2)) DESC
LIMIT 100;
