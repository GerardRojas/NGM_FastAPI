-- ============================================================
-- ai_usage — per-call AI/LLM token + cost ledger.
--
-- One row per OpenAI call (mini/heavy via gpt_client, plus the Art
-- Assistants run). Powers the IT > "AI Usage" page. Fire-and-forget
-- inserts from api/services/ai_usage.py; never blocks the AI call.
-- Idempotent. Run on staging, then prod.
--
-- cost_usd is ESTIMATED from a static price table (PRICING in
-- api/services/ai_usage.py). Reconcile against the real OpenAI invoice.
-- ============================================================

create table if not exists public.ai_usage (
  id            uuid primary key default gen_random_uuid(),
  created_at    timestamptz not null default now(),
  feature       text not null default 'unknown',   -- art | receipt_ocr | categorization | andrew | adu | unknown
  model         text not null,                     -- gpt-5-mini | gpt-5.2 | gpt-4o-mini
  input_tokens  integer not null default 0,
  output_tokens integer not null default 0,
  total_tokens  integer not null default 0,
  cost_usd      numeric(12, 6) not null default 0,
  latency_ms    integer,
  success       boolean not null default true,
  company_id    uuid,                              -- best-effort attribution (set for Art)
  user_id       uuid,
  source        text
);

create index if not exists idx_ai_usage_created_at on public.ai_usage (created_at desc);
create index if not exists idx_ai_usage_feature    on public.ai_usage (feature);
create index if not exists idx_ai_usage_model      on public.ai_usage (model);
create index if not exists idx_ai_usage_company    on public.ai_usage (company_id);
