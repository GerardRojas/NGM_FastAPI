-- ============================================================================
-- RESIDENTIAL DEVELOPMENT: SAVED DEALS (persist an RD calculator run)
-- ----------------------------------------------------------------------------
-- Backs the /rd/deals CRUD endpoints. Mirrors fix_flip_deals exactly; the only
-- difference is the denormalized listing columns (RD headline metrics instead of
-- fix-and-flip ones). One row = one saved calculation (inputs + outputs snapshot
-- in the `data` jsonb blob). Scoped per user; name unique per user (409 on dup).
-- Workspace-scoped via company_id (NULL = shared/all). Idempotent. Run on staging
-- then prod (Supabase SQL editor). No downtime.
-- Path: C:\Users\germa\Desktop\NGM_API\sql\rd_deals.sql
-- ============================================================================

CREATE TABLE IF NOT EXISTS public.rd_deals (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id       text NOT NULL,
  name          text NOT NULL,
  notes         text DEFAULT '',
  mode          text,                  -- rent | sale
  irr_levered   numeric,               -- levered IRR (for listing)
  net_profit    numeric,               -- levered net profit (for listing)
  data          jsonb NOT NULL,        -- { inputs, outputs } snapshot
  company_id    uuid,                  -- owning workspace (NULL = shared/all)
  created_at    timestamptz NOT NULL DEFAULT now(),
  updated_at    timestamptz NOT NULL DEFAULT now()
);

-- One deal name per user (enables the "name already in use" 409).
CREATE UNIQUE INDEX IF NOT EXISTS uq_rd_deals_user_name
  ON public.rd_deals (user_id, name);

-- List a user's deals newest-first.
CREATE INDEX IF NOT EXISTS idx_rd_deals_user
  ON public.rd_deals (user_id, updated_at DESC);

-- VERIFICATION ---------------------------------------------------------------
-- select id, name, mode, irr_levered, net_profit, updated_at
--   from public.rd_deals order by updated_at desc limit 20;
