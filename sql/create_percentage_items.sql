-- ========================================
-- PERCENTAGE ITEMS TABLE
-- Items de porcentaje reutilizables (waste, overhead, profit, etc.)
-- Se usan en el Concept Builder para agregar costos porcentuales
-- ========================================

CREATE TABLE IF NOT EXISTS public.percentage_items (
    id uuid NOT NULL DEFAULT gen_random_uuid(),
    code text NOT NULL,                              -- Codigo unico (ej: WASTE, OVERHEAD)
    description text NOT NULL,                       -- Nombre descriptivo
    applies_to text NOT NULL DEFAULT 'material',     -- A que subtotal aplica: material, labor, total
    default_value numeric(5,2) DEFAULT 0,            -- Valor porcentual por defecto
    is_standard boolean DEFAULT false,               -- Si es true, se precarga automaticamente en cada concepto
    is_active boolean DEFAULT true,
    sort_order integer DEFAULT 0,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),

    CONSTRAINT percentage_items_pkey PRIMARY KEY (id),
    CONSTRAINT percentage_items_code_unique UNIQUE (code),
    CONSTRAINT percentage_items_applies_to_check CHECK (applies_to IN ('material', 'labor', 'total'))
) TABLESPACE pg_default;

-- Indices
CREATE INDEX IF NOT EXISTS idx_percentage_items_code ON public.percentage_items(code);
CREATE INDEX IF NOT EXISTS idx_percentage_items_active ON public.percentage_items(is_active);
CREATE INDEX IF NOT EXISTS idx_percentage_items_standard ON public.percentage_items(is_standard);

-- Trigger para updated_at
CREATE OR REPLACE FUNCTION update_percentage_items_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trigger_percentage_items_updated_at ON public.percentage_items;
CREATE TRIGGER trigger_percentage_items_updated_at
    BEFORE UPDATE ON public.percentage_items
    FOR EACH ROW
    EXECUTE FUNCTION update_percentage_items_updated_at();

-- RLS
ALTER TABLE public.percentage_items ENABLE ROW LEVEL SECURITY;

CREATE POLICY "percentage_items_select_all"
    ON public.percentage_items FOR SELECT USING (true);

CREATE POLICY "percentage_items_insert_auth"
    ON public.percentage_items FOR INSERT WITH CHECK (true);

CREATE POLICY "percentage_items_update_auth"
    ON public.percentage_items FOR UPDATE USING (true);

CREATE POLICY "percentage_items_delete_auth"
    ON public.percentage_items FOR DELETE USING (true);

-- Comments
COMMENT ON TABLE public.percentage_items IS 'Items de porcentaje reutilizables para el Concept Builder (waste, overhead, profit, etc.)';
COMMENT ON COLUMN public.percentage_items.code IS 'Codigo unico del item (ej: WASTE, OVERHEAD, PROFIT)';
COMMENT ON COLUMN public.percentage_items.applies_to IS 'A que subtotal aplica: material, labor, o total';
COMMENT ON COLUMN public.percentage_items.is_standard IS 'Si es true, se precarga automaticamente en cada concepto del builder';
COMMENT ON COLUMN public.percentage_items.default_value IS 'Valor porcentual por defecto (0-100)';

-- ========================================
-- SEED DATA - Items estandar
-- ========================================
INSERT INTO public.percentage_items (code, description, applies_to, default_value, is_standard, sort_order)
VALUES ('WASTE', 'Material Waste', 'material', 5.0, true, 1)
ON CONFLICT (code) DO NOTHING;
