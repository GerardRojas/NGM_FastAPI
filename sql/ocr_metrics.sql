-- ============================================================
-- OCR Metrics Log
-- Tracks extraction method, performance, and results
-- across all 3 OCR flows: receipt_scanner, Daneel, Andrew
-- ============================================================

CREATE TABLE IF NOT EXISTS ocr_metrics (
    id              UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    created_at      TIMESTAMPTZ DEFAULT NOW(),

    -- Which agent / flow triggered the scan
    agent           TEXT NOT NULL,       -- 'receipt_scanner' | 'daneel' | 'andrew'
    source          TEXT,                -- 'human_parse' | 'agent_process' | 'correction' | 'hint_review' | 'mismatch_reconciliation'

    -- Extraction details
    extraction_method TEXT NOT NULL,     -- 'pdfplumber' | 'vision' | 'vision_direct' | 'correction'
    model_used      TEXT,                -- 'gpt-4o' | 'gpt-4o-mini' | null (pdfplumber-only)
    scan_mode       TEXT,                -- 'fast' | 'heavy'
    file_type       TEXT,                -- 'application/pdf' | 'image/jpeg' | etc.

    -- Performance
    processing_ms   INT,                 -- total wall-clock time in milliseconds
    char_count      INT,                 -- characters extracted by pdfplumber (null if vision)

    -- Results
    success         BOOLEAN DEFAULT TRUE,
    confidence      INT,                 -- 0-100 from OCR response
    items_count     INT,                 -- number of line items / expenses found
    tax_detected    BOOLEAN,             -- whether tax > 0 was found
    total_match_type TEXT,               -- 'total' | 'subtotal' | 'mismatch' | null

    -- Context
    bill_id         UUID,
    project_id      UUID,
    receipt_url     TEXT,

    -- Catch-all for future fields
    metadata        JSONB DEFAULT '{}'
);

-- Indexes for dashboard queries
CREATE INDEX idx_ocr_metrics_created    ON ocr_metrics (created_at DESC);
CREATE INDEX idx_ocr_metrics_agent      ON ocr_metrics (agent);
CREATE INDEX idx_ocr_metrics_method     ON ocr_metrics (extraction_method);
CREATE INDEX idx_ocr_metrics_project    ON ocr_metrics (project_id);

-- RLS: service role can read/write, authenticated users can read
ALTER TABLE ocr_metrics ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Service role full access" ON ocr_metrics
    FOR ALL USING (auth.role() = 'service_role');

CREATE POLICY "Authenticated users can read" ON ocr_metrics
    FOR SELECT USING (auth.role() = 'authenticated');
