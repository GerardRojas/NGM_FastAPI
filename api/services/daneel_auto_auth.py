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
from typing import Dict, Any, List, Optional
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx
from supabase import create_client, Client

from api.helpers.daneel_messenger import post_daneel_message, DANEEL_BOT_USER_ID

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
                except Exception:
                    pass
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


# ============================================================================
# Receipt hash (lightweight -- HTTP HEAD only, no file download)
# ============================================================================

def get_receipt_hash(receipt_url: Optional[str]) -> Optional[str]:
    """
    Get a lightweight fingerprint for a receipt file via HTTP HEAD.
    Uses ETag or Content-Length as proxy.  Returns None if unavailable.
    """
    if not receipt_url:
        return None
    try:
        with httpx.Client(timeout=5.0) as client:
            resp = client.head(receipt_url, follow_redirects=True)
            etag = resp.headers.get("etag")
            if etag:
                return f"etag:{etag}"
            cl = resp.headers.get("content-length")
            ct = resp.headers.get("content-type", "")
            if cl:
                return f"cl:{cl}:{ct}"
    except Exception as e:
        logger.warning(f"[DaneelAutoAuth] HEAD failed for {receipt_url}: {e}")
    return None


# ============================================================================
# GPT Vision -- extract invoice total from receipt image
# ============================================================================

_VISION_BILL_TOTAL_PROMPT = (
    "You are a financial document reader. Look at this invoice/receipt image "
    "and extract the GRAND TOTAL amount (the final amount due, including taxes "
    "and any additional charges).\n\n"
    "RESPOND with ONLY a JSON object:\n"
    "{\"total\": 1234.56, \"currency\": \"USD\", \"confidence\": 95}\n\n"
    "Rules:\n"
    "- \"total\" must be a number (no dollar signs, no commas)\n"
    "- If you see multiple totals, use the GRAND TOTAL / AMOUNT DUE / BALANCE DUE\n"
    "- \"confidence\" is 0-100 how sure you are this is the correct total\n"
    "- If you cannot read the total clearly, set confidence below 50\n"
    "- No preamble, no markdown, just the JSON object"
)


