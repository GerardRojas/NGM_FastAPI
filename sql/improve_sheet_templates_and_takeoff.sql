-- =============================================================================
-- SHEET TEMPLATES — polish the shared presets + add the "Take Off Form" preset.
--
-- What this does:
--   1. Refreshes the 4 BASE presets (company_id IS NULL, ids a1–a4) with a more
--      professional default look: split header, heading color, proposal content
--      blocks (scope / payment / terms), a contract-price metric, and an
--      acceptance signature where it makes sense.
--   2. Adds two BASE Take Off presets (NO prices, view_config.sheetType='takeoff'):
--      "Design Take Off Form" (…a5, takeoffScope='design', image-forward) and
--      "Material Take Off Form" (…a6, takeoffScope='material', all materials+qty).
--   3. Provisions any missing preset (incl. both take offs) to every existing
--      company — additive, idempotent, skips by name so per-company copies and
--      their logos/edits are NEVER overwritten.
--
-- IMPORTANT: This intentionally updates ONLY the base rows (company_id IS NULL).
-- Existing companies keep their current copies untouched. Rolling the polished
-- look out to already-provisioned companies is a separate, opt-in step — see
-- TAKEOFF_PLAN.md (it must not clobber a workspace that customized its template).
--
-- Idempotent and additive. Run on STAGING first, verify, then PROD.
-- Pairs with the frontend Take Off Form support (sheetType in SheetViewConfig).
-- Path: C:\Users\germa\Desktop\NGM_API\sql\improve_sheet_templates_and_takeoff.sql
-- =============================================================================

-- 1) ── Polish the base presets ──────────────────────────────────────────────

-- a1 · Client Proposal (Classic) — client-facing, elegant, summary by category.
UPDATE public.sheet_templates SET
  theme = 'classic',
  branding = branding
    || jsonb_build_object(
         'headerLayout', 'split',
         'headingColor', branding->>'accentColor',
         'showCompanyInfo', true,
         'validUntil', '30 days',
         'footerText', 'Thank you for the opportunity to work with you.',
         'scopeText', 'Scope of work as described in the breakdown below. Includes all labor, materials, and equipment required for a complete installation unless otherwise noted.',
         'paymentTerms', 'Deposit due upon acceptance, progress payments per completed phase, final balance upon completion.',
         'termsText', 'Pricing valid for 30 days from issue. Any work not listed is excluded and would be quoted as a separate change order.',
         'showSignature', true
       )
WHERE id = '00000000-0000-0000-0000-0000000000a1' AND company_id IS NULL;

UPDATE public.sheet_templates SET
  view_config = view_config || jsonb_build_object('computedMetrics', jsonb_build_array('contract_price'))
WHERE id = '00000000-0000-0000-0000-0000000000a1' AND company_id IS NULL;

-- a2 · Detailed Estimate — modern, full breakdown. Add a heading color + footer.
UPDATE public.sheet_templates SET
  branding = branding
    || jsonb_build_object(
         'headingColor', branding->>'accentColor',
         'footerText', 'Detailed estimate — figures subject to final measurement and selections.'
       )
WHERE id = '00000000-0000-0000-0000-0000000000a2' AND company_id IS NULL;

-- a4 · Contractor Proposal (Split Header) — add proposal content + signature.
UPDATE public.sheet_templates SET
  branding = branding
    || jsonb_build_object(
         'headingColor', branding->>'accentColor',
         'validUntil', '30 days',
         'scopeText', 'Scope of work as detailed in the budget breakdown below.',
         'paymentTerms', 'Payment schedule: deposit on signing, progress billing per phase, balance on completion.',
         'termsText', 'A contingency allowance may apply for conditions discovered after the site visit. Items not listed are excluded.',
         'showSignature', true
       )
WHERE id = '00000000-0000-0000-0000-0000000000a4' AND company_id IS NULL;

-- (a3 Internal Budget stays lean on purpose — no cover, no proposal blocks.)


