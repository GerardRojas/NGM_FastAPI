# ============================================================================
# Andrew Mismatch Reconciliation Protocol
# ============================================================================
# When Daneel detects a bill total mismatch (filename hint or Vision OCR vs
# sum of expenses), it tags @Andrew.  This service runs the reconciliation:
#
#   1. Download the receipt image from bills.receipt_url
#   2. GPT Vision extracts line-by-line items from the invoice
#   3. Compare OCR line items against DB expenses on the same bill
#   4. Identify: matched, amount mismatches, missing in DB, extra in DB
#   5. Propose or auto-apply corrections
#   6. Post detailed findings to the project chat
#
# Config keys (agent_config table, andrew_ prefix):
#   andrew_mismatch_enabled           - master switch (default: True)
#   andrew_mismatch_auto_correct      - auto-fix amounts (default: False)
#   andrew_mismatch_confidence_min    - min confidence to auto-correct (default: 85)
#   andrew_mismatch_amount_tolerance  - fractional tolerance for "match" (default: 0.02)
# ============================================================================

import logging
import os
import json
import re
import time
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone

import httpx

from api.helpers.andrew_messenger import post_andrew_message, ANDREW_BOT_USER_ID
from api.services.ocr_metrics import log_ocr_metric
from api.services.gpt_client import gpt

logger = logging.getLogger(__name__)

# ============================================================================
# Config defaults
# ============================================================================

_DEFAULT_CONFIG = {
    "andrew_mismatch_enabled": True,
    "andrew_mismatch_auto_correct": False,
    "andrew_mismatch_confidence_min": 85,
    "andrew_mismatch_amount_tolerance": 0.02,
}


def _load_mismatch_config() -> dict:
    """Read andrew_mismatch_* keys from agent_config."""
    try:
        from api.supabase_client import supabase
        result = supabase.table("agent_config") \
            .select("key, value") \
            .like("key", "andrew_mismatch_%") \
            .execute()
        cfg = dict(_DEFAULT_CONFIG)
        for row in (result.data or []):
            val = row["value"]
            if isinstance(val, str):
                try:
                    val = json.loads(val)
                except Exception:
                    pass
            cfg[row["key"]] = val
        return cfg
    except Exception as e:
        logger.error(f"[AndrewMismatch] Config load error: {e}")
        return dict(_DEFAULT_CONFIG)


# ============================================================================
# GPT Vision -- extract invoice line items
# ============================================================================

_VISION_LINE_ITEMS_PROMPT = (
    "You are a financial document reader specialized in construction invoices. "
    "Analyze this invoice/receipt image and extract EVERY line item.\n\n"
    "RESPOND with ONLY a JSON object:\n"
    "{\n"
    '  "vendor": "Vendor Name",\n'
    '  "invoice_number": "INV-123 or null",\n'
    '  "invoice_date": "YYYY-MM-DD or null",\n'
    '  "line_items": [\n'
    '    {"description": "Item description", "quantity": 1, "unit_price": 100.00, "amount": 100.00}\n'
    '  ],\n'
    '  "subtotal": 200.00,\n'
    '  "tax": 16.00,\n'
    '  "total": 216.00,\n'
    '  "confidence": 90\n'
    "}\n\n"
    "CRITICAL RULES:\n"
    "1. ALWAYS use the LINE TOTAL (extended amount), NOT the unit price. "
    "The line total is typically the RIGHTMOST dollar amount on each row.\n"
    "   Examples:\n"
    "   - 'QTY: 80, PRICE EACH: $1.84, EXTENSION: $147.20' -> amount = 147.20\n"
    "   - '2 x $5.00 = $10.00' -> amount = 10.00\n"
    "   - 'Widget (3 @ $25.00) ... $75.00' -> amount = 75.00\n"
    "   - 'Artisan Frost 512 pieces $1,479.68 ... $2.89/piece' -> amount = 1479.68\n"
    "2. Extract ALL line items from ALL sections/pages, not just a summary\n"
    "3. For multi-section invoices, use the AMOUNT column (rightmost), not Rate or Price\n"
    "4. 'subtotal' = sum of all line item amounts BEFORE tax\n"
    "5. 'tax' = the tax amount (Sales Tax, Tax, IVA, VAT, HST, GST). 0 if none\n"
    "6. 'total' = GRAND TOTAL / AMOUNT DUE / BALANCE DUE (final amount at the bottom)\n"
    "7. Fees (delivery, shipping, service, environmental) are separate line items\n"
    "8. DO NOT confuse store numbers, phone numbers, PO numbers, or SKUs with amounts\n"
    "9. SELF-CHECK: sum of line item amounts should equal subtotal (pre-tax). "
    "If it equals the grand total instead, your line items already include tax.\n"
    "10. If quantity or unit_price is unclear, set them to null but always provide 'amount'\n"
    "11. 'confidence' is 0-100 for overall extraction quality\n"
    "- No preamble, no markdown, just the JSON object"
)

