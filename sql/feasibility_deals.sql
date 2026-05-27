-- ============================================================================
-- FEASIBILITY: SAVED DEALS (persist a feasibility study run)
-- ----------------------------------------------------------------------------
-- Backs the /feasibility/deals CRUD endpoints. One row = one saved analysis
-- (parcel + zoning + constraints + yield envelope + pro forma + decision).
-- The full analysis lives in the `data` jsonb blob; a handful of columns are
-- denormalized for fast listing without parsing the blob.
--
-- Scoped per user via user_id. Idempotent. Run on staging first, then prod.
-- Path: C:\Users\germa\Desktop\NGM_API\sql\feasibility_deals.sql
-- ============================================================================

CREATE TABLE IF NOT EXISTS public.feasibility_deals (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id       text NOT NULL,
  name          text,
  address       text,
  apn           text,
  zoning        text,
  decision      text,                 -- go | hold | nogo | dead | null
  total_uses    numeric,              -- total development cost (for listing)
  irr           numeric,              -- levered IRR (for listing)
  max_units     integer,              -- state-law max-yield (for listing)
  data          jsonb NOT NULL,       -- full analysis snapshot
  created_at    timestamptz NOT NULL DEFAULT now(),
  updated_at    timestamptz NOT NULL DEFAULT now()
);

-- List a user's deals newest-first.
CREATE INDEX IF NOT EXISTS idx_feasibility_deals_user
  ON public.feasibility_deals (user_id, created_at DESC);

-- VERIFICATION ---------------------------------------------------------------
-- select id, name, decision, total_uses, irr, created_at
--   from public.feasibility_deals order by created_at desc limit 20;
