"""
Shared OCR metrics logger.
Inserts one row per scan into the `ocr_metrics` table.

Usage (from any service):
    from api.services.ocr_metrics import log_ocr_metric

    log_ocr_metric(
        agent="daneel",
        source="hint_review",
        extraction_method="pdfplumber",
        model_used="gpt-4o",
        scan_mode="heavy",
        file_type="application/pdf",
        processing_ms=342,
        success=True,
        confidence=95,
        items_count=5,
        tax_detected=True,
        total_match_type="total",
        bill_id="...",
        project_id="...",
    )
"""

import logging
import time
from typing import Optional
from contextlib import contextmanager

from api.supabase_client import supabase

logger = logging.getLogger(__name__)


def log_ocr_metric(
    agent: str,
    extraction_method: str,
    source: Optional[str] = None,
    model_used: Optional[str] = None,
    scan_mode: Optional[str] = None,
    file_type: Optional[str] = None,
    processing_ms: Optional[int] = None,
    char_count: Optional[int] = None,
    success: bool = True,
    confidence: Optional[int] = None,
    items_count: Optional[int] = None,
    tax_detected: Optional[bool] = None,
    total_match_type: Optional[str] = None,
    bill_id: Optional[str] = None,
    project_id: Optional[str] = None,
    receipt_url: Optional[str] = None,
    metadata: Optional[dict] = None,
):
    """Insert a single OCR metric row. Fire-and-forget (never raises)."""
    try:
        row = {
            "agent": agent,
            "extraction_method": extraction_method,
        }
        # Optional fields - only include if set
        if source is not None:
            row["source"] = source
        if model_used is not None:
            row["model_used"] = model_used
        if scan_mode is not None:
            row["scan_mode"] = scan_mode
        if file_type is not None:
            row["file_type"] = file_type
        if processing_ms is not None:
            row["processing_ms"] = processing_ms
        if char_count is not None:
            row["char_count"] = char_count
        row["success"] = success
        if confidence is not None:
            row["confidence"] = confidence
        if items_count is not None:
            row["items_count"] = items_count
        if tax_detected is not None:
            row["tax_detected"] = tax_detected
        if total_match_type is not None:
            row["total_match_type"] = total_match_type
        if bill_id is not None:
            row["bill_id"] = bill_id
        if project_id is not None:
            row["project_id"] = project_id
        if receipt_url is not None:
            row["receipt_url"] = receipt_url
        if metadata is not None:
            row["metadata"] = metadata

        supabase.table("ocr_metrics").insert(row).execute()
        logger.info(f"[OCR Metrics] {agent}/{extraction_method} logged")
    except Exception as exc:
        logger.warning(f"[OCR Metrics] Failed to log: {exc}")


@contextmanager
def ocr_timer():
    """Context manager that yields a dict you can read `elapsed_ms` from after the block."""
    t = {"elapsed_ms": 0}
    start = time.perf_counter()
    try:
        yield t
    finally:
        t["elapsed_ms"] = int((time.perf_counter() - start) * 1000)