def gpt_vision_extract_bill_total(receipt_url: str, amount_tolerance: float = 0.05) -> Optional[float]:
    """
    Download a receipt image and ask GPT-4o-mini Vision for the invoice total.
    Returns the extracted amount or None on any failure (fail-open).
    """
    if not receipt_url:
        return None

    import json as _json
    import base64

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        logger.warning("[DaneelAutoAuth] Vision: no OPENAI_API_KEY")
        return None

    try:
        # 1. Download the receipt file
        with httpx.Client(timeout=15.0) as client:
            resp = client.get(receipt_url, follow_redirects=True)
            if resp.status_code != 200:
                logger.warning(f"[DaneelAutoAuth] Vision: download failed {resp.status_code}")
                return None
            file_content = resp.content
            content_type = resp.headers.get("content-type", "")

        # 2. Determine media type and convert to base64 image(s)
        if "pdf" in content_type.lower() or receipt_url.lower().endswith(".pdf"):
            # PDF: convert first page to image
            try:
                from pdf2image import convert_from_bytes
                import io
                import platform
                poppler_path = r'C:\poppler\poppler-24.08.0\Library\bin' if platform.system() == "Windows" else None
                images = convert_from_bytes(file_content, dpi=150, first_page=1, last_page=1,
                                            poppler_path=poppler_path)
                if not images:
                    logger.warning("[DaneelAutoAuth] Vision: PDF conversion produced no images")
                    return None
                buf = io.BytesIO()
                images[0].save(buf, format='PNG')
                buf.seek(0)
                b64_image = base64.b64encode(buf.getvalue()).decode('utf-8')
                media_type = "image/png"
            except Exception as e:
                logger.warning(f"[DaneelAutoAuth] Vision: PDF convert error: {e}")
                return None
        else:
            # Image: direct base64
            b64_image = base64.b64encode(file_content).decode('utf-8')
            media_type = content_type if content_type else "image/jpeg"

        # 3. Call GPT-4o-mini Vision
        from openai import OpenAI
        ai_client = OpenAI(api_key=api_key)
        response = ai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{
                "role": "user",
                "content": [
                    {"type": "text", "text": _VISION_BILL_TOTAL_PROMPT},
                    {"type": "image_url", "image_url": {
                        "url": f"data:{media_type};base64,{b64_image}",
                        "detail": "high"
                    }}
                ]
            }],
            temperature=0.1,
            max_tokens=100,
        )
        raw = response.choices[0].message.content.strip()

        # 4. Parse response
        # Strip markdown code fences if present
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        data = _json.loads(raw)
        total = float(data.get("total", 0))
        confidence = int(data.get("confidence", 0))

        if total <= 0 or confidence < 50:
            logger.info(f"[DaneelAutoAuth] Vision: low confidence ({confidence}%) or zero total")
            return None

        logger.info(f"[DaneelAutoAuth] Vision: extracted total=${total:,.2f} (confidence={confidence}%)")
        return total

    except Exception as e:
        logger.warning(f"[DaneelAutoAuth] Vision extract failed: {e}")
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
    bill_id = (expense.get("bill_id") or "").strip()
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
) -> DuplicateResult:
    """
    Check if expense is a duplicate of any same-vendor expense.
    Returns the first matching rule result.
    """
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
            # Check receipt hashes
            exp_hash = get_receipt_hash(exp_receipt)
            oth_hash = get_receipt_hash(oth_receipt)

            if exp_hash and oth_hash:
                bill_info = bills_map.get((expense.get("bill_id") or "").strip(), {})
                oth_bill_info = bills_map.get((other.get("bill_id") or "").strip(), {})
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
            exp_hash = get_receipt_hash(exp_receipt)
            oth_hash = get_receipt_hash(oth_receipt)
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

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return DuplicateResult("ambiguous", "GPT_NO_KEY", "OpenAI API key not configured")

    exp_a = _format_expense_for_gpt(expense, lookups)
    exp_b = _format_expense_for_gpt(other, lookups)
    user_msg = f"Expense A:\n{exp_a}\n\nExpense B:\n{exp_b}\n\nAre these duplicates?"

    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key)
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": _GPT_SYSTEM_PROMPT},
                {"role": "user", "content": user_msg},
            ],
            temperature=0.1,
            max_tokens=150,
        )
        raw = response.choices[0].message.content.strip()
        data = _json.loads(raw)

        verdict = data.get("verdict", "").lower()
        confidence = int(data.get("confidence", 0))
        reason = data.get("reason", "GPT analysis")

        if confidence < min_confidence:
            return DuplicateResult("ambiguous", "GPT_LOW_CONF",
                                   f"GPT confidence {confidence}% < threshold {min_confidence}%: {reason}")

        if verdict == "duplicate":
            return DuplicateResult("duplicate", "GPT_DUP",
                                   f"GPT ({confidence}%): {reason}",
                                   paired_expense_id=other.get("expense_id") or other.get("id"))
        else:
            return DuplicateResult("not_duplicate", "GPT_CLEAR",
                                   f"GPT ({confidence}%): {reason}")

    except Exception as e:
        logger.warning(f"[DaneelAutoAuth] GPT fallback failed: {e}")
        return DuplicateResult("ambiguous", "GPT_ERROR", f"GPT fallback error: {e}")


# ============================================================================
# GPT Batch Review (final sanity check before authorizing)
# ============================================================================