_TEXT_LINE_ITEMS_PROMPT = (
    "You are a financial document reader specialized in construction invoices. "
    "Analyze this invoice/receipt text and extract EVERY line item.\n\n"
    "RESPOND with ONLY a JSON object:\n"
    "{\n"
    '  "vendor": "Vendor Name",\n'
    '  "invoice_number": "INV-123 or null",\n'
    '  "invoice_date": "YYYY-MM-DD or null",\n'
    '  "line_items": [\n'
    '    {"description": "Item description", "quantity": 1, "unit_price": 100.00, "amount": 100.00}\n'
    '  ],\n'
    '  "subtotal": 200.00,\n'
    '  "tax": 16.00,\n'
    '  "total": 216.00,\n'
    '  "confidence": 90\n'
    "}\n\n"
    "CRITICAL RULES:\n"
    "1. ALWAYS use the LINE TOTAL (extended amount), NOT the unit price. "
    "The line total is typically the RIGHTMOST dollar amount on each row.\n"
    "   Examples:\n"
    "   - 'QTY: 80, PRICE EACH: $1.84, EXTENSION: $147.20' -> amount = 147.20\n"
    "   - '2 x $5.00 = $10.00' -> amount = 10.00\n"
    "   - 'Widget (3 @ $25.00) ... $75.00' -> amount = 75.00\n"
    "   - 'Artisan Frost 512 pieces $1,479.68 ... $2.89/piece' -> amount = 1479.68\n"
    "2. Extract ALL line items from ALL sections, not just a summary\n"
    "3. For multi-section invoices, use the AMOUNT column (rightmost), not Rate or Price\n"
    "4. 'subtotal' = sum of all line item amounts BEFORE tax\n"
    "5. 'tax' = the tax amount (Sales Tax, Tax, IVA, VAT, HST, GST). 0 if none\n"
    "6. 'total' = GRAND TOTAL / AMOUNT DUE / BALANCE DUE (final amount)\n"
    "7. Fees (delivery, shipping, service, environmental) are separate line items\n"
    "8. DO NOT confuse store numbers, phone numbers, PO numbers, or SKUs with amounts\n"
    "9. SELF-CHECK: sum of line item amounts should equal subtotal (pre-tax). "
    "If it equals the grand total instead, your line items already include tax.\n"
    "10. If quantity or unit_price is unclear, set them to null but always provide 'amount'\n"
    "11. 'confidence' is 0-100 for overall extraction quality\n"
    "- No preamble, no markdown, just the JSON object\n\n"
    "--- RECEIPT TEXT ---\n{text}\n--- END ---"
)


def _download_receipt(receipt_url: str) -> Optional[tuple]:
    """Download receipt, return (file_content, content_type) or None."""
    if not receipt_url:
        return None
    try:
        with httpx.Client(timeout=15.0) as client:
            resp = client.get(receipt_url, follow_redirects=True)
            if resp.status_code != 200:
                logger.warning(f"[AndrewMismatch] Download failed {resp.status_code}")
                return None
            return resp.content, resp.headers.get("content-type", "")
    except Exception as e:
        logger.warning(f"[AndrewMismatch] Download error: {e}")
        return None


