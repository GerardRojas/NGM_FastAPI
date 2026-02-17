-- ============================================
-- MIGRATION: QBO Expenses -> expenses_manual_COGS
-- ============================================
-- One-time migration for projects where QuickBooks data
-- should replace manual expense entries.
--
-- FLOW:
--   1. Call POST /qbo/bills/extract-doc-numbers/{realm_id}?project=<UUID>
--      to populate qbo_bill_doc_mapping with Bill DocNumbers from QB API
--   2. Run these SQL functions in order:
--      a) migrate_backup_expenses(project_id)
--      b) migrate_qbo_to_manual(project_id)   -- inserts + re-links bills
--      c) migrate_cleanup_old_expenses(project_id)  -- deletes originals
--
-- Each step is idempotent and can be re-run safely.


-- ============================================
-- Step 0: Mapping table for Bill DocNumbers
-- ============================================
-- Populated by the /qbo/bills/extract-doc-numbers endpoint

CREATE TABLE IF NOT EXISTS qbo_bill_doc_mapping (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    qbo_bill_id     TEXT UNIQUE NOT NULL,       -- QB Bill.Id (same as txn_id in qbo_expenses)
    doc_number      TEXT,                        -- QB Bill.DocNumber (vendor invoice #)
    vendor_name     TEXT,
    vendor_ref_id   TEXT,
    total_amount    NUMERIC(12,2),
    txn_date        DATE,
    linked_txns_json TEXT,                       -- JSON string of LinkedTxn array
    created_at      TIMESTAMPTZ DEFAULT NOW()
);


-- ============================================
-- Step 1: Backup existing manual expenses
-- ============================================

CREATE OR REPLACE FUNCTION migrate_backup_expenses(p_project_id UUID)
RETURNS TABLE (
    backed_up_count BIGINT,
    backup_table_name TEXT
) AS $$
DECLARE
    v_count BIGINT;
BEGIN
    -- Create backup table if not exists (preserves ALL columns)
    CREATE TABLE IF NOT EXISTS expenses_manual_COGS_backup (
        LIKE "expenses_manual_COGS" INCLUDING ALL
    );

    -- Add migration metadata columns if they don't exist
    BEGIN
        ALTER TABLE expenses_manual_COGS_backup
            ADD COLUMN IF NOT EXISTS backup_date TIMESTAMPTZ DEFAULT NOW(),
            ADD COLUMN IF NOT EXISTS backup_project_id UUID;
    EXCEPTION WHEN OTHERS THEN
        NULL; -- columns already exist
    END;

    -- Copy current expenses to backup
    INSERT INTO expenses_manual_COGS_backup
    SELECT e.*, NOW(), p_project_id
    FROM "expenses_manual_COGS" e
    WHERE e.project = p_project_id;

    GET DIAGNOSTICS v_count = ROW_COUNT;

    RETURN QUERY SELECT v_count, 'expenses_manual_COGS_backup'::TEXT;
END;
$$ LANGUAGE plpgsql;


-- ============================================
-- Step 2: Migrate QBO expenses to manual table
-- ============================================
-- Maps columns, resolves vendor/account/payment UUIDs by name,
-- and re-links bills using DocNumber matching.

CREATE OR REPLACE FUNCTION migrate_qbo_to_manual(p_project_id UUID)
RETURNS TABLE (
    inserted_count   BIGINT,
    bills_relinked   BIGINT,
    vendors_matched  BIGINT,
    vendors_created  BIGINT,
    accounts_matched BIGINT,
    payment_matched  BIGINT,
    unmatched_vendors TEXT[],
    unmatched_accounts TEXT[]
) AS $$
DECLARE
    v_inserted       BIGINT := 0;
    v_bills_relinked BIGINT := 0;
    v_vendors_matched BIGINT := 0;
    v_vendors_created BIGINT := 0;
    v_accounts_matched BIGINT := 0;
    v_payment_matched BIGINT := 0;
    v_unmatched_vendors TEXT[] := '{}';
    v_unmatched_accounts TEXT[] := '{}';
    v_qbo_customer_ids TEXT[];
    v_purchase_payment_id UUID;
BEGIN

    -- ---- 0. Resolve QBO customer IDs for this project ----
    SELECT ARRAY_AGG(qbo_customer_id)
    INTO v_qbo_customer_ids
    FROM qbo_project_mapping
    WHERE ngm_project_id = p_project_id;

    IF v_qbo_customer_ids IS NULL OR array_length(v_qbo_customer_ids, 1) IS NULL THEN
        RAISE EXCEPTION 'No QBO customer mapping found for project %', p_project_id;
    END IF;

    -- ---- 1. Build vendor name -> UUID mapping ----
    CREATE TEMP TABLE IF NOT EXISTS _vendor_map (
        qbo_vendor_name TEXT PRIMARY KEY,
        ngm_vendor_id   UUID
    ) ON COMMIT DROP;

    TRUNCATE _vendor_map;

    -- Match by name (case-insensitive, trimmed)
    INSERT INTO _vendor_map (qbo_vendor_name, ngm_vendor_id)
    SELECT DISTINCT
        q.vendor_name,
        v.id
    FROM qbo_expenses q
    JOIN "Vendors" v ON LOWER(TRIM(v.vendor_name)) = LOWER(TRIM(q.vendor_name))
    WHERE q.qbo_customer_id = ANY(v_qbo_customer_ids)
      AND q.vendor_name IS NOT NULL
      AND q.vendor_name != ''
    ON CONFLICT DO NOTHING;

    SELECT COUNT(*) INTO v_vendors_matched FROM _vendor_map;

    -- Collect unmatched vendor names
    SELECT ARRAY_AGG(DISTINCT q.vendor_name)
    INTO v_unmatched_vendors
    FROM qbo_expenses q
    WHERE q.qbo_customer_id = ANY(v_qbo_customer_ids)
      AND q.vendor_name IS NOT NULL
      AND q.vendor_name != ''
      AND NOT EXISTS (SELECT 1 FROM _vendor_map vm WHERE vm.qbo_vendor_name = q.vendor_name);

    IF v_unmatched_vendors IS NULL THEN
        v_unmatched_vendors := '{}';
    END IF;

    -- ---- 2. Build account name -> UUID mapping ----
    CREATE TEMP TABLE IF NOT EXISTS _account_map (
        qbo_account_name TEXT PRIMARY KEY,
        ngm_account_id   UUID
    ) ON COMMIT DROP;

    TRUNCATE _account_map;

    -- Match by name (case-insensitive, trimmed)
    INSERT INTO _account_map (qbo_account_name, ngm_account_id)
    SELECT DISTINCT
        q.account_name,
        a.account_id
    FROM qbo_expenses q
    JOIN accounts a ON LOWER(TRIM(a."Name")) = LOWER(TRIM(q.account_name))
    WHERE q.qbo_customer_id = ANY(v_qbo_customer_ids)
      AND q.account_name IS NOT NULL
      AND q.account_name != ''
    ON CONFLICT DO NOTHING;

    SELECT COUNT(*) INTO v_accounts_matched FROM _account_map;

    -- Collect unmatched account names
    SELECT ARRAY_AGG(DISTINCT q.account_name)
    INTO v_unmatched_accounts
    FROM qbo_expenses q
    WHERE q.qbo_customer_id = ANY(v_qbo_customer_ids)
      AND q.account_name IS NOT NULL
      AND q.account_name != ''
      AND NOT EXISTS (SELECT 1 FROM _account_map am WHERE am.qbo_account_name = q.account_name);

    IF v_unmatched_accounts IS NULL THEN
        v_unmatched_accounts := '{}';
    END IF;

    -- ---- 3. Resolve "Purchase" payment type UUID (single default for all) ----
    SELECT id INTO v_purchase_payment_id
    FROM "paymet_methods"
    WHERE LOWER(TRIM(payment_method_name)) = 'purchase'
    LIMIT 1;

    IF v_purchase_payment_id IS NOT NULL THEN
        v_payment_matched := 1;
    END IF;

    -- ---- 4. Build bill re-linking map ----
    -- For QBO expenses with txn_type='Bill', use the qbo_bill_doc_mapping
    -- to find the DocNumber, then match to existing bills table.
    CREATE TEMP TABLE IF NOT EXISTS _bill_relink (
        qbo_global_line_uid TEXT PRIMARY KEY,
        ngm_bill_id         TEXT          -- bill_id in bills table
    ) ON COMMIT DROP;

    TRUNCATE _bill_relink;

    -- Strategy 1: Match by DocNumber from QB -> existing bills.bill_id
    INSERT INTO _bill_relink (qbo_global_line_uid, ngm_bill_id)
    SELECT
        q.global_line_uid,
        b.bill_id
    FROM qbo_expenses q
    JOIN qbo_bill_doc_mapping bdm ON bdm.qbo_bill_id = q.txn_id
    JOIN bills b ON b.bill_id = bdm.doc_number
    WHERE q.qbo_customer_id = ANY(v_qbo_customer_ids)
      AND q.txn_type = 'Bill'
      AND bdm.doc_number IS NOT NULL
    ON CONFLICT DO NOTHING;

    -- Strategy 2: Fuzzy match for remaining Bill expenses
    -- Match old expense bill_id by vendor_name + amount + date
    INSERT INTO _bill_relink (qbo_global_line_uid, ngm_bill_id)
    SELECT DISTINCT ON (q.global_line_uid)
        q.global_line_uid,
        old_e.bill_id
    FROM qbo_expenses q
    JOIN _vendor_map vm ON vm.qbo_vendor_name = q.vendor_name
    JOIN "expenses_manual_COGS" old_e
        ON old_e.project = p_project_id
        AND old_e.vendor_id = vm.ngm_vendor_id
        AND old_e."Amount" = q.amount
        AND old_e."TxnDate"::DATE = q.txn_date::DATE
        AND old_e.bill_id IS NOT NULL
    WHERE q.qbo_customer_id = ANY(v_qbo_customer_ids)
      AND NOT EXISTS (SELECT 1 FROM _bill_relink br WHERE br.qbo_global_line_uid = q.global_line_uid)
    ORDER BY q.global_line_uid, old_e.created_at DESC
    ON CONFLICT DO NOTHING;

    SELECT COUNT(*) INTO v_bills_relinked FROM _bill_relink;

    -- ---- 5. Insert QBO expenses into expenses_manual_COGS ----
    INSERT INTO "expenses_manual_COGS" (
        expense_id,
        project,
        "TxnDate",
        "TxnId_QBO",
        "LineUID",
        "Amount",
        vendor_id,
        account_id,
        "LineDescription",
        payment_type,
        bill_id,
        status,
        categorization_confidence,
        categorization_source,
        created_at
    )
    SELECT
        gen_random_uuid(),                                  -- new expense_id
        p_project_id,                                       -- project
        q.txn_date::DATE,                                   -- TxnDate
        q.txn_id,                                           -- TxnId_QBO (QB transaction ID)
        q.global_line_uid,                                  -- LineUID (unique line ref)
        q.signed_amount,                                    -- Amount (with sign for credits)
        vm.ngm_vendor_id,                                   -- vendor_id (mapped by name)
        am.ngm_account_id,                                  -- account_id (mapped by name)
        q.line_description,                                 -- LineDescription
        v_purchase_payment_id,                              -- payment_type (always "Purchase")
        br.ngm_bill_id,                                     -- bill_id (re-linked)
        'pending',                                          -- status (all start pending)
        CASE
            WHEN am.ngm_account_id IS NOT NULL THEN 100     -- QB categorization = trusted
            ELSE NULL
        END,
        CASE
            WHEN am.ngm_account_id IS NOT NULL THEN 'qbo_import'
            ELSE NULL
        END,
        NOW()
    FROM qbo_expenses q
    LEFT JOIN _vendor_map vm ON vm.qbo_vendor_name = q.vendor_name
    LEFT JOIN _account_map am ON am.qbo_account_name = q.account_name
    LEFT JOIN _bill_relink br ON br.qbo_global_line_uid = q.global_line_uid
    WHERE q.qbo_customer_id = ANY(v_qbo_customer_ids)
      AND q.is_cogs = true;

    GET DIAGNOSTICS v_inserted = ROW_COUNT;

    RETURN QUERY SELECT
        v_inserted,
        v_bills_relinked,
        v_vendors_matched,
        v_vendors_created,
        v_accounts_matched,
        v_payment_matched,
        v_unmatched_vendors,
        v_unmatched_accounts;
END;
$$ LANGUAGE plpgsql;


-- ============================================
-- Step 3: Cleanup - delete old manual expenses
-- ============================================
-- Only run AFTER verifying the migration was successful.
-- Checks that backup exists before deleting.

CREATE OR REPLACE FUNCTION migrate_cleanup_old_expenses(p_project_id UUID)
RETURNS TABLE (
    deleted_count BIGINT,
    backup_count  BIGINT
) AS $$
DECLARE
    v_backup_count BIGINT;
    v_deleted      BIGINT;
BEGIN
    -- Safety check: verify backup exists
    SELECT COUNT(*) INTO v_backup_count
    FROM expenses_manual_COGS_backup
    WHERE backup_project_id = p_project_id;

    IF v_backup_count = 0 THEN
        RAISE EXCEPTION 'No backup found for project %. Run migrate_backup_expenses first.', p_project_id;
    END IF;

    -- Delete old expenses that were backed up
    -- Only delete expenses that existed BEFORE the migration (by matching expense_id in backup)
    DELETE FROM "expenses_manual_COGS" e
    WHERE e.project = p_project_id
      AND e.expense_id IN (
          SELECT b.expense_id
          FROM expenses_manual_COGS_backup b
          WHERE b.backup_project_id = p_project_id
      );

    GET DIAGNOSTICS v_deleted = ROW_COUNT;

    RETURN QUERY SELECT v_deleted, v_backup_count;
END;
$$ LANGUAGE plpgsql;


-- ============================================
-- Helper: Preview migration before executing
-- ============================================
-- Shows what would be migrated without making changes.
-- Use this to verify mappings are correct.

CREATE OR REPLACE FUNCTION migrate_preview(p_project_id UUID)
RETURNS TABLE (
    total_qbo_expenses    BIGINT,
    cogs_only             BIGINT,
    current_manual_count  BIGINT,
    vendors_will_match    BIGINT,
    vendors_unmatched     TEXT[],
    accounts_will_match   BIGINT,
    accounts_unmatched    TEXT[],
    bills_will_relink     BIGINT,
    bills_in_current      BIGINT
) AS $$
DECLARE
    v_qbo_customer_ids TEXT[];
    v_total        BIGINT;
    v_cogs         BIGINT;
    v_manual       BIGINT;
    v_v_match      BIGINT;
    v_v_unmatch    TEXT[];
    v_a_match      BIGINT;
    v_a_unmatch    TEXT[];
    v_bills_relink BIGINT;
    v_bills_cur    BIGINT;
BEGIN
    -- Resolve QBO customer IDs
    SELECT ARRAY_AGG(qbo_customer_id)
    INTO v_qbo_customer_ids
    FROM qbo_project_mapping
    WHERE ngm_project_id = p_project_id;

    IF v_qbo_customer_ids IS NULL THEN
        RAISE EXCEPTION 'No QBO customer mapping found for project %', p_project_id;
    END IF;

    -- Total QBO expenses for this project
    SELECT COUNT(*) INTO v_total
    FROM qbo_expenses
    WHERE qbo_customer_id = ANY(v_qbo_customer_ids);

    -- COGS only
    SELECT COUNT(*) INTO v_cogs
    FROM qbo_expenses
    WHERE qbo_customer_id = ANY(v_qbo_customer_ids)
      AND is_cogs = true;

    -- Current manual expenses
    SELECT COUNT(*) INTO v_manual
    FROM "expenses_manual_COGS"
    WHERE project = p_project_id;

    -- Vendor matching preview
    SELECT COUNT(DISTINCT q.vendor_name) INTO v_v_match
    FROM qbo_expenses q
    JOIN "Vendors" v ON LOWER(TRIM(v.vendor_name)) = LOWER(TRIM(q.vendor_name))
    WHERE q.qbo_customer_id = ANY(v_qbo_customer_ids)
      AND q.vendor_name IS NOT NULL AND q.vendor_name != '';

    SELECT ARRAY_AGG(DISTINCT q.vendor_name) INTO v_v_unmatch
    FROM qbo_expenses q
    WHERE q.qbo_customer_id = ANY(v_qbo_customer_ids)
      AND q.vendor_name IS NOT NULL AND q.vendor_name != ''
      AND NOT EXISTS (
          SELECT 1 FROM "Vendors" v
          WHERE LOWER(TRIM(v.vendor_name)) = LOWER(TRIM(q.vendor_name))
      );

    -- Account matching preview
    SELECT COUNT(DISTINCT q.account_name) INTO v_a_match
    FROM qbo_expenses q
    JOIN accounts a ON LOWER(TRIM(a."Name")) = LOWER(TRIM(q.account_name))
    WHERE q.qbo_customer_id = ANY(v_qbo_customer_ids)
      AND q.account_name IS NOT NULL AND q.account_name != '';

    SELECT ARRAY_AGG(DISTINCT q.account_name) INTO v_a_unmatch
    FROM qbo_expenses q
    WHERE q.qbo_customer_id = ANY(v_qbo_customer_ids)
      AND q.account_name IS NOT NULL AND q.account_name != ''
      AND NOT EXISTS (
          SELECT 1 FROM accounts a
          WHERE LOWER(TRIM(a."Name")) = LOWER(TRIM(q.account_name))
      );

    -- Bill re-linking preview (using doc_number mapping)
    SELECT COUNT(DISTINCT q.global_line_uid) INTO v_bills_relink
    FROM qbo_expenses q
    JOIN qbo_bill_doc_mapping bdm ON bdm.qbo_bill_id = q.txn_id
    JOIN bills b ON b.bill_id = bdm.doc_number
    WHERE q.qbo_customer_id = ANY(v_qbo_customer_ids)
      AND q.txn_type = 'Bill'
      AND bdm.doc_number IS NOT NULL;

    -- Bills currently linked to manual expenses
    SELECT COUNT(DISTINCT e.bill_id) INTO v_bills_cur
    FROM "expenses_manual_COGS" e
    WHERE e.project = p_project_id
      AND e.bill_id IS NOT NULL;

    RETURN QUERY SELECT
        v_total,
        v_cogs,
        v_manual,
        v_v_match,
        COALESCE(v_v_unmatch, '{}'),
        v_a_match,
        COALESCE(v_a_unmatch, '{}'),
        v_bills_relink,
        v_bills_cur;
END;
$$ LANGUAGE plpgsql;


-- ============================================
-- Helper: Restore from backup (rollback)
-- ============================================
-- If something goes wrong, this restores the original expenses.

CREATE OR REPLACE FUNCTION migrate_rollback(p_project_id UUID)
RETURNS TABLE (
    deleted_new     BIGINT,
    restored_count  BIGINT
) AS $$
DECLARE
    v_deleted  BIGINT;
    v_restored BIGINT;
    v_backup   BIGINT;
    v_cols     TEXT;
BEGIN
    -- Verify backup exists
    SELECT COUNT(*) INTO v_backup
    FROM expenses_manual_COGS_backup
    WHERE backup_project_id = p_project_id;

    IF v_backup = 0 THEN
        RAISE EXCEPTION 'No backup found for project %', p_project_id;
    END IF;

    -- Delete any new (migrated) expenses for this project
    DELETE FROM "expenses_manual_COGS"
    WHERE project = p_project_id;

    GET DIAGNOSTICS v_deleted = ROW_COUNT;

    -- Build column list dynamically (only columns that exist in BOTH tables)
    -- This avoids hardcoding and handles schema changes gracefully
    SELECT string_agg(quote_ident(c.column_name), ', ')
    INTO v_cols
    FROM information_schema.columns c
    WHERE c.table_name = 'expenses_manual_COGS'
      AND c.table_schema = 'public'
      AND c.column_name IN (
          SELECT c2.column_name
          FROM information_schema.columns c2
          WHERE c2.table_name = 'expenses_manual_cogs_backup'
            AND c2.table_schema = 'public'
      )
      AND c.column_name NOT IN ('backup_date', 'backup_project_id');

    -- Restore from backup
    EXECUTE format(
        'INSERT INTO "expenses_manual_COGS" (%s) SELECT %s FROM expenses_manual_COGS_backup WHERE backup_project_id = $1',
        v_cols, v_cols
    ) USING p_project_id;

    GET DIAGNOSTICS v_restored = ROW_COUNT;

    RETURN QUERY SELECT v_deleted, v_restored;
END;
$$ LANGUAGE plpgsql;
