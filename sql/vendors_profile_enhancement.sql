-- =============================================
-- VENDORS PROFILE ENHANCEMENT
-- Phase 1 of QuickBooks-level accounting upgrade
-- =============================================

-- New columns for vendor profiles
ALTER TABLE "Vendors" ADD COLUMN IF NOT EXISTS company_name TEXT;
ALTER TABLE "Vendors" ADD COLUMN IF NOT EXISTS contact_name TEXT;
ALTER TABLE "Vendors" ADD COLUMN IF NOT EXISTS email TEXT;
ALTER TABLE "Vendors" ADD COLUMN IF NOT EXISTS phone TEXT;
ALTER TABLE "Vendors" ADD COLUMN IF NOT EXISTS address_line1 TEXT;
ALTER TABLE "Vendors" ADD COLUMN IF NOT EXISTS address_line2 TEXT;
ALTER TABLE "Vendors" ADD COLUMN IF NOT EXISTS city TEXT;
ALTER TABLE "Vendors" ADD COLUMN IF NOT EXISTS state TEXT;
ALTER TABLE "Vendors" ADD COLUMN IF NOT EXISTS zip_code TEXT;
ALTER TABLE "Vendors" ADD COLUMN IF NOT EXISTS country TEXT DEFAULT 'US';

-- Tax & compliance
ALTER TABLE "Vendors" ADD COLUMN IF NOT EXISTS tax_id TEXT;
ALTER TABLE "Vendors" ADD COLUMN IF NOT EXISTS tax_id_type TEXT;  -- 'EIN' | 'SSN' | 'ITIN'
ALTER TABLE "Vendors" ADD COLUMN IF NOT EXISTS is_1099 BOOLEAN DEFAULT FALSE;
ALTER TABLE "Vendors" ADD COLUMN IF NOT EXISTS w9_status TEXT DEFAULT 'not_requested';  -- 'not_requested' | 'requested' | 'received' | 'expired'
ALTER TABLE "Vendors" ADD COLUMN IF NOT EXISTS w9_file_url TEXT;
ALTER TABLE "Vendors" ADD COLUMN IF NOT EXISTS w9_received_date DATE;
ALTER TABLE "Vendors" ADD COLUMN IF NOT EXISTS w8_status TEXT DEFAULT 'not_applicable';  -- 'not_applicable' | 'requested' | 'received' | 'expired'
ALTER TABLE "Vendors" ADD COLUMN IF NOT EXISTS w8_file_url TEXT;
ALTER TABLE "Vendors" ADD COLUMN IF NOT EXISTS w8_received_date DATE;

-- Accounting defaults
ALTER TABLE "Vendors" ADD COLUMN IF NOT EXISTS vendor_type TEXT DEFAULT 'supplier';  -- 'contractor' | 'supplier' | 'service' | 'utility' | 'government' | 'other'
ALTER TABLE "Vendors" ADD COLUMN IF NOT EXISTS payment_terms TEXT DEFAULT 'due_on_receipt';  -- 'due_on_receipt' | 'net_15' | 'net_30' | 'net_45' | 'net_60'
ALTER TABLE "Vendors" ADD COLUMN IF NOT EXISTS default_account_id UUID;
ALTER TABLE "Vendors" ADD COLUMN IF NOT EXISTS status TEXT DEFAULT 'active';  -- 'active' | 'inactive'
ALTER TABLE "Vendors" ADD COLUMN IF NOT EXISTS notes TEXT;
ALTER TABLE "Vendors" ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ DEFAULT NOW();
ALTER TABLE "Vendors" ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT NOW();

-- Index for common queries
CREATE INDEX IF NOT EXISTS idx_vendors_status ON "Vendors" (status);
CREATE INDEX IF NOT EXISTS idx_vendors_is_1099 ON "Vendors" (is_1099) WHERE is_1099 = TRUE;
CREATE INDEX IF NOT EXISTS idx_vendors_type ON "Vendors" (vendor_type);
CREATE INDEX IF NOT EXISTS idx_vendors_w9_status ON "Vendors" (w9_status);

-- Auto-update updated_at on changes
CREATE OR REPLACE FUNCTION update_vendor_updated_at()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = NOW();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_vendor_updated_at ON "Vendors";
CREATE TRIGGER trg_vendor_updated_at
  BEFORE UPDATE ON "Vendors"
  FOR EACH ROW
  EXECUTE FUNCTION update_vendor_updated_at();
