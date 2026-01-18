-- =============================================
-- Table: budgets_qbo
-- QuickBooks Online Budgets Import Table
-- =============================================
--
-- IMPORTANTE:
-- - Esta tabla almacena budgets importados desde QuickBooks Online
-- - Estructura basada en export QBO → CSV
-- - Sin foreign keys, tabla independiente para imports simples
-- - Se vincula manualmente a proyectos NGM

CREATE TABLE IF NOT EXISTS budgets_qbo (
    -- Internal ID
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- Budget Identification
    budget_name TEXT,                   -- Nombre del budget en QBO
    budget_id_qbo TEXT NOT NULL,        -- Budget ID en QuickBooks
    year INTEGER,                       -- Año del budget
    start_date DATE,                    -- Fecha de inicio
    end_date DATE,                      -- Fecha de fin
    active BOOLEAN DEFAULT true,        -- ¿Budget activo?

    -- Account Information
    account_id TEXT,                    -- Account ID en QuickBooks
    account_name TEXT,                  -- Nombre de la cuenta

    -- Amount Information
    amount_sum NUMERIC(15, 2),          -- Suma total del budget
    lines_count INTEGER,                -- Número de líneas en el budget
    budget_date_min DATE,               -- Fecha mínima de líneas
    budget_date_max DATE,               -- Fecha máxima de líneas

    -- Vinculación con proyecto NGM (manual)
    ngm_project_id UUID,                -- ID del proyecto NGM (sin FK)
    project_mapping_notes TEXT,         -- Notas sobre el mapeo

    -- Import metadata
    imported_at TIMESTAMPTZ DEFAULT NOW(),
    import_batch_id TEXT,               -- ID del batch de importación
    import_source TEXT DEFAULT 'csv',   -- csv, qbo_api, google_sheets, etc.

    -- Timestamps
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- =============================================
-- Índices para performance
-- =============================================

-- Búsqueda por budget
CREATE INDEX IF NOT EXISTS idx_budgets_qbo_budget_id ON budgets_qbo(budget_id_qbo);
CREATE INDEX IF NOT EXISTS idx_budgets_qbo_budget_name ON budgets_qbo(budget_name);

-- Búsqueda por proyecto NGM
CREATE INDEX IF NOT EXISTS idx_budgets_qbo_ngm_project ON budgets_qbo(ngm_project_id);

-- Búsqueda por año
CREATE INDEX IF NOT EXISTS idx_budgets_qbo_year ON budgets_qbo(year DESC);

-- Búsqueda por cuenta
CREATE INDEX IF NOT EXISTS idx_budgets_qbo_account ON budgets_qbo(account_id);
CREATE INDEX IF NOT EXISTS idx_budgets_qbo_account_name ON budgets_qbo(account_name);

-- Budgets activos
CREATE INDEX IF NOT EXISTS idx_budgets_qbo_active ON budgets_qbo(active) WHERE active = true;

-- Import tracking
CREATE INDEX IF NOT EXISTS idx_budgets_qbo_batch ON budgets_qbo(import_batch_id);
CREATE INDEX IF NOT EXISTS idx_budgets_qbo_imported ON budgets_qbo(imported_at DESC);

-- Composite index
CREATE INDEX IF NOT EXISTS idx_budgets_qbo_project_year ON budgets_qbo(ngm_project_id, year DESC);

-- =============================================
-- Trigger para updated_at
-- =============================================

CREATE OR REPLACE FUNCTION update_budgets_qbo_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trigger_budgets_qbo_updated_at
    BEFORE UPDATE ON budgets_qbo
    FOR EACH ROW
    EXECUTE FUNCTION update_budgets_qbo_updated_at();

-- =============================================
-- Comments
-- =============================================

COMMENT ON TABLE budgets_qbo IS 'QuickBooks Online budgets import table. Independent staging table for CSV imports.';
COMMENT ON COLUMN budgets_qbo.budget_id_qbo IS 'QuickBooks Budget ID. Can be combined with AccountId for uniqueness.';
COMMENT ON COLUMN budgets_qbo.amount_sum IS 'Total budgeted amount for this account';
COMMENT ON COLUMN budgets_qbo.lines_count IS 'Number of budget line items';
COMMENT ON COLUMN budgets_qbo.ngm_project_id IS 'Manually mapped NGM Hub project ID';

-- =============================================
-- RLS Policies
-- =============================================

ALTER TABLE budgets_qbo ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Users can view budgets" ON budgets_qbo
    FOR SELECT
    USING (auth.role() = 'authenticated');

CREATE POLICY "Only service role can modify budgets" ON budgets_qbo
    FOR ALL
    USING (auth.role() = 'service_role');

-- =============================================
-- Views
-- =============================================

-- View: Budgets summary by project
CREATE OR REPLACE VIEW v_budgets_by_project AS
SELECT
    b.ngm_project_id,
    b.budget_name,
    b.year,
    COUNT(*) as account_count,
    SUM(b.amount_sum) as total_budget,
    MIN(b.start_date) as budget_start,
    MAX(b.end_date) as budget_end,
    MAX(b.imported_at) as last_imported
FROM budgets_qbo b
WHERE b.active = true
GROUP BY b.ngm_project_id, b.budget_name, b.year
ORDER BY b.year DESC, b.budget_name;

COMMENT ON VIEW v_budgets_by_project IS 'Summary of budgets grouped by project and year';

-- View: Unmapped budgets
CREATE OR REPLACE VIEW v_budgets_unmapped AS
SELECT
    b.budget_id_qbo,
    b.budget_name,
    b.year,
    COUNT(*) as account_count,
    SUM(b.amount_sum) as total_amount,
    MAX(b.imported_at) as last_imported
FROM budgets_qbo b
WHERE b.ngm_project_id IS NULL
GROUP BY b.budget_id_qbo, b.budget_name, b.year
ORDER BY MAX(b.imported_at) DESC;

COMMENT ON VIEW v_budgets_unmapped IS 'Budgets that need mapping to NGM projects';

-- View: Budget vs Actual (requires expenses table)
-- Este view está comentado porque requiere la tabla expenses
/*
CREATE OR REPLACE VIEW v_budget_vs_actual AS
SELECT
    b.ngm_project_id,
    b.budget_name,
    b.year,
    b.account_name,
    b.amount_sum as budgeted_amount,
    COALESCE(SUM(e.amount), 0) as actual_amount,
    b.amount_sum - COALESCE(SUM(e.amount), 0) as variance,
    CASE
        WHEN b.amount_sum > 0 THEN
            (COALESCE(SUM(e.amount), 0) / b.amount_sum * 100)
        ELSE 0
    END as percentage_used
FROM budgets_qbo b
LEFT JOIN expenses e ON
    e.project_id = b.ngm_project_id
    AND EXTRACT(YEAR FROM e.TxnDate) = b.year
WHERE b.active = true
GROUP BY b.ngm_project_id, b.budget_name, b.year, b.account_name, b.amount_sum
ORDER BY b.year DESC, b.budget_name, b.account_name;

COMMENT ON VIEW v_budget_vs_actual IS 'Budget vs Actual expenses comparison';
*/

-- =============================================
-- Helper Functions
-- =============================================

-- Mapear budget QBO a proyecto NGM
CREATE OR REPLACE FUNCTION map_budget_to_project(
    p_budget_id_qbo TEXT,
    p_ngm_project_id UUID,
    p_notes TEXT DEFAULT NULL
)
RETURNS INTEGER AS $$
DECLARE
    rows_updated INTEGER;
BEGIN
    UPDATE budgets_qbo
    SET ngm_project_id = p_ngm_project_id,
        project_mapping_notes = COALESCE(p_notes, project_mapping_notes),
        updated_at = NOW()
    WHERE budget_id_qbo = p_budget_id_qbo
      AND (ngm_project_id IS NULL OR ngm_project_id != p_ngm_project_id);

    GET DIAGNOSTICS rows_updated = ROW_COUNT;

    RETURN rows_updated;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION map_budget_to_project IS 'Map all budget lines from a QBO budget to an NGM project';

-- =============================================
-- Example Usage
-- =============================================

/*
-- Método 1: INSERT directo desde CSV
INSERT INTO budgets_qbo (
    budget_name, budget_id_qbo, year, start_date, end_date, active,
    account_id, account_name, amount_sum, lines_count,
    budget_date_min, budget_date_max,
    import_batch_id, import_source
)
VALUES (
    'FY2024 Operations',
    'BDG-001',
    2024,
    '2024-01-01',
    '2024-12-31',
    true,
    'ACC-100',
    'Job Materials',
    50000.00,
    12,
    '2024-01-01',
    '2024-12-31',
    'batch_2024_01_20',
    'csv'
);

-- Método 2: COPY desde CSV
COPY budgets_qbo (
    budget_name, budget_id_qbo, year, start_date, end_date, active,
    account_id, account_name, amount_sum, lines_count,
    budget_date_min, budget_date_max
)
FROM '/path/to/budgets_export.csv'
WITH (FORMAT CSV, HEADER true, DELIMITER ',');

-- Método 3: Bulk insert
INSERT INTO budgets_qbo (
    budget_name, budget_id_qbo, year, account_name, amount_sum, ngm_project_id
)
VALUES
    ('2024 Budget', 'BDG-001', 2024, 'Materials', 50000.00, 'project-uuid-1'),
    ('2024 Budget', 'BDG-001', 2024, 'Labor', 100000.00, 'project-uuid-1'),
    ('2024 Budget', 'BDG-001', 2024, 'Equipment', 25000.00, 'project-uuid-1');

-- Ver budgets sin mapear
SELECT * FROM v_budgets_unmapped;

-- Mapear budget a proyecto
SELECT map_budget_to_project('BDG-001', 'ngm-project-uuid-here', 'Mapped to Smith Residence');

-- Ver resumen por proyecto
SELECT * FROM v_budgets_by_project WHERE ngm_project_id = 'project-uuid-here';

-- Buscar budgets por año
SELECT * FROM budgets_qbo WHERE year = 2024 AND active = true;
*/