def _encode_to_vision_image(file_content: bytes, content_type: str, receipt_url: str) -> Optional[tuple]:
    """Convert file bytes to (b64_image, media_type) for Vision API, or None."""
    import base64
    is_pdf = "pdf" in content_type.lower() or receipt_url.lower().endswith(".pdf")

    if is_pdf:
        try:
            from pdf2image import convert_from_bytes
            import io
            import platform
            poppler_path = r'C:\poppler\poppler-24.08.0\Library\bin' if platform.system() == "Windows" else None
            images = convert_from_bytes(file_content, dpi=250, first_page=1, last_page=1,
                                        poppler_path=poppler_path)
            if not images:
                return None
            buf = io.BytesIO()
            images[0].save(buf, format='PNG')
            buf.seek(0)
            return base64.b64encode(buf.getvalue()).decode('utf-8'), "image/png"
        except Exception as e:
            logger.warning(f"[AndrewMismatch] PDF convert error: {e}")
            return None
    else:
        return base64.b64encode(file_content).decode('utf-8'), content_type or "image/jpeg"


def extract_invoice_line_items(receipt_url: str) -> Optional[dict]:
    """
    Extract detailed line items from an invoice.
    Tries pdfplumber text extraction first for PDFs, falls back to GPT-4o Vision.
    Returns parsed dict or None on failure.
    """
    t0 = time.monotonic()
    downloaded = _download_receipt(receipt_url)
    if not downloaded:
        return None

    file_content, content_type = downloaded
    is_pdf = "pdf" in content_type.lower() or receipt_url.lower().endswith(".pdf")

    # Try pdfplumber text extraction first for PDFs
    extracted_text = None
    if is_pdf:
        try:
            from services.receipt_scanner import extract_text_from_pdf
            success, text = extract_text_from_pdf(file_content)
            if success:
                extracted_text = text
                logger.info(f"[AndrewMismatch] pdfplumber: extracted {len(text)} chars")
        except Exception as e:
            logger.info(f"[AndrewMismatch] pdfplumber unavailable: {e}")

    try:
        if extracted_text:
            # Text mode (pdfplumber succeeded) -- mini w/ fallback to heavy
            prompt = _TEXT_LINE_ITEMS_PROMPT.format(text=extracted_text)
            raw = gpt.mini(prompt, "Analyze this invoice.", max_tokens=2000)
            # Confidence fallback: escalate to heavy if mini < 60%
            if raw:
                try:
                    _t = raw
                    if _t.startswith("```"):
                        _t = _t.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
                    _conf = int(json.loads(_t).get("confidence", 0))
                    if _conf < 60:
                        logger.info("[AndrewMismatch] mini confidence %d%% < 60%%, escalating to heavy", _conf)
                        raw = None
                except (ValueError, json.JSONDecodeError, TypeError):
                    pass
            if not raw:
                raw = gpt.heavy(prompt, "Analyze this invoice.", temperature=0.1, max_tokens=2000)
        else:
            # Vision mode (fallback) -- gpt-5.2
            encoded = _encode_to_vision_image(file_content, content_type, receipt_url)
            if not encoded:
                return None
            b64_image, media_type = encoded
            raw = gpt.heavy(
                system=_VISION_LINE_ITEMS_PROMPT,
                user=[{"type": "image_url", "image_url": {
                    "url": f"data:{media_type};base64,{b64_image}",
                    "detail": "high"
                }}],
                temperature=0.1,
                max_tokens=2000,
            )

        if not raw:
            logger.info("[AndrewMismatch] GPT returned empty response")
            return None
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        data = json.loads(raw)
        data["_extraction_method"] = "pdfplumber" if extracted_text else "vision"

        confidence = int(data.get("confidence", 0))
        if confidence < 30:
            logger.info(f"[AndrewMismatch] Confidence too low ({confidence}%)")
            return None

        # Cross-validate: line items sum vs total/subtotal
        line_items = data.get("line_items", [])
        ocr_total = float(data.get("total", 0))
        ocr_subtotal = float(data.get("subtotal", 0)) or ocr_total
        ocr_tax = float(data.get("tax", 0))
        items_sum = sum(float(item.get("amount") or 0) for item in line_items)

        if items_sum > 0 and ocr_total > 0:
            gap_total = abs(items_sum - ocr_total)
            gap_subtotal = abs(items_sum - ocr_subtotal)

            if gap_total / ocr_total <= 0.02:
                data["_line_items_match"] = "total"
            elif ocr_subtotal != ocr_total and ocr_subtotal > 0 and gap_subtotal / ocr_subtotal <= 0.02:
                data["_line_items_match"] = "subtotal"
            else:
                data["_line_items_match"] = "inconsistent"
                data["confidence"] = max(0, confidence - 10)
                logger.info(
                    f"[AndrewMismatch] Cross-validation: items_sum=${items_sum:,.2f} "
                    f"vs total=${ocr_total:,.2f} / subtotal=${ocr_subtotal:,.2f} -- inconsistent"
                )

        extract_ms = int((time.monotonic() - t0) * 1000)
        logger.info(
            "[AndrewMismatch] Extracted %d line items via %s in %dms | "
            "subtotal=$%.2f tax=$%.2f total=$%.2f (confidence=%d%%)",
            len(line_items), 'pdfplumber+gpt' if extracted_text else 'vision',
            extract_ms, ocr_subtotal, ocr_tax, ocr_total,
            data.get('confidence', confidence)
        )
        log_ocr_metric(
            agent="andrew",
            source="mismatch_reconciliation",
            extraction_method=data["_extraction_method"],
            model_used="gpt-5-mini" if extracted_text else "gpt-5.2",
            file_type="application/pdf" if is_pdf else (content_type or "unknown"),
            char_count=len(extracted_text) if extracted_text else None,
            success=True,
            confidence=data.get("confidence", confidence),
            items_count=len(line_items),
            tax_detected=ocr_tax > 0,
            total_match_type=data.get("_line_items_match"),
            receipt_url=receipt_url,
        )
        return data
    except Exception as e:
        logger.warning(f"[AndrewMismatch] Extract failed: {e}")
        log_ocr_metric(
            agent="andrew",
            source="mismatch_reconciliation",
            extraction_method="error",
            success=False,
            receipt_url=receipt_url,
        )
        return None


