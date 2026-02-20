# ============================================================================
# NGM Hub - Daneel Auto-Authorization Service
# ============================================================================
# Rule-based engine that auto-authorizes new pending expenses that pass
# health check and duplicate verification.  Zero LLM tokens for routine
# operations -- only file-hash comparison (HTTP HEAD) is used for the rare
# case of same-date / different-bill duplicates.
#
# Run modes:
# 1. Event-driven: BackgroundTask after expense create / update
# 2. Manual trigger: POST /daneel/auto-auth/run
# ============================================================================

import logging
import re
import os
import time
from typing import Dict, Any, List, Optional
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx
from supabase import create_client, Client

from api.helpers.daneel_messenger import post_daneel_message, DANEEL_BOT_USER_ID
from api.services.ocr_metrics import log_ocr_metric
from api.services.gpt_client import gpt

logger = logging.getLogger(__name__)

# ============================================================================
# Supabase client
# ============================================================================

def _get_supabase() -> Client:
    from api.supabase_client import supabase
    return supabase


# ============================================================================
# Data classes
# ============================================================================

@dataclass
class DuplicateResult:
    verdict: str   # 'duplicate', 'not_duplicate', 'ambiguous', 'need_info'
    rule: str      # e.g. 'R3', 'R5'
    details: str   # human-readable explanation
    paired_expense_id: Optional[str] = None


# ============================================================================
# Config
# ============================================================================

_DEFAULT_CONFIG = {
    "daneel_auto_auth_enabled": False,
    "daneel_auto_auth_require_bill": True,
    "daneel_auto_auth_require_receipt": True,
    "daneel_fuzzy_threshold": 85,
    "daneel_amount_tolerance": 0.05,
    "daneel_labor_keywords": "labor",
    "daneel_bookkeeping_role": None,
    "daneel_accounting_mgr_role": None,
    "daneel_bookkeeping_users": "[]",
    "daneel_accounting_mgr_users": "[]",
    "daneel_auto_auth_last_run": None,
    "daneel_gpt_fallback_enabled": False,
    "daneel_gpt_fallback_confidence": 75,
    "daneel_mismatch_notify_andrew": True,
    "daneel_receipt_hash_check_enabled": True,
    "daneel_digest_enabled": True,
    "daneel_digest_interval_hours": 4,
}


def load_auto_auth_config() -> dict:
    """Read daneel_* keys from agent_config table."""
    try:
        sb = _get_supabase()
        result = sb.table("agent_config").select("key, value").like("key", "daneel_%").execute()
        cfg = dict(_DEFAULT_CONFIG)
        for row in (result.data or []):
            val = row["value"]
            # JSONB values come back as native Python types
            if isinstance(val, str):
                try:
                    import json
                    val = json.loads(val)
                except Exception as _exc:
                    logger.debug("Suppressed JSON parse: %s", _exc)
            cfg[row["key"]] = val
        return cfg
    except Exception as e:
        logger.error(f"[DaneelAutoAuth] Config load error: {e}")
        return dict(_DEFAULT_CONFIG)


def _save_config_key(key: str, value):
    """Persist a single config key."""
    import json
    try:
        sb = _get_supabase()
        now = datetime.now(timezone.utc).isoformat()
        json_val = value if isinstance(value, str) else json.dumps(value)
        # Explicit SELECT + UPDATE/INSERT (upsert can silently no-op)
        existing = sb.table("agent_config") \
            .select("key") \
            .eq("key", key) \
            .execute()
        if existing.data:
            sb.table("agent_config") \
                .update({"value": json_val, "updated_at": now}) \
                .eq("key", key) \
                .execute()
        else:
            sb.table("agent_config") \
                .insert({"key": key, "value": json_val, "updated_at": now}) \
                .execute()
    except Exception as e:
        logger.error(f"[DaneelAutoAuth] Config save error for {key}: {e}")


# ============================================================================
# Lookup helpers  (resolve UUIDs to names, cached per run)
# ============================================================================

def _load_lookups(sb) -> dict:
    """Load accounts, payment_methods, vendors into dicts keyed by UUID."""
    lookups = {"accounts": {}, "payment_methods": {}, "vendors": {}}
    try:
        accts = sb.table("accounts").select("account_id, Name").execute()
        for a in (accts.data or []):
            lookups["accounts"][a["account_id"]] = a["Name"]
    except Exception as e:
        logger.error(f"[DaneelAutoAuth] accounts lookup error: {e}")

    try:
        pms = sb.table("paymet_methods").select("id, payment_method_name").execute()
        for p in (pms.data or []):
            lookups["payment_methods"][p["id"]] = p["payment_method_name"]
    except Exception as e:
        logger.error(f"[DaneelAutoAuth] payment_methods lookup error: {e}")

    try:
        vends = sb.table("Vendors").select("id, vendor_name").execute()
        for v in (vends.data or []):
            lookups["vendors"][v["id"]] = v["vendor_name"]
    except Exception as e:
        logger.error(f"[DaneelAutoAuth] vendors lookup error: {e}")

    return lookups


# ============================================================================
# String helpers (ported from expenses.js)
# ============================================================================

def levenshtein_distance(s1: str, s2: str) -> int:
    if not s1:
        return len(s2) if s2 else 0
    if not s2:
        return len(s1)
    m, n = len(s1), len(s2)
    prev = list(range(n + 1))
    curr = [0] * (n + 1)
    for i in range(1, m + 1):
        curr[0] = i
        for j in range(1, n + 1):
            cost = 0 if s1[i - 1] == s2[j - 1] else 1
            curr[j] = min(prev[j] + 1, curr[j - 1] + 1, prev[j - 1] + cost)
        prev, curr = curr, prev
    return prev[n]


def string_similarity(s1: str, s2: str) -> float:
    """Returns 0-100 similarity percentage."""
    a = (s1 or "").strip().lower()
    b = (s2 or "").strip().lower()
    if not a and not b:
        return 100.0
    if not a or not b:
        return 0.0
    max_len = max(len(a), len(b))
    dist = levenshtein_distance(a, b)
    return round((1 - dist / max_len) * 100, 1)


def normalize_bill_id(bill_id: str) -> str:
    if not bill_id:
        return ""
    return re.sub(r"[^A-Z0-9]", "", bill_id.upper())


def _get_bill_siblings(norm_bill: str, bills_map: dict, receipt_groups: dict) -> set:
    """Return set of normalized bill_ids sharing the same receipt as *norm_bill*.

    Enables receipt-URL fallback grouping: if two different bill_id strings
    point to the same PDF, expenses on both are treated as one bill.
    """
    bill_data = bills_map.get(norm_bill)
    if not bill_data:
        return {norm_bill}
    url = (bill_data.get("receipt_url") or "").strip()
    if url and url in receipt_groups:
        return receipt_groups[url]
    return {norm_bill}


# ============================================================================
# Receipt hash (lightweight -- HTTP HEAD only, no file download)
# ============================================================================

def get_receipt_hash(receipt_url: Optional[str], client: Optional[httpx.Client] = None) -> Optional[str]:
    """
    Get a lightweight fingerprint for a receipt file via HTTP HEAD.
    Uses ETag or Content-Length as proxy.  Returns None if unavailable.
    Pass a shared ``client`` to reuse TCP connections across calls.
    """
    if not receipt_url:
        return None
    try:
        _own_client = client is None
        _client = client or httpx.Client(timeout=5.0)
        try:
            resp = _client.head(receipt_url, follow_redirects=True)
            etag = resp.headers.get("etag")
            if etag:
                return f"etag:{etag}"
            cl = resp.headers.get("content-length")
            ct = resp.headers.get("content-type", "")
            if cl:
                return f"cl:{cl}:{ct}"
        finally:
            if _own_client:
                _client.close()
    except Exception as e:
        logger.warning(f"[DaneelAutoAuth] HEAD failed for {receipt_url}: {e}")
    return None


# ============================================================================
# GPT Vision -- extract invoice total from receipt image
# ============================================================================

_VISION_BILL_TOTAL_PROMPT = (
    "You are a financial document reader. Look at this invoice/receipt image "
    "and extract the billing amounts.\n\n"
    "RESPOND with ONLY a JSON object:\n"
    '{"subtotal": 990.50, "tax": 57.55, "total": 1048.05, "currency": "USD", "confidence": 95}\n\n'
    "Rules:\n"
    '- "total" is the GRAND TOTAL / AMOUNT DUE / BALANCE DUE (final amount including everything)\n'
    '- "subtotal" is the sum of line items BEFORE tax (often labeled Subtotal, Merchandise Total, etc.)\n'
    '- "tax" is the tax amount (Sales Tax, Tax, IVA, VAT, HST, GST). Set to 0 if no tax line visible\n'
    '- If the document only shows a single total with no subtotal/tax breakdown, '
    'set "subtotal" equal to "total" and "tax" to 0\n'
    "- All values must be numbers (no dollar signs, no commas)\n"
    '- If you see multiple totals, use the GRAND TOTAL / AMOUNT DUE / BALANCE DUE\n'
    "- Look for totals in the BOTTOM section of the document\n"
    "- DO NOT confuse store numbers, phone numbers, PO numbers, SKUs, "
    "or product codes with monetary amounts\n"
    '- "confidence" is 0-100 how sure you are about the extracted values\n'
    "- If you cannot read the amounts clearly, set confidence below 50\n"
    "- No preamble, no markdown, just the JSON object"
)

_TEXT_BILL_TOTAL_PROMPT = (
    "You are a financial document reader. Extract the billing totals from this "
    "invoice/receipt text.\n\n"
    "RESPOND with ONLY a JSON object:\n"
    '{"subtotal": 990.50, "tax": 57.55, "total": 1048.05, "currency": "USD", "confidence": 95}\n\n'
    "Rules:\n"
    '- "total" is the GRAND TOTAL / AMOUNT DUE / BALANCE DUE (final amount including everything)\n'
    '- "subtotal" is the sum of line items BEFORE tax (Subtotal, Merchandise Total, etc.)\n'
    '- "tax" is the tax amount (Sales Tax, Tax, IVA, VAT, HST, GST). Set to 0 if none visible\n'
    '- If only a single total with no breakdown, set "subtotal" equal to "total" and "tax" to 0\n'
    "- All values must be numbers (no dollar signs, no commas)\n"
    "- Look for totals in the BOTTOM section of the document\n"
    "- DO NOT confuse store numbers, phone numbers, PO numbers, SKUs, "
    "or product codes with monetary amounts\n"
    '- "confidence" is 0-100\n'
    "- No preamble, no markdown, just the JSON object\n\n"
    "--- RECEIPT TEXT ---\n{text}\n--- END ---"
)


