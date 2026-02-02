-- ========================================
-- MATERIALS DATABASE SCHEMA
-- Tablas de lookup y migracion de materials
-- ========================================

-- ========================================
-- 1. MATERIAL CATEGORIES
-- ========================================
CREATE TABLE IF NOT EXISTS public.material_categories (
    id uuid NOT NULL DEFAULT gen_random_uuid(),
    name text NOT NULL,
    description text,
    parent_id uuid REFERENCES public.material_categories(id) ON DELETE SET NULL,
    sort_order integer DEFAULT 0,
    is_active boolean DEFAULT true,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    CONSTRAINT material_categories_pkey PRIMARY KEY (id),
    CONSTRAINT material_categories_name_unique UNIQUE (name)
) TABLESPACE pg_default;

CREATE INDEX IF NOT EXISTS idx_material_categories_parent ON public.material_categories(parent_id);
CREATE INDEX IF NOT EXISTS idx_material_categories_active ON public.material_categories(is_active);

COMMENT ON TABLE public.material_categories IS 'Categorias de materiales para el estimator database';
COMMENT ON COLUMN public.material_categories.parent_id IS 'Permite jerarquia de categorias (categoria padre)';

-- ========================================
-- 2. MATERIAL CLASSES
-- ========================================
CREATE TABLE IF NOT EXISTS public.material_classes (
    id uuid NOT NULL DEFAULT gen_random_uuid(),
    name text NOT NULL,
    description text,
    sort_order integer DEFAULT 0,
    is_active boolean DEFAULT true,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    CONSTRAINT material_classes_pkey PRIMARY KEY (id),
    CONSTRAINT material_classes_name_unique UNIQUE (name)
) TABLESPACE pg_default;

CREATE INDEX IF NOT EXISTS idx_material_classes_active ON public.material_classes(is_active);

COMMENT ON TABLE public.material_classes IS 'Clases de materiales (tipos de acabado, calidad, etc.)';

-- ========================================
-- 3. UNITS TABLE (ya existe)
-- ========================================
-- Esquema existente:
-- create table public.units (
--   unit_name text null,
--   id_unit uuid not null default gen_random_uuid(),
--   constraint units_pkey primary key (id_unit)
-- )

-- Insertar unidades comunes si no existen
INSERT INTO public.units (unit_name)
SELECT u.name FROM (
    VALUES ('EA'), ('LF'), ('SF'), ('CF'), ('CY'), ('SY'), ('GAL'), ('LB'), ('TON'),
           ('BAG'), ('BOX'), ('ROLL'), ('SHEET'), ('SET'), ('HR'), ('DAY'), ('LS'), ('LN'), ('PR'), ('PCS')
) AS u(name)
WHERE NOT EXISTS (
    SELECT 1 FROM public.units WHERE unit_name = u.name
);

-- ========================================
-- 4. MODIFICAR TABLA MATERIALS
-- Agregar columnas FK y migrar datos
-- ========================================

-- Agregar nuevas columnas FK (si no existen)
DO $$
BEGIN
    -- vendor_id
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_schema = 'public'
                   AND table_name = 'materials'
                   AND column_name = 'vendor_id') THEN
        ALTER TABLE public.materials ADD COLUMN vendor_id uuid;
    END IF;

    -- category_id
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_schema = 'public'
                   AND table_name = 'materials'
                   AND column_name = 'category_id') THEN
        ALTER TABLE public.materials ADD COLUMN category_id uuid;
    END IF;

    -- class_id
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_schema = 'public'
                   AND table_name = 'materials'
                   AND column_name = 'class_id') THEN
        ALTER TABLE public.materials ADD COLUMN class_id uuid;
    END IF;

    -- unit_id
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_schema = 'public'
                   AND table_name = 'materials'
                   AND column_name = 'unit_id') THEN
        ALTER TABLE public.materials ADD COLUMN unit_id uuid;
    END IF;

    -- price_numeric (para tener precio como numero)
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_schema = 'public'
                   AND table_name = 'materials'
                   AND column_name = 'price_numeric') THEN
        ALTER TABLE public.materials ADD COLUMN price_numeric numeric(12,2);
    END IF;

    -- updated_at timestamp
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_schema = 'public'
                   AND table_name = 'materials'
                   AND column_name = 'updated_at') THEN
        ALTER TABLE public.materials ADD COLUMN updated_at timestamp with time zone DEFAULT now();
    END IF;
END $$;

-- ========================================
-- 5. AGREGAR FOREIGN KEYS
-- ========================================

