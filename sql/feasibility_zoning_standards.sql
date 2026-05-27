-- ============================================================================
-- FEASIBILITY: ZONING STANDARDS (San Diego development standards lookup)
-- ----------------------------------------------------------------------------
-- Backs the /feasibility/lookup + /feasibility/zoning-standards endpoints.
-- There is no machine-readable City of San Diego Chapter 13 (Section 131.04)
-- standards API, so the development standards per zone are seeded here and the
-- backend joins them to the live ZONE_NAME returned by the DSD zoning service.
--
-- `verified` = false means the row is a starting estimate and the FAR / height /
-- setback / coverage values must still be confirmed against the current code PDF
-- (https://docs.sandiego.gov/municode/municodechapter13/ch13art01division04.pdf)
-- before being treated as authoritative. The frontend lets the user override any
-- value, so an unverified or missing row never blocks an analysis.
--
-- Idempotent. Run on staging first, then prod (Supabase SQL editor). No downtime.
-- Path: C:\Users\germa\Desktop\NGM_API\sql\feasibility_zoning_standards.sql
-- ============================================================================

CREATE TABLE IF NOT EXISTS public.zoning_standards (
  id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  zone_code       text NOT NULL,
  jurisdiction    text NOT NULL DEFAULT 'San Diego',
  label           text,
  zone_type       text,                  -- 'commercial' | 'residential' | 'residential_sf'
  is_planned_district boolean NOT NULL DEFAULT false,
  far             numeric,               -- floor area ratio
  max_height      numeric,               -- feet
  coverage        numeric,               -- max lot coverage %
  set_front       numeric,               -- ft
  set_side        numeric,               -- ft
  set_rear        numeric,               -- ft
  density         numeric,               -- max dwelling units / acre
  parking_ratio   numeric,               -- spaces / unit
  affordable_min  numeric,               -- baseline affordable mandate %
  source_url      text,
  source_section  text,
  verified        boolean NOT NULL DEFAULT false,
  notes           text,
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now()
);

-- One row per zone code within a jurisdiction.
CREATE UNIQUE INDEX IF NOT EXISTS uq_zoning_standards_zone
  ON public.zoning_standards (jurisdiction, zone_code);

-- ----------------------------------------------------------------------------
-- Seed: common City of San Diego base zones (Chapter 13) + a planned district.
-- Density values (min lot area per dwelling unit -> du/acre) are the most
-- reliable figures; FAR/height/setbacks are starting estimates (verified=false).
-- ----------------------------------------------------------------------------
INSERT INTO public.zoning_standards
  (zone_code, label, zone_type, is_planned_district, far, max_height, coverage,
   set_front, set_side, set_rear, density, parking_ratio, affordable_min,
   source_url, source_section, verified, notes)
VALUES
  ('RS-1-7', 'RS-1-7 Residential Single Unit (5,000 sf/du)', 'residential_sf', false,
   0.5, 30, 50, 15, 4, 13, 8, 2.0, 0,
   'https://docs.sandiego.gov/municode/municodechapter13/ch13art01division04.pdf',
   'SDMC 131.0431 (Table 131-04G)', false,
   'Min lot 5,000 sf/du. FAR/height/setbacks need PDF confirmation.'),

  ('RS-1-1', 'RS-1-1 Residential Single Unit (40,000 sf/du)', 'residential_sf', false,
   0.45, 30, 40, 25, 10, 25, 1, 2.0, 0,
   'https://docs.sandiego.gov/municode/municodechapter13/ch13art01division04.pdf',
   'SDMC 131.0431 (Table 131-04G)', false,
   'Large-lot single unit. Values estimated.'),

  ('RM-1-1', 'RM-1-1 Residential Multiple Unit', 'residential', false,
   1.35, 30, 60, 10, 5, 13, 29, 1.25, 10,
   'https://docs.sandiego.gov/municode/municodechapter13/ch13art01division04.pdf',
   'SDMC 131.0431', false, 'Values estimated.'),

  ('RM-2-5', 'RM-2-5 Residential Multiple Unit', 'residential', false,
   1.8, 40, 65, 10, 5, 13, 44, 1.0, 10,
   'https://docs.sandiego.gov/municode/municodechapter13/ch13art01division04.pdf',
   'SDMC 131.0431', false, 'Values estimated.'),

  ('RM-3-9', 'RM-3-9 Residential Multiple Unit', 'residential', false,
   2.75, 60, 75, 10, 5, 10, 72, 1.0, 10,
   'https://docs.sandiego.gov/municode/municodechapter13/ch13art01division04.pdf',
   'SDMC 131.0431', false, 'Values estimated.'),

  ('RM-4-10', 'RM-4-10 Residential Multiple Unit', 'residential', false,
   3.0, 60, 75, 10, 5, 10, 109, 1.0, 10,
   'https://docs.sandiego.gov/municode/municodechapter13/ch13art01division04.pdf',
   'SDMC 131.0431', false, 'Values estimated.'),

  ('CC-3-5', 'CC-3-5 Commercial Community', 'commercial', false,
   3.0, 65, 80, 0, 0, 10, 90, 0.75, 10,
   'https://docs.sandiego.gov/municode/municodechapter13/ch13art01division05.pdf',
   'SDMC 131.0531', false, 'Values estimated.'),

  ('CN-1-3', 'CN-1-3 Commercial Neighborhood', 'commercial', false,
   2.0, 45, 80, 5, 0, 10, 60, 1.0, 10,
   'https://docs.sandiego.gov/municode/municodechapter13/ch13art01division05.pdf',
   'SDMC 131.0531', false, 'Values estimated.'),

  ('CV-1-1', 'CV-1-1 Commercial Visitor', 'commercial', false,
   2.0, 45, 80, 10, 5, 10, 0, 1.0, 0,
   'https://docs.sandiego.gov/municode/municodechapter13/ch13art01division05.pdf',
   'SDMC 131.0531', false, 'Values estimated.')
ON CONFLICT (jurisdiction, zone_code) DO NOTHING;

-- VERIFICATION ---------------------------------------------------------------
-- select zone_code, label, density, far, max_height, verified
--   from public.zoning_standards order by zone_code;