def gpt_vision_extract_bill_total(receipt_url: str, amount_tolerance: float = 0.05) -> Optional[dict]:
    """
    Download a receipt and extract invoice totals.
    Tries pdfplumber text extraction first for PDFs, falls back to GPT Vision.
    Returns dict with {total, subtotal, tax, currency, confidence} or None on failure.
    """
    if not receipt_url:
        return None

    import json as _json
    import base64

    try:
        # 1. Download the receipt file
        with httpx.Client(timeout=15.0) as client:
            resp = client.get(receipt_url, follow_redirects=True)
            if resp.status_code != 200:
                logger.warning(f"[DaneelAutoAuth] Vision: download failed {resp.status_code}")
                return None
            file_content = resp.content
            content_type = resp.headers.get("content-type", "")

        is_pdf = "pdf" in content_type.lower() or receipt_url.lower().endswith(".pdf")

        # 2. Try pdfplumber text extraction first for PDFs
        extracted_text = None
        if is_pdf:
            try:
                from services.receipt_scanner import extract_text_from_pdf
                success, text = extract_text_from_pdf(file_content)
                if success:
                    extracted_text = text
                    logger.info(f"[DaneelAutoAuth] pdfplumber: extracted {len(text)} chars")
            except Exception as e:
                logger.info(f"[DaneelAutoAuth] pdfplumber unavailable: {e}")

        if extracted_text:
            # 3a. Text mode (pdfplumber succeeded) -- mini w/ fallback to heavy
            prompt = _TEXT_BILL_TOTAL_PROMPT.format(text=extracted_text)
            raw = gpt.mini(prompt, "Analyze this bill.", max_tokens=200)
            # Confidence fallback: escalate to heavy if mini < 75%
            if raw:
                try:
                    _t = raw
                    if _t.startswith("```"):
                        _t = _t.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
                    _conf = int(_json.loads(_t).get("confidence", 0))
                    if _conf < 75:
                        logger.info("[DaneelAutoAuth] mini confidence %d%% < 75%%, escalating to heavy", _conf)
                        raw = None
                except (ValueError, _json.JSONDecodeError, TypeError):
                    pass
            if not raw:
                raw = gpt.heavy(prompt, "Analyze this bill.", temperature=0.1, max_tokens=200)
        else:
            # 3b. Vision mode (fallback) -- convert to image
            if is_pdf:
                try:
                    from pdf2image import convert_from_bytes
                    import io
                    import platform
                    poppler_path = r'C:\poppler\poppler-24.08.0\Library\bin' if platform.system() == "Windows" else None
                    images = convert_from_bytes(file_content, dpi=250, first_page=1, last_page=1,
                                                poppler_path=poppler_path)
                    if not images:
                        logger.warning("[DaneelAutoAuth] Vision: PDF conversion produced no images")
                        return None
                    buf = io.BytesIO()
                    images[0].save(buf, format='PNG')
                    buf.seek(0)
                    b64_image = base64.b64encode(buf.getvalue()).decode('utf-8')
                    media_type = "image/png"
                    buf.close()
                    del buf, images  # free PIL + BytesIO before GPT call
                except Exception as e:
                    logger.warning(f"[DaneelAutoAuth] Vision: PDF convert error: {e}")
                    return None
            else:
                b64_image = base64.b64encode(file_content).decode('utf-8')
                media_type = content_type if content_type else "image/jpeg"

            del file_content  # free download buffer before GPT call

            raw = gpt.heavy(
                system=_VISION_BILL_TOTAL_PROMPT,
                user=[{"type": "image_url", "image_url": {
                    "url": f"data:{media_type};base64,{b64_image}",
                    "detail": "high"
                }}],
                temperature=0.1,
                max_tokens=200,
            )

        if not raw:
            logger.info("[DaneelAutoAuth] GPT returned empty response")
            return None

        # 4. Parse response
        # Strip markdown code fences if present
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        data = _json.loads(raw)
        total = float(data.get("total", 0))
        subtotal = float(data.get("subtotal", 0))
        tax = float(data.get("tax", 0))
        confidence = int(data.get("confidence", 0))

        if total <= 0 or confidence < 50:
            logger.info(f"[DaneelAutoAuth] Vision: low confidence ({confidence}%) or zero total")
            return None

        # Self-validation: subtotal + tax should approximate total
        if subtotal > 0 and tax >= 0:
            computed = subtotal + tax
            gap = abs(computed - total)
            if total > 0 and gap / total <= 0.01:
                confidence = min(100, confidence + 5)
            elif total > 0 and gap / total > 0.05:
                confidence = max(0, confidence - 15)
                logger.info(
                    f"[DaneelAutoAuth] Vision: self-validation gap "
                    f"(subtotal={subtotal} + tax={tax} = {computed} vs total={total}), "
                    f"confidence lowered to {confidence}%"
                )

        if subtotal <= 0:
            subtotal = total

        _extraction_method = "pdfplumber" if extracted_text else "vision"
        result = {
            "total": total,
            "subtotal": subtotal,
            "tax": tax,
            "currency": data.get("currency", "USD"),
            "confidence": confidence,
        }
        logger.info(
            f"[DaneelAutoAuth] Vision: subtotal=${subtotal:,.2f} tax=${tax:,.2f} "
            f"total=${total:,.2f} (confidence={confidence}%)"
        )
        log_ocr_metric(
            agent="daneel",
            source="hint_review",
            extraction_method=_extraction_method,
            model_used="gpt-5-mini" if extracted_text else "gpt-5.2",
            file_type="application/pdf" if is_pdf else content_type,
            char_count=len(extracted_text) if extracted_text else None,
            success=True,
            confidence=confidence,
            tax_detected=tax > 0,
            receipt_url=receipt_url,
        )
        return result

    except Exception as e:
        logger.warning(f"[DaneelAutoAuth] Vision extract failed: {e}")
        log_ocr_metric(
            agent="daneel",
            source="hint_review",
            extraction_method="error",
            success=False,
            receipt_url=receipt_url,
        )
        return None


# ============================================================================
# Health Check
# ============================================================================

def run_health_check(expense: dict, config: dict, bills_map: dict) -> List[str]:
    """
    Returns list of missing field names.  Empty = healthy.
    Does NOT block processing -- caller collects and continues.
    """
    missing = []

    if not expense.get("vendor_id"):
        missing.append("vendor")
    if not expense.get("Amount") and expense.get("Amount") != 0:
        missing.append("amount")
    if not expense.get("TxnDate"):
        missing.append("date")
    if not expense.get("account_id"):
        missing.append("account")

    if config.get("daneel_auto_auth_require_bill", True):
        bill = (expense.get("bill_id") or "").strip()
        if not bill:
            missing.append("bill_id")

    if config.get("daneel_auto_auth_require_receipt", True):
        receipt = _get_expense_receipt(expense, bills_map)
        if not receipt:
            missing.append("receipt")

    return missing


def _get_expense_receipt(expense: dict, bills_map: dict) -> Optional[str]:
    """Resolve receipt URL: check bills table first, then legacy field."""
    bill_id = normalize_bill_id(expense.get("bill_id") or "")
    if bill_id and bill_id in bills_map:
        url = bills_map[bill_id].get("receipt_url")
        if url:
            return url
    return expense.get("receipt_url") or None


# ============================================================================
# Duplicate Rules Engine
# ============================================================================

def check_duplicate(
    expense: dict,
    same_vendor_expenses: List[dict],
    bills_map: dict,
    config: dict,
    lookups: dict,
    hash_cache: Optional[dict] = None,
    hash_client: Optional[httpx.Client] = None,
) -> DuplicateResult:
    """
    Check if expense is a duplicate of any same-vendor expense.
    Returns the first matching rule result.
    """
    if hash_cache is None:
        hash_cache = {}
    tolerance = float(config.get("daneel_amount_tolerance", 0.05))
    fuzzy_thresh = float(config.get("daneel_fuzzy_threshold", 85))
    labor_kw = str(config.get("daneel_labor_keywords", "labor")).lower()

    exp_amount = float(expense.get("Amount") or 0)
    exp_date = (expense.get("TxnDate") or "")[:10]
    exp_bill = normalize_bill_id(expense.get("bill_id") or "")
    exp_desc = (expense.get("LineDescription") or "").strip().lower()
    exp_account_name = lookups["accounts"].get(expense.get("account_id"), "").lower()
    exp_payment_name = lookups["payment_methods"].get(expense.get("payment_type"), "").lower()
    exp_receipt = _get_expense_receipt(expense, bills_map)
    exp_id = expense.get("expense_id") or expense.get("id")

    is_labor = labor_kw in exp_account_name
    is_check = "check" in exp_payment_name

    worst_dup = None  # track the most concerning duplicate found

    for other in same_vendor_expenses:
        other_id = other.get("expense_id") or other.get("id")
        if other_id == exp_id:
            continue

        oth_amount = float(other.get("Amount") or 0)
        oth_date = (other.get("TxnDate") or "")[:10]
        oth_bill = normalize_bill_id(other.get("bill_id") or "")
        oth_desc = (other.get("LineDescription") or "").strip().lower()
        oth_account_name = lookups["accounts"].get(other.get("account_id"), "").lower()
        oth_payment_name = lookups["payment_methods"].get(other.get("payment_type"), "").lower()
        oth_receipt = _get_expense_receipt(other, bills_map)

        oth_is_labor = labor_kw in oth_account_name
        oth_is_check = "check" in oth_payment_name

        amount_diff = abs(exp_amount - oth_amount)
        same_amount = amount_diff <= tolerance
        same_date = exp_date == oth_date and exp_date != ""
        diff_date = not same_date

        # ------ R1: Amount mismatch -- quick discard ------
        if not same_amount:
            continue

        # ------ R2: Different account = NOT a duplicate ------
        exp_account_id = expense.get("account_id") or ""
        oth_account_id = other.get("account_id") or ""
        if exp_account_id and oth_account_id and exp_account_id != oth_account_id:
            continue

        # ------ R5: Recurring labor payment ------
        if (same_amount and diff_date
                and (is_labor or oth_is_labor)
                and (is_check or oth_is_check)):
            # Not a duplicate -- labor payments recur
            continue

        # ------ R8: Labor + same check number ------
        if is_labor and oth_is_labor and same_amount and exp_bill and oth_bill:
            if exp_bill == oth_bill and diff_date:
                # Same check number, different date = separate check payments
                continue

        # ------ R6: Same bill, diff date, check ------
        if (exp_bill and oth_bill
                and string_similarity(exp_bill, oth_bill) >= 90
                and same_amount and diff_date
                and (is_check or oth_is_check)):
            continue

        # ------ R3: Identical purchase (same date + same bill) ------
        if (same_amount and same_date
                and exp_bill and oth_bill
                and string_similarity(exp_bill, oth_bill) >= 90):
            return DuplicateResult(
                verdict="duplicate",
                rule="R3",
                details=f"Identical: same vendor, amount, date, bill #{exp_bill}",
                paired_expense_id=other_id,
            )

        # ------ R4: Same date + same description, no bill ------
        if (same_amount and same_date
                and (not exp_bill or not oth_bill)
                and string_similarity(exp_desc, oth_desc) >= fuzzy_thresh):
            return DuplicateResult(
                verdict="duplicate",
                rule="R4",
                details=f"Same vendor, amount, date, description (no bill to distinguish)",
                paired_expense_id=other_id,
            )

        # ------ R7: Same date, DIFFERENT bill ------
        if same_amount and same_date and exp_bill and oth_bill and exp_bill != oth_bill:
            # Check receipt hashes (cached)
            if exp_receipt and exp_receipt not in hash_cache:
                hash_cache[exp_receipt] = get_receipt_hash(exp_receipt, client=hash_client)
            exp_hash = hash_cache.get(exp_receipt) if exp_receipt else None
            if oth_receipt and oth_receipt not in hash_cache:
                hash_cache[oth_receipt] = get_receipt_hash(oth_receipt, client=hash_client)
            oth_hash = hash_cache.get(oth_receipt) if oth_receipt else None

            if exp_hash and oth_hash:
                bill_info = bills_map.get(normalize_bill_id((expense.get("bill_id") or "")), {})
                oth_bill_info = bills_map.get(normalize_bill_id((other.get("bill_id") or "")), {})
                is_split = (bill_info.get("status") == "split"
                            or oth_bill_info.get("status") == "split")

                if exp_hash == oth_hash and not is_split:
                    # R7b: Same file, not a split = duplicate
                    return DuplicateResult(
                        verdict="duplicate",
                        rule="R7b",
                        details="Same vendor, amount, date; different bills but same receipt file",
                        paired_expense_id=other_id,
                    )
                # R7a: Different files = separate invoices, not dup
                continue
            elif not exp_hash or not oth_hash:
                # R7c: Can't verify without receipts
                worst_dup = worst_dup or DuplicateResult(
                    verdict="need_info",
                    rule="R7c",
                    details="Same vendor, amount, date, different bills -- need receipt files to verify",
                    paired_expense_id=other_id,
                )
                continue

        # ------ R9: Labor, no check number, receipt comparison ------
        if is_labor and oth_is_labor and same_amount and not exp_bill and not oth_bill:
            if exp_receipt and exp_receipt not in hash_cache:
                hash_cache[exp_receipt] = get_receipt_hash(exp_receipt, client=hash_client)
            exp_hash = hash_cache.get(exp_receipt) if exp_receipt else None
            if oth_receipt and oth_receipt not in hash_cache:
                hash_cache[oth_receipt] = get_receipt_hash(oth_receipt, client=hash_client)
            oth_hash = hash_cache.get(oth_receipt) if oth_receipt else None
            if exp_hash and oth_hash:
                if exp_hash != oth_hash:
                    continue  # R9: different checks visually
                else:
                    return DuplicateResult(
                        verdict="duplicate",
                        rule="R9_same_hash",
                        details="Labor expenses with same receipt file and no bill number",
                        paired_expense_id=other_id,
                    )
            else:
                # R9b: need receipt
                worst_dup = worst_dup or DuplicateResult(
                    verdict="need_info",
                    rule="R9b",
                    details="Labor expenses with same amount, no bill -- need check image to verify",
                    paired_expense_id=other_id,
                )
                continue

        # ------ Catch-all for same amount + same date with no clear rule ------
        if same_amount and same_date:
            worst_dup = worst_dup or DuplicateResult(
                verdict="ambiguous",
                rule="DEFAULT",
                details=f"Same vendor, amount (${exp_amount:.2f}), date ({exp_date}) -- needs human review",
                paired_expense_id=other_id,
            )

    # Return worst finding, or clean pass
    return worst_dup or DuplicateResult(
        verdict="not_duplicate",
        rule="CLEAR",
        details="No duplicate patterns found",
    )


