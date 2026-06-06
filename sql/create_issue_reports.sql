-- ============================================================
-- issue_reports + issue_attachments — internal Issues / Feedback board.
--
-- Hub users raise an issue or suggestion (with optional screenshot
-- attachments); admins review them on the Issues page and flag each as
-- resolved or not. Idempotent. Run on staging, then prod.
--
-- Pairs with the NGM_API router api/routers/issues.py (prefix /issues) and
-- the React route /issues.
-- ============================================================

-- 1) TICKETS --------------------------------------------------
create table if not exists issue_reports (
  id              uuid primary key default gen_random_uuid(),
  type            text not null default 'issue',     -- 'issue' | 'suggestion'
  title           text not null,
  description     text,
  status          text not null default 'open',      -- 'open' | 'resolved'
  created_by      uuid,                               -- users.user_id (best-effort)
  created_by_name text,
  created_by_email text,
  resolved_at     timestamptz,
  created_at      timestamptz default now(),
  updated_at      timestamptz default now()
);

-- Backfill columns if the table pre-existed.
alter table issue_reports add column if not exists type             text not null default 'issue';
alter table issue_reports add column if not exists status           text not null default 'open';
alter table issue_reports add column if not exists created_by       uuid;
alter table issue_reports add column if not exists created_by_name  text;
alter table issue_reports add column if not exists created_by_email text;
alter table issue_reports add column if not exists resolved_at      timestamptz;
alter table issue_reports add column if not exists updated_at       timestamptz default now();

create index if not exists idx_issue_reports_status     on issue_reports (status);
create index if not exists idx_issue_reports_created_at on issue_reports (created_at desc);
create index if not exists idx_issue_reports_created_by on issue_reports (created_by);

-- 2) ATTACHMENTS (screenshots / files) -----------------------
create table if not exists issue_attachments (
  id           uuid primary key default gen_random_uuid(),
  issue_id     uuid not null references issue_reports(id) on delete cascade,
  file_name    text,
  bucket_path  text,            -- path inside the 'issue-attachments' storage bucket
  file_url     text,            -- public URL for display/download
  mime_type    text,
  size_bytes   bigint default 0,
  uploaded_by  uuid,
  created_at   timestamptz default now()
);

create index if not exists idx_issue_attachments_issue_id on issue_attachments (issue_id);

-- 3) STORAGE BUCKET ------------------------------------------
-- Public bucket so screenshots render with a simple URL; object paths are
-- uuid-based and unguessable. Internal tool, mirrors the existing 'vault' bucket.
insert into storage.buckets (id, name, public)
values ('issue-attachments', 'issue-attachments', true)
on conflict (id) do nothing;
