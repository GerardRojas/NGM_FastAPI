-- =============================================================================
-- SEED — 'send_to_review' automation. This is a BOARD-LEVEL automation (not a
-- backend duty): when a task's status is moved to "Awaiting Approval" on the
-- Pipeline board (inline status badge or drag), the frontend runs the full
-- Send to Review workflow (capture deliverables + create the reviewer task)
-- instead of a bare status change.
--
-- This row only registers the on/off switch so the automation shows in the
-- Responsibilities catalog / the Operations automations page, where an admin can
-- disable it. When disabled, moving a task to Awaiting Approval just sets the
-- status. Reviewers are resolved by the send-to-review backend logic (the task's
-- managers / project manager / CEO-COO), so no assignee/department config here.
--
-- Enabled by default to preserve current behavior. Event-driven (not scheduled).
-- Idempotent. Run on STAGING first, verify, then PROD.
-- Path: C:\Users\germa\Desktop\NGM_API\sql\seed_send_to_review_automation.sql
-- =============================================================================

INSERT INTO public.automation_settings
    (automation_type, display_name, is_enabled, default_priority, config)
VALUES
    ('send_to_review', 'Send to Review (status change)', true, 3, '{}'::jsonb)
ON CONFLICT (automation_type) DO NOTHING;

-- VERIFICATION:
-- select automation_type, display_name, is_enabled
--   from public.automation_settings where automation_type = 'send_to_review';
-- =============================================================================