# ============================================================================
# GPT Fallback for ambiguous cases
# ============================================================================

_GPT_SYSTEM_PROMPT = (
    "You are Daneel, a financial duplicate-detection agent for a construction company. "
    "You receive two expenses that the rule engine flagged as ambiguous. "
    "Determine if they are duplicates or distinct transactions.\n\n"
    "RULES:\n"
    "- Respond with ONLY a JSON object: {\"verdict\": \"duplicate\" | \"not_duplicate\", \"confidence\": 0-100, \"reason\": \"brief explanation\"}\n"
    "- Consider: same vendor + similar amount + same date is suspicious\n"
    "- Recurring labor/payroll payments on different dates are NOT duplicates\n"
    "- Same bill on a split invoice across projects is NOT a duplicate\n"
    "- Slightly different descriptions for identical items ARE duplicates\n"
    "- If truly uncertain, use confidence < 50\n"
    "- No preamble, no markdown, just the JSON object"
)


def _format_expense_for_gpt(exp: dict, lookups: dict) -> str:
    """Format a single expense as a concise string for GPT context."""
    vendor = lookups["vendors"].get(exp.get("vendor_id"), "Unknown")
    account = lookups["accounts"].get(exp.get("account_id"), "Unknown")
    payment = lookups["payment_methods"].get(exp.get("payment_type"), "Unknown")
    return (
        f"Vendor: {vendor} | Amount: ${float(exp.get('Amount') or 0):,.2f} | "
        f"Date: {(exp.get('TxnDate') or '')[:10]} | Bill#: {exp.get('bill_id') or '-'} | "
        f"Account: {account} | Payment: {payment} | "
        f"Description: {exp.get('LineDescription') or '-'}"
    )


def gpt_resolve_ambiguous(
    expense: dict,
    other: dict,
    lookups: dict,
    min_confidence: int = 75,
) -> DuplicateResult:
    """
    Ask GPT-4o-mini to resolve an ambiguous duplicate pair.
    Returns a DuplicateResult. Falls back to 'ambiguous' on any error.
    """
    import json as _json

    exp_a = _format_expense_for_gpt(expense, lookups)
    exp_b = _format_expense_for_gpt(other, lookups)
    user_msg = f"Expense A:\n{exp_a}\n\nExpense B:\n{exp_b}\n\nAre these duplicates?"

    try:
        # Step 1: Try gpt-5-mini first (fast, cheap)
        raw = gpt.mini(_GPT_SYSTEM_PROMPT, user_msg, json_mode=True, max_tokens=150)
        tier_used = "mini"

        if raw:
            try:
                data = _json.loads(raw)
                confidence = int(data.get("confidence", 0))
                if confidence < min_confidence:
                    logger.info(
                        f"[DaneelAutoAuth] mini confidence {confidence}% < {min_confidence}%, "
                        f"escalating to heavy"
                    )
                    raw = None  # trigger fallback
            except (ValueError, _json.JSONDecodeError):
                raw = None  # trigger fallback

        # Step 2: Fallback to gpt-5.2 (heavy tier)
        if not raw:
            raw = gpt.heavy(
                _GPT_SYSTEM_PROMPT, user_msg,
                temperature=0.1, max_tokens=150, json_mode=True,
            )
            tier_used = "heavy"

        if not raw:
            return DuplicateResult("ambiguous", "GPT_EMPTY", "Both GPT tiers returned empty")

        data = _json.loads(raw)
        verdict = data.get("verdict", "").lower()
        confidence = int(data.get("confidence", 0))
        reason = data.get("reason", "GPT analysis")

        logger.info(f"[DaneelAutoAuth] duplicate resolution via {tier_used}: {verdict} ({confidence}%)")

        if confidence < min_confidence:
            return DuplicateResult("ambiguous", "GPT_LOW_CONF",
                                   f"GPT confidence {confidence}% < threshold {min_confidence}%: {reason}")

        if verdict == "duplicate":
            return DuplicateResult("duplicate", "GPT_DUP",
                                   f"GPT-{tier_used} ({confidence}%): {reason}",
                                   paired_expense_id=other.get("expense_id") or other.get("id"))
        else:
            return DuplicateResult("not_duplicate", "GPT_CLEAR",
                                   f"GPT-{tier_used} ({confidence}%): {reason}")

    except Exception as e:
        logger.warning(f"[DaneelAutoAuth] GPT fallback failed: {e}")
        return DuplicateResult("ambiguous", "GPT_ERROR", f"GPT fallback error: {e}")



# ============================================================================
# Authorize a single expense (mirrors update_expense_status logic)
# ============================================================================

def authorize_expense(sb, expense_id: str, project_id: str, rule: str = "passed_all_checks") -> bool:
    """Set expense status to 'auth' as Daneel.  Only acts on 'pending' expenses."""
    try:
        # Guard: only authorize if expense is still pending (prevents race with human review)
        current = sb.table("expenses_manual_COGS") \
            .select("status") \
            .eq("expense_id", expense_id) \
            .single() \
            .execute()
        current_status = (current.data or {}).get("status", "")
        if current_status != "pending":
            logger.info("[DaneelAutoAuth] Skipping authorize for %s — status is '%s', not 'pending'",
                        expense_id, current_status)
            return False

        sb.table("expenses_manual_COGS").update({
            "status": "auth",
            "auth_status": True,
            "auth_by": DANEEL_BOT_USER_ID,
        }).eq("expense_id", expense_id).eq("status", "pending").execute()

        sb.table("expense_status_log").insert({
            "expense_id": expense_id,
            "old_status": "pending",
            "new_status": "auth",
            "changed_by": DANEEL_BOT_USER_ID,
            "reason": "Auto-authorized by Daneel",
            "metadata": {"agent": "daneel", "rule": rule},
        }).execute()

        # Trigger budget monitor (same as human auth)
        try:
            from api.services.budget_monitor import trigger_project_budget_check
            import asyncio
            loop = asyncio.get_running_loop()
            loop.create_task(trigger_project_budget_check(project_id))
        except Exception as e:
            logger.warning(f"[DaneelAutoAuth] Budget check trigger failed: {e}")

        return True
    except Exception as e:
        logger.error(f"[DaneelAutoAuth] authorize_expense failed for {expense_id}: {e}")
        return False


# ============================================================================
# Pending info tracking
# ============================================================================

def _track_pending_info(sb, expense_id: str, project_id: str, missing: List[str], message_id: Optional[str] = None):
    try:
        sb.table("daneel_pending_info").upsert({
            "expense_id": expense_id,
            "project_id": project_id,
            "missing_fields": missing,
            "requested_at": datetime.now(timezone.utc).isoformat(),
            "resolved_at": None,
            "message_id": message_id,
        }).execute()
    except Exception as e:
        logger.error(f"[DaneelAutoAuth] track_pending_info failed: {e}")


def _resolve_pending_info(sb, expense_id: str):
    try:
        sb.table("daneel_pending_info").update({
            "resolved_at": datetime.now(timezone.utc).isoformat(),
        }).eq("expense_id", expense_id).execute()
    except Exception as e:
        logger.error(f"[DaneelAutoAuth] resolve_pending_info failed: {e}")


# ============================================================================
# Auth report persistence
# ============================================================================

def _save_auth_report(sb, report_type: str, summary: dict, decisions: list,
                      project_id: Optional[str] = None, project_name: Optional[str] = None):
    """Save an auth report with per-expense decisions."""
    try:
        sb.table("daneel_auth_reports").insert({
            "report_type": report_type,
            "project_id": project_id,
            "project_name": project_name,
            "summary": summary,
            "decisions": decisions,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }).execute()
    except Exception as e:
        logger.error(f"[DaneelAutoAuth] save_auth_report failed: {e}")


