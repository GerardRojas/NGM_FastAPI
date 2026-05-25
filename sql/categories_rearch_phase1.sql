-- =============================================================================
-- CATEGORIES RE-ARCH — PHASE 1: parity overlay (ADDITIVE, NON-BREAKING)
-- =============================================================================
-- Introduces the new Category -> Subcategory hierarchy + a cost_type dimension
-- and a 1:1 overlay (account_category_map) that translates each existing flat
-- account into (subcategory_id, cost_type). NOTHING here touches `accounts`,
-- `expenses_manual_COGS`, estimates, or any read path — `account_id` stays the
-- source of truth. The new structure is derived from this overlay and validated
-- by the parity report (build_category_map.py) before anything switches over.
--
-- qbo_cost_codes is created here only so Categories can FK to it; it is OWNED and
-- synced by the future Accounting page. Seed it there (or by hand) — left empty
-- on purpose (categories.cost_code_id is nullable until assigned in curation).
--
-- Idempotent. Run on staging first, then prod. Plan: features/accounts/CATEGORIES_REARCH_PLAN.md
-- =============================================================================

-- 1. Shared classification dimension ------------------------------------------
-- material / labor / external_service for COGS, plus change_order and
-- other_expenses for change orders and non-COGS / overhead spend.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'cost_type') THEN
        CREATE TYPE cost_type AS ENUM
            ('material', 'labor', 'external_service', 'change_order', 'other_expenses');
    END IF;
END$$;

-- For a DB where cost_type already exists with the original 3 values, add the
-- two new ones. ALTER TYPE ... ADD VALUE cannot run inside a transaction block,
-- so these stay top-level (idempotent via IF NOT EXISTS).
ALTER TYPE cost_type ADD VALUE IF NOT EXISTS 'change_order';
ALTER TYPE cost_type ADD VALUE IF NOT EXISTS 'other_expenses';

-- 2. QBO cost codes (the 11) — managed/synced by the Accounting page ----------
CREATE TABLE IF NOT EXISTS public.qbo_cost_codes (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    code             TEXT UNIQUE NOT NULL,
    name             TEXT NOT NULL,
    qbo_class_ref_id TEXT,
    is_cogs          BOOLEAN NOT NULL DEFAULT TRUE,
    sort_order       INTEGER NOT NULL DEFAULT 0,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 3. Categories (level 1) -----------------------------------------------------
CREATE TABLE IF NOT EXISTS public.categories (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name         TEXT NOT NULL,
    -- The accounting<->costs link, shown as a dropdown column in the Categories
    -- table. Nullable until assigned in curation.
    cost_code_id UUID REFERENCES public.qbo_cost_codes(id) ON DELETE SET NULL,
    is_active    BOOLEAN NOT NULL DEFAULT TRUE,
    sort_order   INTEGER NOT NULL DEFAULT 0,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (name)
);

-- 4. Subcategories (level 2, type-agnostic) -----------------------------------
CREATE TABLE IF NOT EXISTS public.subcategories (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    category_id UUID NOT NULL REFERENCES public.categories(id) ON DELETE RESTRICT,
    name        TEXT NOT NULL,
    is_active   BOOLEAN NOT NULL DEFAULT TRUE,
    sort_order  INTEGER NOT NULL DEFAULT 0,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (category_id, name)
);

-- 5. Parity overlay: existing flat account -> (subcategory, cost_type) --------
-- 1:1 with `accounts`. `reviewed` = a human confirmed the auto-derived mapping.
CREATE TABLE IF NOT EXISTS public.account_category_map (
    account_id     UUID PRIMARY KEY REFERENCES public.accounts(account_id) ON DELETE CASCADE,
    subcategory_id UUID NOT NULL REFERENCES public.subcategories(id) ON DELETE RESTRICT,
    cost_type      cost_type NOT NULL,
    reviewed       BOOLEAN NOT NULL DEFAULT FALSE,
    source         TEXT NOT NULL DEFAULT 'auto',   -- 'auto' | 'manual'
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_subcategories_category ON public.subcategories(category_id);
CREATE INDEX IF NOT EXISTS idx_acct_map_subcategory   ON public.account_category_map(subcategory_id);

-- 6. RLS: backend uses the service role; keep these tables off the anon key ----
ALTER TABLE public.qbo_cost_codes       ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.categories           ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.subcategories        ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.account_category_map ENABLE ROW LEVEL SECURITY;

DO $$
DECLARE t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY['qbo_cost_codes','categories','subcategories','account_category_map']
    LOOP
        EXECUTE format('DROP POLICY IF EXISTS "Service role full access" ON public.%I', t);
        EXECUTE format(
            'CREATE POLICY "Service role full access" ON public.%I FOR ALL '
            'USING (auth.role() = ''service_role'') WITH CHECK (auth.role() = ''service_role'')', t);
    END LOOP;
END$$;

-- VERIFICATION ----------------------------------------------------------------
-- select count(*) from public.accounts;                       -- universe to map
-- select count(*) from public.account_category_map;           -- == above after Phase 1
-- select c.name category, s.name subcategory
--   from public.subcategories s join public.categories c on c.id = s.category_id
--   order by 1,2;
