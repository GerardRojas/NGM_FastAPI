-- ============================================================================
-- FEASIBILITY: REGULATORY RULESET (data-driven, updatable)
-- ----------------------------------------------------------------------------
-- Single-row config (config_key = 'main') holding the rules that drive the
-- feasibility yield engine AND the "Regulatory Basis" UI card: ADU state
-- by-right counts, the San Diego ADU Bonus Program (lot-size caps, SDA bonus,
-- excluded zones, coastal status), density-bonus tiers, AB 2011 floors, SB 9,
-- plus the citation/source list. Edit this row (or PUT /feasibility/regulations)
-- when the City or State change the rules - no code deploy needed.
--
-- If this table/row is absent the backend falls back to DEFAULT_REGULATIONS in
-- api/routers/feasibility.py, so running this seed is optional but recommended.
--
-- Idempotent. Run on staging first, then prod (Supabase SQL editor).
-- Path: C:\Users\germa\Desktop\NGM_API\sql\feasibility_regulations.sql
-- ============================================================================

CREATE TABLE IF NOT EXISTS public.feasibility_regulations (
  config_key  text PRIMARY KEY,
  rules       jsonb NOT NULL,
  updated_at  timestamptz NOT NULL DEFAULT now()
);

INSERT INTO public.feasibility_regulations (config_key, rules)
VALUES ('main', '{
  "version": "2026-05-27",
  "last_verified": "2026-05-27",
  "jurisdiction": "City of San Diego",
  "adu": {
    "state_byright_sf": {"adu": 1, "jadu": 1, "detached": 1, "detached_max_sf": 800},
    "state_byright_mf": {"conversion_pct": 0.25, "detached_max": 8},
    "bonus_program": {
      "enabled": true,
      "lot_caps": [
        {"max_lot_sf": 8000, "cap": 4},
        {"max_lot_sf": 10000, "cap": 5},
        {"max_lot_sf": null, "cap": 6}
      ],
      "outside_sda_bonus": 1,
      "affordability_term_years": 15,
      "excluded_zones": ["RS-1-1","RS-1-2","RS-1-3","RS-1-4","RS-1-8","RS-1-9","RS-1-10","RS-1-11"],
      "coastal_effective": false,
      "far_lot_area_cap_sf": 8000
    },
    "parking": {"required_per_bonus_outside_tpa": 1, "transit_waiver_mi": 0.5}
  },
  "density_bonus": {"tiers": [
    {"min_affordable": 15, "bonus": 50},
    {"min_affordable": 10, "bonus": 35},
    {"min_affordable": 5, "bonus": 20}
  ]},
  "ab2011": {"floor_density_base": 40, "floor_density_tpa": 80},
  "sb9": {"max_units": 4},
  "sources": [
    {"rule": "ADU / JADU (state by-right)", "citation": "Cal. Gov. Code 66310-66342 (66323)", "url": "https://leginfo.legislature.ca.gov/faces/codes_displaySection.xhtml?sectionNum=66323&lawCode=GOV", "effective": "2024-01-01", "note": "1 ADU + 1 JADU + 1 detached (<=800 sf) on a single-family lot."},
    {"rule": "San Diego ADU Bonus Program", "citation": "SDMC 141.0302", "url": "https://docs.sandiego.gov/municode/municodechapter14/ch14art01division03.pdf", "effective": "2025-08-22", "note": "Lot-size caps 4/5/6; 1 market bonus per affordable inside an SDA. Coastal zone pending LCP certification (~2026)."},
    {"rule": "ADU/JADU (City info)", "citation": "Information Bulletin 400", "url": "https://www.sandiego.gov/development-services/forms-publications/information-bulletins/400", "effective": "2026-01-01", "note": null},
    {"rule": "SB 9 (lot split + duplex)", "citation": "Cal. Gov. Code 65852.21 / 66411.7", "url": "https://leginfo.legislature.ca.gov/faces/billNavClient.xhtml?bill_id=202120220SB9", "effective": "2022-01-01", "note": null},
    {"rule": "Density Bonus + AB 1287", "citation": "Cal. Gov. Code 65915", "url": "https://codes.findlaw.com/ca/government-code/gov-sect-65915/", "effective": "2024-01-01", "note": null},
    {"rule": "AB 2011 (commercial corridors)", "citation": "Cal. Gov. Code 65912.100+", "url": "https://leginfo.legislature.ca.gov/faces/codes_displayexpandedbranch.xhtml?lawCode=GOV&division=1.&title=7.&part=&chapter=4.1.", "effective": "2023-07-01", "note": null},
    {"rule": "AB 2097 (parking near transit)", "citation": "Cal. Gov. Code 65863.2", "url": "https://leginfo.legislature.ca.gov/faces/billNavClient.xhtml?bill_id=202120220AB2097", "effective": "2023-01-01", "note": null},
    {"rule": "SB 35 / SB 423 (streamlining)", "citation": "Cal. Gov. Code 65913.4", "url": "https://leginfo.legislature.ca.gov/faces/billNavClient.xhtml?bill_id=202320240SB423", "effective": "2024-01-01", "note": "Requires the City to be behind its RHNA - verify HCD status."}
  ]
}'::jsonb)
ON CONFLICT (config_key) DO NOTHING;

-- VERIFICATION ---------------------------------------------------------------
-- select rules->>'version' as version, rules->'adu'->'bonus_program'->'lot_caps'
--   from public.feasibility_regulations where config_key = 'main';