def _make_decision_entry(expense: dict, lookups: dict, decision: str,
                         rule: str = "", reason: str = "", missing_fields: list = None,
                         checks: list = None) -> dict:
    """Build a compact decision object for the report."""
    vendor_name = lookups["vendors"].get(expense.get("vendor_id"), "")
    if not vendor_name:
        vendor_name = expense.get("bill_id") or "Unknown"
    entry = {
        "expense_id": expense.get("expense_id") or expense.get("id"),
        "vendor": vendor_name,
        "amount": float(expense.get("Amount") or 0),
        "date": (expense.get("TxnDate") or "")[:10],
        "bill_id": expense.get("bill_id") or "",
        "decision": decision,
        "rule": rule,
        "reason": reason,
        "missing_fields": missing_fields or [],
    }
    if checks:
        entry["checks"] = checks
    return entry


# ============================================================================
# Message builders
# ============================================================================


_ANDREW_MISMATCH_CALLOUTS = [
    "@Andrew Heads up -- the numbers on this bill don't add up. Can you take a look and sort it out?",
    "@Andrew Found a discrepancy on this one. Mind double-checking the line items against the invoice total?",
    "@Andrew Something's off here -- the invoice total and the expense breakdown aren't matching. Could you reconcile this?",
    "@Andrew Hey, I ran the numbers and they don't line up. Can you review and fix the amounts on this bill?",
    "@Andrew Flagging this for you -- there's a gap between the invoice total and what was logged. Please review when you get a chance.",
]


def _build_mismatch_message(
    bill_id: str,
    ocr_result,
    expenses_sum: float,
    source: str,
    expenses: List[dict],
    lookups: dict,
    n_items: int = 0,
    notify_andrew: bool = False,
) -> str:
    """
    Build a bill total mismatch notification.
    ocr_result: float (hint path) or dict (vision path with subtotal/tax/total).
    source: 'hint' (filename) or 'vision' (GPT Vision OCR).
    """
    import random

    if isinstance(ocr_result, dict):
        ocr_total = ocr_result.get("total", 0)
        ocr_subtotal = ocr_result.get("subtotal", ocr_total)
        ocr_tax = ocr_result.get("tax", 0)
    else:
        ocr_total = float(ocr_result)
        ocr_subtotal = ocr_total
        ocr_tax = 0

    diff_total = abs(ocr_total - expenses_sum)
    diff_subtotal = abs(ocr_subtotal - expenses_sum)

    lines = [
        f"**Bill Amount Mismatch Detected** - Bill #{bill_id}",
        "",
    ]

    if ocr_tax > 0 and ocr_subtotal != ocr_total:
        lines.extend([
            f"| | Invoice (OCR) | Expenses Sum | Difference |",
            f"|--|--------------|--------------|------------|",
            f"| **Subtotal** | ${ocr_subtotal:,.2f} | ${expenses_sum:,.2f} | ${diff_subtotal:,.2f} |",
            f"| **Tax** | ${ocr_tax:,.2f} | -- | -- |",
            f"| **Total** | ${ocr_total:,.2f} | ${expenses_sum:,.2f} | ${diff_total:,.2f} |",
            "",
        ])
    else:
        lines.extend([
            f"| Source | Invoice Total | Expenses Sum | Difference |",
            f"|--------|--------------|--------------|------------|",
            f"| {source.capitalize()} | ${ocr_total:,.2f} | ${expenses_sum:,.2f} | ${diff_total:,.2f} |",
            "",
        ])

    if ocr_tax > 0 and diff_subtotal < diff_total and diff_subtotal < 1.0:
        lines.append(
            f"*Note: Expenses sum closely matches the subtotal. "
            f"The ${ocr_tax:,.2f} tax may not have been distributed into the line items.*"
        )
        lines.append("")

    if n_items > 0:
        rounding_max = n_items * 0.01
        lines.append(
            f"*Rounding tolerance: {n_items} items x $0.01 = ${rounding_max:,.2f}*"
        )
        lines.append("")

    if expenses:
        lines.append(f"**Expenses on this bill** ({len(expenses)}):")
        lines.append("")
        for e in expenses[:10]:
            vname = lookups["vendors"].get(e.get("vendor_id"), "Unknown")
            amt = f"${float(e.get('Amount') or 0):,.2f}"
            desc = (e.get("LineDescription") or "-")[:40]
            lines.append(f"- {vname}: {amt} - {desc}")
        if len(expenses) > 10:
            lines.append(f"- ...and {len(expenses) - 10} more")
        lines.append("")

    if notify_andrew:
        lines.append(random.choice(_ANDREW_MISMATCH_CALLOUTS))
    else:
        lines.append("Please review this bill and reconcile the expense amounts.")

    return "\n".join(lines)


def _resolve_role_name(sb, role_id: Optional[str]) -> str:
    """Resolve a role UUID to its name for @mentions (legacy, kept for fallback)."""
    if not role_id:
        return ""
    try:
        result = sb.table("rols").select("rol_name").eq("rol_id", role_id).single().execute()
        if result.data:
            return result.data["rol_name"].replace(" ", "")
        return ""
    except Exception as _exc:
        logger.debug("Suppressed role resolve: %s", _exc)
        return ""


def _resolve_user_mentions(sb, user_ids_json) -> str:
    """Convert a JSON array of user_ids to '@User1 @User2' mention string."""
    import json as _json
    if not user_ids_json:
        return ""
    try:
        ids = _json.loads(user_ids_json) if isinstance(user_ids_json, str) else user_ids_json
        if not ids or not isinstance(ids, list):
            return ""
        result = sb.table("users").select("user_name").in_("user_id", ids).execute()
        names = [u["user_name"] for u in (result.data or []) if u.get("user_name")]
        return " ".join(f"@{name}" for name in names)
    except Exception as _exc:
        logger.debug("Suppressed user mention resolve: %s", _exc)
        return ""


def _resolve_mentions(sb, cfg: dict, key_users: str, key_role: str) -> str:
    """Resolve mentions: try new user-based keys first, fall back to legacy role key."""
    mentions = _resolve_user_mentions(sb, cfg.get(key_users))
    if not mentions:
        mentions = _resolve_role_name(sb, cfg.get(key_role))
    return mentions


# ============================================================================
# Andrew mismatch protocol trigger
# ============================================================================

def _trigger_andrew_reconciliation(bill_id: str, project_id: str, source: str):
    """Fire Andrew's mismatch reconciliation in a background thread (non-blocking)."""
    try:
        from api.services.andrew_mismatch_protocol import run_mismatch_reconciliation
        import asyncio
        loop = asyncio.get_running_loop()
        loop.run_in_executor(
            None, run_mismatch_reconciliation, bill_id, project_id, source
        )
    except Exception as e:
        logger.warning(f"[DaneelAutoAuth] Andrew reconciliation trigger failed: {e}")


# ============================================================================
# Main orchestrator
# ============================================================================

