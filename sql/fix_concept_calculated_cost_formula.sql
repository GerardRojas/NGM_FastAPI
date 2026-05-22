-- ============================================================
-- FIX: concepts.calculated_cost debe usar la formula COMPLETA
-- ============================================================
-- Problema: el trigger update_concept_calculated_cost usaba
-- calculate_concept_cost() (solo SUM(qty*cost)), inconsistente con
-- recalculate_concept_cost() en api/routers/concepts.py, que aplica
-- waste% + base + labor + overhead%.
--
-- Esta migracion:
--   1. Crea calculate_concept_total() con la formula completa.
--   2. Reapunta el trigger a esa funcion.
--   3. Backfillea calculated_cost de todos los conceptos existentes.
--
-- Idempotente: se puede correr varias veces sin efectos adversos.
-- ============================================================

-- 1. Funcion con la formula completa (igual a la del backend Python)
CREATE OR REPLACE FUNCTION calculate_concept_total(p_concept_id uuid)
RETURNS numeric AS $$
DECLARE
    v_materials numeric := 0;
    v_waste     numeric := 0;
    v_overhead  numeric := 0;
    v_base      numeric := 0;
    v_labor     numeric := 0;
    v_subtotal  numeric := 0;
BEGIN
    -- Costo crudo de materiales (reusa el helper existente)
    v_materials := calculate_concept_cost(p_concept_id);

    SELECT COALESCE(waste_percent, 0),
           COALESCE(overhead_percentage, 0),
           COALESCE(base_cost, 0),
           COALESCE(labor_cost, 0)
    INTO v_waste, v_overhead, v_base, v_labor
    FROM public.concepts
    WHERE id = p_concept_id;

    v_subtotal := (v_materials * (1 + v_waste / 100.0)) + v_base + v_labor;

    RETURN round(v_subtotal * (1 + v_overhead / 100.0), 2);
END;
$$ LANGUAGE plpgsql;

-- 2. Reapuntar el trigger a la formula completa
CREATE OR REPLACE FUNCTION update_concept_calculated_cost()
RETURNS TRIGGER AS $$
BEGIN
    UPDATE public.concepts
    SET calculated_cost = calculate_concept_total(
        CASE
            WHEN TG_OP = 'DELETE' THEN OLD.concept_id
            ELSE NEW.concept_id
        END
    )
    WHERE id = CASE
        WHEN TG_OP = 'DELETE' THEN OLD.concept_id
        ELSE NEW.concept_id
    END;

    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- (el trigger trigger_concept_materials_cost ya apunta a esta funcion;
--  CREATE OR REPLACE actualiza el cuerpo sin necesidad de recrearlo)

-- 3. Backfill: recalcular todos los conceptos existentes con la formula nueva
UPDATE public.concepts
SET calculated_cost = calculate_concept_total(id);

-- Verificacion rapida
SELECT id, code, base_cost, labor_cost, waste_percent, overhead_percentage, calculated_cost
FROM public.concepts
ORDER BY updated_at DESC
LIMIT 20;