# ============================================================================
# Matching engine -- compare OCR items vs DB expenses
# ============================================================================

def _fuzzy_match_description(ocr_desc: str, db_desc: str) -> float:
    """Simple word-overlap similarity (0-100)."""
    if not ocr_desc or not db_desc:
        return 0.0
    a_words = set(re.sub(r'[^\w\s]', '', ocr_desc.lower()).split())
    b_words = set(re.sub(r'[^\w\s]', '', db_desc.lower()).split())
    if not a_words or not b_words:
        return 0.0
    overlap = len(a_words & b_words)
    total = max(len(a_words), len(b_words))
    return round(overlap / total * 100, 1)


def reconcile_bill(
    ocr_data: dict,
    db_expenses: List[dict],
    amount_tolerance: float = 0.02,
) -> dict:
    """
    Match OCR line items against DB expenses.

    Returns:
        {
            "matched": [{"ocr_item": {...}, "db_expense": {...}, "amount_diff": 0.0}],
            "amount_mismatches": [{"ocr_item": {...}, "db_expense": {...}, "ocr_amount": X, "db_amount": Y}],
            "missing_in_db": [{"ocr_item": {...}}],   -- on invoice but not in DB
            "extra_in_db": [{"db_expense": {...}}],     -- in DB but not on invoice
            "ocr_total": float,
            "db_total": float,
            "confidence": int,
        }
    """
    ocr_items = ocr_data.get("line_items", [])
    ocr_total = float(ocr_data.get("total", 0))
    ocr_subtotal = float(ocr_data.get("subtotal", 0)) or ocr_total
    ocr_tax = float(ocr_data.get("tax", 0))
    confidence = int(ocr_data.get("confidence", 0))

    db_total = sum(float(e.get("Amount") or 0) for e in db_expenses)
    n_items = len(db_expenses)

    # Cross-validate OCR line items sum
    ocr_line_items_sum = sum(float(item.get("amount") or 0) for item in ocr_items)
    line_items_consistency = ocr_data.get("_line_items_match", "unknown")

    # Two-tier total comparison
    rounding_tolerance = n_items * 0.01
    larger = max(abs(ocr_total), abs(db_total)) if (ocr_total or db_total) else 1
    pct_tolerance = larger * amount_tolerance
    abs_tolerance = max(pct_tolerance, rounding_tolerance)

    total_diff = abs(ocr_total - db_total)
    subtotal_diff = abs(ocr_subtotal - db_total) if ocr_subtotal != ocr_total else float('inf')

    total_match_type = "none"
    if total_diff <= abs_tolerance:
        total_match_type = "total"
    elif subtotal_diff < float('inf') and subtotal_diff <= abs_tolerance:
        total_match_type = "subtotal"

    matched = []
    amount_mismatches = []
    missing_in_db = []

    # Track which DB expenses have been matched
    db_used = set()

    for ocr_item in ocr_items:
        ocr_amount = float(ocr_item.get("amount") or 0)
        ocr_desc = ocr_item.get("description", "")
        best_match = None
        best_score = -1

        for i, db_exp in enumerate(db_expenses):
            if i in db_used:
                continue
            db_amount = float(db_exp.get("Amount") or 0)

            # Amount proximity score (0-50 points)
            if ocr_amount == 0 and db_amount == 0:
                amt_score = 50
            elif ocr_amount == 0 or db_amount == 0:
                amt_score = 0
            else:
                larger = max(abs(ocr_amount), abs(db_amount))
                diff_pct = abs(ocr_amount - db_amount) / larger if larger > 0 else 0
                amt_score = max(0, 50 * (1 - diff_pct / 0.5))  # 0% diff = 50, 50% diff = 0

            # Description similarity score (0-50 points)
            db_desc = db_exp.get("LineDescription", "")
            desc_score = _fuzzy_match_description(ocr_desc, db_desc) * 0.5

            total_score = amt_score + desc_score
            if total_score > best_score:
                best_score = total_score
                best_match = (i, db_exp)

        if best_match and best_score >= 30:
            idx, db_exp = best_match
            db_used.add(idx)
            db_amount = float(db_exp.get("Amount") or 0)
            diff = abs(ocr_amount - db_amount)
            larger = max(abs(ocr_amount), abs(db_amount)) if (ocr_amount or db_amount) else 1
            within_tolerance = diff <= larger * amount_tolerance

            if within_tolerance:
                matched.append({
                    "ocr_item": ocr_item,
                    "db_expense": _slim_expense(db_exp),
                    "amount_diff": round(diff, 2),
                })
            else:
                amount_mismatches.append({
                    "ocr_item": ocr_item,
                    "db_expense": _slim_expense(db_exp),
                    "ocr_amount": ocr_amount,
                    "db_amount": db_amount,
                    "diff": round(ocr_amount - db_amount, 2),
                })
        else:
            missing_in_db.append({"ocr_item": ocr_item})

    # DB expenses not matched to any OCR item
    extra_in_db = []
    for i, db_exp in enumerate(db_expenses):
        if i not in db_used:
            extra_in_db.append({"db_expense": _slim_expense(db_exp)})

    return {
        "matched": matched,
        "amount_mismatches": amount_mismatches,
        "missing_in_db": missing_in_db,
        "extra_in_db": extra_in_db,
        "ocr_total": ocr_total,
        "ocr_subtotal": ocr_subtotal,
        "ocr_tax": ocr_tax,
        "ocr_line_items_sum": round(ocr_line_items_sum, 2),
        "line_items_consistency": line_items_consistency,
        "db_total": round(db_total, 2),
        "n_items": n_items,
        "total_match_type": total_match_type,
        "confidence": confidence,
    }


