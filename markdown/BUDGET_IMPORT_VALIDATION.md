# Budget CSV Import - Validation Checklist

## Expected CSV Format

### Required Columns (in any order)
```
BudgetName, BudgetId, Year, StartDate, EndDate, Active, AccountId, AccountName, Amount_SUM
```

### Column Details

| Column Name | Required | Type | Description | Example |
|------------|----------|------|-------------|---------|
| **BudgetName** | ✅ Yes | Text | Name of the budget in QuickBooks | `FY2024 Operations` |
| **BudgetId** | ✅ Yes | Text | Budget ID from QuickBooks | `BDG-001` |
| **Amount_SUM** | ✅ Yes | Decimal | Total budgeted amount | `50000.00` or `$50,000.00` |
| **Year** | ⚠️ Optional | Integer | Budget year | `2024` |
| **StartDate** | ⚠️ Optional | Date | Budget start date | `01/01/2024` or `2024-01-01` |
| **EndDate** | ⚠️ Optional | Date | Budget end date | `12/31/2024` or `2024-12-31` |
| **Active** | ⚠️ Optional | Boolean | Is budget active? | `true`, `false`, `yes`, `no`, `1`, `0` |
| **AccountId** | ⚠️ Optional | Text | Account ID in QuickBooks | `ACC-100` |
| **AccountName** | ⚠️ Optional | Text | Account name | `Job Materials` |

## Validation Rules

### ✅ What the system ACCEPTS:

1. **CSV Format:**
   - Tab-delimited (TSV)
   - Comma-delimited (CSV)
   - Files with BOM (Byte Order Mark)
   - Different line endings (Windows CRLF, Unix LF, Mac CR)

2. **Headers:**
   - Case-insensitive matching (`budgetname` = `BudgetName`)
   - Extra whitespace is trimmed
   - Can be in any order

3. **Quoted Fields:**
   - Fields with commas: `"Company, Inc."`
   - Fields with quotes: `"Value with ""quotes"""`

4. **Date Formats:**
   - `YYYY-MM-DD` (2024-01-01)
   - `MM/DD/YYYY` (01/01/2024)
   - `DD/MM/YYYY` (01/01/2024)
   - `YYYY/MM/DD` (2024/01/01)
   - `MM-DD-YYYY` (01-01-2024)

5. **Amount Formats:**
   - With dollar sign: `$50,000.00`
   - With commas: `50,000.00`
   - Plain decimal: `50000.00`
   - With spaces: `$ 50 000.00`

6. **Boolean Formats:**
   - True: `true`, `t`, `yes`, `y`, `1`, `active`
   - False: `false`, `f`, `no`, `n`, `0`, `inactive`

### ❌ What the system REJECTS:

1. **Missing Required Columns:**
   - Must have: `BudgetName`, `BudgetId`, `Amount_SUM`

2. **File Issues:**
   - Files larger than 5MB
   - Non-CSV file extensions
   - Empty files
   - Files with only headers (no data)

3. **Row Issues:**
   - Rows with all required fields empty (skipped, not rejected)

## Frontend Validation (budgets.js)

### Lines 431-496: `handleCSVFileSelect()`

**Checks performed:**
1. ✅ File selected
2. ✅ File extension is `.csv`
3. ✅ File size < 5MB
4. ✅ BOM removal
5. ✅ Line ending normalization
6. ✅ Empty line filtering
7. ✅ CSV parsing with quote handling
8. ✅ Required headers present
9. ✅ Preview generation

**Logging:**
```javascript
console.log('[BUDGETS] CSV validation:', validation);
console.log('[BUDGETS] Successfully parsed CSV:', {
  headers: parsedCSVData.headers,
  rowCount: parsedCSVData.data.length,
  preview: parsedCSVData.data.slice(0, 3)
});
```

## Backend Validation (budgets.py)

### Lines 139-257: `import_budgets()`

**Processing steps:**
1. ✅ Header mapping (case-insensitive)
2. ✅ Required field validation
3. ✅ Batch ID generation
4. ✅ Row-by-row processing:
   - Extract values by header position
   - Parse dates (handles multiple formats)
   - Parse amounts (removes $, commas, spaces)
   - Parse booleans (multiple formats)
   - Skip rows with missing required fields
5. ✅ Bulk insert to `budgets_qbo` table
6. ✅ Link to project via `ngm_project_id`

**Error Handling:**
- Row errors: logged and skipped (continues processing)
- No valid records: returns 400 error
- Database errors: returns 500 error

## Database Schema (create_budgets_qbo.sql)

```sql
CREATE TABLE budgets_qbo (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- Budget Identification
    budget_name TEXT,
    budget_id_qbo TEXT NOT NULL,
    year INTEGER,
    start_date DATE,
    end_date DATE,
    active BOOLEAN DEFAULT true,

    -- Account Information
    account_id TEXT,
    account_name TEXT,
    amount_sum NUMERIC(15, 2),

    -- Project Link
    ngm_project_id UUID,  -- Links to project from dropdown

    -- Import Metadata
    imported_at TIMESTAMPTZ DEFAULT NOW(),
    import_batch_id TEXT,
    import_source TEXT DEFAULT 'csv',

    -- Timestamps
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
```

