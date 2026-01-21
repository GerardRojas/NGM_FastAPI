-- =============================================
-- Table: qbo_tokens
-- QuickBooks Online OAuth2 Tokens Storage
-- =============================================
--
-- IMPORTANTE:
-- - Esta tabla almacena tokens de OAuth2 para QuickBooks Online
-- - Los tokens se renuevan automáticamente cuando expiran
-- - Realm ID es el identificador único de la compañía en QBO
-- - El service role DEBE tener acceso completo para refresh automático

-- La tabla ya existe (creada por el usuario), pero estas policies son necesarias:

-- =============================================
-- RLS Policies (Row Level Security)
-- =============================================

-- Asegurarse que RLS está habilitado
ALTER TABLE qbo_tokens ENABLE ROW LEVEL SECURITY;

-- IMPORTANTE: Eliminar políticas existentes que puedan bloquear acceso
DROP POLICY IF EXISTS "Service role has full access to qbo_tokens" ON qbo_tokens;
DROP POLICY IF EXISTS "Users can view qbo_tokens" ON qbo_tokens;

-- Policy: Service role tiene acceso completo (CRÍTICO para el backend)
CREATE POLICY "Service role has full access to qbo_tokens" ON qbo_tokens
    FOR ALL
    TO service_role
    USING (true)
    WITH CHECK (true);

-- Policy: Authenticated users solo pueden ver (opcional, para frontend)
CREATE POLICY "Authenticated users can view qbo_tokens" ON qbo_tokens
    FOR SELECT
    TO authenticated
    USING (true);

-- =============================================
-- Índices (si no existen)
-- =============================================

CREATE INDEX IF NOT EXISTS idx_qbo_tokens_realm ON qbo_tokens(realm_id);
CREATE INDEX IF NOT EXISTS idx_qbo_tokens_expires ON qbo_tokens(access_token_expires_at);

-- =============================================
-- Trigger para updated_at
-- =============================================

CREATE OR REPLACE FUNCTION update_qbo_tokens_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trigger_qbo_tokens_updated_at ON qbo_tokens;
CREATE TRIGGER trigger_qbo_tokens_updated_at
    BEFORE UPDATE ON qbo_tokens
    FOR EACH ROW
    EXECUTE FUNCTION update_qbo_tokens_updated_at();

-- =============================================
-- Comments
-- =============================================

COMMENT ON TABLE qbo_tokens IS 'QuickBooks Online OAuth2 tokens. One row per connected company.';
COMMENT ON COLUMN qbo_tokens.realm_id IS 'QuickBooks company identifier (unique per company)';
COMMENT ON COLUMN qbo_tokens.access_token IS 'OAuth2 access token (expires in ~1 hour)';
COMMENT ON COLUMN qbo_tokens.refresh_token IS 'OAuth2 refresh token (expires in ~100 days)';
COMMENT ON COLUMN qbo_tokens.access_token_expires_at IS 'When access token expires';
COMMENT ON COLUMN qbo_tokens.refresh_token_expires_at IS 'When refresh token expires (need to reauthorize)';
