-- ============================================================
-- NGM HUB - Estimates index + branches
-- estimate.ngm files live in Storage (source of truth for full data).
-- This table mirrors searchable metadata and powers the branch system
-- (variants of an estimate, git-like).
-- ============================================================

create or replace function set_updated_at()
returns trigger as $$
begin
  new.updated_at = now();
  return new;
end;
$$ language plpgsql;

create table if not exists estimates (
  id            text primary key,                -- matches Storage folder name
  project_name  text not null,
  project_type  text,

  -- Cost snapshot of latest save (full breakdown lives in Storage)
  subtotal        numeric,
  overhead_amount numeric,
  total           numeric,

  -- Branching (variants)
  branch_of     text references estimates(id) on delete set null,  -- direct parent
  branch_name   text default 'main',
  root_id       text,                            -- original root estimate of the chain

  archived      boolean not null default false,
  metadata      jsonb not null default '{}'::jsonb,

  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now()
);

create index if not exists idx_estimates_branch_of on estimates(branch_of);
create index if not exists idx_estimates_root_id on estimates(root_id);
create index if not exists idx_estimates_archived on estimates(archived);

drop trigger if exists trg_estimates_updated on estimates;
create trigger trg_estimates_updated before update on estimates
  for each row execute function set_updated_at();
