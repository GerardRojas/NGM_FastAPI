-- =============================================
-- Add status_reason column to expenses_manual_COGS
-- =============================================
-- Required by PATCH /expenses/{id} endpoint:
--   - status_reason is kept in data dict and stored in the expenses table
--   - Used by frontend for soft-delete strikethrough styling and audit trail
-- Without this column, bookkeeper edits that trigger auto-review crash with:
--   "cannot extract elements from a scalar" (PostgREST rejects unknown column)

ALTER TABLE public."expenses_manual_COGS"
ADD COLUMN IF NOT EXISTS status_reason text;

COMMENT ON COLUMN public."expenses_manual_COGS".status_reason IS 'Reason for status change (auto-review: field modification details, manual: user-provided reason)';
