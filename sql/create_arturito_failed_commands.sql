-- =====================================================
-- Tabla: arturito_failed_commands
-- =====================================================
-- Registra comandos copilot que fallaron para análisis
-- y mejora continua del sistema de reconocimiento

CREATE TABLE IF NOT EXISTS arturito_failed_commands (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,

    -- Comando original
    command_text TEXT NOT NULL,
    current_page TEXT,  -- expenses.html, pipeline.html, etc.

    -- Contexto de detección
    intent_detected TEXT,  -- COPILOT, UNKNOWN, etc.
    entities_detected JSONB,  -- Entities extraídas por NLU

    -- Razón del fallo
    error_reason TEXT,  -- 'no_exact_match', 'gpt_failed', 'low_confidence', etc.

    -- Intento de GPT
    gpt_attempted BOOLEAN DEFAULT FALSE,
    gpt_response JSONB,  -- Response de GPT si se intentó
    gpt_confidence DECIMAL(3,2),  -- Confianza de GPT (0.00-1.00)

    -- Metadata
    user_agent TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Índices para queries comunes
CREATE INDEX IF NOT EXISTS idx_failed_commands_user_id
    ON arturito_failed_commands(user_id);

CREATE INDEX IF NOT EXISTS idx_failed_commands_created_at
    ON arturito_failed_commands(created_at DESC);

CREATE INDEX IF NOT EXISTS idx_failed_commands_current_page
    ON arturito_failed_commands(current_page);

CREATE INDEX IF NOT EXISTS idx_failed_commands_error_reason
    ON arturito_failed_commands(error_reason);

-- Índice para búsqueda de texto en comandos
CREATE INDEX IF NOT EXISTS idx_failed_commands_command_text
    ON arturito_failed_commands USING gin(to_tsvector('spanish', command_text));

-- RLS Policies
ALTER TABLE arturito_failed_commands ENABLE ROW LEVEL SECURITY;

-- Usuarios pueden ver solo sus propios comandos fallidos
CREATE POLICY "Users can view their own failed commands"
    ON arturito_failed_commands
    FOR SELECT
    USING (auth.uid() = user_id);

-- Solo el sistema puede insertar (a través de service role)
-- Los usuarios no deberían poder insertar directamente
CREATE POLICY "Service can insert failed commands"
    ON arturito_failed_commands
    FOR INSERT
    WITH CHECK (true);  -- Se validará en el backend

-- Función auxiliar para obtener estadísticas agregadas
CREATE OR REPLACE FUNCTION get_failed_commands_stats(
    p_user_id UUID DEFAULT NULL,
    p_days_back INTEGER DEFAULT 30
)
RETURNS TABLE (
    total_failures BIGINT,
    unique_commands BIGINT,
    gpt_attempt_rate DECIMAL,
    top_pages JSONB,
    top_errors JSONB,
    most_common_commands JSONB
)
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
BEGIN
    RETURN QUERY
    WITH stats AS (
        SELECT
            COUNT(*) as total,
            COUNT(DISTINCT command_text) as unique_cmds,
            ROUND(100.0 * SUM(CASE WHEN gpt_attempted THEN 1 ELSE 0 END) / COUNT(*), 2) as gpt_rate
        FROM arturito_failed_commands
        WHERE (p_user_id IS NULL OR user_id = p_user_id)
          AND created_at >= NOW() - (p_days_back || ' days')::INTERVAL
    ),
    top_pages_cte AS (
        SELECT jsonb_agg(
            jsonb_build_object(
                'page', current_page,
                'count', cnt
            ) ORDER BY cnt DESC
        ) as pages
        FROM (
            SELECT current_page, COUNT(*) as cnt
            FROM arturito_failed_commands
            WHERE (p_user_id IS NULL OR user_id = p_user_id)
              AND created_at >= NOW() - (p_days_back || ' days')::INTERVAL
            GROUP BY current_page
            ORDER BY cnt DESC
            LIMIT 5
        ) t
    ),
    top_errors_cte AS (
        SELECT jsonb_agg(
            jsonb_build_object(
                'error', error_reason,
                'count', cnt
            ) ORDER BY cnt DESC
        ) as errors
        FROM (
            SELECT error_reason, COUNT(*) as cnt
            FROM arturito_failed_commands
            WHERE (p_user_id IS NULL OR user_id = p_user_id)
              AND created_at >= NOW() - (p_days_back || ' days')::INTERVAL
            GROUP BY error_reason
            ORDER BY cnt DESC
            LIMIT 5
        ) t
    ),
    top_commands_cte AS (
        SELECT jsonb_agg(
            jsonb_build_object(
                'command', command_text,
                'count', cnt,
                'page', current_page
            ) ORDER BY cnt DESC
        ) as commands
        FROM (
            SELECT command_text, current_page, COUNT(*) as cnt
            FROM arturito_failed_commands
            WHERE (p_user_id IS NULL OR user_id = p_user_id)
              AND created_at >= NOW() - (p_days_back || ' days')::INTERVAL
            GROUP BY command_text, current_page
            ORDER BY cnt DESC
            LIMIT 10
        ) t
    )
    SELECT
        s.total,
        s.unique_cmds,
        s.gpt_rate,
        COALESCE(p.pages, '[]'::jsonb),
        COALESCE(e.errors, '[]'::jsonb),
        COALESCE(c.commands, '[]'::jsonb)
    FROM stats s
    CROSS JOIN top_pages_cte p
    CROSS JOIN top_errors_cte e
    CROSS JOIN top_commands_cte c;
END;
$$;

-- Comentarios
COMMENT ON TABLE arturito_failed_commands IS 'Registro de comandos copilot que fallaron para análisis y mejora continua';
COMMENT ON COLUMN arturito_failed_commands.command_text IS 'Texto original del comando del usuario';
COMMENT ON COLUMN arturito_failed_commands.current_page IS 'Página donde estaba el usuario (expenses.html, etc.)';
COMMENT ON COLUMN arturito_failed_commands.error_reason IS 'Razón del fallo: no_exact_match, gpt_failed, low_confidence, no_copilot_support';
COMMENT ON COLUMN arturito_failed_commands.gpt_response IS 'Respuesta de GPT si se intentó interpretación';