def run_auto_auth(process_all: bool = False, project_id: Optional[str] = None) -> dict:
    """
    Process pending expenses.
    process_all=False (default): only new since last run.
    process_all=True: process ALL pending expenses (backlog).
    project_id: if provided, only process expenses for this project.
    """
    t0 = time.monotonic()
    cfg = load_auto_auth_config()

    # Per-project manual runs bypass the global auto-auth toggle
    if not project_id and not cfg.get("daneel_auto_auth_enabled"):
        return {"status": "disabled", "message": "Auto-auth is disabled"}

    sb = _get_supabase()
    lookups = _load_lookups(sb)
    logger.info("[DaneelAutoAuth] Starting run | process_all=%s project_id=%s", process_all, project_id)

    last_run = cfg.get("daneel_auto_auth_last_run")
    now = datetime.now(timezone.utc).isoformat()

    # Fetch pending expenses
    query = sb.table("expenses_manual_COGS") \
        .select("*") \
        .eq("status", "pending")
    if project_id:
        # Per-project run: always process ALL pending for that project
        query = query.eq("project", project_id)
    elif not process_all and last_run:
        query = query.gt("created_at", last_run)
    result = query.order("created_at").execute()

    pending = result.data or []
    if not pending:
        _save_config_key("daneel_auto_auth_last_run", now)
        logger.info("[DaneelAutoAuth] No pending expenses found")
        return {"status": "ok", "message": "No new pending expenses", "authorized": 0}
    logger.info("[DaneelAutoAuth] Found %d pending expenses across projects", len(pending))

    # Load all bills metadata (normalized keys for case-insensitive lookup)
    bills_result = sb.table("bills").select("bill_id, receipt_url, status, split_projects").execute()
    bills_map = {}
    receipt_groups = {}  # receipt_url -> set of normalized bill_ids
    for b in (bills_result.data or []):
        nb = normalize_bill_id(b["bill_id"])
        bills_map[nb] = b
        url = (b.get("receipt_url") or "").strip()
        if url:
            receipt_groups.setdefault(url, set()).add(nb)

    # Group by project
    projects = {}
    for exp in pending:
        pid = exp.get("project")
        if pid:
            projects.setdefault(pid, []).append(exp)

    # Resolve mention targets (user-based with legacy role fallback)
    bookkeeping_mentions = _resolve_mentions(sb, cfg, "daneel_bookkeeping_users", "daneel_bookkeeping_role")
    escalation_mentions = _resolve_mentions(sb, cfg, "daneel_accounting_mgr_users", "daneel_accounting_mgr_role")

    total_authorized = 0
    total_missing = 0
    total_duplicates = 0
    total_escalated = 0
    missing_detail = []   # [{expense_id, vendor, missing_fields}]
    all_decisions = []    # decision entries for auth report

    # Resolve project names for reports
    proj_names = {}
    try:
        pn_result = sb.table("projects").select("project_id, project_name").execute()
        proj_names = {p["project_id"]: p["project_name"] for p in (pn_result.data or [])}
    except Exception as _exc:
        logger.debug("Suppressed project names load: %s", _exc)

    # Shared HTTP client for receipt hash checks (connection pooling)
    hash_client = httpx.Client(timeout=5.0)

    for pid, expenses in projects.items():
        authorized_list = []
        missing_info_list = []
        duplicate_list = []
        escalation_list = []
        decisions = []  # per-project decisions for report
        all_resolve_attempts = []  # smart layer resolution attempts
        all_auto_resolved = []    # fields that were auto-resolved

        # Load all expenses for this project (for duplicate comparison)
        all_project = sb.table("expenses_manual_COGS") \
            .select("*") \
            .eq("project", pid) \
            .execute()
        all_project_expenses = all_project.data or []

        # Load dismissed duplicate pairs (user said "not a duplicate" in health check)
        # Build a set of frozensets for O(1) lookup: {frozenset({id1, id2}), ...}
        dismissed_pairs: set = set()
        try:
            dismissed_result = sb.table("dismissed_expense_duplicates") \
                .select("expense_id_1, expense_id_2") \
                .execute()
            for dp in (dismissed_result.data or []):
                dismissed_pairs.add(frozenset({dp["expense_id_1"], dp["expense_id_2"]}))
        except Exception as _exc:
            logger.warning("[DaneelAutoAuth] Could not load dismissed pairs: %s", _exc)

        # Group all project expenses by vendor for O(n) duplicate comparison
        by_vendor = {}
        for e in all_project_expenses:
            vid = e.get("vendor_id")
            if vid:
                by_vendor.setdefault(vid, []).append(e)

        # Phase 1: Rule engine -- classify each expense
        # Each candidate tracks a checks trail: [{check, passed, detail}]
        auth_candidates = []  # (expense, rule, reason, checks) tuples
        _vision_cache = {}  # bill_id -> vision_total (avoid repeated GPT calls for same bill)
        _mismatch_notified = set()  # bill_ids already notified (one message per bill)
        _hash_cache = {}  # receipt_url -> hash string (avoid repeated HTTP HEAD per run)

        for expense in expenses:
            exp_id = expense.get("expense_id") or expense.get("id")
            exp_checks = []  # audit trail for this expense

            # 1. Health check (non-blocking)
            missing = run_health_check(expense, cfg, bills_map)
            if missing:
                # 1a. Smart resolution: try to auto-fill missing fields
                from api.services.daneel_smart_layer import (
                    try_resolve_missing, apply_auto_updates,
                )
                auto_updates, still_missing, resolve_attempts = try_resolve_missing(
                    expense, missing, pid, bills_map, lookups)
                if auto_updates:
                    apply_auto_updates(sb, exp_id, auto_updates)
                    # Merge updates into in-memory expense for subsequent checks
                    expense.update(auto_updates)
                    all_resolve_attempts.extend(resolve_attempts)
                    all_auto_resolved.extend([f for f in missing if f not in still_missing])
                    exp_checks.append({
                        "check": "smart_resolve", "passed": True,
                        "detail": f"Auto-resolved: {list(auto_updates.keys())}"
                    })
                if still_missing:
                    exp_checks.append({"check": "health", "passed": False,
                                       "detail": "Still missing: " + ", ".join(still_missing)})
                    missing_info_list.append({"expense": expense, "missing": still_missing})
                    _track_pending_info(sb, exp_id, pid, still_missing)
                    decisions.append(_make_decision_entry(
                        expense, lookups, "missing_info",
                        rule="HEALTH", reason="Health check failed (after smart resolve)",
                        missing_fields=still_missing, checks=exp_checks))
                    continue  # Do not proceed to hint/dup checks with incomplete data
                else:
                    # Fully resolved! Continue to duplicate check
                    exp_checks.append({"check": "health", "passed": True,
                                       "detail": "Resolved by smart layer: " + ", ".join(missing)})
                    _resolve_pending_info(sb, exp_id)
                    missing = []  # Clear so it proceeds to duplicate check
            if not missing:
                exp_checks.append({"check": "health", "passed": True, "detail": "All required fields present"})
                # Resolve any stale pending-info record from a previous run
                _resolve_pending_info(sb, exp_id)

            # 1b. Bill hint cross-validation (soft armoring layer)
            # Only validate CLOSED bills — open bills are still accumulating items
            # so comparing partial sums against the invoice total is meaningless.
            from api.helpers.bill_hint_parser import parse_bill_hint, cross_validate_bill_hint
            from urllib.parse import unquote
            bill_id_raw = (expense.get("bill_id") or "").strip()
            bill_id_str = normalize_bill_id(bill_id_raw)
            _siblings = _get_bill_siblings(bill_id_str, bills_map, receipt_groups)
            bill_data = bills_map.get(bill_id_str)
            bill_status = (bill_data.get("status") or "").lower() if bill_data else ""
            if bill_id_str and bill_data and bill_status == "closed":
                receipt_url = bill_data.get("receipt_url") or ""
                # Extract filename from URL path and decode URL encoding
                hint_source = unquote(receipt_url.rsplit("/", 1)[-1]) if "/" in receipt_url else unquote(receipt_url)
                hint = parse_bill_hint(hint_source) if hint_source else {}
                if hint and hint.get("amount_hint") is not None:
                    # Sum ALL expenses on the same bill (including receipt-URL siblings)
                    bill_total = sum(
                        float(e.get("Amount") or 0)
                        for e in all_project_expenses
                        if normalize_bill_id(e.get("bill_id") or "") in _siblings
                    )
                    exp_vendor_name = lookups["vendors"].get(expense.get("vendor_id"), "")
                    hint_val = cross_validate_bill_hint(
                        hint,
                        vendor_name=exp_vendor_name,
                        amount=bill_total,
                        date_str=(expense.get("TxnDate") or ""),
                    )
                    if hint_val.get("amount_match") is False:
                        reason_txt = f"Bill hint amount mismatch: {hint_val['mismatches'][0]}"
                        exp_checks.append({"check": "bill_hint", "passed": False, "detail": reason_txt})
                        escalation_list.append({"expense": expense, "reason": reason_txt})
                        decisions.append(_make_decision_entry(
                            expense, lookups, "escalated", rule="BILL_HINT", reason=reason_txt,
                            checks=exp_checks))
                        # Mismatch notification (once per bill)
                        if bill_id_str not in _mismatch_notified:
                            _mismatch_notified.add(bill_id_str)
                            bill_expenses = [e for e in all_project_expenses
                                             if normalize_bill_id(e.get("bill_id") or "") in _siblings]
                            n_items_hint = len(bill_expenses)
                            mismatch_msg = _build_mismatch_message(
                                bill_id_raw, hint["amount_hint"], bill_total, "hint",
                                bill_expenses, lookups, n_items=n_items_hint,
                                notify_andrew=cfg.get("daneel_mismatch_notify_andrew", True))
                            post_daneel_message(
                                content=mismatch_msg, project_id=pid,
                                channel_type="project_general",
                                metadata={"type": "bill_mismatch", "bill_id": bill_id_str,
                                          "source": "hint"})
                            # Trigger Andrew's reconciliation protocol
                            if cfg.get("daneel_mismatch_notify_andrew", True):
                                _trigger_andrew_reconciliation(bill_id_raw, pid, "daneel_hint")
                        continue
                    else:
                        exp_checks.append({"check": "bill_hint", "passed": True,
                                           "detail": f"Bill total ${bill_total:,.2f} matches hint ${hint['amount_hint']:,.2f}"})
                else:
                    # No amount in filename -- fall back to GPT Vision OCR
                    if receipt_url:
                        if bill_id_str in _vision_cache:
                            vision_result = _vision_cache[bill_id_str]
                        else:
                            vision_result = gpt_vision_extract_bill_total(receipt_url)
                            _vision_cache[bill_id_str] = vision_result
                        if vision_result is not None:
                            bill_total = sum(
                                float(e.get("Amount") or 0)
                                for e in all_project_expenses
                                if normalize_bill_id(e.get("bill_id") or "") in _siblings
                            )
                            n_items = sum(
                                1 for e in all_project_expenses
                                if normalize_bill_id(e.get("bill_id") or "") in _siblings
                            )
                            tolerance_pct = float(cfg.get("daneel_bill_validation_tolerance_pct", 0.01))
                            vision_total = vision_result["total"]
                            vision_subtotal = vision_result.get("subtotal", vision_total)
                            vision_tax = vision_result.get("tax", 0)

                            # Smart tolerance: max(percentage-based, rounding-based)
                            rounding_tolerance = n_items * 0.01
                            larger = max(vision_total, bill_total) if bill_total else vision_total
                            pct_tolerance = larger * tolerance_pct if larger > 0 else 0
                            abs_tolerance = max(pct_tolerance, rounding_tolerance)

                            diff_vs_total = abs(vision_total - bill_total)
                            diff_vs_subtotal = abs(vision_subtotal - bill_total)

                            match_type = None
                            if diff_vs_total <= abs_tolerance:
                                match_type = "total"
                            elif vision_subtotal != vision_total and diff_vs_subtotal <= abs_tolerance:
                                match_type = "subtotal"

                            if match_type == "total":
                                exp_checks.append({
                                    "check": "bill_hint_vision", "passed": True,
                                    "detail": (
                                        f"Vision OCR total ${vision_total:,.2f} matches "
                                        f"expenses sum ${bill_total:,.2f}"
                                    )
                                })
                            elif match_type == "subtotal":
                                exp_checks.append({
                                    "check": "bill_hint_vision", "passed": True,
                                    "detail": (
                                        f"Vision OCR subtotal ${vision_subtotal:,.2f} matches "
                                        f"expenses sum ${bill_total:,.2f} "
                                        f"(tax ${vision_tax:,.2f} may not be included in expenses)"
                                    )
                                })
                                if bill_id_str not in _mismatch_notified:
                                    _mismatch_notified.add(bill_id_str)
                                    # Tax note is informational — deferred to periodic digest
                                    logger.info(
                                        "[DaneelAutoAuth] Tax note: bill %s subtotal $%.2f, "
                                        "total $%.2f, tax $%.2f (deferred to digest)",
                                        bill_id_str, vision_subtotal, vision_total, vision_tax,
                                    )
                            else:
                                reason_txt = (
                                    f"OCR mismatch: invoice ${vision_total:,.2f} "
                                    f"vs logged ${bill_total:,.2f}"
                                )
                                exp_checks.append({
                                    "check": "bill_hint_vision", "passed": False,
                                    "detail": reason_txt
                                })
                                escalation_list.append({"expense": expense, "reason": reason_txt})
                                decisions.append(_make_decision_entry(
                                    expense, lookups, "escalated",
                                    rule="BILL_HINT_VISION", reason=reason_txt,
                                    checks=exp_checks))
                                if bill_id_str not in _mismatch_notified:
                                    _mismatch_notified.add(bill_id_str)
                                    bill_expenses = [
                                        e for e in all_project_expenses
                                        if normalize_bill_id(e.get("bill_id") or "") in _siblings
                                    ]
                                    mismatch_msg = _build_mismatch_message(
                                        bill_id_raw, vision_result, bill_total, "vision",
                                        bill_expenses, lookups, n_items=n_items,
                                        notify_andrew=cfg.get("daneel_mismatch_notify_andrew", True))
                                    post_daneel_message(
                                        content=mismatch_msg, project_id=pid,
                                        channel_type="project_general",
                                        metadata={
                                            "type": "bill_mismatch", "bill_id": bill_id_str,
                                            "source": "vision"
                                        })
                                    if cfg.get("daneel_mismatch_notify_andrew", True):
                                        _trigger_andrew_reconciliation(
                                            bill_id_raw, pid, "daneel_vision")
                                continue
                        else:
                            exp_checks.append({"check": "bill_hint", "passed": True,
                                               "detail": "No filename hint; Vision OCR could not extract total"})
                    else:
                        exp_checks.append({"check": "bill_hint", "passed": True,
                                           "detail": "No amount hint in receipt filename (no receipt URL)"})
            else:
                if not bill_id_str:
                    skip_reason = "No bill"
                elif not bill_data:
                    skip_reason = "Bill not in bills table"
                else:
                    skip_reason = f"Bill status is '{bill_status}' (only closed bills are validated)"
                exp_checks.append({"check": "bill_hint", "passed": True, "detail": skip_reason})

            # 1c. Per-project receipt hash check (detect same receipt on different bills)
            if cfg.get("daneel_receipt_hash_check_enabled", True):
                exp_receipt_url = _get_expense_receipt(expense, bills_map)
                if exp_receipt_url:
                    if exp_receipt_url not in _hash_cache:
                        _hash_cache[exp_receipt_url] = get_receipt_hash(exp_receipt_url, client=hash_client)
                    exp_hash = _hash_cache[exp_receipt_url]
                    if exp_hash:
                        hash_collision = False
                        for other_exp in all_project_expenses:
                            other_id = other_exp.get("expense_id") or other_exp.get("id")
                            if other_id == exp_id:
                                continue
                            other_bill = normalize_bill_id(other_exp.get("bill_id") or "")
                            if other_bill == bill_id_str or other_bill in _siblings:
                                continue  # same bill / same receipt group
                            other_receipt_url = _get_expense_receipt(other_exp, bills_map)
                            if not other_receipt_url:
                                continue
                            if other_receipt_url not in _hash_cache:
                                _hash_cache[other_receipt_url] = get_receipt_hash(other_receipt_url, client=hash_client)
                            other_hash = _hash_cache[other_receipt_url]
                            if other_hash and other_hash == exp_hash:
                                # Same receipt file on different bills
                                exp_bill_info = bills_map.get(bill_id_str, {})
                                oth_bill_info = bills_map.get(other_bill, {})
                                exp_status = (exp_bill_info.get("status") or "").lower()
                                oth_status = (oth_bill_info.get("status") or "").lower()
                                # Split bills can share receipts across projects - skip
                                if exp_status == "split" or oth_status == "split":
                                    continue
                                # Only flag as duplicate when BOTH bills are closed
                                # (open bills may have system glitches with hashes)
                                if exp_status == "closed" and oth_status == "closed":
                                    # Check if user already dismissed this pair
                                    hash_pair_key = frozenset({exp_id, other_id})
                                    if hash_pair_key in dismissed_pairs:
                                        exp_checks.append({"check": "receipt_hash", "passed": True,
                                                           "detail": "Hash collision but pair dismissed by user"})
                                        continue
                                    reason_txt = (
                                        f"Receipt hash match: same file used on bill #{bill_id_raw} "
                                        f"and bill #{(other_exp.get('bill_id') or '').strip()} (both closed)"
                                    )
                                    exp_checks.append({"check": "receipt_hash", "passed": False, "detail": reason_txt})
                                    escalation_list.append({"expense": expense, "reason": reason_txt})
                                    decisions.append(_make_decision_entry(
                                        expense, lookups, "escalated", rule="HASH_DUP",
                                        reason=reason_txt, checks=exp_checks))
                                    hash_collision = True
                                    break
                        if hash_collision:
                            continue
                        exp_checks.append({"check": "receipt_hash", "passed": True,
                                           "detail": "No cross-bill hash collision in project"})
                    else:
                        exp_checks.append({"check": "receipt_hash", "passed": True,
                                           "detail": "Could not compute receipt hash"})
                else:
                    exp_checks.append({"check": "receipt_hash", "passed": True,
                                       "detail": "No receipt URL available"})

            # 2. Duplicate check
            vendor_id = expense.get("vendor_id")
            same_vendor = by_vendor.get(vendor_id, []) if vendor_id else []
            dup_result = check_duplicate(expense, same_vendor, bills_map, cfg, lookups, hash_cache=_hash_cache, hash_client=hash_client)

            if dup_result.verdict == "duplicate":
                # Check if user already dismissed this pair via health check modal
                pair_key = frozenset({exp_id, dup_result.paired_expense_id})
                if pair_key in dismissed_pairs:
                    exp_checks.append({"check": "duplicate", "passed": True,
                                       "detail": f"{dup_result.rule} triggered but pair was dismissed by user"})
                    logger.info("[DaneelAutoAuth] Skipping dismissed duplicate pair: %s <-> %s",
                                exp_id, dup_result.paired_expense_id)
                else:
                    exp_checks.append({"check": "duplicate", "passed": False, "detail": f"{dup_result.rule}: {dup_result.details}"})
                    duplicate_list.append({"expense": expense, "result": dup_result})
                    decisions.append(_make_decision_entry(
                        expense, lookups, "duplicate", rule=dup_result.rule, reason=dup_result.details,
                        checks=exp_checks))
                    continue  # do NOT authorize

            if dup_result.verdict == "need_info":
                # Add to missing info if not already there
                if not missing:
                    need_fields = ["receipt"] if "receipt" in dup_result.rule.lower() else ["bill_id", "receipt"]
                    exp_checks.append({"check": "duplicate", "passed": False, "detail": f"Need info: {dup_result.details}"})
                    missing_info_list.append({"expense": expense, "missing": need_fields})
                    _track_pending_info(sb, exp_id, pid, need_fields)
                    decisions.append(_make_decision_entry(
                        expense, lookups, "missing_info",
                        rule=dup_result.rule, reason=dup_result.details, missing_fields=need_fields,
                        checks=exp_checks))
                continue

            if dup_result.verdict == "ambiguous":
                exp_checks.append({"check": "duplicate", "passed": False, "detail": f"Ambiguous: {dup_result.details}"})
                # Try GPT fallback if enabled
                if cfg.get("daneel_gpt_fallback_enabled") and dup_result.paired_expense_id:
                    paired = next((e for e in all_project_expenses
                                   if (e.get("expense_id") or e.get("id")) == dup_result.paired_expense_id), None)
                    if paired:
                        gpt_result = gpt_resolve_ambiguous(
                            expense, paired, lookups,
                            min_confidence=int(cfg.get("daneel_gpt_fallback_confidence", 75)),
                        )
                        if gpt_result.verdict == "duplicate":
                            gpt_pair_key = frozenset({exp_id, gpt_result.paired_expense_id})
                            if gpt_pair_key in dismissed_pairs:
                                exp_checks.append({"check": "gpt_resolve", "passed": True,
                                                   "detail": "GPT flagged duplicate but pair was dismissed by user"})
                            else:
                                exp_checks.append({"check": "gpt_resolve", "passed": False, "detail": gpt_result.details})
                                duplicate_list.append({"expense": expense, "result": gpt_result})
                                decisions.append(_make_decision_entry(
                                    expense, lookups, "duplicate", rule=gpt_result.rule, reason=gpt_result.details,
                                    checks=exp_checks))
                                continue
                        if gpt_result.verdict == "not_duplicate":
                            exp_checks.append({"check": "gpt_resolve", "passed": True, "detail": gpt_result.details})
                            if not missing:
                                auth_candidates.append((expense, gpt_result.rule, gpt_result.details, exp_checks))
                            continue
                        # GPT also ambiguous
                        exp_checks.append({"check": "gpt_resolve", "passed": False, "detail": gpt_result.details})
                # Still ambiguous -- escalate to human
                escalation_list.append({"expense": expense, "reason": dup_result.details})
                decisions.append(_make_decision_entry(
                    expense, lookups, "escalated", rule=dup_result.rule, reason=dup_result.details,
                    checks=exp_checks))
                continue

            # Duplicate check passed
            exp_checks.append({"check": "duplicate", "passed": True, "detail": f"R1-R9 clear: {dup_result.details}"})

            # 3. If missing critical info, don't authorize but continue
            if missing:
                continue

            # 4. Rule engine cleared -- add to candidates (NOT authorized yet)
            auth_candidates.append((expense, dup_result.rule, dup_result.details, exp_checks))

        # Phase 2: Authorize all approved candidates
        for expense, rule, det, chks in auth_candidates:
            exp_id = expense.get("expense_id") or expense.get("id")
            if authorize_expense(sb, exp_id, pid, rule):
                authorized_list.append(expense)
                decisions.append(_make_decision_entry(
                    expense, lookups, "authorized", rule=rule, reason=det,
                    checks=chks))

        # Phase 4: Results deferred to periodic digest (no immediate messages)
        # The auth report saved below feeds the digest engine.

        total_authorized += len(authorized_list)
        total_missing += len(missing_info_list)
        total_duplicates += len(duplicate_list)
        total_escalated += len(escalation_list)
        all_decisions.extend(decisions)

        # Collect detail for debugging
        for item in missing_info_list:
            e = item["expense"]
            missing_detail.append({
                "expense_id": e.get("expense_id") or e.get("id"),
                "vendor": lookups["vendors"].get(e.get("vendor_id"), "Unknown"),
                "amount": float(e.get("Amount") or 0),
                "missing_fields": item["missing"],
            })

    hash_client.close()

    # Always update last run timestamp so the dashboard shows activity
    _save_config_key("daneel_auto_auth_last_run", now)

    elapsed_ms = int((time.monotonic() - t0) * 1000)
    summary = {
        "status": "ok",
        "authorized": total_authorized,
        "missing_info": total_missing,
        "duplicates": total_duplicates,
        "escalated": total_escalated,
        "expenses_processed": len(pending),
        "missing_detail": missing_detail[:20],  # cap for response size
    }
    logger.info("[DaneelAutoAuth] Run complete in %dms | processed=%d authorized=%d missing=%d duplicates=%d escalated=%d",
                 elapsed_ms, len(pending), total_authorized, total_missing, total_duplicates, total_escalated)

    # Save auth report
    if all_decisions:
        rtype = "project_run" if project_id else ("backlog" if process_all else "scheduled")
        rname = proj_names.get(project_id, "") if project_id else None
        _save_auth_report(sb, rtype, summary, all_decisions,
                          project_id=project_id, project_name=rname)

    return summary


