-- =============================================================================
-- Intake inboxes — ONE-SHOT setup for the 3 landing/hub intake flows.
-- Run this single file in the Supabase SQL editor (STAGING first, then PROD).
-- IDEMPOTENT: safe to re-run; safe on a fresh DB or one already partly deployed.
-- After running, users must refresh their session so the cached sidebar menu
-- picks up the labels.
-- Path: C:\Users\germa\Desktop\NGM_API\sql\setup_intake_inboxes.sql
--
-- The 3 flows (kept SEPARATE — different tables, lifecycles, never mixed):
--
--   1) "Leads"   (sidebar group ADMIN) -- landing "Let's talk" / Contact modal.
--        POST /contact  -> contact_messages       -> hub /contact-messages
--        status: new -> read -> replied -> archived            (CEO/COO only)
--
--   2) "Requests" (sidebar group IT)   -- landing "Request demo" / beta form.
--        POST /beta/request-access -> beta_access_requests -> hub /leads-management
--        status: pending -> contacted -> qualified -> converted/rejected (CEO/COO)
--
--   3) "Issues / Feedback" (group IT)  -- in-hub reports (Art floating toggle).
--        POST /issues -> issue_reports (+ issue_attachments) -> hub /issues
--        type issue|suggestion, status open->resolved   (all view; CEO/COO edit)
--
-- NAMING CAVEAT: display labels are intentionally inverted vs internal slugs --
-- route /leads-management shows as "Requests", /contact-messages shows as
-- "Leads". Slugs/module_keys stay stable so endpoints & permissions never break;
-- only menu_items.item_name + role_permissions.module_name carry the label.
-- =============================================================================


-- =============================================================================
-- 0) CATEGORIES (sidebar groups) ----------------------------------------------
-- =============================================================================
INSERT INTO menu_categories (name, "order") VALUES ('Admin', 6) ON CONFLICT (name) DO NOTHING;
INSERT INTO menu_categories (name, "order") VALUES ('IT', 7)    ON CONFLICT (name) DO NOTHING;


-- =============================================================================
-- 1) TABLES (IF NOT EXISTS — no-op when already deployed) ----------------------
-- =============================================================================

-- 1a) contact_messages — "Leads" inbox.
CREATE TABLE IF NOT EXISTS public.contact_messages (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name         text NOT NULL,
    email        text NOT NULL,
    message      text,
    source       text DEFAULT 'landing-contact',
    lang         text,
    status       text NOT NULL DEFAULT 'new',
    notes        text,
    submitted_at timestamptz NOT NULL DEFAULT now(),
    updated_at   timestamptz
);
CREATE INDEX IF NOT EXISTS idx_contact_messages_submitted_at ON public.contact_messages (submitted_at DESC);
CREATE INDEX IF NOT EXISTS idx_contact_messages_status       ON public.contact_messages (status);

-- 1b) beta_access_requests — "Requests" inbox.
CREATE TABLE IF NOT EXISTS public.beta_access_requests (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name            text NOT NULL,
    email           text NOT NULL,
    company         text,
    phone           text,
    role            text,
    industry        text,
    active_projects text,
    plan_interest   text,
    billing_period  text,
    team_size       text,
    message         text,
    source          text DEFAULT 'landing-beta',
    lang            text,
    status          text DEFAULT 'pending',
    notes           text,
    requested_at    timestamptz DEFAULT now(),
    updated_at      timestamptz
);
-- Backfill admin columns if the table pre-existed without them.
ALTER TABLE public.beta_access_requests ADD COLUMN IF NOT EXISTS status       text DEFAULT 'pending';
ALTER TABLE public.beta_access_requests ADD COLUMN IF NOT EXISTS notes        text;
ALTER TABLE public.beta_access_requests ADD COLUMN IF NOT EXISTS updated_at   timestamptz;
ALTER TABLE public.beta_access_requests ADD COLUMN IF NOT EXISTS requested_at timestamptz DEFAULT now();
CREATE INDEX IF NOT EXISTS idx_beta_access_requests_status       ON public.beta_access_requests (status);
CREATE INDEX IF NOT EXISTS idx_beta_access_requests_requested_at ON public.beta_access_requests (requested_at DESC);