def _slim_expense(exp: dict) -> dict:
    """Compact representation of a DB expense for reporting."""
    return {
        "expense_id": exp.get("expense_id") or exp.get("id"),
        "amount": float(exp.get("Amount") or 0),
        "date": (exp.get("TxnDate") or "")[:10],
        "description": (exp.get("LineDescription") or "")[:60],
        "vendor_id": exp.get("vendor_id"),
    }


# ============================================================================
# Auto-correct engine
# ============================================================================

def apply_corrections(
    mismatches: List[dict],
    confidence: int,
    cfg: dict,
) -> List[dict]:
    """
    Auto-correct expense amounts where OCR confidence is high enough.
    Returns list of corrections applied: [{expense_id, old_amount, new_amount}].
    """
    if not cfg.get("andrew_mismatch_auto_correct", False):
        return []

    min_conf = int(cfg.get("andrew_mismatch_confidence_min", 85))
    if confidence < min_conf:
        logger.info(
            f"[AndrewMismatch] Skipping auto-correct: confidence {confidence}% < {min_conf}%"
        )
        return []

    from api.supabase_client import supabase
    corrections = []

    for m in mismatches:
        exp_id = m["db_expense"]["expense_id"]
        old_amount = m["db_amount"]
        new_amount = m["ocr_amount"]

        try:
            supabase.table("expenses_manual_COGS") \
                .update({"Amount": new_amount}) \
                .eq("expense_id", exp_id) \
                .execute()

            corrections.append({
                "expense_id": exp_id,
                "old_amount": old_amount,
                "new_amount": new_amount,
            })
            logger.info(
                f"[AndrewMismatch] Corrected {exp_id}: "
                f"${old_amount:,.2f} -> ${new_amount:,.2f}"
            )
        except Exception as e:
            logger.error(f"[AndrewMismatch] Correction failed for {exp_id}: {e}")

    return corrections


