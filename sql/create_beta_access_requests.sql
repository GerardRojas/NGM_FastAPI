-- ============================================================
-- beta_access_requests — landing-page leads + internal management.
--
-- Backs the public POST /beta/request-access (landing "request early
-- access" form) and the internal Leads Management page
-- (GET/PATCH/DELETE /beta/requests). Idempotent: creates the table if
-- missing and adds the admin columns (status / notes / updated_at) if
-- the table was created manually before this migration existed.
-- ============================================================

create table if not exists beta_access_requests (
  id              uuid primary key default gen_random_uuid(),
  name            text not null,
  email           text not null,
  company         text,
  phone           text,
  role            text,
  industry        text,
  active_projects text,
  plan_interest   text,
  billing_period  text,
  team_size       text,
  message         text,
  source          text default 'landing-beta',
  lang            text,
  status          text default 'pending',
  notes           text,
  requested_at    timestamptz default now(),
  updated_at      timestamptz
);

-- Backfill columns on a pre-existing table (no-op if already present).
alter table beta_access_requests add column if not exists status       text default 'pending';
alter table beta_access_requests add column if not exists notes        text;
alter table beta_access_requests add column if not exists updated_at   timestamptz;
alter table beta_access_requests add column if not exists requested_at timestamptz default now();

-- Common access patterns: filter by status, sort by recency.
create index if not exists idx_beta_access_requests_status       on beta_access_requests (status);
create index if not exists idx_beta_access_requests_requested_at on beta_access_requests (requested_at desc);