_GPT_BATCH_REVIEW_PROMPT = (
    "You are Daneel, a financial supervisor for a construction company. "
    "A rule engine has cleared the following expenses for auto-authorization. "
    "Review the batch and flag anything suspicious BEFORE they are authorized.\n\n"
    "FLAG these patterns:\n"
    "- Unusually high amounts compared to other expenses in the batch\n"
    "- Multiple expenses from the same vendor on the same day with similar amounts\n"
    "- Round-number amounts that look like estimates rather than real invoices (e.g. $10,000.00 exactly)\n"
    "- Anything that feels off based on common construction expense patterns\n\n"
    "RESPOND with ONLY a JSON object:\n"
    "- If all clear: {\"approve\": true, \"note\": \"brief summary\"}\n"
    "- If flagged: {\"approve\": false, \"flagged\": [list of 0-based indices], \"reason\": \"explanation\"}\n"
    "- Be conservative: when in doubt, approve. Only flag truly suspicious patterns.\n"
    "- No preamble, no markdown, just the JSON object"
)


def gpt_batch_review(candidates: List[dict], lookups: dict) -> dict:
    """
    GPT reviews a batch of expenses before authorization.
    Returns {"approve_all": True} or {"approve_all": False, "flagged_indices": [...], "reason": "..."}.
    Fails open (approves all) on any error.
    """
    if not candidates:
        return {"approve_all": True}

    import json as _json

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return {"approve_all": True}

    # Build summary table
    lines = []
    for i, exp in enumerate(candidates):
        vendor = lookups["vendors"].get(exp.get("vendor_id"), "Unknown")
        account = lookups["accounts"].get(exp.get("account_id"), "Unknown")
        amt = f"${float(exp.get('Amount') or 0):,.2f}"
        date = (exp.get("TxnDate") or "")[:10]
        desc = (exp.get("LineDescription") or "-")[:60]
        bill = exp.get("bill_id") or "-"
        lines.append(f"[{i}] {vendor} | {amt} | {date} | {account} | Bill#{bill} | {desc}")

    user_msg = f"Batch of {len(candidates)} expenses to authorize:\n\n" + "\n".join(lines)

    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key)
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": _GPT_BATCH_REVIEW_PROMPT},
                {"role": "user", "content": user_msg},
            ],
            temperature=0.2,
            max_tokens=300,
        )
        raw = response.choices[0].message.content.strip()
        data = _json.loads(raw)

        if data.get("approve", True):
            logger.info(f"[DaneelAutoAuth] Batch review APPROVED {len(candidates)} expenses: {data.get('note', '')}")
            return {"approve_all": True}

        flagged = data.get("flagged", [])
        reason = data.get("reason", "GPT flagged suspicious pattern")
        logger.info(f"[DaneelAutoAuth] Batch review FLAGGED indices {flagged}: {reason}")
        return {"approve_all": False, "flagged_indices": flagged, "reason": reason}

    except Exception as e:
        logger.warning(f"[DaneelAutoAuth] Batch review failed (approving all): {e}")
        return {"approve_all": True}


# ============================================================================
# Authorize a single expense (mirrors update_expense_status logic)
# ============================================================================