# ============================================================================
# Re-process pending info
# ============================================================================

def reprocess_pending_info() -> dict:
    """Re-check expenses that were waiting for missing info."""
    cfg = load_auto_auth_config()
    if not cfg.get("daneel_auto_auth_enabled"):
        return {"status": "disabled"}

    sb = _get_supabase()
    lookups = _load_lookups(sb)

    # Fetch unresolved pending info
    result = sb.table("daneel_pending_info") \
        .select("*") \
        .is_("resolved_at", "null") \
        .execute()
    pending = result.data or []

    if not pending:
        return {"status": "ok", "reprocessed": 0, "authorized": 0}

    # Load bills (normalized keys)
    bills_result = sb.table("bills").select("bill_id, receipt_url, status, split_projects").execute()
    bills_map = {normalize_bill_id(b["bill_id"]): b for b in (bills_result.data or [])}

    reprocessed = 0
    authorized = 0

    for item in pending:
        exp_id = item["expense_id"]
        project_id = item.get("project_id")

        # Re-fetch expense
        exp_result = sb.table("expenses_manual_COGS") \
            .select("*") \
            .eq("expense_id", exp_id) \
            .single() \
            .execute()
        if not exp_result.data:
            _resolve_pending_info(sb, exp_id)
            continue

        expense = exp_result.data

        # Skip if already authorized
        if expense.get("status") != "pending":
            _resolve_pending_info(sb, exp_id)
            continue

        # Re-run health check
        missing = run_health_check(expense, cfg, bills_map)
        if missing:
            # Still missing -- update fields
            try:
                sb.table("daneel_pending_info").update({
                    "missing_fields": missing,
                }).eq("expense_id", exp_id).execute()
            except Exception as _exc:
                logger.debug("Suppressed pending info update: %s", _exc)
            continue

        # Info is now complete -- run duplicate check
        vendor_id = expense.get("vendor_id")
        same_vendor_result = sb.table("expenses_manual_COGS") \
            .select("*") \
            .eq("project", project_id) \
            .eq("vendor_id", vendor_id) \
            .execute()
        same_vendor = same_vendor_result.data or []

        dup_result = check_duplicate(expense, same_vendor, bills_map, cfg, lookups, hash_cache={})

        if dup_result.verdict == "ambiguous" and cfg.get("daneel_gpt_fallback_enabled") and dup_result.paired_expense_id:
            paired = next((e for e in same_vendor
                           if (e.get("expense_id") or e.get("id")) == dup_result.paired_expense_id), None)
            if paired:
                dup_result = gpt_resolve_ambiguous(
                    expense, paired, lookups,
                    min_confidence=int(cfg.get("daneel_gpt_fallback_confidence", 75)),
                )

        if dup_result.verdict in ("duplicate", "ambiguous", "need_info"):
            # Still problematic -- keep pending
            continue

        # All clear -- authorize
        if authorize_expense(sb, exp_id, project_id, "reprocessed_" + dup_result.rule):
            authorized += 1

        _resolve_pending_info(sb, exp_id)
        reprocessed += 1

    return {"status": "ok", "reprocessed": reprocessed, "authorized": authorized}


