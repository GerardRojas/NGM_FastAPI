-- =============================================================================
-- Contact inbox — ONE-SHOT setup (table + sidebar menu_item + role permissions)
-- =============================================================================
-- Run this single file in the Supabase SQL editor (STAGING first, then PROD).
-- It is the combination of create_contact_messages.sql + contact_menu_item.sql,
-- in the correct order. IDEMPOTENT: safe to re-run.
--
-- Backs the landing "Let's talk" / "Contact us" modal -> api/routers/contact.py
-- -> the hub "Leads" inbox (/contact-messages, Admin group). Kept SEPARATE from
-- beta_access_requests (the "Requests" inbox: Request-demo / early-access leads
-- in the IT group) so the two inboxes never mix.
--
-- After running, users must refresh their session for the cached sidebar menu
-- to pick up the new "Leads" item.
-- Path: C:\Users\germa\Desktop\NGM_API\sql\setup_contact_messages.sql
-- =============================================================================


-- 1) TABLE -------------------------------------------------------------------
-- Lifecycle status: new -> read -> replied -> archived (default 'new').
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

-- Newest-first listing is the default query in the inbox.
CREATE INDEX IF NOT EXISTS idx_contact_messages_submitted_at
    ON public.contact_messages (submitted_at DESC);

-- Status filter chips in the inbox.
CREATE INDEX IF NOT EXISTS idx_contact_messages_status
    ON public.contact_messages (status);


-- 2) PER-ROLE PERMISSIONS (admin roles only) ---------------------------------
-- Admin tooling, like Leads Management: can_view granted only to CEO/COO.
-- Widen later from Roles Management if other roles should triage contacts.
INSERT INTO role_permissions (rol_id, module_key, module_name, module_url, can_view, can_edit, can_delete)
SELECT r.rol_id, 'contact-messages', 'Leads', 'contact-messages',
    CASE WHEN r.rol_name IN ('CEO', 'COO') THEN true ELSE false END,  -- can_view
    CASE WHEN r.rol_name IN ('CEO', 'COO') THEN true ELSE false END,  -- can_edit (status + notes)
    CASE WHEN r.rol_name IN ('CEO', 'COO') THEN true ELSE false END   -- can_delete
FROM rols r
WHERE NOT EXISTS (
    SELECT 1 FROM role_permissions rp
    WHERE rp.rol_id = r.rol_id AND rp.module_key = 'contact-messages'
);


-- 3) MENU ITEM (Admin group) -------------------------------------------------
-- Shown in the sidebar as "Leads" (contact requests from the landing
-- "Let's talk" CTA). DO UPDATE so a prior run that placed it under "General"
-- as "Contact" gets relabeled/regrouped on re-run.
INSERT INTO menu_items (slug, item_name, icon_type, icon_text, category_id, "order")
SELECT 'contact-messages', 'Leads', 'material', 'forward_to_inbox',
       (SELECT id FROM public.menu_categories WHERE name = 'Admin' LIMIT 1),
       3
ON CONFLICT (slug) DO UPDATE SET
  item_name = EXCLUDED.item_name,
  category_id = EXCLUDED.category_id,
  "order" = EXCLUDED."order";


-- 4) LINK role_permissions -> menu_item --------------------------------------
-- The React sidebar is built from menu_items JOINed to role_permissions via
-- role_permissions.menu_item_id, so the grant needs this link to show up.
UPDATE role_permissions rp
SET menu_item_id = mi.id
FROM menu_items mi
WHERE mi.slug = 'contact-messages'
  AND rp.module_key = 'contact-messages'
  AND (rp.menu_item_id IS NULL OR rp.menu_item_id <> mi.id);

-- 5) RELABEL prior runs ------------------------------------------------------
-- Keep the Roles UI label in sync if an earlier run stored it as 'Contact'.
UPDATE role_permissions
SET module_name = 'Leads'
WHERE module_key = 'contact-messages' AND module_name <> 'Leads';


-- =============================================================================
-- VERIFICATION (optional)
-- =============================================================================
-- select mi.slug, mi.item_name, mc.name as category, mi."order"
--   from public.menu_items mi
--   left join public.menu_categories mc on mc.id = mi.category_id
--  where mi.slug = 'contact-messages';
--
-- select r.rol_name, rp.can_view, rp.can_edit, rp.can_delete, mi.slug
--   from public.role_permissions rp
--   join public.rols r on r.rol_id = rp.rol_id
--   left join public.menu_items mi on mi.id = rp.menu_item_id
--  where rp.module_key = 'contact-messages'
--  order by r.rol_name;
-- =============================================================================