-- 1c) issue_reports + issue_attachments — "Issues / Feedback" board.
CREATE TABLE IF NOT EXISTS public.issue_reports (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    type             text NOT NULL DEFAULT 'issue',   -- 'issue' | 'suggestion'
    title            text NOT NULL,
    description      text,
    status           text NOT NULL DEFAULT 'open',    -- 'open' | 'resolved'
    created_by       uuid,
    created_by_name  text,
    created_by_email text,
    resolved_at      timestamptz,
    created_at       timestamptz DEFAULT now(),
    updated_at       timestamptz DEFAULT now()
);
ALTER TABLE public.issue_reports ADD COLUMN IF NOT EXISTS type             text NOT NULL DEFAULT 'issue';
ALTER TABLE public.issue_reports ADD COLUMN IF NOT EXISTS status           text NOT NULL DEFAULT 'open';
ALTER TABLE public.issue_reports ADD COLUMN IF NOT EXISTS created_by       uuid;
ALTER TABLE public.issue_reports ADD COLUMN IF NOT EXISTS created_by_name  text;
ALTER TABLE public.issue_reports ADD COLUMN IF NOT EXISTS created_by_email text;
ALTER TABLE public.issue_reports ADD COLUMN IF NOT EXISTS resolved_at      timestamptz;
ALTER TABLE public.issue_reports ADD COLUMN IF NOT EXISTS updated_at       timestamptz DEFAULT now();
CREATE INDEX IF NOT EXISTS idx_issue_reports_status     ON public.issue_reports (status);
CREATE INDEX IF NOT EXISTS idx_issue_reports_created_at ON public.issue_reports (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_issue_reports_created_by ON public.issue_reports (created_by);

CREATE TABLE IF NOT EXISTS public.issue_attachments (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    issue_id    uuid NOT NULL REFERENCES public.issue_reports(id) ON DELETE CASCADE,
    file_name   text,
    bucket_path text,
    file_url    text,
    mime_type   text,
    size_bytes  bigint DEFAULT 0,
    uploaded_by uuid,
    created_at  timestamptz DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_issue_attachments_issue_id ON public.issue_attachments (issue_id);

-- Public storage bucket for issue screenshots/files.
INSERT INTO storage.buckets (id, name, public)
VALUES ('issue-attachments', 'issue-attachments', true)
ON CONFLICT (id) DO NOTHING;


-- =============================================================================
-- 2) PER-ROLE PERMISSIONS ------------------------------------------------------
-- WHERE NOT EXISTS so existing grants/customizations are preserved on re-run.
-- =============================================================================

-- 2a) Leads (contact-messages) — CEO/COO only.
INSERT INTO role_permissions (rol_id, module_key, module_name, module_url, can_view, can_edit, can_delete)
SELECT r.rol_id, 'contact-messages', 'Leads', 'contact-messages',
    CASE WHEN r.rol_name IN ('CEO', 'COO') THEN true ELSE false END,
    CASE WHEN r.rol_name IN ('CEO', 'COO') THEN true ELSE false END,
    CASE WHEN r.rol_name IN ('CEO', 'COO') THEN true ELSE false END
FROM rols r
WHERE NOT EXISTS (
    SELECT 1 FROM role_permissions rp
    WHERE rp.rol_id = r.rol_id AND rp.module_key = 'contact-messages'
);

-- 2b) Requests (leads_management) — CEO/COO only.
INSERT INTO role_permissions (rol_id, module_key, module_name, module_url, can_view, can_edit, can_delete)
SELECT r.rol_id, 'leads_management', 'Requests', 'leads-management',
    CASE WHEN r.rol_name IN ('CEO', 'COO') THEN true ELSE false END,
    CASE WHEN r.rol_name IN ('CEO', 'COO') THEN true ELSE false END,
    CASE WHEN r.rol_name IN ('CEO', 'COO') THEN true ELSE false END
FROM rols r
WHERE NOT EXISTS (
    SELECT 1 FROM role_permissions rp
    WHERE rp.rol_id = r.rol_id AND rp.module_key = 'leads_management'
);

-- 2c) Issues / Feedback (issues) — everyone can view+submit; CEO/COO edit+delete.
INSERT INTO role_permissions (rol_id, module_key, module_name, module_url, can_view, can_edit, can_delete)
SELECT r.rol_id, 'issues', 'Issues / Feedback', 'issues',
    true,
    CASE WHEN r.rol_name IN ('CEO', 'COO') THEN true ELSE false END,
    CASE WHEN r.rol_name IN ('CEO', 'COO') THEN true ELSE false END