# ============================================================================
# Bill-level trigger (batch of expenses from same bill)
# ============================================================================

async def trigger_auto_auth_for_bill(
    expense_ids: List[str],
    bill_id: str,
    project_id: str,
    vendor_name: str = "",
    total_amount: float = 0.0,
):
    """
    Bill-level auto-auth: processes all expenses from the same bill in one pass.
    Posts a "reviewing" message, authorizes each through the rule engine in real-time,
    then posts a consolidated summary.
    Called after Andrew creates expenses from a receipt.
    """
    tag = "[DaneelBillAuth]"
    logger.info(f"{tag} START | bill={bill_id} | expenses={len(expense_ids)} | project={project_id}")

    try:
        # 1. Guard: check kill switch
        cfg = load_auto_auth_config()
        if not cfg.get("daneel_auto_auth_enabled"):
            logger.info(f"{tag} SKIPPED - auto-auth disabled | bill={bill_id}")
            return

        # 2. Load shared resources ONCE
        sb = _get_supabase()
        lookups = _load_lookups(sb)

        bills_result = sb.table("bills").select("bill_id, receipt_url, status, split_projects").execute()
        bills_map = {}
        receipt_groups = {}
        for b in (bills_result.data or []):
            nb = normalize_bill_id(b["bill_id"])
            bills_map[nb] = b
            url = (b.get("receipt_url") or "").strip()
            if url:
                receipt_groups.setdefault(url, set()).add(nb)

        logger.info(f"{tag} Resources loaded | lookups OK | bills_map={len(bills_map)} entries")

        # 3. Fetch all expenses in one query
        exp_result = sb.table("expenses_manual_COGS") \
            .select("*") \
            .in_("expense_id", expense_ids) \
            .execute()
        all_fetched = exp_result.data or []
        expenses = [e for e in all_fetched if e.get("status") == "pending"]
        logger.info(f"{tag} Fetched {len(expenses)} pending expenses ({len(all_fetched) - len(expenses)} skipped non-pending)")

        if not expenses:
            logger.info(f"{tag} No pending expenses to process | bill={bill_id}")
            return

        # Resolve vendor info
        first_vendor_id = expenses[0].get("vendor_id")
        vname = vendor_name or lookups["vendors"].get(first_vendor_id, "Unknown")
        calc_total = total_amount or sum(float(e.get("Amount") or 0) for e in expenses)

        # 4. Post "reviewing" message
        post_daneel_message(
            content=f"Reviewing **{len(expenses)}** expenses from **{vname}** (${calc_total:,.2f})...",
            project_id=project_id,
            channel_type="project_general",
            metadata={"type": "auto_auth_bill_review", "bill_id": bill_id, "count": len(expenses)},
        )
        logger.info(f"{tag} Posted reviewing message | bill={bill_id}")

        # 5. Receipt hash check ONCE per bill
        hash_collision = False
        norm_bill = normalize_bill_id(bill_id)
        bill_receipt_url = None
        if norm_bill in bills_map:
            bill_receipt_url = bills_map[norm_bill].get("receipt_url")
        if not bill_receipt_url and expenses:
            bill_receipt_url = _get_expense_receipt(expenses[0], bills_map)

        if cfg.get("daneel_receipt_hash_check_enabled", True) and bill_receipt_url:
            _hclient = httpx.Client(timeout=5.0)
            bill_hash = get_receipt_hash(bill_receipt_url, client=_hclient)
            if bill_hash:
                all_proj_result = sb.table("expenses_manual_COGS") \
                    .select("expense_id, bill_id") \
                    .eq("project", project_id) \
                    .execute()
                for other_exp in (all_proj_result.data or []):
                    if other_exp.get("expense_id") in expense_ids:
                        continue
                    other_bill = normalize_bill_id(other_exp.get("bill_id") or "")
                    if other_bill == norm_bill:
                        continue
                    other_receipt = None
                    if other_bill and other_bill in bills_map:
                        other_receipt = bills_map[other_bill].get("receipt_url")
                    if not other_receipt:
                        continue
                    other_hash = get_receipt_hash(other_receipt, client=_hclient)
                    if other_hash and other_hash == bill_hash:
                        exp_bill_info = bills_map.get(norm_bill, {})
                        oth_bill_info = bills_map.get(other_bill, {})
                        if (exp_bill_info.get("status") or "").lower() == "split" or \
                           (oth_bill_info.get("status") or "").lower() == "split":
                            continue
                        if (exp_bill_info.get("status") or "").lower() == "closed" and \
                           (oth_bill_info.get("status") or "").lower() == "closed":
                            hash_collision = True
                            reason_txt = (
                                f"Receipt hash match: same file on bill #{bill_id} "
                                f"and bill #{(other_exp.get('bill_id') or '').strip()} (both closed)"
                            )
                            logger.warning(f"{tag} Receipt hash: COLLISION | bill={bill_id} | {reason_txt}")
                            escalation_mentions = _resolve_mentions(sb, cfg, "daneel_accounting_mgr_users", "daneel_accounting_mgr_role")
                            msg = (f"{escalation_mentions} " if escalation_mentions else "") + \
                                  f"All **{len(expenses)}** expenses from **{vname}** flagged: {reason_txt}"
                            post_daneel_message(
                                content=msg,
                                project_id=project_id,
                                channel_type="project_general",
                                metadata={"type": "auto_auth_bill_escalation", "bill_id": bill_id},
                            )
                            break
            if not hash_collision:
                logger.info(f"{tag} Receipt hash: clear | bill={bill_id}")
            _hclient.close()
        else:
            logger.info(f"{tag} Receipt hash: skipped (disabled or no receipt URL) | bill={bill_id}")

        if hash_collision:
            return

        # 6. Load same-vendor expenses ONCE for duplicate checking
        same_vendor = []
        if first_vendor_id:
            sv_result = sb.table("expenses_manual_COGS") \
                .select("*") \
                .eq("project", project_id) \
                .eq("vendor_id", first_vendor_id) \
                .execute()
            same_vendor = sv_result.data or []
        logger.info(f"{tag} Loaded {len(same_vendor)} same-vendor expenses for duplicate check")

        # Resolve mentions for potential messages
        bookkeeping_mentions = _resolve_mentions(sb, cfg, "daneel_bookkeeping_users", "daneel_bookkeeping_role")
        escalation_mentions = _resolve_mentions(sb, cfg, "daneel_accounting_mgr_users", "daneel_accounting_mgr_role")

        # 7. Process each expense through rule engine
        authorized_list = []
        missing_info_list = []
        escalation_list = []
        duplicate_list = []
        decisions = []
        _hash_cache = {}

        for expense in expenses:
            exp_id = expense.get("expense_id") or expense.get("id")
            rt_checks = []

            # Health check
            missing = run_health_check(expense, cfg, bills_map)
            if missing:
                logger.info(f"{tag} Health {exp_id[:8]}... -> FAIL: {', '.join(missing)}")
                rt_checks.append({"check": "health", "passed": False, "detail": "Missing: " + ", ".join(missing)})
                missing_info_list.append({"expense": expense, "missing": missing})
                _track_pending_info(sb, exp_id, project_id, missing)
                decisions.append(_make_decision_entry(expense, lookups, "missing_info",
                                                       reason=f"Missing: {', '.join(missing)}",
                                                       missing_fields=missing,
                                                       checks=rt_checks))
                continue
            rt_checks.append({"check": "health", "passed": True, "detail": "All required fields present"})
            logger.info(f"{tag} Health {exp_id[:8]}... -> PASS")

            # Duplicate check
            dup_result = check_duplicate(expense, same_vendor, bills_map, cfg, lookups, hash_cache=_hash_cache)

            if dup_result.verdict == "duplicate":
                logger.info(f"{tag} Dup {exp_id[:8]}... -> DUPLICATE ({dup_result.rule})")
                rt_checks.append({"check": "duplicate", "passed": False, "detail": f"{dup_result.rule}: {dup_result.details}"})
                duplicate_list.append({"expense": expense, "rule": dup_result.rule, "details": dup_result.details})
                decisions.append(_make_decision_entry(expense, lookups, "duplicate",
                                                       rule=dup_result.rule, reason=dup_result.details,
                                                       checks=rt_checks))
                continue

            if dup_result.verdict == "need_info":
                logger.info(f"{tag} Dup {exp_id[:8]}... -> NEED_INFO ({dup_result.details})")
                rt_checks.append({"check": "duplicate", "passed": False, "detail": f"Need info: {dup_result.details}"})
                need_fields = ["receipt"] if "receipt" in dup_result.rule.lower() else ["bill_id", "receipt"]
                missing_info_list.append({"expense": expense, "missing": need_fields})
                _track_pending_info(sb, exp_id, project_id, need_fields)
                decisions.append(_make_decision_entry(expense, lookups, "need_info",
                                                       rule=dup_result.rule, reason=dup_result.details,
                                                       checks=rt_checks))
                continue

            if dup_result.verdict == "ambiguous":
                logger.warning(f"{tag} Dup {exp_id[:8]}... -> AMBIGUOUS ({dup_result.details})")
                rt_checks.append({"check": "duplicate", "passed": False, "detail": f"Ambiguous: {dup_result.details}"})
                escalation_list.append({"expense": expense, "reason": dup_result.details})
                decisions.append(_make_decision_entry(expense, lookups, "escalated",
                                                       rule=dup_result.rule, reason=dup_result.details,
                                                       checks=rt_checks))
                continue

            # All checks passed - authorize in real-time
            rt_checks.append({"check": "duplicate", "passed": True, "detail": f"R1-R9 clear: {dup_result.details}"})
            logger.info(f"{tag} Dup {exp_id[:8]}... -> CLEAR ({dup_result.rule})")
            if authorize_expense(sb, exp_id, project_id, dup_result.rule):
                authorized_list.append(expense)
                amt = f"${float(expense.get('Amount') or 0):,.2f}"
                logger.info(f"{tag} Authorized {exp_id[:8]}... | {vname} {amt}")
                decisions.append(_make_decision_entry(expense, lookups, "authorized",
                                                       rule=dup_result.rule, reason=dup_result.details,
                                                       checks=rt_checks))

        # 8. Compute summary stats (message deferred to periodic digest)
        total_auth = sum(float(e.get("Amount") or 0) for e in authorized_list)
        n_auth = len(authorized_list)
        n_total = len(expenses)
        n_missing = len(missing_info_list)
        n_dup = len(duplicate_list)
        n_esc = len(escalation_list)

        # 9. Save audit trail (digest will read these reports)
        _save_auth_report(sb, "bill_realtime", {
            "bill_id": bill_id,
            "authorized": n_auth,
            "missing_info": n_missing,
            "duplicates": n_dup,
            "escalated": n_esc,
            "total": n_total,
            "total_amount": total_auth,
        }, decisions, project_id=project_id)

        logger.info(f"{tag} DONE | bill={bill_id} | auth={n_auth}/{n_total} | missing={n_missing} | dup={n_dup} | escalated={n_esc}")

    except Exception as e:
        logger.error(f"{tag} trigger_auto_auth_for_bill error: {e}", exc_info=True)


