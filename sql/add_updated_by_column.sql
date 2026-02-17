-- =============================================
-- Add updated_by column to expenses_manual_COGS
-- =============================================
-- Required by trigger functions:
--   - log_category_correction() references NEW.updated_by
--   - log_labor_category_correction() references NEW.updated_by
-- Without this column, any account_id change crashes the triggers
-- with: record "new" has no field "updated_by"

ALTER TABLE public."expenses_manual_COGS"
ADD COLUMN IF NOT EXISTS updated_by uuid REFERENCES public.users(user_id);

COMMENT ON COLUMN public."expenses_manual_COGS".updated_by IS 'UUID of the user who last modified this expense. Set by the API on every PATCH/batch update.';