-- FK a Vendors
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.table_constraints
                   WHERE constraint_name = 'materials_vendor_fkey') THEN
        ALTER TABLE public.materials
        ADD CONSTRAINT materials_vendor_fkey
        FOREIGN KEY (vendor_id) REFERENCES public."Vendors"(id) ON DELETE SET NULL;
    END IF;
END $$;

-- FK a material_categories
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.table_constraints
                   WHERE constraint_name = 'materials_category_fkey') THEN
        ALTER TABLE public.materials
        ADD CONSTRAINT materials_category_fkey
        FOREIGN KEY (category_id) REFERENCES public.material_categories(id) ON DELETE SET NULL;
    END IF;
END $$;

-- FK a material_classes
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.table_constraints
                   WHERE constraint_name = 'materials_class_fkey') THEN
        ALTER TABLE public.materials
        ADD CONSTRAINT materials_class_fkey
        FOREIGN KEY (class_id) REFERENCES public.material_classes(id) ON DELETE SET NULL;
    END IF;
END $$;

-- FK a units (usa id_unit como PK)
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.table_constraints
                   WHERE constraint_name = 'materials_unit_fkey') THEN
        ALTER TABLE public.materials
        ADD CONSTRAINT materials_unit_fkey
        FOREIGN KEY (unit_id) REFERENCES public.units(id_unit) ON DELETE SET NULL;
    END IF;
END $$;

-- ========================================
-- 6. INDICES PARA PERFORMANCE
-- ========================================
CREATE INDEX IF NOT EXISTS idx_materials_vendor ON public.materials(vendor_id);
CREATE INDEX IF NOT EXISTS idx_materials_category ON public.materials(category_id);
CREATE INDEX IF NOT EXISTS idx_materials_class ON public.materials(class_id);
CREATE INDEX IF NOT EXISTS idx_materials_unit ON public.materials(unit_id);

-- ========================================
-- 7. MIGRACION DE DATOS EXISTENTES
-- Ejecutar despues de crear las tablas de lookup
-- ========================================

-- Migrar vendors existentes (crear categorias desde texto existente)
-- NOTA: Ejecutar manualmente despues de revisar los datos
/*
-- Crear categorias desde los valores unicos de "Item Category"
INSERT INTO public.material_categories (name)
SELECT DISTINCT "Item Category"
FROM public.materials
WHERE "Item Category" IS NOT NULL AND "Item Category" != ''
ON CONFLICT (name) DO NOTHING;

-- Crear clases desde los valores unicos de "Item Class"
INSERT INTO public.material_classes (name)
SELECT DISTINCT "Item Class"
FROM public.materials
WHERE "Item Class" IS NOT NULL AND "Item Class" != ''
ON CONFLICT (name) DO NOTHING;

-- Actualizar materials con los IDs de las nuevas tablas
UPDATE public.materials m
SET category_id = mc.id
FROM public.material_categories mc
WHERE m."Item Category" = mc.name;

UPDATE public.materials m
SET class_id = mcl.id
FROM public.material_classes mcl
WHERE m."Item Class" = mcl.name;

UPDATE public.materials m
SET unit_id = u.id_unit
FROM public.units u
WHERE m."Unit" = u.unit_name;

UPDATE public.materials m
SET vendor_id = v.id
FROM public."Vendors" v
WHERE m."Vendor" = v.vendor_name;

-- Migrar precio a numerico
UPDATE public.materials
SET price_numeric = NULLIF(regexp_replace("Price", '[^0-9.]', '', 'g'), '')::numeric
WHERE "Price" IS NOT NULL;
*/

-- ========================================
-- 8. TRIGGER PARA updated_at
-- ========================================
CREATE OR REPLACE FUNCTION update_materials_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trigger_materials_updated_at ON public.materials;
CREATE TRIGGER trigger_materials_updated_at
    BEFORE UPDATE ON public.materials
    FOR EACH ROW
    EXECUTE FUNCTION update_materials_updated_at();

-- ========================================
-- COMENTARIOS FINALES
-- ========================================
COMMENT ON COLUMN public.materials.vendor_id IS 'FK a Vendors - proveedor del material';
COMMENT ON COLUMN public.materials.category_id IS 'FK a material_categories - categoria del material';
COMMENT ON COLUMN public.materials.class_id IS 'FK a material_classes - clase/tipo del material';
COMMENT ON COLUMN public.materials.unit_id IS 'FK a units - unidad de medida';
COMMENT ON COLUMN public.materials.price_numeric IS 'Precio como numero para calculos';