# ============================================================================
# Event trigger (single expense, called as BackgroundTask)
# ============================================================================

async def trigger_auto_auth_check(expense_id: str, project_id: str):
    """
    Lightweight check for a single expense.
    Called as BackgroundTask after expense creation or update.
    """
    try:
        cfg = load_auto_auth_config()
        if not cfg.get("daneel_auto_auth_enabled"):
            return

        sb = _get_supabase()

        # Fetch the expense
        exp_result = sb.table("expenses_manual_COGS") \
            .select("*") \
            .eq("expense_id", expense_id) \
            .single() \
            .execute()

        if not exp_result.data:
            return
        expense = exp_result.data

        if expense.get("status") != "pending":
            return

        lookups = _load_lookups(sb)

        # Load bills (normalized keys)
        bills_result = sb.table("bills").select("bill_id, receipt_url, status, split_projects").execute()
        bills_map = {normalize_bill_id(b["bill_id"]): b for b in (bills_result.data or [])}

        # Post ACK message so humans know auto-auth is active
        vname = lookups["vendors"].get(expense.get("vendor_id"), "Unknown")
        amt = f"${float(expense.get('Amount') or 0):,.2f}"
        post_daneel_message(
            content=f"Reviewing expense from **{vname}** ({amt})...",
            project_id=project_id,
            channel_type="project_general",
            metadata={"type": "auto_auth_check", "expense_id": expense_id},
        )

        # Build checks trail for this expense
        rt_checks = []

        # Health check
        missing = run_health_check(expense, cfg, bills_map)
        if missing:
            rt_checks.append({"check": "health", "passed": False, "detail": "Missing: " + ", ".join(missing)})
            _track_pending_info(sb, expense_id, project_id, missing)
            # Save to report for periodic digest (no immediate message)
            decision = _make_decision_entry(expense, lookups, "missing_info",
                                            rule="HEALTH", reason=f"Missing: {', '.join(missing)}",
                                            missing_fields=missing, checks=rt_checks)
            _save_auth_report(sb, "realtime", {"missing_info": 1}, [decision],
                              project_id=project_id)
            return  # don't authorize yet
        rt_checks.append({"check": "health", "passed": True, "detail": "All required fields present"})

        # Receipt hash check (per-project, cross-bill)
        if cfg.get("daneel_receipt_hash_check_enabled", True):
            exp_bill_raw = (expense.get("bill_id") or "").strip()
            exp_bill_str = normalize_bill_id(exp_bill_raw)
            exp_receipt_url = _get_expense_receipt(expense, bills_map)
            if exp_receipt_url:
                _hclient_rt = httpx.Client(timeout=5.0)
                exp_hash = get_receipt_hash(exp_receipt_url, client=_hclient_rt)
                if exp_hash:
                    # Check all expenses in the project for same hash, different bill
                    all_proj_result = sb.table("expenses_manual_COGS") \
                        .select("expense_id, bill_id") \
                        .eq("project", project_id) \
                        .neq("expense_id", expense_id) \
                        .execute()
                    hash_collision = False
                    for other_exp in (all_proj_result.data or []):
                        other_bill = normalize_bill_id(other_exp.get("bill_id") or "")
                        if other_bill == exp_bill_str:
                            continue
                        # Resolve receipt URL via bills_map
                        other_receipt = None
                        if other_bill and other_bill in bills_map:
                            other_receipt = bills_map[other_bill].get("receipt_url")
                        if not other_receipt:
                            continue
                        other_hash = get_receipt_hash(other_receipt, client=_hclient_rt)
                        if other_hash and other_hash == exp_hash:
                            exp_bill_info = bills_map.get(exp_bill_str, {})
                            oth_bill_info = bills_map.get(other_bill, {})
                            exp_status = (exp_bill_info.get("status") or "").lower()
                            oth_status = (oth_bill_info.get("status") or "").lower()
                            # Split bills can share receipts across projects
                            if exp_status == "split" or oth_status == "split":
                                pass  # skip, not a duplicate
                            elif exp_status == "closed" and oth_status == "closed":
                                reason_txt = (
                                    f"Receipt hash match: same file on bill #{exp_bill_raw} "
                                    f"and bill #{(other_exp.get('bill_id') or '').strip()} (both closed)"
                                )
                                rt_checks.append({"check": "receipt_hash", "passed": False, "detail": reason_txt})
                                escalation_mentions = _resolve_mentions(sb, cfg, "daneel_accounting_mgr_users", "daneel_accounting_mgr_role")
                                vname = lookups["vendors"].get(expense.get("vendor_id"), "Unknown")
                                amt = f"${float(expense.get('Amount') or 0):,.2f}"
                                msg = (f"{escalation_mentions} " if escalation_mentions else "") + \
                                      f"Expense from **{vname}** ({amt}) flagged: {reason_txt}"
                                post_daneel_message(
                                    content=msg,
                                    project_id=project_id,
                                    channel_type="project_general",
                                    metadata={"type": "auto_auth_escalation", "count": 1},
                                )
                                hash_collision = True
                                break
                    if hash_collision:
                        _hclient_rt.close()
                        return
                    rt_checks.append({"check": "receipt_hash", "passed": True,
                                       "detail": "No cross-bill hash collision"})
                _hclient_rt.close()

        # Duplicate check
        vendor_id = expense.get("vendor_id")
        if vendor_id:
            same_vendor_result = sb.table("expenses_manual_COGS") \
                .select("*") \
                .eq("project", project_id) \
                .eq("vendor_id", vendor_id) \
                .execute()
            same_vendor = same_vendor_result.data or []
        else:
            same_vendor = []

        _rt_hash_cache = {}
        dup_result = check_duplicate(expense, same_vendor, bills_map, cfg, lookups, hash_cache=_rt_hash_cache)

        if dup_result.verdict == "duplicate":
            rt_checks.append({"check": "duplicate", "passed": False, "detail": f"{dup_result.rule}: {dup_result.details}"})
            logger.info(f"[DaneelAutoAuth] Duplicate detected: {expense_id} ({dup_result.rule})")
            decision = _make_decision_entry(expense, lookups, "duplicate",
                                            rule=dup_result.rule, reason=dup_result.details,
                                            checks=rt_checks)
            _save_auth_report(sb, "realtime", {"duplicates": 1}, [decision],
                              project_id=project_id)
            return

        if dup_result.verdict == "need_info":
            need_fields = ["receipt"] if "receipt" in dup_result.rule.lower() else ["bill_id", "receipt"]
            rt_checks.append({"check": "duplicate", "passed": False, "detail": f"Need info: {dup_result.details}"})
            _track_pending_info(sb, expense_id, project_id, need_fields)
            decision = _make_decision_entry(expense, lookups, "missing_info",
                                            rule=dup_result.rule, reason=dup_result.details,
                                            missing_fields=need_fields, checks=rt_checks)
            _save_auth_report(sb, "realtime", {"missing_info": 1}, [decision],
                              project_id=project_id)
            return

        if dup_result.verdict == "ambiguous":
            rt_checks.append({"check": "duplicate", "passed": False, "detail": f"Ambiguous: {dup_result.details}"})
            # Try GPT fallback if enabled
            if cfg.get("daneel_gpt_fallback_enabled") and dup_result.paired_expense_id:
                paired = next((e for e in same_vendor
                               if (e.get("expense_id") or e.get("id")) == dup_result.paired_expense_id), None)
                if paired:
                    gpt_result = gpt_resolve_ambiguous(
                        expense, paired, lookups,
                        min_confidence=int(cfg.get("daneel_gpt_fallback_confidence", 75)),
                    )
                    if gpt_result.verdict == "duplicate":
                        rt_checks.append({"check": "gpt_resolve", "passed": False, "detail": gpt_result.details})
                        logger.info(f"[DaneelAutoAuth] GPT flagged duplicate: {expense_id} ({gpt_result.rule})")
                        return
                    if gpt_result.verdict == "not_duplicate":
                        rt_checks.append({"check": "gpt_resolve", "passed": True, "detail": gpt_result.details})
                        if authorize_expense(sb, expense_id, project_id, gpt_result.rule):
                            vname = lookups["vendors"].get(expense.get("vendor_id"), "Unknown")
                            amt = f"${float(expense.get('Amount') or 0):,.2f}"
                            logger.info(f"[DaneelAutoAuth] GPT cleared + auto-authorized: {expense_id} ({vname} {amt})")
                            decision = _make_decision_entry(expense, lookups, "authorized",
                                                            rule=gpt_result.rule, reason=gpt_result.details,
                                                            checks=rt_checks)
                            _save_auth_report(sb, "realtime", {"authorized": 1}, [decision],
                                              project_id=project_id)
                        return

            # Still ambiguous -- save to report for digest (no immediate message)
            decision = _make_decision_entry(expense, lookups, "escalated",
                                            rule=dup_result.rule, reason=dup_result.details,
                                            checks=rt_checks)
            _save_auth_report(sb, "realtime", {"escalated": 1}, [decision],
                              project_id=project_id)
            return

        # Duplicate check passed
        rt_checks.append({"check": "duplicate", "passed": True, "detail": f"R1-R9 clear: {dup_result.details}"})

        # Authorize (result goes to periodic digest, no immediate message)
        decision = None
        if authorize_expense(sb, expense_id, project_id, dup_result.rule):
            vname = lookups["vendors"].get(expense.get("vendor_id"), "Unknown")
            amt = f"${float(expense.get('Amount') or 0):,.2f}"
            logger.info(f"[DaneelAutoAuth] Auto-authorized: {expense_id} ({vname} {amt})")
            decision = _make_decision_entry(expense, lookups, "authorized",
                                            rule=dup_result.rule, reason=dup_result.details,
                                            checks=rt_checks)

        # Save realtime decision to report log
        if decision:
            _save_auth_report(sb, "realtime", {"authorized": 1}, [decision],
                              project_id=project_id)

    except Exception as e:
        logger.error(f"[DaneelAutoAuth] trigger_auto_auth_check error: {e}")
