-- ============================================================
-- issue_reports — resolution feedback loop + human-friendly ticket numbers.
--
-- Two related upgrades to the Issues / Feedback board, run as one migration:
--
-- 1) FEEDBACK LOOP — when the team marks a ticket resolved, Art asks the person
--    who raised it to confirm the fix. Their answer is captured so we can measure
--    how often a "resolved" ticket actually satisfied the reporter (vs reopened).
--      resolved_by / resolved_by_name  — who on the team marked it resolved
--      feedback_status                 — NULL | 'pending' | 'satisfied' | 'unsatisfied'
--      feedback_comment                — the reporter's optional note
--      feedback_at                     — when the reporter answered
--      feedback_requested_at           — when resolution triggered the ask
--      reopen_count                    — times the reporter sent it back to 'open'
--
-- 2) TICKET NUMBERS — IDs are UUIDs (not user-facing). ticket_number increments
--    per ticket so people (and Art) can refer to "ticket #123". Existing rows are
--    backfilled in creation order; new tickets get the next value via a sequence.
--
-- Idempotent. Run on staging, then prod. Pairs with api/routers/issues.py.
-- ============================================================

-- ── 1) FEEDBACK LOOP ─────────────────────────────────────────
alter table issue_reports add column if not exists resolved_by           uuid;
alter table issue_reports add column if not exists resolved_by_name      text;
alter table issue_reports add column if not exists feedback_status       text;   -- NULL | pending | satisfied | unsatisfied
alter table issue_reports add column if not exists feedback_comment      text;
alter table issue_reports add column if not exists feedback_at           timestamptz;
alter table issue_reports add column if not exists feedback_requested_at timestamptz;
alter table issue_reports add column if not exists reopen_count          int not null default 0;

-- The assistant polls "pending feedback requests for me", so index the hot path.
create index if not exists idx_issue_reports_pending_feedback
  on issue_reports (created_by)
  where feedback_status = 'pending';

-- ── 2) TICKET NUMBERS ────────────────────────────────────────
alter table issue_reports add column if not exists ticket_number int;

-- Backfill rows that don't have one yet, in creation order, continuing past any
-- numbers already assigned (so re-running never collides).
with ordered as (
  select id, row_number() over (order by created_at, id) as rn
  from issue_reports
  where ticket_number is null
)
update issue_reports t
set ticket_number = o.rn + coalesce((select max(ticket_number) from issue_reports), 0)
from ordered o
where t.id = o.id;

-- Sequence drives new inserts.
create sequence if not exists issue_reports_ticket_number_seq
  owned by issue_reports.ticket_number;

alter table issue_reports
  alter column ticket_number set default nextval('issue_reports_ticket_number_seq');

-- Advance the sequence so the next ticket is max+1 (is_called=false => the next
-- nextval() returns exactly this value).
select setval(
  'issue_reports_ticket_number_seq',
  coalesce((select max(ticket_number) from issue_reports), 0) + 1,
  false
);

create unique index if not exists idx_issue_reports_ticket_number
  on issue_reports (ticket_number);