# ============================================================================
# Message builder
# ============================================================================

_RECONCILIATION_OPENERS = [
    "Hey Daneel, I ran through the invoice and here's what I found.",
    "Got it -- I re-scanned the bill and compared it line by line. Here's the breakdown.",
    "Finished reviewing the bill. Here's the reconciliation report.",
    "Alright, I've gone through the numbers. Here's what doesn't add up.",
    "Took a close look at this one. Here's the full reconciliation.",
]


def _build_reconciliation_message(
    bill_id: str,
    result: dict,
    corrections: List[dict],
    vendor_names: dict,
) -> str:
    """Build the reconciliation report message."""
    import random

    ocr_total = result["ocr_total"]
    ocr_subtotal = result.get("ocr_subtotal", ocr_total)
    ocr_tax = result.get("ocr_tax", 0)
    db_total = result["db_total"]
    total_match_type = result.get("total_match_type", "none")
    line_items_consistency = result.get("line_items_consistency", "unknown")

    lines = [
        random.choice(_RECONCILIATION_OPENERS),
        "",
        f"**Bill #{bill_id} -- Reconciliation Report**",
        "",
    ]

    # Enhanced comparison table with subtotal/tax/total
    if ocr_tax > 0 and ocr_subtotal != ocr_total:
        lines.extend([
            f"| | Invoice (OCR) | Database | Difference |",
            f"|--|--------------|----------|------------|",
            f"| **Subtotal** | ${ocr_subtotal:,.2f} | -- | -- |",
            f"| **Tax** | ${ocr_tax:,.2f} | -- | -- |",
            f"| **Total** | ${ocr_total:,.2f} | ${db_total:,.2f} | "
            f"${abs(ocr_total - db_total):,.2f} |",
            "",
        ])
    else:
        lines.extend([
            f"| | Invoice (OCR) | Database | Difference |",
            f"|--|--------------|----------|------------|",
            f"| **Total** | ${ocr_total:,.2f} | ${db_total:,.2f} | "
            f"${abs(ocr_total - db_total):,.2f} |",
            "",
        ])

    # Line items cross-validation note
    ocr_line_items_sum = result.get("ocr_line_items_sum", 0)
    if ocr_line_items_sum > 0:
        lines.append(
            f"OCR line items sum: ${ocr_line_items_sum:,.2f} "
            f"({line_items_consistency.replace('_', ' ')})"
        )
        lines.append("")

    # Tax-related diagnostic
    if total_match_type == "subtotal":
        lines.append(
            f"*Expenses sum (${db_total:,.2f}) matches the invoice subtotal "
            f"(${ocr_subtotal:,.2f}). Tax of ${ocr_tax:,.2f} may not be included "
            f"in the expense line items.*"
        )
        lines.append("")
    elif total_match_type == "total":
        lines.append("Totals match within tolerance.")
        lines.append("")

    # Matched items
    if result["matched"]:
        lines.append(f"**Matched items** ({len(result['matched'])})")
        lines.append("")
        for m in result["matched"][:10]:
            desc = (m["ocr_item"].get("description") or "")[:40]
            amt = f"${m['ocr_item'].get('amount', 0):,.2f}"
            lines.append(f"- {desc}: {amt}")
        if len(result["matched"]) > 10:
            lines.append(f"- ... and {len(result['matched']) - 10} more")
        lines.append("")

    # Amount mismatches
    if result["amount_mismatches"]:
        lines.append(f"**Amount mismatches** ({len(result['amount_mismatches'])})")
        lines.append("")
        lines.append("| Item | Invoice | Database | Diff |")
        lines.append("|------|---------|----------|------|")
        for m in result["amount_mismatches"][:10]:
            desc = (m["ocr_item"].get("description") or "")[:30]
            lines.append(
                f"| {desc} | ${m['ocr_amount']:,.2f} | ${m['db_amount']:,.2f} | "
                f"${m['diff']:+,.2f} |"
            )
        lines.append("")

    # Missing in DB (on invoice but not in expenses)
    if result["missing_in_db"]:
        lines.append(f"**On invoice but NOT in database** ({len(result['missing_in_db'])})")
        lines.append("")
        for m in result["missing_in_db"][:10]:
            desc = (m["ocr_item"].get("description") or "")[:40]
            amt = f"${m['ocr_item'].get('amount', 0):,.2f}"
            lines.append(f"- {desc}: {amt}")
        lines.append("")

    # Extra in DB (in expenses but not on invoice)
    if result["extra_in_db"]:
        lines.append(f"**In database but NOT on invoice** ({len(result['extra_in_db'])})")
        lines.append("")
        for m in result["extra_in_db"][:10]:
            db = m["db_expense"]
            vname = vendor_names.get(db.get("vendor_id"), "Unknown")
            lines.append(f"- {vname}: ${db['amount']:,.2f} ({db['date']})")
        lines.append("")

    # Corrections applied
    if corrections:
        lines.append(f"**Auto-corrected** ({len(corrections)} expenses)")
        lines.append("")
        for c in corrections:
            lines.append(
                f"- Expense {c['expense_id'][:8]}...: "
                f"${c['old_amount']:,.2f} -> ${c['new_amount']:,.2f}"
            )
        lines.append("")
    elif result["amount_mismatches"]:
        lines.append("Auto-correct is disabled. Please review and fix the amounts manually.")
        lines.append("")

    # Smart summary
    total_issues = (len(result["amount_mismatches"])
                    + len(result["missing_in_db"])
                    + len(result["extra_in_db"]))
    if total_issues == 0 and total_match_type != "none":
        if total_match_type == "subtotal":
            lines.append(
                "All line items match. The totals discrepancy is due to tax "
                "not being distributed into the expense amounts."
            )
        else:
            lines.append("All line items and totals match. No issues found.")
    elif total_issues == 0:
        lines.append(
            "All line items match individually, but the overall totals "
            "don't reconcile. This may be from tax, rounding, or a "
            "fee not captured as a line item."
        )
    else:
        lines.append(f"Found **{total_issues}** issue(s) that need attention.")

    return "\n".join(lines)


