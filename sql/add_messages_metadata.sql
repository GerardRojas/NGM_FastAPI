-- ================================================================
-- Messages: Add metadata JSONB column + service role policy
-- ================================================================
-- Required for:
--   1. Arturito bot messages (receipt status, action buttons, flow state)
--   2. Receipt messages (pending_receipt_id, receipt_status tags)
--   3. Any future message-level metadata
--
-- Also adds a service_role policy so the backend (bot_messenger,
-- messages router) can insert/update messages via the service key.
-- ================================================================

-- 1. Add metadata JSONB column
ALTER TABLE messages ADD COLUMN IF NOT EXISTS metadata JSONB DEFAULT '{}';

-- 2. Service role full access policy (backend uses service_role key)
--    Without this, bot inserts could be blocked by RLS.
DROP POLICY IF EXISTS "Service role full access" ON messages;
CREATE POLICY "Service role full access" ON messages
  FOR ALL
  USING (auth.role() = 'service_role')
  WITH CHECK (auth.role() = 'service_role');

-- Same for message_attachments (backend inserts attachments after message creation)
DROP POLICY IF EXISTS "Service role full access" ON message_attachments;
CREATE POLICY "Service role full access" ON message_attachments
  FOR ALL
  USING (auth.role() = 'service_role')
  WITH CHECK (auth.role() = 'service_role');

-- 3. Arturito bot user
--    Uses DO UPDATE to ensure it exists even if password_hash is NOT NULL.
--    The password is a dummy bcrypt hash -- bot can never login.
INSERT INTO users (user_id, user_name, avatar_color, password_hash)
VALUES (
  '00000000-0000-0000-0000-000000000001',
  'Arturito',
  145,
  '$2b$12$BotNoLoginBotNoLoginBotNO1.1.1.1.1.1.1.1.1.1.1.1.1.1'
)
ON CONFLICT (user_id) DO UPDATE SET
  user_name = EXCLUDED.user_name,
  avatar_color = EXCLUDED.avatar_color;
