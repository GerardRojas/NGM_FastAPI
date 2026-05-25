-- =============================================
-- Table: sheet_templates
-- Budget Sheet Manager — reusable export templates for the Estimator.
-- =============================================
--
-- A "sheet template" bundles branding (logo/header/footer/accent) with a
-- view_config that decides WHAT an exported sheet shows (line items, quantities,
-- material/labor breakdown, granularity, etc.). The same template drives the
-- PDF carátula, the Excel export, and the estimate -> budget conversion, so the
-- three stay consistent.
--
-- branding   jsonb : { companyName, logoUrl, showLogo, accentColor,
--                      headerText, footerText, companyInfo }
-- view_config jsonb: { showCover, projectFields[], lineGranularity,
--                      showLineItems, showQuantities, showUnitCosts,
--                      showSubtotals, showImages, breakdown,
--                      showOverheadBreakdown, showGrandTotal }
--
-- Idempotent. Run on staging, then prod.

CREATE TABLE IF NOT EXISTS sheet_templates (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name        TEXT NOT NULL,
    theme       TEXT NOT NULL DEFAULT 'classic',   -- 'classic' | 'modern'
    branding    JSONB NOT NULL DEFAULT '{}'::jsonb,
    view_config JSONB NOT NULL DEFAULT '{}'::jsonb,
    is_default  BOOLEAN NOT NULL DEFAULT false,
    is_preset   BOOLEAN NOT NULL DEFAULT false,     -- seeded by code; UI marks as built-in
    created_by  UUID,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_sheet_templates_default ON sheet_templates (is_default) WHERE is_default;
CREATE INDEX IF NOT EXISTS idx_sheet_templates_name ON sheet_templates (lower(name));

-- Stable IDs so the seed is idempotent and the frontend can reference presets.
-- 1) Client Proposal (Classic) — client-facing, summary by category, no qty/unit
--    cost, overhead hidden as one number. Default.
-- 2) Detailed Estimate — full breakdown, quantities, unit costs, material/labor.
-- 3) Internal Budget — per-category lines, geared to feed the Budgets module.

INSERT INTO sheet_templates (id, name, theme, branding, view_config, is_default, is_preset)
VALUES
(
  '00000000-0000-0000-0000-0000000000a1',
  'Client Proposal (Classic)',
  'classic',
  jsonb_build_object(
    'companyName', 'NGM Management',
    'logoUrl', '',
    'showLogo', true,
    'accentColor', '#3dca8b',
    'headerText', 'PROPOSAL',
    'footerText', 'Thank you for the opportunity to work with you.',
    'companyInfo', 'NGM Management',
    'headerLayout', 'stacked',
    'showCompanyInfo', false
  ),
  jsonb_build_object(
    'showCover', true,
    'projectFields', jsonb_build_array('client_name','address','city_state_zip','date','project_type'),
    'computedMetrics', jsonb_build_array(),
    'lineGranularity', 'category',
    'showLineItems', false,
    'showQuantities', false,
    'showUnitCosts', false,
    'showSubtotals', true,
    'showImages', false,
    'breakdown', 'none',
    'showOverheadBreakdown', false,
    'showGrandTotal', true
  ),
  true,
  true
),
(
  '00000000-0000-0000-0000-0000000000a2',
  'Detailed Estimate',
  'modern',
  jsonb_build_object(
    'companyName', 'NGM Management',
    'logoUrl', '',
    'showLogo', true,
    'accentColor', '#2f6df6',
    'headerText', 'DETAILED ESTIMATE',
    'footerText', '',
    'companyInfo', 'NGM Management',
    'headerLayout', 'stacked',
    'showCompanyInfo', false
  ),
  jsonb_build_object(
    'showCover', true,
    'projectFields', jsonb_build_array('client_name','address','city_state_zip','date','heated_sqft','bedrooms','bathrooms'),
    'computedMetrics', jsonb_build_array(),
    'lineGranularity', 'item',
    'showLineItems', true,
    'showQuantities', true,
    'showUnitCosts', true,
    'showSubtotals', true,
    'showImages', false,
    'breakdown', 'material_labor',
    'showOverheadBreakdown', true,
    'showGrandTotal', true
  ),
  false,
  true
),
(
  '00000000-0000-0000-0000-0000000000a3',
  'Internal Budget',
  'classic',
  jsonb_build_object(
    'companyName', 'NGM Management',
    'logoUrl', '',
    'showLogo', false,
    'accentColor', '#6b7280',
    'headerText', 'BUDGET',
    'footerText', '',
    'companyInfo', '',
    'headerLayout', 'stacked',
    'showCompanyInfo', false
  ),
  jsonb_build_object(
    'showCover', false,
    'projectFields', jsonb_build_array('client_name','address'),
    'computedMetrics', jsonb_build_array(),
    'lineGranularity', 'category',
    'showLineItems', false,
    'showQuantities', false,
    'showUnitCosts', false,
    'showSubtotals', true,
    'showImages', false,
    'breakdown', 'none',
    'showOverheadBreakdown', true,
    'showGrandTotal', true
  ),
  false,
  true
),
(
  '00000000-0000-0000-0000-0000000000a4',
  'Contractor Proposal (Split Header)',
  'classic',
  jsonb_build_object(
    'companyName', 'NGM Management',
    'logoUrl', '',
    'showLogo', true,
    'accentColor', '#1f2430',
    'headerText', 'PROPOSAL',
    'footerText', '',
    'companyInfo', E'(000) 000-0000\n100 Main St, City ST 00000\nLicense #0000000',
    'headerLayout', 'split',
    'showCompanyInfo', true
  ),
  jsonb_build_object(
    'showCover', true,
    'projectFields', jsonb_build_array('address','units','project_type','estimated_work_time','heated_sqft'),
    'computedMetrics', jsonb_build_array('contract_price','price_per_unit','price_per_sqft'),
    'lineGranularity', 'subcategory',
    'showLineItems', true,
    'showQuantities', true,
    'showUnitCosts', true,
    'showSubtotals', true,
    'showImages', false,
    'breakdown', 'none',
    'showOverheadBreakdown', false,
    'showGrandTotal', true
  ),
  false,
  true
)
ON CONFLICT (id) DO NOTHING;

-- VERIFICACIÓN ------------------------------------------------
select name, theme, is_default, is_preset,
       view_config->>'lineGranularity' as granularity,
       view_config->>'breakdown' as breakdown
from sheet_templates
order by is_preset desc, name;
