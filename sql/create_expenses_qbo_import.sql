-- =============================================
-- Table: expenses_qbo_import
-- QuickBooks Online COGS Import Table
-- Estructura basada en export QBO → Google Sheets
-- =============================================
--
-- IMPORTANTE:
-- - Esta tabla replica exactamente la estructura de tu export de QBO
-- - Sirve como staging area para importar datos desde QuickBooks
-- - Se puede usar para conciliar con expenses manuales
-- - Los campos mantienen los nombres exactos de tu estructura actual
--

CREATE TABLE IF NOT EXISTS expenses_qbo_import (
    -- Internal ID (generado por Supabase)
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- Bucket & Project Identification
    bucket TEXT,                        -- Agrupación/clasificación de proyectos
    project_id_qbo TEXT,                -- Project ID en QuickBooks (CustomerID)
    project_name TEXT,                  -- Nombre del proyecto en QuickBooks
    customer_id TEXT,                   -- Customer ID en QBO
    customer_name TEXT,                 -- Customer Name en QBO

    -- Transaction Identification
    txn_type TEXT,                      -- Tipo de transacción (Purchase, Check, Bill, etc.)
    txn_id TEXT NOT NULL,               -- Transaction ID de QuickBooks
    line_id TEXT,                       -- Line ID (para splits)
    global_line_uid TEXT UNIQUE,        -- UID único por línea (GlobalLineUID)

    -- Transaction Details
    txn_date DATE NOT NULL,             -- Fecha de la transacción
    vendor_name TEXT,                   -- Nombre del vendor/proveedor
    payment_type TEXT,                  -- Tipo de pago (Check, Credit Card, Cash, etc.)

    -- Account Information
    account_id TEXT,                    -- Account ID en QuickBooks
    account_name TEXT,                  -- Nombre de la cuenta
    account_type TEXT,                  -- Tipo de cuenta (Expense, Cost of Goods Sold, etc.)
    account_sub_type TEXT,              -- Subtipo de cuenta
    is_cogs BOOLEAN,                    -- Flag: ¿Es Cost of Goods Sold?

    -- Amount Information
    amount NUMERIC(15, 2),              -- Monto original
    sign TEXT,                          -- Signo (+/-)
    signed_amount NUMERIC(15, 2),       -- Monto con signo aplicado
    sign_source TEXT,                   -- Origen del signo
    posting_type TEXT,                  -- Tipo de posting (Debit/Credit)

    -- Description
    line_description TEXT,              -- Descripción de la línea

    -- Reconciliation con NGM Hub (sin foreign keys - tabla independiente)
    reconciled_expense_id UUID,         -- ID del expense manual (sin FK)
    reconciliation_status TEXT DEFAULT 'pending' CHECK (reconciliation_status IN ('pending', 'matched', 'reviewed', 'ignored', 'discrepancy')),
    reconciled_at TIMESTAMPTZ,
    reconciled_by UUID,                 -- ID del usuario (sin FK)
    reconciliation_notes TEXT,

    -- Vinculación con proyecto NGM (mapeado manualmente, sin FK)
    ngm_project_id UUID,                -- ID del proyecto NGM (sin FK)

    -- Import metadata
    imported_at TIMESTAMPTZ DEFAULT NOW(),
    import_batch_id TEXT,               -- ID del batch de importación
    import_source TEXT DEFAULT 'google_sheets', -- Fuente: google_sheets, qbo_api, csv, etc.

    -- Timestamps
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- =============================================
-- Índices para performance
-- =============================================

-- Búsqueda por proyecto QBO
CREATE INDEX IF NOT EXISTS idx_qbo_import_project_qbo ON expenses_qbo_import(project_id_qbo);
CREATE INDEX IF NOT EXISTS idx_qbo_import_project_ngm ON expenses_qbo_import(ngm_project_id);

-- Búsqueda por transacción
CREATE INDEX IF NOT EXISTS idx_qbo_import_txn_id ON expenses_qbo_import(txn_id);
CREATE INDEX IF NOT EXISTS idx_qbo_import_global_uid ON expenses_qbo_import(global_line_uid);

-- Búsqueda por fecha
CREATE INDEX IF NOT EXISTS idx_qbo_import_date ON expenses_qbo_import(txn_date DESC);

-- Búsqueda por vendor
CREATE INDEX IF NOT EXISTS idx_qbo_import_vendor ON expenses_qbo_import(vendor_name);

-- Búsqueda por cuenta
CREATE INDEX IF NOT EXISTS idx_qbo_import_account ON expenses_qbo_import(account_id);
CREATE INDEX IF NOT EXISTS idx_qbo_import_account_name ON expenses_qbo_import(account_name);

-- Estado de reconciliación
CREATE INDEX IF NOT EXISTS idx_qbo_import_reconciliation ON expenses_qbo_import(reconciliation_status);
CREATE INDEX IF NOT EXISTS idx_qbo_import_reconciled_expense ON expenses_qbo_import(reconciled_expense_id);

-- Import tracking
CREATE INDEX IF NOT EXISTS idx_qbo_import_batch ON expenses_qbo_import(import_batch_id);
CREATE INDEX IF NOT EXISTS idx_qbo_import_date_imported ON expenses_qbo_import(imported_at DESC);

-- COGS filtering
CREATE INDEX IF NOT EXISTS idx_qbo_import_is_cogs ON expenses_qbo_import(is_cogs) WHERE is_cogs = true;

-- Composite index para queries comunes
CREATE INDEX IF NOT EXISTS idx_qbo_import_project_date ON expenses_qbo_import(ngm_project_id, txn_date DESC);
CREATE INDEX IF NOT EXISTS idx_qbo_import_project_status ON expenses_qbo_import(ngm_project_id, reconciliation_status);

-- =============================================
-- Trigger para updated_at
-- =============================================

CREATE OR REPLACE FUNCTION update_qbo_import_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trigger_qbo_import_updated_at
    BEFORE UPDATE ON expenses_qbo_import
    FOR EACH ROW
    EXECUTE FUNCTION update_qbo_import_updated_at();

-- =============================================
-- Comments para documentación
-- =============================================

COMMENT ON TABLE expenses_qbo_import IS 'QuickBooks Online COGS import staging table. Matches exact structure from QBO → Google Sheets export.';

COMMENT ON COLUMN expenses_qbo_import.bucket IS 'Project grouping/classification bucket';
COMMENT ON COLUMN expenses_qbo_import.project_id_qbo IS 'QuickBooks Project/Customer ID (matches CustomerID in QBO)';
COMMENT ON COLUMN expenses_qbo_import.global_line_uid IS 'Unique identifier per line item. Use this as primary key for imports to avoid duplicates.';
COMMENT ON COLUMN expenses_qbo_import.txn_id IS 'QuickBooks Transaction ID. Can repeat for split transactions.';
COMMENT ON COLUMN expenses_qbo_import.line_id IS 'Line number within transaction for splits';
COMMENT ON COLUMN expenses_qbo_import.is_cogs IS 'Flag indicating if this is a Cost of Goods Sold expense';
COMMENT ON COLUMN expenses_qbo_import.signed_amount IS 'Amount with sign applied (+/-)';
COMMENT ON COLUMN expenses_qbo_import.reconciled_expense_id IS 'Link to manual expense entry if reconciled';
COMMENT ON COLUMN expenses_qbo_import.ngm_project_id IS 'Mapped NGM Hub project ID (requires manual mapping of QBO project → NGM project)';
COMMENT ON COLUMN expenses_qbo_import.import_batch_id IS 'Batch identifier for tracking bulk imports';

-- =============================================
-- RLS Policies (Row Level Security)
-- =============================================

-- Habilitar RLS
ALTER TABLE expenses_qbo_import ENABLE ROW LEVEL SECURITY;

-- Policy: Solo usuarios autenticados pueden ver
CREATE POLICY "Users can view QBO imports" ON expenses_qbo_import
    FOR SELECT
    USING (auth.role() = 'authenticated');

-- Policy: Solo admin/service role puede insertar/actualizar
CREATE POLICY "Only service role can modify QBO imports" ON expenses_qbo_import
    FOR ALL
    USING (auth.role() = 'service_role');

-- =============================================
-- Views útiles
-- =============================================

-- View: COGS expenses pending reconciliation
CREATE OR REPLACE VIEW v_qbo_cogs_pending AS
SELECT
    i.id,
    i.global_line_uid,
    i.txn_date,
    i.project_name,
    i.project_id_qbo,
    i.vendor_name,
    i.line_description,
    i.signed_amount,
    i.account_name,
    i.payment_type,
    i.ngm_project_id,
    i.imported_at
FROM expenses_qbo_import i
WHERE i.is_cogs = true
  AND i.reconciliation_status = 'pending'
ORDER BY i.txn_date DESC;

COMMENT ON VIEW v_qbo_cogs_pending IS 'COGS expenses from QBO pending reconciliation with manual books';

-- View: Import summary by project
CREATE OR REPLACE VIEW v_qbo_import_summary AS
SELECT
    i.project_name,
    i.project_id_qbo,
    i.ngm_project_id,
    COUNT(*) as total_lines,
    COUNT(*) FILTER (WHERE i.is_cogs = true) as cogs_lines,
    SUM(i.signed_amount) as total_amount,
    SUM(i.signed_amount) FILTER (WHERE i.is_cogs = true) as cogs_amount,
    COUNT(*) FILTER (WHERE i.reconciliation_status = 'pending') as pending_reconciliation,
    COUNT(*) FILTER (WHERE i.reconciliation_status = 'matched') as matched,
    MAX(i.imported_at) as last_import_date
FROM expenses_qbo_import i
GROUP BY i.project_name, i.project_id_qbo, i.ngm_project_id
ORDER BY MAX(i.imported_at) DESC;

COMMENT ON VIEW v_qbo_import_summary IS 'Summary of imported QBO data by project';

-- View: Unmapped projects (QBO projects without NGM mapping)
CREATE OR REPLACE VIEW v_qbo_unmapped_projects AS
SELECT DISTINCT
    i.project_id_qbo,
    i.project_name,
    i.customer_id,
    i.customer_name,
    COUNT(*) as line_count,
    SUM(i.signed_amount) as total_amount,
    MAX(i.imported_at) as last_imported
FROM expenses_qbo_import i
WHERE i.ngm_project_id IS NULL
  AND i.project_id_qbo IS NOT NULL
GROUP BY i.project_id_qbo, i.project_name, i.customer_id, i.customer_name
ORDER BY MAX(i.imported_at) DESC;

COMMENT ON VIEW v_qbo_unmapped_projects IS 'QBO projects that need mapping to NGM Hub projects';

-- =============================================
-- Helper function: Map QBO project to NGM project
-- =============================================

CREATE OR REPLACE FUNCTION map_qbo_project_to_ngm(
    p_project_id_qbo TEXT,
    p_ngm_project_id UUID
)
RETURNS INTEGER AS $$
DECLARE
    rows_updated INTEGER;
BEGIN
    UPDATE expenses_qbo_import
    SET ngm_project_id = p_ngm_project_id,
        updated_at = NOW()
    WHERE project_id_qbo = p_project_id_qbo
      AND (ngm_project_id IS NULL OR ngm_project_id != p_ngm_project_id);

    GET DIAGNOSTICS rows_updated = ROW_COUNT;

    RETURN rows_updated;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION map_qbo_project_to_ngm IS 'Map all expenses from a QBO project to an NGM Hub project';

-- =============================================
-- Example usage for importing from Google Sheets
-- =============================================

-- Ejemplo de INSERT desde tu estructura de Google Sheets:
/*

-- Método 1: INSERT directo (un registro a la vez)
INSERT INTO expenses_qbo_import (
    bucket, project_id_qbo, project_name, customer_id, customer_name,
    txn_type, txn_id, line_id, txn_date, vendor_name,
    payment_type, account_id, account_name, account_type, account_sub_type,
    is_cogs, amount, sign, signed_amount, sign_source,
    posting_type, line_description, global_line_uid,
    import_batch_id, import_source
)
VALUES (
    'Project Bucket A',
    '123',
    'Residential Remodel - Smith',
    '456',
    'John Smith',
    'Check',
    'TXN-789',
    '1',
    '2024-01-15',
    'Home Depot',
    'Credit Card',
    'ACC-001',
    'Job Materials',
    'Cost of Goods Sold',
    'SuppliesMaterialsCogs',
    true,
    1250.50,
    '-',
    -1250.50,
    'Account Type',
    'Debit',
    'Lumber and hardware for framing',
    'TXN-789-1',
    'batch_2024_01_15_001',
    'google_sheets'
)
ON CONFLICT (global_line_uid) DO UPDATE SET
    -- Update fields if row already exists (based on GlobalLineUID)
    amount = EXCLUDED.amount,
    signed_amount = EXCLUDED.signed_amount,
    line_description = EXCLUDED.line_description,
    updated_at = NOW();

-- Método 2: COPY desde CSV exportado de Google Sheets
-- Primero exporta tu hoja a CSV, luego:

COPY expenses_qbo_import (
    bucket, project_id_qbo, project_name, customer_id, customer_name,
    txn_type, txn_id, line_id, txn_date, vendor_name,
    payment_type, account_id, account_name, account_type, account_sub_type,
    is_cogs, amount, sign, signed_amount, sign_source,
    posting_type, line_description, global_line_uid
)
FROM '/path/to/qbo_export.csv'
WITH (FORMAT CSV, HEADER true, DELIMITER ',');

-- Método 3: Bulk insert para múltiples registros
INSERT INTO expenses_qbo_import (
    bucket, project_id_qbo, project_name, txn_type, txn_id,
    txn_date, vendor_name, account_name, is_cogs,
    signed_amount, line_description, global_line_uid
)
VALUES
    ('Bucket A', 'QBO-001', 'Project Alpha', 'Check', 'TXN-001', '2024-01-10', 'Vendor A', 'Materials', true, -500.00, 'Wood supplies', 'TXN-001-1'),
    ('Bucket A', 'QBO-001', 'Project Alpha', 'Check', 'TXN-001', '2024-01-10', 'Vendor A', 'Labor', true, -1500.00, 'Carpentry work', 'TXN-001-2'),
    ('Bucket B', 'QBO-002', 'Project Beta', 'Credit Card', 'TXN-002', '2024-01-12', 'Vendor B', 'Tools', true, -300.00, 'Power tools', 'TXN-002-1')
ON CONFLICT (global_line_uid) DO NOTHING;

*/

-- =============================================
-- Queries útiles para reconciliación
-- =============================================

-- 1. Ver todos los COGS sin reconciliar
/*
SELECT * FROM v_qbo_cogs_pending LIMIT 100;
*/

-- 2. Buscar posibles matches entre QBO y expenses manuales (por monto y fecha)
-- Nota: Requiere que las tablas 'expenses' y 'vendors' existan
/*
SELECT
    qbo.global_line_uid,
    qbo.txn_date,
    qbo.vendor_name as qbo_vendor,
    qbo.signed_amount as qbo_amount,
    qbo.line_description as qbo_desc,
    qbo.project_name as qbo_project,
    qbo.reconciliation_status
FROM expenses_qbo_import qbo
WHERE qbo.is_cogs = true
  AND qbo.reconciliation_status = 'pending'
ORDER BY qbo.txn_date DESC;

-- Si ya tienes la tabla 'expenses', puedes hacer join para encontrar matches:
-- SELECT
--     qbo.global_line_uid,
--     qbo.txn_date,
--     qbo.vendor_name as qbo_vendor,
--     qbo.signed_amount as qbo_amount,
--     qbo.line_description as qbo_desc,
--     e.expense_id,
--     e.TxnDate as manual_date,
--     e.amount as manual_amount,
--     e.description as manual_desc,
--     ABS(qbo.signed_amount - e.amount) as amount_diff,
--     ABS(EXTRACT(EPOCH FROM (qbo.txn_date - e.TxnDate))/86400) as date_diff_days
-- FROM expenses_qbo_import qbo
-- CROSS JOIN expenses e
-- WHERE qbo.is_cogs = true
--   AND qbo.reconciliation_status = 'pending'
--   AND qbo.ngm_project_id = e.project_id
--   AND ABS(qbo.signed_amount - e.amount) < 0.01
--   AND ABS(EXTRACT(EPOCH FROM (qbo.txn_date - e.TxnDate))/86400) <= 7
-- ORDER BY qbo.txn_date DESC, amount_diff ASC;
*/

-- 3. Mapear proyecto QBO a proyecto NGM
/*
SELECT map_qbo_project_to_ngm('QBO-PROJECT-123', 'ngm-project-uuid-here');
*/

-- 4. Ver resumen de importaciones
/*
SELECT * FROM v_qbo_import_summary;
*/

-- 5. Ver proyectos QBO que necesitan mapeo
/*
SELECT * FROM v_qbo_unmapped_projects;
*/

-- 6. Reconciliar manualmente un expense QBO con uno manual
/*
UPDATE expenses_qbo_import
SET
    reconciled_expense_id = 'manual-expense-uuid-here',
    reconciliation_status = 'matched',
    reconciled_at = NOW(),
    reconciled_by = 'user-uuid-here',
    reconciliation_notes = 'Matched by amount and date'
WHERE global_line_uid = 'TXN-123-1';
*/
