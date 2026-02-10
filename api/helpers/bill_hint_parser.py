# ============================================================================
# Bill Hint Parser - Cross-validation utility for receipt filenames & bill IDs
# ============================================================================
# Receipt filenames and bill_id strings often contain structured data like:
#   "Wayfair - 12-22-2025 - $1,108.41 ON HUB"
# This module parses vendor, date, and amount from these strings and
# cross-validates them against OCR or expense data.
#
# Used by: Andrew (receipt processing), Daneel (expense auto-auth)
# ============================================================================

import re
from datetime import datetime
from typing import Optional


def parse_bill_hint(text: str) -> dict:
    """
    Parse a filename or bill_id string for vendor, date, and amount hints.

    Examples:
        "Wayfair - 12-22-2025 - $1,108.41 ON HUB.jpg"
        "Home Depot - 01-15-2026 - $542.33"
        "Lowes - $1200.pdf"

    Returns dict with keys: vendor_hint, date_hint, amount_hint (any may be absent).
    Returns empty dict if nothing parseable.
    """
    if not text or not isinstance(text, str):
        return {}

    # Strip file extension
    name = re.sub(r'\.[a-zA-Z]{2,5}$', '', text).strip()
    if not name:
        return {}

    result = {}

    # 1. Extract dollar amount: $1,108.41 or $542.33 or $1200
    amount_match = re.search(r'\$([\d,]+\.?\d*)', name)
    if amount_match:
        amount_str = amount_match.group(1).replace(',', '')
        try:
            val = float(amount_str)
            if val > 0:
                result["amount_hint"] = val
        except ValueError:
            pass

    # 2. Extract date (multiple formats)
    # MM-DD-YYYY or MM/DD/YYYY
    md_match = re.search(r'(\d{1,2})[-/](\d{1,2})[-/](\d{4})', name)
    if md_match:
        for fmt in ('%m-%d-%Y', '%m/%d/%Y'):
            try:
                sep = '-' if '-' in md_match.group() else '/'
                date_str = f"{md_match.group(1)}{sep}{md_match.group(2)}{sep}{md_match.group(3)}"
                dt = datetime.strptime(date_str, fmt)
                result["date_hint"] = dt.date().isoformat()
                break
            except ValueError:
                continue

    # YYYY-MM-DD
    if "date_hint" not in result:
        iso_match = re.search(r'(\d{4})[-/](\d{1,2})[-/](\d{1,2})', name)
        if iso_match:
            try:
                dt = datetime.strptime(
                    f"{iso_match.group(1)}-{iso_match.group(2)}-{iso_match.group(3)}",
                    '%Y-%m-%d'
                )
                result["date_hint"] = dt.date().isoformat()
            except ValueError:
                pass

    # 3. Extract vendor: first segment before " - "
    parts = name.split(" - ")
    if parts:
        vendor = parts[0].strip()
        # Skip if it looks like a number, amount, or is empty
        if vendor and not re.match(r'^[\d$]', vendor):
            result["vendor_hint"] = vendor

    return result


def cross_validate_bill_hint(
    hint: dict,
    vendor_name: Optional[str] = None,
    amount: Optional[float] = None,
    date_str: Optional[str] = None,
    amount_tolerance: float = 0.01,
) -> dict:
    """
    Compare parsed bill hints against actual OCR/expense data.

    Args:
        hint: Output from parse_bill_hint()
        vendor_name: OCR-extracted or resolved vendor name
        amount: OCR-extracted or expense amount
        date_str: OCR-extracted or expense date (ISO format)
        amount_tolerance: Fractional tolerance for amount comparison (default 1%)

    Returns dict with keys:
        vendor_match (bool or absent), amount_match (bool or absent),
        date_match (bool or absent), mismatches (list of human-readable strings)
    """
    result = {"mismatches": []}

    # Vendor comparison (substring, case-insensitive)
    if hint.get("vendor_hint") and vendor_name:
        hint_v = hint["vendor_hint"].lower().strip()
        actual_v = vendor_name.lower().strip()
        result["vendor_match"] = hint_v in actual_v or actual_v in hint_v
        if not result["vendor_match"]:
            result["mismatches"].append(
                f"Vendor: filename says '{hint['vendor_hint']}', data says '{vendor_name}'"
            )

    # Amount comparison (tolerance-based)
    if hint.get("amount_hint") is not None and amount is not None and amount > 0:
        larger = max(hint["amount_hint"], amount)
        diff = abs(hint["amount_hint"] - amount)
        threshold = larger * amount_tolerance
        result["amount_match"] = diff <= threshold
        if not result["amount_match"]:
            result["mismatches"].append(
                f"Amount: filename says ${hint['amount_hint']:,.2f}, data says ${amount:,.2f}"
            )

    # Date comparison (exact on ISO date)
    if hint.get("date_hint") and date_str:
        actual_date = str(date_str)[:10]
        result["date_match"] = hint["date_hint"] == actual_date
        if not result["date_match"]:
            result["mismatches"].append(
                f"Date: filename says {hint['date_hint']}, data says {actual_date}"
            )

    return result