FROM rols r
WHERE NOT EXISTS (
    SELECT 1 FROM role_permissions rp
    WHERE rp.rol_id = r.rol_id AND rp.module_key = 'issues'
);


-- =============================================================================
-- 3) MENU ITEMS ---------------------------------------------------------------
-- DO UPDATE so prior runs (e.g. "Contact"/General, "Beta Leads") get relabeled.
-- =============================================================================

-- 3a) "Leads" -> Admin group.
INSERT INTO menu_items (slug, item_name, icon_type, icon_text, category_id, "order")
SELECT 'contact-messages', 'Leads', 'material', 'forward_to_inbox',
       (SELECT id FROM public.menu_categories WHERE name = 'Admin' LIMIT 1), 3
ON CONFLICT (slug) DO UPDATE SET
  item_name = EXCLUDED.item_name, icon_type = EXCLUDED.icon_type,
  icon_text = EXCLUDED.icon_text, category_id = EXCLUDED.category_id, "order" = EXCLUDED."order";

-- 3b) "Requests" -> IT group.
INSERT INTO menu_items (slug, item_name, icon_type, icon_text, category_id, "order")
SELECT 'leads-management', 'Requests', 'material', 'inbox',
       (SELECT id FROM public.menu_categories WHERE name = 'IT' LIMIT 1), 1
ON CONFLICT (slug) DO UPDATE SET
  item_name = EXCLUDED.item_name, icon_type = EXCLUDED.icon_type,
  icon_text = EXCLUDED.icon_text, category_id = EXCLUDED.category_id, "order" = EXCLUDED."order";

-- 3c) "Issues / Feedback" -> IT group.
INSERT INTO menu_items (slug, item_name, icon_type, icon_text, category_id, "order")
SELECT 'issues', 'Issues / Feedback', 'material', 'bug_report',
       (SELECT id FROM public.menu_categories WHERE name = 'IT' LIMIT 1), 2
ON CONFLICT (slug) DO UPDATE SET
  item_name = EXCLUDED.item_name, icon_type = EXCLUDED.icon_type,
  icon_text = EXCLUDED.icon_text, category_id = EXCLUDED.category_id, "order" = EXCLUDED."order";


-- =============================================================================
-- 4) LINK role_permissions -> menu_item (the sidebar JOIN needs this) ----------
-- =============================================================================
UPDATE role_permissions rp
SET menu_item_id = mi.id
FROM menu_items mi
WHERE mi.slug = 'contact-messages' AND rp.module_key = 'contact-messages'
  AND (rp.menu_item_id IS NULL OR rp.menu_item_id <> mi.id);

UPDATE role_permissions rp
SET menu_item_id = mi.id
FROM menu_items mi
WHERE mi.slug = 'leads-management' AND rp.module_key = 'leads_management'
  AND (rp.menu_item_id IS NULL OR rp.menu_item_id <> mi.id);

UPDATE role_permissions rp
SET menu_item_id = mi.id
FROM menu_items mi
WHERE mi.slug = 'issues' AND rp.module_key = 'issues'
  AND (rp.menu_item_id IS NULL OR rp.menu_item_id <> mi.id);


-- =============================================================================
-- 5) RELABEL prior runs (keep the Roles UI labels in sync) ---------------------
-- =============================================================================
UPDATE role_permissions SET module_name = 'Leads'             WHERE module_key = 'contact-messages' AND module_name <> 'Leads';
UPDATE role_permissions SET module_name = 'Requests'          WHERE module_key = 'leads_management'  AND module_name <> 'Requests';
UPDATE role_permissions SET module_name = 'Issues / Feedback' WHERE module_key = 'issues'            AND module_name <> 'Issues / Feedback';


-- =============================================================================
-- VERIFICATION (optional) -----------------------------------------------------
-- =============================================================================
-- select mi.slug, mi.item_name, mc.name as category, mi."order"
--   from public.menu_items mi
--   left join public.menu_categories mc on mc.id = mi.category_id
--  where mi.slug in ('contact-messages','leads-management','issues')
--  order by mc."order", mi."order";
--
-- select r.rol_name, rp.module_key, rp.module_name, rp.can_view, rp.can_edit, rp.can_delete, mi.slug
--   from public.role_permissions rp
--   join public.rols r on r.rol_id = rp.rol_id
--   left join public.menu_items mi on mi.id = rp.menu_item_id
--  where rp.module_key in ('contact-messages','leads_management','issues')
--  order by rp.module_key, r.rol_name;
-- =============================================================================
