-- ========================================
-- VERIFICAR Y AGREGAR COLUMNAS A MATERIALS
-- Ejecutar si el concepts schema falla
-- ========================================

-- Verificar columnas existentes
SELECT column_name, data_type
FROM information_schema.columns
WHERE table_schema = 'public' AND table_name = 'materials'
ORDER BY ordinal_position;

-- Agregar columnas FK si no existen
DO $$
BEGIN
    -- vendor_id
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_schema = 'public'
                   AND table_name = 'materials'
                   AND column_name = 'vendor_id') THEN
        ALTER TABLE public.materials ADD COLUMN vendor_id uuid;
        RAISE NOTICE 'Added vendor_id column';
    END IF;

    -- category_id
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_schema = 'public'
                   AND table_name = 'materials'
                   AND column_name = 'category_id') THEN
        ALTER TABLE public.materials ADD COLUMN category_id uuid;
        RAISE NOTICE 'Added category_id column';
    END IF;

    -- class_id
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_schema = 'public'
                   AND table_name = 'materials'
                   AND column_name = 'class_id') THEN
        ALTER TABLE public.materials ADD COLUMN class_id uuid;
        RAISE NOTICE 'Added class_id column';
    END IF;

    -- unit_id
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_schema = 'public'
                   AND table_name = 'materials'
                   AND column_name = 'unit_id') THEN
        ALTER TABLE public.materials ADD COLUMN unit_id uuid;
        RAISE NOTICE 'Added unit_id column';
    END IF;

    -- price_numeric
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_schema = 'public'
                   AND table_name = 'materials'
                   AND column_name = 'price_numeric') THEN
        ALTER TABLE public.materials ADD COLUMN price_numeric numeric(12,2);
        RAISE NOTICE 'Added price_numeric column';
    END IF;

    -- updated_at
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_schema = 'public'
                   AND table_name = 'materials'
                   AND column_name = 'updated_at') THEN
        ALTER TABLE public.materials ADD COLUMN updated_at timestamp with time zone DEFAULT now();
        RAISE NOTICE 'Added updated_at column';
    END IF;
END $$;

-- Verificar que las tablas de lookup existan
SELECT table_name
FROM information_schema.tables
WHERE table_schema = 'public'
AND table_name IN ('material_categories', 'material_classes', 'units');
