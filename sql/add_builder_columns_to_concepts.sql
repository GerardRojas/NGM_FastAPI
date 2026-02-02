-- ========================================
-- ADD BUILDER COLUMNS TO CONCEPTS TABLE
-- Adds waste_percent and builder JSONB columns
-- ========================================

-- Add waste_percent column if not exists
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_schema = 'public'
                   AND table_name = 'concepts'
                   AND column_name = 'waste_percent') THEN
        ALTER TABLE public.concepts ADD COLUMN waste_percent numeric(5,2) DEFAULT 0;
        RAISE NOTICE 'Added waste_percent column';
    ELSE
        RAISE NOTICE 'waste_percent column already exists';
    END IF;
END $$;

-- Add builder JSONB column if not exists
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_schema = 'public'
                   AND table_name = 'concepts'
                   AND column_name = 'builder') THEN
        ALTER TABLE public.concepts ADD COLUMN builder jsonb;
        RAISE NOTICE 'Added builder column';
    ELSE
        RAISE NOTICE 'builder column already exists';
    END IF;
END $$;

-- Add comments
COMMENT ON COLUMN public.concepts.waste_percent IS 'Waste percentage applied to materials total';
COMMENT ON COLUMN public.concepts.builder IS 'Builder state JSON containing inline items, labor items, and calculated totals';

-- Verify the columns were added
SELECT column_name, data_type, column_default
FROM information_schema.columns
WHERE table_schema = 'public'
AND table_name = 'concepts'
AND column_name IN ('waste_percent', 'builder')
ORDER BY column_name;
