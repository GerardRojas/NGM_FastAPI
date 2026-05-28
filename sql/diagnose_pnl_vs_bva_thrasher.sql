-- ============================================================================
-- DIAGNOSTIC: why P&L total != Budget vs Actuals total for "251 Thrasher Way"
-- ----------------------------------------------------------------------------
-- Root cause: the on-screen React reports and the backend PDF/Andrew reports use
-- DIFFERENT definitions of "authorized expense":
--
--   ON-SCREEN (React isAuthorized): status='auth' if status set, else auth_status
--   BACKEND   (fetch_expenses):     auth_status=true AND status <> 'review'
--                                   (.neq also drops rows whose status IS NULL)
--
-- This script computes BOTH totals and lists the exact rows that diverge.
-- Read-only. Run in the Supabase SQL editor.
-- Path: C:\Users\germa\Desktop\NGM_API\sql\diagnose_pnl_vs_bva_thrasher.sql
-- NOTE: amount column is "Amount" (case-sensitive). project = projects.project_id.
-- ============================================================================

WITH proj AS (
    SELECT project_id, project_name
    FROM projects
    WHERE project_name ILIKE '%thrasher%' OR project_name ILIKE '%trasher%'
),
exp AS (
    SELECT e.*,
           COALESCE(e."Amount", 0)::numeric AS amt,
           -- ON-SCREEN rule (React): both P&L and BVA pages use this
           (CASE
              WHEN e.status IS NOT NULL AND e.status <> ''
                  THEN e.status = 'auth'
              ELSE e.auth_status IS TRUE
            END) AS counts_onscreen,
           -- BACKEND rule (PDF / Andrew): NULL status is excluded by .neq
           (e.auth_status IS TRUE AND e.status IS NOT NULL AND e.status <> 'review') AS counts_pdf
    FROM "expenses_manual_COGS" e
    JOIN proj p ON p.project_id = e.project
)

-- 1) The two grand totals side by side ---------------------------------------
SELECT 'TOTALS' AS section,
       (SELECT project_name FROM proj LIMIT 1)                              AS project,
       round(SUM(amt) FILTER (WHERE counts_onscreen), 2)                    AS total_onscreen_bva_pnl,
       round(SUM(amt) FILTER (WHERE counts_pdf), 2)                         AS total_pdf_pnl,
       round(SUM(amt) FILTER (WHERE counts_onscreen)
           - SUM(amt) FILTER (WHERE counts_pdf), 2)                         AS difference,
       count(*) FILTER (WHERE counts_onscreen <> counts_pdf)                AS divergent_rows
FROM exp;

-- 2) The exact rows that diverge (run separately) ----------------------------
-- Uncomment to inspect which expenses cause the gap and by how much:
--
-- SELECT
--     CASE
--       WHEN counts_onscreen AND NOT counts_pdf THEN 'only on-screen (inflates BVA/P&L page)'
--       WHEN counts_pdf AND NOT counts_onscreen THEN 'only PDF (inflates P&L PDF)'
--     END AS appears_in,
--     status, auth_status, amt AS amount, "TxnDate", account_name, account_id, id
-- FROM exp
-- WHERE counts_onscreen <> counts_pdf
-- ORDER BY appears_in, amt DESC;

-- 3) Breakdown of the divergence by the three known classes ------------------
-- SELECT
--   sum(amt) FILTER (WHERE auth_status IS TRUE AND (status IS NULL OR status='')) AS class1_legacy_null_status,
--   sum(amt) FILTER (WHERE auth_status IS TRUE AND status='pending')             AS class2_pending,
--   sum(amt) FILTER (WHERE auth_status IS FALSE AND status='auth')               AS class3_auth_flag_off
-- FROM exp;
