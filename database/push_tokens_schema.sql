-- ============================================================================
-- NGM Hub - Push Notification Tokens Schema
-- ============================================================================
-- Stores FCM (Firebase Cloud Messaging) tokens for each user/device
-- Enables sending push notifications when users are @mentioned

-- ============================================================================
-- Push Tokens Table
-- ============================================================================

CREATE TABLE IF NOT EXISTS push_tokens (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    fcm_token TEXT NOT NULL,
    device_info TEXT,  -- User agent or device identifier
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),

    -- Each user can have multiple devices, but same token shouldn't be duplicated
    UNIQUE(fcm_token)
);

-- Index for fast lookup by user_id (when sending notifications)
CREATE INDEX IF NOT EXISTS idx_push_tokens_user_id ON push_tokens(user_id);

-- Index for active tokens only
CREATE INDEX IF NOT EXISTS idx_push_tokens_active ON push_tokens(user_id, is_active) WHERE is_active = TRUE;

-- ============================================================================
-- Trigger to update updated_at
-- ============================================================================

CREATE OR REPLACE FUNCTION update_push_tokens_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trigger_push_tokens_updated_at ON push_tokens;
CREATE TRIGGER trigger_push_tokens_updated_at
    BEFORE UPDATE ON push_tokens
    FOR EACH ROW
    EXECUTE FUNCTION update_push_tokens_updated_at();

-- ============================================================================
-- RLS Policies (Row Level Security)
-- ============================================================================

ALTER TABLE push_tokens ENABLE ROW LEVEL SECURITY;

-- Users can only see/manage their own tokens
CREATE POLICY push_tokens_select_own ON push_tokens
    FOR SELECT USING (user_id = auth.uid());

CREATE POLICY push_tokens_insert_own ON push_tokens
    FOR INSERT WITH CHECK (user_id = auth.uid());

CREATE POLICY push_tokens_update_own ON push_tokens
    FOR UPDATE USING (user_id = auth.uid());

CREATE POLICY push_tokens_delete_own ON push_tokens
    FOR DELETE USING (user_id = auth.uid());

-- Service role can access all tokens (for sending notifications)
CREATE POLICY push_tokens_service_all ON push_tokens
    FOR ALL USING (auth.role() = 'service_role');
