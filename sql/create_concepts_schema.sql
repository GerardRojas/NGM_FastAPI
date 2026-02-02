-- ========================================
-- CONCEPTS DATABASE SCHEMA
-- Conceptos = contenedores de materiales
-- ========================================

-- ========================================
-- 1. CONCEPT CATEGORIES (opcional, puede reusar material_categories)
-- ========================================
-- Si quieres categorias separadas para conceptos, descomenta esto:
/*
CREATE TABLE IF NOT EXISTS public.concept_categories (
    id uuid NOT NULL DEFAULT gen_random_uuid(),
    name text NOT NULL,
    description text,
    parent_id uuid REFERENCES public.concept_categories(id) ON DELETE SET NULL,
    sort_order integer DEFAULT 0,
    is_active boolean DEFAULT true,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    CONSTRAINT concept_categories_pkey PRIMARY KEY (id),
    CONSTRAINT concept_categories_name_unique UNIQUE (name)
) TABLESPACE pg_default;
*/

-- ========================================
-- 2. CONCEPTS TABLE (estructura similar a materials)
-- ========================================
CREATE TABLE IF NOT EXISTS public.concepts (
    id uuid NOT NULL DEFAULT gen_random_uuid(),
    code text NOT NULL,                              -- Codigo unico del concepto (ej: CON-001)
    short_description text,                          -- Nombre corto
    full_description text,                           -- Descripcion completa

    -- Relaciones (mismas que materials)
    category_id uuid REFERENCES public.material_categories(id) ON DELETE SET NULL,
    subcategory_id uuid REFERENCES public.material_categories(id) ON DELETE SET NULL,
    class_id uuid REFERENCES public.material_classes(id) ON DELETE SET NULL,
    unit_id uuid REFERENCES public.units(id_unit) ON DELETE SET NULL,

    -- Costos
    base_cost numeric(12,2) DEFAULT 0,               -- Costo base manual (opcional)
    labor_cost numeric(12,2) DEFAULT 0,              -- Costo de mano de obra
    overhead_percentage numeric(5,2) DEFAULT 0,      -- % de overhead
    calculated_cost numeric(12,2),                   -- Costo calculado desde materiales

    -- Metadata
    image text,                                      -- URL de imagen
    notes text,                                      -- Notas adicionales

    -- Estado
    is_active boolean DEFAULT true,
    is_template boolean DEFAULT false,               -- Si es un template reutilizable

    -- Timestamps
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    created_by uuid,                                 -- Usuario que lo creo

    CONSTRAINT concepts_pkey PRIMARY KEY (id),
    CONSTRAINT concepts_code_unique UNIQUE (code)
) TABLESPACE pg_default;

-- Indices
CREATE INDEX IF NOT EXISTS idx_concepts_category ON public.concepts(category_id);
CREATE INDEX IF NOT EXISTS idx_concepts_subcategory ON public.concepts(subcategory_id);
CREATE INDEX IF NOT EXISTS idx_concepts_class ON public.concepts(class_id);
CREATE INDEX IF NOT EXISTS idx_concepts_unit ON public.concepts(unit_id);
CREATE INDEX IF NOT EXISTS idx_concepts_active ON public.concepts(is_active);
CREATE INDEX IF NOT EXISTS idx_concepts_code ON public.concepts(code);

COMMENT ON TABLE public.concepts IS 'Conceptos de estimacion - contenedores de materiales';
COMMENT ON COLUMN public.concepts.code IS 'Codigo unico del concepto (ej: CON-001, ELEC-001)';
COMMENT ON COLUMN public.concepts.calculated_cost IS 'Suma de (material.price * quantity) de concept_materials';
COMMENT ON COLUMN public.concepts.is_template IS 'Indica si es un template que puede ser reutilizado';

-- ========================================
-- 3. CONCEPT_MATERIALS (tabla de union)
-- Relaciona conceptos con materiales
-- ========================================
CREATE TABLE IF NOT EXISTS public.concept_materials (
    id uuid NOT NULL DEFAULT gen_random_uuid(),
    concept_id uuid NOT NULL REFERENCES public.concepts(id) ON DELETE CASCADE,
    material_id text NOT NULL,                       -- FK a materials."ID" (es text)

    -- Cantidad y unidad
    quantity numeric(12,4) NOT NULL DEFAULT 1,       -- Cantidad de material en el concepto
    unit_id uuid REFERENCES public.units(id_unit) ON DELETE SET NULL,  -- Puede override la unidad del material

    -- Costos override (opcional)
    unit_cost_override numeric(12,2),                -- Si quieres override el precio del material

    -- Metadata
    notes text,
    sort_order integer DEFAULT 0,                    -- Orden de los materiales en el concepto

    -- Timestamps
    created_at timestamp with time zone DEFAULT now(),

    CONSTRAINT concept_materials_pkey PRIMARY KEY (id),
    CONSTRAINT concept_materials_unique UNIQUE (concept_id, material_id)
) TABLESPACE pg_default;

