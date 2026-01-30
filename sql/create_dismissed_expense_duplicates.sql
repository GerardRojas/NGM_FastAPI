-- =====================================================
-- Tabla: dismissed_expense_duplicates
-- =====================================================
-- Almacena pares de expenses que el usuario ha marcado
-- como "No es duplicado" para no volver a alertar sobre ellos

CREATE TABLE IF NOT EXISTS dismissed_expense_duplicates (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,

    -- Par de expense IDs (siempre ordenados: menor primero)
    expense_id_1 UUID NOT NULL,
    expense_id_2 UUID NOT NULL,

    -- Metadata
    dismissed_at TIMESTAMPTZ DEFAULT NOW(),
    dismissed_reason TEXT,  -- Opcional: 'not_duplicate', 'already_reviewed', etc.

    -- Constraint: No duplicar el mismo par
    CONSTRAINT unique_dismissed_pair UNIQUE (user_id, expense_id_1, expense_id_2),

    -- Constraint: expense_id_1 debe ser menor que expense_id_2 (ordenamiento consistente)
    CONSTRAINT ordered_expense_ids CHECK (expense_id_1 < expense_id_2)
);

-- Índices para queries rápidos
CREATE INDEX IF NOT EXISTS idx_dismissed_duplicates_user_id
    ON dismissed_expense_duplicates(user_id);

CREATE INDEX IF NOT EXISTS idx_dismissed_duplicates_expense_id_1
    ON dismissed_expense_duplicates(expense_id_1);

CREATE INDEX IF NOT EXISTS idx_dismissed_duplicates_expense_id_2
    ON dismissed_expense_duplicates(expense_id_2);

CREATE INDEX IF NOT EXISTS idx_dismissed_duplicates_created_at
    ON dismissed_expense_duplicates(dismissed_at DESC);

-- RLS Policies
ALTER TABLE dismissed_expense_duplicates ENABLE ROW LEVEL SECURITY;

-- Los usuarios solo pueden ver sus propios dismissals
CREATE POLICY "Users can view their own dismissed duplicates"
    ON dismissed_expense_duplicates
    FOR SELECT
    USING (auth.uid() = user_id);

-- Los usuarios pueden insertar sus propios dismissals
CREATE POLICY "Users can insert their own dismissed duplicates"
    ON dismissed_expense_duplicates
    FOR INSERT
    WITH CHECK (auth.uid() = user_id);

-- Los usuarios pueden eliminar sus propios dismissals (si quieren "reactivar" la alerta)
CREATE POLICY "Users can delete their own dismissed duplicates"
    ON dismissed_expense_duplicates
    FOR DELETE
    USING (auth.uid() = user_id);

-- Comentarios
COMMENT ON TABLE dismissed_expense_duplicates IS 'Pares de expenses marcados como "no duplicados" por usuarios';
COMMENT ON COLUMN dismissed_expense_duplicates.expense_id_1 IS 'ID del primer expense (menor UUID)';
COMMENT ON COLUMN dismissed_expense_duplicates.expense_id_2 IS 'ID del segundo expense (mayor UUID)';
COMMENT ON COLUMN dismissed_expense_duplicates.dismissed_reason IS 'Razón opcional del dismissal';

-- Función helper para normalizar el orden de IDs al insertar
CREATE OR REPLACE FUNCTION dismiss_duplicate_pair(
    p_user_id UUID,
    p_expense_id_1 UUID,
    p_expense_id_2 UUID,
    p_reason TEXT DEFAULT 'not_duplicate'
)
RETURNS UUID
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
DECLARE
    v_id1 UUID;
    v_id2 UUID;
    v_result_id UUID;
BEGIN
    -- Ordenar los IDs para mantener consistencia
    IF p_expense_id_1 < p_expense_id_2 THEN
        v_id1 := p_expense_id_1;
        v_id2 := p_expense_id_2;
    ELSE
        v_id1 := p_expense_id_2;
        v_id2 := p_expense_id_1;
    END IF;

    -- Insertar o retornar existente (idempotente)
    INSERT INTO dismissed_expense_duplicates (user_id, expense_id_1, expense_id_2, dismissed_reason)
    VALUES (p_user_id, v_id1, v_id2, p_reason)
    ON CONFLICT (user_id, expense_id_1, expense_id_2) DO UPDATE
        SET dismissed_at = NOW(),
            dismissed_reason = p_reason
    RETURNING id INTO v_result_id;

    RETURN v_result_id;
END;
$$;

COMMENT ON FUNCTION dismiss_duplicate_pair IS 'Helper para insertar dismissals con IDs ordenados automáticamente';
