-- =============================================================================
-- workspace_links — anonymous, link-only access to a curated workspace
-- =============================================================================
-- The third audience type of NGM Connect (after client accounts and external
-- users): "anyone with the link". A staff member generates a signed link that
-- opens a curated, READ-ONLY workspace for one project — no account, no login.
--
-- Discipline mirrors invoice_links.py: a JWT (signed with JWT_SECRET, carries an
-- exp) is stored here in full; the public read endpoint (/public/workspace)
-- decodes the JWT (signature + expiry) AND re-checks this row (status + expiry),
-- so a link can be revoked server-side at any time. The row also carries the
-- curated module set — the public plane resolves scope ONLY from this row, never
-- from request params, and reuses the same portal.py builders (default-deny:
-- only portal_shares content shows). Client-only modules (messages, invoices)
-- are never served on the link plane — they need an identity.
--
--   token        -> the signed JWT embedded in the share URL (?token=)
--   project_id   -> the single project this link exposes
--   modules      -> curated module bag {overview, photos, plans, timeline,
--                   documents, deals, estimates}: bool (messages/invoices ignored)
--   label        -> optional human note ("For investor Jane")
--   status       -> active | revoked
--   expires_at   -> hard expiry (also enforced by the JWT exp)
--   view_count   -> best-effort analytics; viewed_at = last open
--   company_id   -> owning workspace (org scoping), optional
--
-- Idempotent. Run on staging, then prod (Supabase SQL editor).
-- Path: C:\Users\germa\Desktop\NGM_API\sql\workspace_links.sql
-- =============================================================================

create table if not exists workspace_links (
  id          uuid primary key default gen_random_uuid(),
  token       text not null unique,
  project_id  uuid not null,
  modules     jsonb not null default '{}'::jsonb,
  label       text,
  status      text not null default 'active',   -- active | revoked
  created_by  uuid,
  company_id  uuid,
  expires_at  timestamptz,
  created_at  timestamptz not null default now(),
  viewed_at   timestamptz,
  view_count  integer not null default 0
);

-- Hot path: the public endpoint looks up an active link by its token.
create index if not exists idx_workspace_links_token
  on workspace_links (token)
  where status = 'active';

-- Team dashboard lists a project's links.
create index if not exists idx_workspace_links_project
  on workspace_links (project_id);
