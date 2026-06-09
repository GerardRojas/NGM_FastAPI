-- =============================================================================
-- DESIGN PACKAGES — slots (reuse material_classes) + packages + package items.
--
-- A "design package" (e.g. Standard / Elite) is a set of products, one per SLOT
-- (WC, Bath Sink, Floor Tile…). Slots reuse the existing material_classes table
-- (materials.class_id = the slot a product fills). Switching a package on an
-- estimate swaps each design line's product to the package's product for that
-- slot (done client-side over the .ngm). See estimator-database/DESIGN_PACKAGES_PLAN.md.
--
-- Idempotent and additive. Run on STAGING first, verify, then PROD.
-- Path: C:\Users\germa\Desktop\NGM_API\sql\design_packages.sql
-- =============================================================================

-- 1) ── Seed the slot taxonomy into material_classes (skip existing by name) ───
INSERT INTO public.material_classes (name, sort_order, is_active)
SELECT s.name, s.ord, true
FROM (VALUES
    ('WC', 10),
    ('Bath Sink', 20),
    ('Kitchen Sink', 30),
    ('Bathtub', 40),
    ('Shower', 50),
    ('Shower Glass / Door', 60),
    ('Bath Faucet', 70),
    ('Kitchen Faucet', 80),
    ('Bath Accessories', 90),
    ('Floor Tile', 100),
    ('Wall Tile', 110),
    ('Flooring (Vinyl / Wood)', 120),
    ('Kitchen Cabinets', 130),
    ('Vanity Cabinets', 140),
    ('Kitchen Countertop', 150),
    ('Bath Countertop', 160),
    ('Interior Paint', 170),
    ('Exterior Paint', 180),
    ('Interior Doors', 190),
    ('Door Hardware', 200),
    ('Light Fixtures', 210),
    ('Range / Stove', 220),
    ('Microwave', 230),
    ('Dishwasher', 240),
    ('Refrigerator', 250),
    ('Washer / Dryer', 260),
    ('Water Heater', 270),
    ('HVAC Unit', 280),
    ('Windows', 290)
) AS s(name, ord)
WHERE NOT EXISTS (
    SELECT 1 FROM public.material_classes c WHERE lower(c.name) = lower(s.name)
);


-- 2) ── Packages + items tables ───────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.design_packages (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name        text NOT NULL,
    company_id  text,                          -- owning workspace (null = shared)
    is_default  boolean NOT NULL DEFAULT false,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.design_package_items (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    package_id  uuid NOT NULL REFERENCES public.design_packages(id) ON DELETE CASCADE,
    material_id text NOT NULL,                 -- materials."ID" (slot via material.class_id)
    created_at  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (package_id, material_id)
);

CREATE INDEX IF NOT EXISTS idx_design_packages_company ON public.design_packages (company_id);
CREATE INDEX IF NOT EXISTS idx_design_package_items_package ON public.design_package_items (package_id);


-- =============================================================================
-- VERIFICATION (optional)
-- -----------------------------------------------------------------------------
-- select name, sort_order from public.material_classes order by sort_order, name;
-- select to_regclass('public.design_packages'), to_regclass('public.design_package_items');
-- =============================================================================
