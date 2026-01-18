-- =============================================
-- Table: expenses_qbo_cogs
-- QuickBooks Online Expenses (Cost of Goods Sold)
-- =============================================
--
-- IMPORTANTE:
-- - En QuickBooks, un TxnID puede repetirse para splits
-- - qbo_unique_id se genera como: TxnID + Line number o Detail ID
-- - Esta tabla es READ-ONLY desde NGM Hub (datos vienen de QBO)
-- - Se sincroniza manualmente con botón "Sync QBO"

CREATE TABLE IF NOT EXISTS expenses_qbo_cogs (
    -- Identificadores únicos
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    qbo_unique_id TEXT UNIQUE NOT NULL,  -- TxnID + LineNum concatenado
    qbo_txn_id TEXT NOT NULL,            -- Transaction ID de QuickBooks (puede repetirse en splits)
    qbo_line_num INTEGER,                -- Número de línea en caso de split

    -- Vinculación con proyecto NGM
    project_id UUID REFERENCES projects(id) ON DELETE SET NULL,

    -- Datos de la transacción
    txn_date DATE NOT NULL,
    doc_number TEXT,                     -- Número de documento/check

    -- Descripción y detalles
    description TEXT,
    memo TEXT,
    private_note TEXT,

    -- Vendor/Payee
    vendor_ref_id TEXT,                  -- QuickBooks Vendor ID
    vendor_name TEXT,

    -- Amounts
    amount NUMERIC(15, 2) NOT NULL,

    -- Account mapping
    account_ref_id TEXT,                 -- QuickBooks Account ID
    account_name TEXT,
    account_type TEXT,                   -- Account type en QBO

    -- Payment method
    payment_type TEXT,                   -- Check, Credit Card, Cash, etc.
    payment_account_ref_id TEXT,         -- ID de cuenta de pago
    payment_account_name TEXT,           -- Nombre de cuenta de pago

    -- Category/Class (si aplica en QBO)
    class_ref_id TEXT,
    class_name TEXT,
    department_ref_id TEXT,
    department_name TEXT,

    -- Customer/Job (si expense es billable)
    customer_ref_id TEXT,
    customer_name TEXT,
    billable_status TEXT,                -- Billable, NotBillable, HasBeenBilled

    -- Tax information
    tax_code_ref_id TEXT,
    tax_amount NUMERIC(15, 2),

    -- Metadata de QuickBooks
    qbo_created_time TIMESTAMPTZ,
    qbo_last_updated_time TIMESTAMPTZ,
    qbo_entity_type TEXT,                -- Purchase, Check, CreditCardCharge, etc.
    qbo_status TEXT,                     -- Active, Deleted, Voided

    -- Reconciliation con expenses manuales
    reconciled_expense_id UUID REFERENCES expenses(id) ON DELETE SET NULL,
    reconciliation_status TEXT DEFAULT 'pending' CHECK (reconciliation_status IN ('pending', 'matched', 'reviewed', 'discrepancy')),
    reconciled_at TIMESTAMPTZ,
    reconciled_by UUID REFERENCES users(user_id) ON DELETE SET NULL,
    reconciliation_notes TEXT,

    -- Sync metadata
    last_synced_at TIMESTAMPTZ DEFAULT NOW(),
    sync_source TEXT DEFAULT 'qbo_api',  -- qbo_api, manual_import, etc.

    -- Raw data de QBO (JSON completo para debugging)
    qbo_raw_data JSONB,

    -- Timestamps
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- =============================================
-- Índices para performance
-- =============================================

-- Búsqueda por proyecto
CREATE INDEX IF NOT EXISTS idx_expenses_qbo_project ON expenses_qbo_cogs(project_id);

-- Búsqueda por fecha
CREATE INDEX IF NOT EXISTS idx_expenses_qbo_date ON expenses_qbo_cogs(txn_date DESC);

-- Búsqueda por vendor
CREATE INDEX IF NOT EXISTS idx_expenses_qbo_vendor ON expenses_qbo_cogs(vendor_ref_id);
CREATE INDEX IF NOT EXISTS idx_expenses_qbo_vendor_name ON expenses_qbo_cogs(vendor_name);

-- Búsqueda por QuickBooks IDs
CREATE INDEX IF NOT EXISTS idx_expenses_qbo_txn_id ON expenses_qbo_cogs(qbo_txn_id);
CREATE INDEX IF NOT EXISTS idx_expenses_qbo_unique_id ON expenses_qbo_cogs(qbo_unique_id);

-- Estado de reconciliación
CREATE INDEX IF NOT EXISTS idx_expenses_qbo_reconciliation ON expenses_qbo_cogs(reconciliation_status);
CREATE INDEX IF NOT EXISTS idx_expenses_qbo_reconciled_expense ON expenses_qbo_cogs(reconciled_expense_id);

-- Búsqueda por cuenta
CREATE INDEX IF NOT EXISTS idx_expenses_qbo_account ON expenses_qbo_cogs(account_ref_id);

-- Sync status
CREATE INDEX IF NOT EXISTS idx_expenses_qbo_last_synced ON expenses_qbo_cogs(last_synced_at DESC);

-- Composite index para queries comunes
CREATE INDEX IF NOT EXISTS idx_expenses_qbo_project_date ON expenses_qbo_cogs(project_id, txn_date DESC);

-- =============================================
-- Trigger para updated_at
-- =============================================

CREATE OR REPLACE FUNCTION update_expenses_qbo_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trigger_expenses_qbo_updated_at
    BEFORE UPDATE ON expenses_qbo_cogs
    FOR EACH ROW
    EXECUTE FUNCTION update_expenses_qbo_updated_at();

-- =============================================
-- Comments para documentación
-- =============================================

COMMENT ON TABLE expenses_qbo_cogs IS 'QuickBooks Online expenses synced for reconciliation with manual books. READ-ONLY from NGM Hub UI.';

COMMENT ON COLUMN expenses_qbo_cogs.qbo_unique_id IS 'Unique identifier combining TxnID + LineNum to handle splits. Format: {TxnID}-{LineNum}';
COMMENT ON COLUMN expenses_qbo_cogs.qbo_txn_id IS 'QuickBooks Transaction ID. Can repeat for split transactions.';
COMMENT ON COLUMN expenses_qbo_cogs.qbo_line_num IS 'Line number within the transaction. Used for split entries.';
COMMENT ON COLUMN expenses_qbo_cogs.reconciled_expense_id IS 'Link to manual expense entry if reconciled.';
COMMENT ON COLUMN expenses_qbo_cogs.qbo_raw_data IS 'Full JSON response from QuickBooks API for debugging.';

-- =============================================
-- RLS Policies (si usas Row Level Security)
-- =============================================

-- Habilitar RLS
ALTER TABLE expenses_qbo_cogs ENABLE ROW LEVEL SECURITY;

-- Policy: Solo usuarios autenticados pueden ver
CREATE POLICY "Users can view QBO expenses" ON expenses_qbo_cogs
    FOR SELECT
    USING (auth.role() = 'authenticated');

-- Policy: Solo admin puede insertar/actualizar (via backend)
CREATE POLICY "Only service role can modify QBO expenses" ON expenses_qbo_cogs
    FOR ALL
    USING (auth.role() = 'service_role');

-- =============================================
-- View útil: Expenses no reconciliados
-- =============================================

CREATE OR REPLACE VIEW v_qbo_expenses_pending_reconciliation AS
SELECT
    e.id,
    e.qbo_unique_id,
    e.txn_date,
    e.vendor_name,
    e.description,
    e.amount,
    e.account_name,
    e.payment_type,
    p.name as project_name,
    e.last_synced_at
FROM expenses_qbo_cogs e
LEFT JOIN projects p ON e.project_id = p.id
WHERE e.reconciliation_status = 'pending'
    AND e.qbo_status = 'Active'
ORDER BY e.txn_date DESC;

COMMENT ON VIEW v_qbo_expenses_pending_reconciliation IS 'QuickBooks expenses pending reconciliation with manual books';

-- =============================================
-- Ejemplo de query para generar qbo_unique_id
-- =============================================

-- En tu script de importación de Python, genera el qbo_unique_id así:
-- qbo_unique_id = f"{txn['Id']}-{line_num}" if line_num else txn['Id']
--
-- Ejemplo:
-- TxnID: "12345", LineNum: 0 → qbo_unique_id: "12345-0"
-- TxnID: "12345", LineNum: 1 → qbo_unique_id: "12345-1"
-- TxnID: "67890", Sin split  → qbo_unique_id: "67890-0"
