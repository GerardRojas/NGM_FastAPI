-- ================================================================
-- Andrew Agent: Database setup
-- ================================================================
-- Andrew is the Receipt Processing AI agent.
-- Receives, itemizes, catalogs and registers expenses.
-- Posts receipt flow messages to project receipts channels.
-- ================================================================

-- 1. Andrew bot user
-- Uses a well-known UUID so backend and frontend can reference it.
-- The password is a unique dummy bcrypt hash -- bot can never login.
INSERT INTO users (user_id, user_name, avatar_color, password_hash)
VALUES (
  '00000000-0000-0000-0000-000000000003',
  'Andrew',
  35,
  '$2b$12$AndrewNoLoginAndrewNoLogAN3.3.3.3.3.3.3.3.3.3.3.3.3.3'
)
ON CONFLICT (user_id) DO UPDATE SET
  user_name = EXCLUDED.user_name,
  avatar_color = EXCLUDED.avatar_color;