-- 2) ── New base presets: Design + Material Take Off Forms (a5, a6) ───────────
INSERT INTO public.sheet_templates (id, name, theme, branding, view_config, is_default, is_preset)
VALUES
(
  '00000000-0000-0000-0000-0000000000a5',
  'Design Take Off Form',
  'modern',
  jsonb_build_object(
    'companyName', 'NGM Management', 'logoUrl', '', 'showLogo', true,
    'accentColor', '#2f6df6', 'headerText', 'DESIGN TAKE OFF', 'footerText', '',
    'companyInfo', 'NGM Management', 'headerLayout', 'stacked',
    'showCompanyInfo', false, 'headingColor', '#2f6df6'
  ),
  jsonb_build_object(
    'sheetType', 'takeoff',
    'takeoffScope', 'design',
    'designElementsOnly', false,
    'showCover', true,
    'projectFields', jsonb_build_array('client_name','address','project_type','date'),
    'computedMetrics', jsonb_build_array(),
    'lineGranularity', 'item',
    'showLineItems', true, 'showQuantities', true, 'showUnitCosts', false,
    'showSubtotals', false, 'showImages', true, 'breakdown', 'none',
    'showOverheadBreakdown', false, 'showGrandTotal', false
  ),
  false, true
),
(
  '00000000-0000-0000-0000-0000000000a6',
  'Material Take Off Form',
  'modern',
  jsonb_build_object(
    'companyName', 'NGM Management', 'logoUrl', '', 'showLogo', true,
    'accentColor', '#2f6df6', 'headerText', 'MATERIAL TAKE OFF', 'footerText', '',
    'companyInfo', 'NGM Management', 'headerLayout', 'stacked',
    'showCompanyInfo', false, 'headingColor', '#2f6df6'
  ),
  jsonb_build_object(
    'sheetType', 'takeoff',
    'takeoffScope', 'material',
    'designElementsOnly', false,
    'showCover', true,
    'projectFields', jsonb_build_array('client_name','address','project_type','date'),
    'computedMetrics', jsonb_build_array(),
    'lineGranularity', 'item',
    'showLineItems', true, 'showQuantities', true, 'showUnitCosts', false,
    'showSubtotals', false, 'showImages', false, 'breakdown', 'none',
    'showOverheadBreakdown', false, 'showGrandTotal', false
  ),
  false, true
)
ON CONFLICT (id) DO NOTHING;


-- 3) ── Provision any missing preset to existing companies (additive) ─────────
DO $$
BEGIN
    IF to_regclass('public.sheet_templates') IS NULL OR to_regclass('public.companies') IS NULL THEN
        RAISE NOTICE 'Skipping provision: sheet_templates or companies table missing.';
        RETURN;
    END IF;

    INSERT INTO public.sheet_templates (name, theme, branding, view_config, is_default, is_preset, company_id)
    SELECT
        t.name,
        t.theme,
        jsonb_set(
            jsonb_set(coalesce(t.branding, '{}'::jsonb), '{companyName}', to_jsonb(c.name)),
            '{companyInfo}', to_jsonb(c.name)
        ),
        t.view_config,
        t.is_default,
        true,
        c.id
    FROM public.sheet_templates t
    CROSS JOIN public.companies c
    WHERE t.is_preset = true
      AND t.company_id IS NULL
      AND NOT EXISTS (
          SELECT 1 FROM public.sheet_templates x
          WHERE x.company_id = c.id AND x.name = t.name
      );
END $$;


-- VERIFICATION ----------------------------------------------------------------
-- -- Base presets and their type:
-- select name, view_config->>'sheetType' as sheet_type, view_config->>'lineGranularity' as gran
--   from public.sheet_templates where company_id is null order by name;
--
-- -- Both Take Off forms reached every company:
-- select c.name, count(st.id) as takeoff_count from public.companies c
--   left join public.sheet_templates st on st.company_id = c.id
--        and st.name in ('Design Take Off Form','Material Take Off Form')
--   group by c.name order by c.name;
-- =============================================================================
