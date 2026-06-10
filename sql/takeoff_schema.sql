-- =============================================================================
-- TAKEOFF — base schema (plans + pages + measurements) for the measurable plan
-- viewer used by the Estimator ("Fill from plan") and the standalone Takeoff page.
--
-- These tables back api/routers/takeoff.py. They were never created in this DB
-- (the feature shipped without a schema migration), so run THIS file — it
-- supersedes takeoff_plan_type_status.sql (plan_type + status are included here).
--
-- Notes derived from the router:
--   * takeoff_plans.id is TEXT — the frontend supplies it (e.g. "to-abc123").
--   * page calibration is stored as flat columns (cal_p1_x … cal_unit).
--   * measurement points are stored as a JSON string (text) — the router does
--     json.dumps on write / json.loads on read.
--   * pages/measurements cascade-delete with their plan.
--
-- Idempotent and additive. Run on STAGING first, verify, then PROD.
-- Path: C:\Users\germa\Desktop\NGM_API\sql\takeoff_schema.sql
-- =============================================================================

CREATE TABLE IF NOT EXISTS public.takeoff_plans (
    id          text PRIMARY KEY,                 -- frontend-supplied id
    filename    text NOT NULL,
    project_id  text,                             -- optional link to an NGM project
    plan_type   text,                             -- Floor Plan, Footings, Title 24…
    status      text NOT NULL DEFAULT 'draft',    -- draft | official
    created_by  text,
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.takeoff_plan_pages (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    plan_id       text NOT NULL REFERENCES public.takeoff_plans(id) ON DELETE CASCADE,
    page_number   integer NOT NULL DEFAULT 1,
    image_url     text,                           -- data URL or storage URL (rasterized page)
    image_width   integer,
    image_height  integer,
    thumbnail_url text,
    -- Manual calibration (two points + a known distance) → scale factor.
    cal_p1_x      double precision,
    cal_p1_y      double precision,
    cal_p2_x      double precision,
    cal_p2_y      double precision,
    cal_distance  double precision,
    cal_unit      text,
    created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.takeoff_measurements (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    plan_id     text NOT NULL REFERENCES public.takeoff_plans(id) ON DELETE CASCADE,
    page_number integer NOT NULL DEFAULT 1,
    type        text NOT NULL,                    -- line | area | count
    label       text DEFAULT '',
    points      text,                             -- JSON string of [{x,y}, …]
    value       double precision DEFAULT 0,
    unit        text DEFAULT 'ft',
    color       text,
    created_by  text,
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_takeoff_plans_project ON public.takeoff_plans (project_id);
CREATE INDEX IF NOT EXISTS idx_takeoff_plan_pages_plan ON public.takeoff_plan_pages (plan_id);
CREATE INDEX IF NOT EXISTS idx_takeoff_measurements_plan ON public.takeoff_measurements (plan_id, page_number);

-- VERIFICATION
-- select to_regclass('public.takeoff_plans'), to_regclass('public.takeoff_plan_pages'), to_regclass('public.takeoff_measurements');
-- =============================================================================