-- FK a materials (la tabla materials usa "ID" como PK tipo text)
-- Nota: No podemos crear FK directa porque materials."ID" es text
-- La integridad se manejara a nivel de aplicacion

-- Indices
CREATE INDEX IF NOT EXISTS idx_concept_materials_concept ON public.concept_materials(concept_id);
CREATE INDEX IF NOT EXISTS idx_concept_materials_material ON public.concept_materials(material_id);

COMMENT ON TABLE public.concept_materials IS 'Tabla de union entre conceptos y materiales';
COMMENT ON COLUMN public.concept_materials.quantity IS 'Cantidad de este material necesaria para el concepto';
COMMENT ON COLUMN public.concept_materials.unit_cost_override IS 'Override del precio unitario del material (opcional)';

-- ========================================
-- 4. TRIGGER PARA updated_at EN CONCEPTS
-- ========================================
CREATE OR REPLACE FUNCTION update_concepts_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trigger_concepts_updated_at ON public.concepts;
CREATE TRIGGER trigger_concepts_updated_at
    BEFORE UPDATE ON public.concepts
    FOR EACH ROW
    EXECUTE FUNCTION update_concepts_updated_at();

-- ========================================
-- 5. FUNCION PARA CALCULAR COSTO DEL CONCEPTO
-- ========================================
CREATE OR REPLACE FUNCTION calculate_concept_cost(p_concept_id uuid)
RETURNS numeric AS $$
DECLARE
    v_total numeric := 0;
BEGIN
    SELECT COALESCE(SUM(
        cm.quantity * COALESCE(cm.unit_cost_override, m.price_numeric, 0)
    ), 0)
    INTO v_total
    FROM public.concept_materials cm
    LEFT JOIN public.materials m ON cm.material_id = m."ID"
    WHERE cm.concept_id = p_concept_id;

    RETURN v_total;
END;
$$ LANGUAGE plpgsql;

-- ========================================
-- 6. TRIGGER PARA ACTUALIZAR calculated_cost
-- Cuando se modifican los materiales del concepto
-- ========================================
CREATE OR REPLACE FUNCTION update_concept_calculated_cost()
RETURNS TRIGGER AS $$
BEGIN
    -- Actualizar el costo calculado del concepto
    UPDATE public.concepts
    SET calculated_cost = calculate_concept_cost(
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

DROP TRIGGER IF EXISTS trigger_concept_materials_cost ON public.concept_materials;
CREATE TRIGGER trigger_concept_materials_cost
    AFTER INSERT OR UPDATE OR DELETE ON public.concept_materials
    FOR EACH ROW
    EXECUTE FUNCTION update_concept_calculated_cost();

-- ========================================
-- 7. VIEW PARA CONCEPTOS CON INFO COMPLETA
-- ========================================
CREATE OR REPLACE VIEW public.concepts_with_details AS
SELECT
    c.*,
    cat.name AS category_name,
    subcat.name AS subcategory_name,
    cls.name AS class_name,
    u.unit_name,
    (
        SELECT COUNT(*)::integer
        FROM public.concept_materials cm
        WHERE cm.concept_id = c.id
    ) AS materials_count,
    calculate_concept_cost(c.id) AS total_material_cost
FROM public.concepts c
LEFT JOIN public.material_categories cat ON c.category_id = cat.id
LEFT JOIN public.material_categories subcat ON c.subcategory_id = subcat.id
LEFT JOIN public.material_classes cls ON c.class_id = cls.id
LEFT JOIN public.units u ON c.unit_id = u.id_unit;

COMMENT ON VIEW public.concepts_with_details IS 'Vista de conceptos con nombres de relaciones y conteo de materiales';

-- ========================================
-- 8. VIEW PARA MATERIALES DE UN CONCEPTO
-- ========================================
CREATE OR REPLACE VIEW public.concept_materials_with_details AS
SELECT
    cm.*,
    m."Short Description" AS material_name,
    m."Full Description" AS material_full_description,
    m."Brand" AS material_brand,
    m.price_numeric AS material_price,
    m."Price" AS material_price_text,
    m."Image" AS material_image,
    m."Unit" AS material_unit_text,
    u.unit_name AS override_unit,
    COALESCE(cm.unit_cost_override, m.price_numeric, 0) AS effective_unit_cost,
    cm.quantity * COALESCE(cm.unit_cost_override, m.price_numeric, 0) AS line_total
FROM public.concept_materials cm
LEFT JOIN public.materials m ON cm.material_id = m."ID"
LEFT JOIN public.units u ON cm.unit_id = u.id_unit;

COMMENT ON VIEW public.concept_materials_with_details IS 'Vista de materiales de concepto con info del material';