def authorize_expense(sb, expense_id: str, project_id: str, rule: str = "passed_all_checks") -> bool:
    """Set expense status to 'auth' as Daneel."""
    try:
        sb.table("expenses_manual_COGS").update({
            "status": "auth",
            "auth_status": True,
            "auth_by": DANEEL_BOT_USER_ID,
        }).eq("expense_id", expense_id).execute()

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
            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(trigger_project_budget_check(project_id))
            else:
                asyncio.run(trigger_project_budget_check(project_id))
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
    import json as _json
    try:
        sb.table("daneel_auth_reports").insert({
            "report_type": report_type,
            "project_id": project_id,
            "project_name": project_name,
            "summary": _json.dumps(summary),
            "decisions": _json.dumps(decisions),
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

def _build_batch_auth_message(authorized: List[dict], lookups: dict) -> str:
    total = sum(float(e.get("Amount") or 0) for e in authorized)
    lines = [
        f"**Expense Authorization Report**",
        f"Authorized **{len(authorized)}** expenses totaling **${total:,.2f}**",
        "",
        "| Vendor | Amount | Date | Bill # |",
        "|--------|--------|------|--------|",
    ]
    for e in authorized[:20]:  # cap table rows
        vname = lookups["vendors"].get(e.get("vendor_id"), "Unknown")
        amt = f"${float(e.get('Amount') or 0):,.2f}"
        date = (e.get("TxnDate") or "")[:10]
        bill = e.get("bill_id") or "-"
        lines.append(f"| {vname} | {amt} | {date} | {bill} |")
    if len(authorized) > 20:
        lines.append(f"| ... and {len(authorized) - 20} more | | | |")
    lines.append("")
    lines.append("All expenses passed health check and duplicate verification.")
    return "\n".join(lines)


def _build_missing_info_message(missing_list: List[dict], lookups: dict, mention_str: str) -> str:
    mention = mention_str if mention_str else ""
    lines = [
        f"{mention} The following expenses need additional information before authorization:".strip(),
        "",
        "| Vendor | Amount | Date | Missing |",
        "|--------|--------|------|---------|",
    ]
    for item in missing_list[:20]:
        e = item["expense"]
        vname = lookups["vendors"].get(e.get("vendor_id"), "Unknown")
        amt = f"${float(e.get('Amount') or 0):,.2f}"
        date = (e.get("TxnDate") or "")[:10]
        fields = ", ".join(item["missing"])
        lines.append(f"| {vname} | {amt} | {date} | {fields} |")
    if len(missing_list) > 20:
        lines.append(f"| ... and {len(missing_list) - 20} more | | | |")
    lines.append("")
    lines.append("Please update these expenses with the missing information.")
    return "\n".join(lines)


def _build_escalation_message(escalated: List[dict], lookups: dict, mention_str: str) -> str:
    mention = mention_str if mention_str else ""
    lines = [
        f"{mention} The following expenses require manual review:".strip(),
        "",
        "| Vendor | Amount | Date | Reason |",
        "|--------|--------|------|--------|",
    ]
    for item in escalated[:20]:
        e = item["expense"]
        vname = lookups["vendors"].get(e.get("vendor_id"), "Unknown")
        amt = f"${float(e.get('Amount') or 0):,.2f}"
        date = (e.get("TxnDate") or "")[:10]
        reason = item["reason"]
        lines.append(f"| {vname} | {amt} | {date} | {reason} |")
    if len(escalated) > 20:
        lines.append(f"| ... and {len(escalated) - 20} more | | | |")
    return "\n".join(lines)


_ANDREW_MISMATCH_CALLOUTS = [
    "@Andrew Heads up -- the numbers on this bill don't add up. Can you take a look and sort it out?",
    "@Andrew Found a discrepancy on this one. Mind double-checking the line items against the invoice total?",
    "@Andrew Something's off here -- the invoice total and the expense breakdown aren't matching. Could you reconcile this?",
    "@Andrew Hey, I ran the numbers and they don't line up. Can you review and fix the amounts on this bill?",
    "@Andrew Flagging this for you -- there's a gap between the invoice total and what was logged. Please review when you get a chance.",
]


def _build_mismatch_message(
    bill_id: str,
    bill_total_expected: float,
    expenses_sum: float,
    source: str,
    expenses: List[dict],
    lookups: dict,
    notify_andrew: bool = False,
) -> str:
    """
    Build a bill total mismatch notification.
    source: 'hint' (filename) or 'vision' (GPT Vision OCR).
    """
    import random
    diff = abs(bill_total_expected - expenses_sum)
    lines = [
        f"**Bill Amount Mismatch Detected** - Bill #{bill_id}",
        "",
        f"| Source | Invoice Total | Expenses Sum | Difference |",
        f"|--------|--------------|--------------|------------|",
        f"| {source.capitalize()} | ${bill_total_expected:,.2f} | ${expenses_sum:,.2f} | ${diff:,.2f} |",
        "",
    ]
    if expenses:
        lines.append("**Expenses on this bill:**")
        lines.append("")
        lines.append("| Vendor | Amount | Date | Description |")
        lines.append("|--------|--------|------|-------------|")
        for e in expenses[:15]:
            vname = lookups["vendors"].get(e.get("vendor_id"), "Unknown")
            amt = f"${float(e.get('Amount') or 0):,.2f}"
            date = (e.get("TxnDate") or "")[:10]
            desc = (e.get("LineDescription") or "-")[:40]
            lines.append(f"| {vname} | {amt} | {date} | {desc} |")
        if len(expenses) > 15:
            lines.append(f"| ... and {len(expenses) - 15} more | | | |")
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
    except Exception:
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
    except Exception:
        return ""


def _resolve_mentions(sb, cfg: dict, key_users: str, key_role: str) -> str:
    """Resolve mentions: try new user-based keys first, fall back to legacy role key."""
    mentions = _resolve_user_mentions(sb, cfg.get(key_users))
    if not mentions:
        mentions = _resolve_role_name(sb, cfg.get(key_role))
    return mentions


# ============================================================================
# Main orchestrator
# ============================================================================

async def run_auto_auth(process_all: bool = False, project_id: Optional[str] = None) -> dict:
    """
    Process pending expenses.
    process_all=False (default): only new since last run.
    process_all=True: process ALL pending expenses (backlog).
    project_id: if provided, only process expenses for this project.
    """
    cfg = load_auto_auth_config()

    # Per-project manual runs bypass the global auto-auth toggle
    if not project_id and not cfg.get("daneel_auto_auth_enabled"):
        return {"status": "disabled", "message": "Auto-auth is disabled"}

    sb = _get_supabase()
    lookups = _load_lookups(sb)

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
        return {"status": "ok", "message": "No new pending expenses", "authorized": 0}

    # Load all bills metadata
    bills_result = sb.table("bills").select("bill_id, receipt_url, status, split_projects").execute()
    bills_map = {}
    for b in (bills_result.data or []):
        bills_map[b["bill_id"]] = b

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
    except Exception:
        pass

    for pid, expenses in projects.items():
        authorized_list = []
        missing_info_list = []
        duplicate_list = []
        escalation_list = []
        decisions = []  # per-project decisions for report

        # Load all expenses for this project (for duplicate comparison)
        all_project = sb.table("expenses_manual_COGS") \
            .select("*") \
            .eq("project", pid) \
            .execute()
        all_project_expenses = all_project.data or []

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

        for expense in expenses:
            exp_id = expense.get("expense_id") or expense.get("id")
            exp_checks = []  # audit trail for this expense

            # 1. Health check (non-blocking)
            missing = run_health_check(expense, cfg, bills_map)
            if missing:
                exp_checks.append({"check": "health", "passed": False, "detail": "Missing: " + ", ".join(missing)})
                missing_info_list.append({"expense": expense, "missing": missing})
                _track_pending_info(sb, exp_id, pid, missing)
                decisions.append(_make_decision_entry(
                    expense, lookups, "missing_info",
                    rule="HEALTH", reason="Health check failed", missing_fields=missing,
                    checks=exp_checks))
            else:
                exp_checks.append({"check": "health", "passed": True, "detail": "All required fields present"})
                # Resolve any stale pending-info record from a previous run
                _resolve_pending_info(sb, exp_id)

            # 1b. Bill hint cross-validation (soft armoring layer)
            # Parse the receipt FILENAME (not bill_id) for embedded amount hints,
            # then compare against the SUM of all expenses on the same bill.
            from api.helpers.bill_hint_parser import parse_bill_hint, cross_validate_bill_hint
            from urllib.parse import unquote
            bill_id_str = (expense.get("bill_id") or "").strip()
            if bill_id_str and bill_id_str in bills_map:
                receipt_url = bills_map[bill_id_str].get("receipt_url") or ""
                # Extract filename from URL path and decode URL encoding
                hint_source = unquote(receipt_url.rsplit("/", 1)[-1]) if "/" in receipt_url else unquote(receipt_url)
                hint = parse_bill_hint(hint_source) if hint_source else {}
                if hint and hint.get("amount_hint") is not None:
                    # Sum ALL expenses with the same bill_id (bill total, not single line)
                    bill_total = sum(
                        float(e.get("Amount") or 0)
                        for e in all_project_expenses
                        if (e.get("bill_id") or "").strip() == bill_id_str
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
                                             if (e.get("bill_id") or "").strip() == bill_id_str]
                            mismatch_msg = _build_mismatch_message(
                                bill_id_str, hint["amount_hint"], bill_total, "hint",
                                bill_expenses, lookups,
                                notify_andrew=cfg.get("daneel_mismatch_notify_andrew", True))
                            post_daneel_message(
                                content=mismatch_msg, project_id=pid,
                                channel_type="project_general",
                                metadata={"type": "bill_mismatch", "bill_id": bill_id_str,
                                          "source": "hint"})
                        continue
                    else:
                        exp_checks.append({"check": "bill_hint", "passed": True,
                                           "detail": f"Bill total ${bill_total:,.2f} matches hint ${hint['amount_hint']:,.2f}"})
                else:
                    # No amount in filename -- fall back to GPT Vision OCR
                    if receipt_url:
                        if bill_id_str in _vision_cache:
                            vision_total = _vision_cache[bill_id_str]
                        else:
                            vision_total = gpt_vision_extract_bill_total(receipt_url)
                            _vision_cache[bill_id_str] = vision_total
                        if vision_total is not None:
                            bill_total = sum(
                                float(e.get("Amount") or 0)
                                for e in all_project_expenses
                                if (e.get("bill_id") or "").strip() == bill_id_str
                            )
                            tolerance = float(cfg.get("daneel_amount_tolerance", 0.05))
                            larger = max(vision_total, bill_total) if bill_total else vision_total
                            diff = abs(vision_total - bill_total)
                            if larger > 0 and diff > larger * tolerance:
                                reason_txt = (
                                    f"Vision OCR amount mismatch: invoice total ${vision_total:,.2f} "
                                    f"vs expenses sum ${bill_total:,.2f}"
                                )
                                exp_checks.append({"check": "bill_hint_vision", "passed": False, "detail": reason_txt})
                                escalation_list.append({"expense": expense, "reason": reason_txt})
                                decisions.append(_make_decision_entry(
                                    expense, lookups, "escalated", rule="BILL_HINT_VISION", reason=reason_txt,
                                    checks=exp_checks))
                                # Mismatch notification (once per bill)
                                if bill_id_str not in _mismatch_notified:
                                    _mismatch_notified.add(bill_id_str)
                                    bill_expenses = [e for e in all_project_expenses
                                                     if (e.get("bill_id") or "").strip() == bill_id_str]
                                    mismatch_msg = _build_mismatch_message(
                                        bill_id_str, vision_total, bill_total, "vision",
                                        bill_expenses, lookups,
                                        notify_andrew=cfg.get("daneel_mismatch_notify_andrew", True))
                                    post_daneel_message(
                                        content=mismatch_msg, project_id=pid,
                                        channel_type="project_general",
                                        metadata={"type": "bill_mismatch", "bill_id": bill_id_str,
                                                  "source": "vision"})
                                continue
                            else:
                                exp_checks.append({"check": "bill_hint_vision", "passed": True,
                                                   "detail": f"Vision OCR total ${vision_total:,.2f} matches expenses sum ${bill_total:,.2f}"})
                        else:
                            exp_checks.append({"check": "bill_hint", "passed": True,
                                               "detail": "No filename hint; Vision OCR could not extract total"})
                    else:
                        exp_checks.append({"check": "bill_hint", "passed": True,
                                           "detail": "No amount hint in receipt filename (no receipt URL)"})
            else:
                exp_checks.append({"check": "bill_hint", "passed": True,
                                   "detail": "No bill" if not bill_id_str else "Bill not in bills table"})

            # 2. Duplicate check
            vendor_id = expense.get("vendor_id")
            same_vendor = by_vendor.get(vendor_id, []) if vendor_id else []
            dup_result = check_duplicate(expense, same_vendor, bills_map, cfg, lookups)

            if dup_result.verdict == "duplicate":
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

        # Phase 2: GPT batch review -- final sanity check before authorizing
        if auth_candidates and cfg.get("daneel_gpt_fallback_enabled"):
            review = gpt_batch_review([c[0] for c in auth_candidates], lookups)
            if not review.get("approve_all"):
                flagged_set = set(review.get("flagged_indices", []))
                reason = review.get("reason", "GPT batch review flagged suspicious pattern")
                surviving = []
                for i, (exp, rule, det, chks) in enumerate(auth_candidates):
                    if i in flagged_set:
                        chks.append({"check": "gpt_batch", "passed": False, "detail": reason})
                        escalation_list.append({"expense": exp, "reason": f"[Batch review] {reason}"})
                        decisions.append(_make_decision_entry(
                            exp, lookups, "escalated", rule="GPT_BATCH", reason=reason,
                            checks=chks))
                    else:
                        chks.append({"check": "gpt_batch", "passed": True, "detail": "Batch review approved"})
                        surviving.append((exp, rule, det, chks))
                auth_candidates = surviving
            else:
                # All approved by batch review
                for exp, rule, det, chks in auth_candidates:
                    chks.append({"check": "gpt_batch", "passed": True, "detail": "Batch review approved"})

        # Phase 3: Authorize all approved candidates
        for expense, rule, det, chks in auth_candidates:
            exp_id = expense.get("expense_id") or expense.get("id")
            if authorize_expense(sb, exp_id, pid, rule):
                authorized_list.append(expense)
                decisions.append(_make_decision_entry(
                    expense, lookups, "authorized", rule=rule, reason=det,
                    checks=chks))

        # Phase 4: Post batch messages for this project
        if authorized_list:
            msg = _build_batch_auth_message(authorized_list, lookups)
            post_daneel_message(
                content=msg,
                project_id=pid,
                channel_type="project_general",
                metadata={"type": "auto_auth_batch", "count": len(authorized_list),
                          "total": sum(float(e.get("Amount") or 0) for e in authorized_list)},
            )

        if missing_info_list:
            msg = _build_missing_info_message(missing_info_list, lookups, bookkeeping_mentions)
            post_daneel_message(
                content=msg,
                project_id=pid,
                channel_type="project_general",
                metadata={"type": "auto_auth_missing_info", "count": len(missing_info_list)},
            )

        if escalation_list:
            msg = _build_escalation_message(escalation_list, lookups, escalation_mentions)
            post_daneel_message(
                content=msg,
                project_id=pid,
                channel_type="project_general",
                metadata={"type": "auto_auth_escalation", "count": len(escalation_list)},
            )

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

    # Update last run timestamp (skip for per-project runs to not affect global runs)
    if not project_id:
        _save_config_key("daneel_auto_auth_last_run", now)

    summary = {
        "status": "ok",
        "authorized": total_authorized,
        "missing_info": total_missing,
        "duplicates": total_duplicates,
        "escalated": total_escalated,
        "expenses_processed": len(pending),
        "missing_detail": missing_detail[:20],  # cap for response size
    }
    logger.info(f"[DaneelAutoAuth] Run complete: {summary}")

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

async def reprocess_pending_info() -> dict:
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

    # Load bills
    bills_result = sb.table("bills").select("bill_id, receipt_url, status, split_projects").execute()
    bills_map = {b["bill_id"]: b for b in (bills_result.data or [])}

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
            except Exception:
                pass
            continue

        # Info is now complete -- run duplicate check
        vendor_id = expense.get("vendor_id")
        same_vendor_result = sb.table("expenses_manual_COGS") \
            .select("*") \
            .eq("project", project_id) \
            .eq("vendor_id", vendor_id) \
            .execute()
        same_vendor = same_vendor_result.data or []

        dup_result = check_duplicate(expense, same_vendor, bills_map, cfg, lookups)

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

        # Load bills
        bills_result = sb.table("bills").select("bill_id, receipt_url, status, split_projects").execute()
        bills_map = {b["bill_id"]: b for b in (bills_result.data or [])}

        # Build checks trail for this expense
        rt_checks = []

        # Health check
        missing = run_health_check(expense, cfg, bills_map)
        if missing:
            rt_checks.append({"check": "health", "passed": False, "detail": "Missing: " + ", ".join(missing)})
            _track_pending_info(sb, expense_id, project_id, missing)
            # Post individual missing info message
            bookkeeping_mentions = _resolve_mentions(sb, cfg, "daneel_bookkeeping_users", "daneel_bookkeeping_role")
            vname = lookups["vendors"].get(expense.get("vendor_id"), "Unknown")
            amt = f"${float(expense.get('Amount') or 0):,.2f}"
            fields_str = ", ".join(missing)
            msg = (f"{bookkeeping_mentions} " if bookkeeping_mentions else "") + \
                  f"Expense from **{vname}** ({amt}) is missing: **{fields_str}**. " \
                  f"Please provide to proceed with authorization."
            post_daneel_message(
                content=msg,
                project_id=project_id,
                channel_type="project_general",
                metadata={"type": "auto_auth_missing_info", "count": 1},
            )
            return  # don't authorize yet
        rt_checks.append({"check": "health", "passed": True, "detail": "All required fields present"})

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

        dup_result = check_duplicate(expense, same_vendor, bills_map, cfg, lookups)

        if dup_result.verdict == "duplicate":
            rt_checks.append({"check": "duplicate", "passed": False, "detail": f"{dup_result.rule}: {dup_result.details}"})
            logger.info(f"[DaneelAutoAuth] Duplicate detected: {expense_id} ({dup_result.rule})")
            return

        if dup_result.verdict == "need_info":
            need_fields = ["receipt"] if "receipt" in dup_result.rule.lower() else ["bill_id", "receipt"]
            rt_checks.append({"check": "duplicate", "passed": False, "detail": f"Need info: {dup_result.details}"})
            _track_pending_info(sb, expense_id, project_id, need_fields)
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

            # Still ambiguous -- escalate to human
            escalation_mentions = _resolve_mentions(sb, cfg, "daneel_accounting_mgr_users", "daneel_accounting_mgr_role")
            vname = lookups["vendors"].get(expense.get("vendor_id"), "Unknown")
            amt = f"${float(expense.get('Amount') or 0):,.2f}"
            msg = (f"{escalation_mentions} " if escalation_mentions else "") + \
                  f"Expense from **{vname}** ({amt}) requires manual review: {dup_result.details}"
            post_daneel_message(
                content=msg,
                project_id=project_id,
                channel_type="project_general",
                metadata={"type": "auto_auth_escalation", "count": 1},
            )
            return

        # Duplicate check passed
        rt_checks.append({"check": "duplicate", "passed": True, "detail": f"R1-R9 clear: {dup_result.details}"})

        # GPT batch review as final check
        if cfg.get("daneel_gpt_fallback_enabled"):
            review = gpt_batch_review([expense], lookups)
            if not review.get("approve_all"):
                reason = review.get("reason", "GPT flagged suspicious pattern")
                rt_checks.append({"check": "gpt_batch", "passed": False, "detail": reason})
                escalation_mentions = _resolve_mentions(sb, cfg, "daneel_accounting_mgr_users", "daneel_accounting_mgr_role")
                vname = lookups["vendors"].get(expense.get("vendor_id"), "Unknown")
                amt = f"${float(expense.get('Amount') or 0):,.2f}"
                msg = (f"{escalation_mentions} " if escalation_mentions else "") + \
                      f"Expense from **{vname}** ({amt}) flagged by batch review: {reason}"
                post_daneel_message(
                    content=msg,
                    project_id=project_id,
                    channel_type="project_general",
                    metadata={"type": "auto_auth_escalation", "count": 1},
                )
                return
            rt_checks.append({"check": "gpt_batch", "passed": True, "detail": "Batch review approved"})

        # Authorize
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
