-- ================================================================
-- Daneel Agent: Database setup
-- ================================================================
-- Daneel is the Budget Monitor AI agent.
-- Posts budget alerts to project accounting channels.
-- ================================================================

-- 1. Daneel bot user
-- Uses a well-known UUID so backend and frontend can reference it.
-- The password is a unique dummy bcrypt hash -- bot can never login.
INSERT INTO users (user_id, user_name, avatar_color, password_hash)
VALUES (
  '00000000-0000-0000-0000-000000000002',
  'Daneel',
  210,
  '$2b$12$DaneelNoLoginDaneelNoLogDN2.2.2.2.2.2.2.2.2.2.2.2.2.2'
)
ON CONFLICT (user_id) DO UPDATE SET
  user_name = EXCLUDED.user_name,
  avatar_color = EXCLUDED.avatar_color;