# ============================================================================
# Main orchestrator
# ============================================================================

def run_mismatch_reconciliation(
    bill_id: str,
    project_id: str,
    source: str = "daneel",
    expected_total: Optional[float] = None,
) -> dict:
    """
    Run the full mismatch reconciliation protocol for a bill.

    Args:
        bill_id: The bill_id string
        project_id: Project UUID
        source: Who triggered this ('daneel', 'manual')
        expected_total: The expected total from hint/vision (informational)

    Returns:
        Summary dict with reconciliation results.
    """
    t0 = time.monotonic()
    cfg = _load_mismatch_config()

    if not cfg.get("andrew_mismatch_enabled", True):
        return {"status": "disabled", "message": "Andrew mismatch protocol is disabled"}

    from api.supabase_client import supabase as sb
    logger.info("[AndrewMismatch] Starting reconciliation | bill=%s project=%s source=%s", bill_id, project_id, source)

    # 1. Get the bill and its receipt URL
    bill_result = sb.table("bills") \
        .select("bill_id, receipt_url, status") \
        .eq("bill_id", bill_id) \
        .execute()

    if not bill_result.data:
        return {"status": "error", "message": f"Bill {bill_id} not found"}

    bill = bill_result.data[0]
    receipt_url = bill.get("receipt_url")
    if not receipt_url:
        return {"status": "error", "message": f"Bill {bill_id} has no receipt URL"}

    # 2. Get all DB expenses for this bill in this project
    exp_result = sb.table("expenses_manual_COGS") \
        .select("*") \
        .eq("bill_id", bill_id) \
        .eq("project", project_id) \
        .execute()
    db_expenses = exp_result.data or []

    if not db_expenses:
        return {"status": "error", "message": f"No expenses found for bill {bill_id} in project"}

    # 3. Extract invoice line items via GPT Vision
    ocr_data = extract_invoice_line_items(receipt_url)
    if not ocr_data:
        # Post a message saying we couldn't read the receipt
        post_andrew_message(
            content=(
                f"I tried to reconcile bill #{bill_id} but couldn't extract the line items "
                f"from the receipt. The file may be too blurry or in an unsupported format. "
                f"Please review manually."
            ),
            project_id=project_id,
            channel_type="project_general",
            metadata={"type": "mismatch_reconciliation", "bill_id": bill_id, "status": "ocr_failed"},
        )
        return {"status": "ocr_failed", "message": "Could not extract line items from receipt"}

    # 4. Run the matching engine
    tolerance = float(cfg.get("andrew_mismatch_amount_tolerance", 0.02))
    result = reconcile_bill(ocr_data, db_expenses, amount_tolerance=tolerance)

    # 5. Auto-correct if enabled and confident
    corrections = apply_corrections(
        result["amount_mismatches"],
        result["confidence"],
        cfg,
    )

    # 6. Resolve vendor names for the message
    vendor_ids = set()
    for e in db_expenses:
        vid = e.get("vendor_id")
        if vid:
            vendor_ids.add(vid)
    vendor_names = {}
    if vendor_ids:
        try:
            vr = sb.table("Vendors").select("id, vendor_name").in_("id", list(vendor_ids)).execute()
            vendor_names = {v["id"]: v["vendor_name"] for v in (vr.data or [])}
        except Exception:
            pass

    # 7. Build and post the reconciliation message
    msg = _build_reconciliation_message(bill_id, result, corrections, vendor_names)
    post_andrew_message(
        content=msg,
        project_id=project_id,
        channel_type="project_general",
        metadata={
            "type": "mismatch_reconciliation",
            "bill_id": bill_id,
            "source": source,
            "matched": len(result["matched"]),
            "mismatches": len(result["amount_mismatches"]),
            "missing": len(result["missing_in_db"]),
            "extra": len(result["extra_in_db"]),
            "corrections": len(corrections),
        },
    )

    summary = {
        "status": "ok",
        "bill_id": bill_id,
        "ocr_total": result["ocr_total"],
        "ocr_subtotal": result.get("ocr_subtotal", result["ocr_total"]),
        "ocr_tax": result.get("ocr_tax", 0),
        "db_total": result["db_total"],
        "total_match_type": result.get("total_match_type", "none"),
        "line_items_consistency": result.get("line_items_consistency", "unknown"),
        "matched": len(result["matched"]),
        "amount_mismatches": len(result["amount_mismatches"]),
        "missing_in_db": len(result["missing_in_db"]),
        "extra_in_db": len(result["extra_in_db"]),
        "corrections_applied": len(corrections),
        "confidence": result["confidence"],
    }

    # Update the most recent OCR metric row with bill/project context
    try:
        from api.supabase_client import supabase as _sb
        # SELECT the latest row's id first, then UPDATE by id
        # (Supabase ignores .order/.limit on UPDATE operations)
        latest = _sb.table("ocr_metrics").select("id").eq(
            "agent", "andrew"
        ).eq("receipt_url", receipt_url).order(
            "created_at", desc=True
        ).limit(1).execute()
        if latest.data:
            _sb.table("ocr_metrics").update({
                "bill_id": bill_id,
                "project_id": project_id,
                "total_match_type": result.get("total_match_type", "none"),
                "items_count": result.get("n_items", len(db_expenses)),
            }).eq("id", latest.data[0]["id"]).execute()
    except Exception:
        pass

    elapsed_ms = int((time.monotonic() - t0) * 1000)
    logger.info("[AndrewMismatch] Reconciliation complete in %dms | matched=%d mismatches=%d missing=%d extra=%d confidence=%d%%",
                 elapsed_ms, summary["matched"], summary["amount_mismatches"],
                 summary["missing_in_db"], summary["extra_in_db"], summary["confidence"])
    return summary
