-- ============================================================================
-- FIX & FLIP: SAVED DEALS (persist a fix-and-flip calculator run)
-- ----------------------------------------------------------------------------
-- Backs the /fix-flip/deals CRUD endpoints. One row = one saved calculation
-- (inputs + computed outputs snapshot). The full snapshot lives in the `data`
-- jsonb blob; a handful of columns are denormalized for fast listing.
--
-- Scoped per user via user_id. Name is unique per user (so "Save as new" needs a
-- distinct name, mirroring the original SQLite app). Idempotent. Run on staging
-- first, then prod (Supabase SQL editor). No downtime.
-- Path: C:\Users\germa\Desktop\NGM_API\sql\fix_flip_deals.sql
-- ============================================================================

CREATE TABLE IF NOT EXISTS public.fix_flip_deals (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id        text NOT NULL,
  name           text NOT NULL,
  notes          text DEFAULT '',
  scenario       text,                 -- pessimistic | base | optimistic
  net_profit_hm  numeric,              -- net profit w/ hard money (for listing)
  roi_hm         numeric,              -- ROI w/ hard money (for listing)
  data           jsonb NOT NULL,       -- { inputs, outputs } snapshot
  created_at     timestamptz NOT NULL DEFAULT now(),
  updated_at     timestamptz NOT NULL DEFAULT now()
);

-- One deal name per user (enables the "name already in use" 409).
CREATE UNIQUE INDEX IF NOT EXISTS uq_fix_flip_deals_user_name
  ON public.fix_flip_deals (user_id, name);

-- List a user's deals newest-first.
CREATE INDEX IF NOT EXISTS idx_fix_flip_deals_user
  ON public.fix_flip_deals (user_id, updated_at DESC);

-- VERIFICATION ---------------------------------------------------------------
-- select id, name, scenario, net_profit_hm, roi_hm, updated_at
--   from public.fix_flip_deals order by updated_at desc limit 20;