## Test Cases

### ✅ Test Case 1: Standard CSV
**Input:**
```csv
BudgetName,BudgetId,Year,StartDate,EndDate,Active,AccountId,AccountName,Amount_SUM
FY2024 Operations,BDG-001,2024,01/01/2024,12/31/2024,true,ACC-100,Job Materials,50000.00
```

**Expected Result:** ✅ Imports 1 record

### ✅ Test Case 2: Tab-delimited (TSV)
**Input:**
```tsv
BudgetName	BudgetId	Year	StartDate	EndDate	Active	AccountId	AccountName	Amount_SUM
FY2024 Operations	BDG-001	2024	01/01/2024	12/31/2024	true	ACC-100	Job Materials	50000.00
```

**Expected Result:** ✅ Imports 1 record

### ✅ Test Case 3: Quoted Fields with Commas
**Input:**
```csv
BudgetName,BudgetId,Year,StartDate,EndDate,Active,AccountId,AccountName,Amount_SUM
"FY2024 Operations, Phase 1",BDG-001,2024,01/01/2024,12/31/2024,true,ACC-100,"Materials, Job",50000.00
```

**Expected Result:** ✅ Imports 1 record

### ✅ Test Case 4: Amount with Currency Formatting
**Input:**
```csv
BudgetName,BudgetId,Year,StartDate,EndDate,Active,AccountId,AccountName,Amount_SUM
FY2024 Operations,BDG-001,2024,01/01/2024,12/31/2024,true,ACC-100,Job Materials,"$50,000.00"
```

**Expected Result:** ✅ Imports 1 record with amount = 50000.00

### ✅ Test Case 5: Different Date Formats
**Input:**
```csv
BudgetName,BudgetId,Year,StartDate,EndDate,Active,AccountId,AccountName,Amount_SUM
FY2024 Operations,BDG-001,2024,2024-01-01,2024-12-31,true,ACC-100,Job Materials,50000.00
```

**Expected Result:** ✅ Imports 1 record

### ✅ Test Case 6: Missing Optional Fields
**Input:**
```csv
BudgetName,BudgetId,Amount_SUM
FY2024 Operations,BDG-001,50000.00
```

**Expected Result:** ✅ Imports 1 record with nulls for optional fields

### ❌ Test Case 7: Missing Required Field
**Input:**
```csv
BudgetName,BudgetId,Year
FY2024 Operations,BDG-001,2024
```

**Expected Result:** ❌ Error: "Missing required CSV columns: Amount_SUM"

### ❌ Test Case 8: Empty File
**Input:**
```csv

```

**Expected Result:** ❌ Error: "CSV file is empty"

### ✅ Test Case 9: Multiple Rows Same Budget
**Input:**
```csv
BudgetName,BudgetId,Year,StartDate,EndDate,Active,AccountId,AccountName,Amount_SUM
FY2024 Operations,BDG-001,2024,01/01/2024,12/31/2024,true,ACC-100,Job Materials,50000.00
FY2024 Operations,BDG-001,2024,01/01/2024,12/31/2024,true,ACC-200,Labor,100000.00
FY2024 Operations,BDG-001,2024,01/01/2024,12/31/2024,true,ACC-300,Equipment,25000.00
```

**Expected Result:** ✅ Imports 3 records (all linked to same BudgetId)

## API Response Format

### Success Response:
```json
{
  "message": "Successfully imported 15 budget records",
  "count": 15,
  "batch_id": "batch_20240120_143022_a1b2c3d4",
  "project_id": "550e8400-e29b-41d4-a716-446655440000"
}
```

### Error Response:
```json
{
  "detail": "Missing required CSV columns: BudgetName, Amount_SUM"
}
```

## Troubleshooting

### Issue: "Missing required columns"
**Cause:** Headers don't match expected names
**Solution:** Verify CSV has `BudgetName`, `BudgetId`, and `Amount_SUM` (case-insensitive)

### Issue: "No valid budget records found in CSV"
**Cause:** All rows missing required fields
**Solution:** Check that data rows have values for BudgetName, BudgetId, and Amount_SUM

### Issue: "File is too large"
**Cause:** CSV exceeds 5MB limit
**Solution:** Split into smaller files or reduce columns

### Issue: Dates not parsing correctly
**Cause:** Unusual date format
**Solution:** Backend supports 5 common formats. Use one of: YYYY-MM-DD, MM/DD/YYYY, etc.

## Next Steps

1. ✅ Create `budgets_qbo` table in Supabase (run SQL)
2. ✅ Deploy backend changes to Render
3. ✅ Test with sample CSV file
4. ✅ Verify data appears in budgets page when project is selected

## Sample CSV File Location

See: `c:\Users\germa\Desktop\NGM_API\sample_budget_import.csv`
