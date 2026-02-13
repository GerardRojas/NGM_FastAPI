# services/receipt_regex.py
# ============================================================================
# Regex-based pre-extraction of receipt/invoice metadata from pdfplumber text.
#
# Extracts structural data (totals, tax, date, bill_id, line items) so GPT
# only needs to handle vendor matching + description cleanup.
#
# Test locally:
#   python services/receipt_regex.py path/to/invoice.pdf
#   python services/receipt_regex.py path/to/invoice.pdf --raw
# ============================================================================

import json
import re
import sys
from typing import List, Optional, Tuple

# ── Dollar amount regex ─────────────────────────────────────────
# Matches: $1,234.56 | $1234.56 | 1,234.56 | 1234.56
# Requires 2 decimal places (avoids matching years like 2024)
_AMT_RE = re.compile(r'\$\s*(\d{1,3}(?:,\d{3})*\.\d{2})')
# Fallback: amount without $ sign (used only when $ version fails)
_AMT_NOSIGN_RE = re.compile(r'(?<!\d)(\d{1,3}(?:,\d{3})*\.\d{2})(?!\d)')

_MONTH_MAP = {
    'jan': '01', 'feb': '02', 'mar': '03', 'apr': '04',
    'may': '05', 'jun': '06', 'jul': '07', 'aug': '08',
    'sep': '09', 'oct': '10', 'nov': '11', 'dec': '12',
}

# Lines to skip when extracting line items
_SKIP_PATTERNS = [
    r'GRAND\s+TOTAL', r'TOTAL\s+DUE', r'AMOUNT\s+DUE', r'BALANCE\s+DUE',
    r'PLEASE\s+PAY', r'(?:SUB|MERCH\w*)\s*-?\s*TOTAL',
    r'\bTOTAL\s*:?\s*\$',                     # Standalone "TOTAL $X" or "Total: $X"
    r'^ORDER\s+TOTAL', r'^ORDER\s+\$',         # "ORDER $X" (split ORDER/TOTAL)
    r'MERCHANDISE\s+TOTAL',                     # Summary total lines
    r'SALES\s+TAX', r'STATE\s+TAX', r'COUNTY\s+TAX', r'CITY\s+TAX',
    r'LOCAL\s+TAX', r'(?<!\w)TAX(?:\s+\d)', r'\bHST\b', r'\bGST\b',
    r'\bPST\b', r'\bVAT\b', r'\bIVA\b',
    r'(?<!\w)TAX\s*:?\s*\$',                   # "Tax $X" (tax amount line)
    r'^\bTAXES?\b\s',                          # "Taxes $X" or "Tax $X" at line start
    r'TAX\s+EXEMPT',                            # Tax exemption lines
    r'\$/\s*(?:piece|each|unit|ft|yd|sq|lb|oz|gal|ton|hr|hour)',  # Unit price lines
    r'CHANGE\s+DUE', r'CASH\s+TENDERED', r'AMOUNT\s+TENDERED',
    r'CARD\s+ENDING', r'APPROVAL\s+CODE', r'AUTH\s+CODE',
    r'THANK\s+YOU', r'HAVE\s+A\s+', r'RETURN\s+POLICY',
    r'SOLD\s+TO', r'SHIP\s+TO', r'BILL\s+TO',
    r'PAGE\s+\d', r'^-{3,}', r'^={3,}',
]
_SKIP_RE = re.compile('|'.join(_SKIP_PATTERNS), re.IGNORECASE)

# Column header lines (no useful data)
_HEADER_WORDS = [
    'DESCRIPTION', 'QTY', 'QUANTITY', 'UNIT\\s*PRICE', 'PRICE\\s*EACH',
    'RATE', 'AMOUNT', 'EXT(?:ENSION)?', 'UOM', 'UM', 'ITEM', 'SKU',
]
_HEADER_RE = re.compile(
    r'^\s*(?:' + '|'.join(_HEADER_WORDS) + r')(?:\s+(?:' + '|'.join(_HEADER_WORDS) + r'))*\s*$',
    re.IGNORECASE,
)


# ── Helpers ─────────────────────────────────────────────────────

def _parse_amt(s: str) -> float:
    """Convert '1,234.56' to 1234.56."""
    return float(s.replace(',', ''))


def _find_amounts(line: str) -> List[float]:
    """Find all dollar amounts in a line. Prefer $-prefixed, fallback to bare."""
    hits = _AMT_RE.findall(line)
    if hits:
        return [_parse_amt(h) for h in hits]
    hits = _AMT_NOSIGN_RE.findall(line)
    return [_parse_amt(h) for h in hits]


# ── Extractors ──────────────────────────────────────────────────

def _extract_grand_total(text: str) -> Optional[float]:
    """Extract the invoice grand total."""
    lines = text.split('\n')

    # Pass 1: Explicit labels (highest confidence)
    for line in lines:
        stripped = line.strip()
        if re.match(
            r'(?:GRAND\s+TOTAL|TOTAL\s+DUE|AMOUNT\s+DUE|BALANCE\s+DUE|PLEASE\s+PAY)\s*:?',
            stripped, re.IGNORECASE
        ):
            amts = _find_amounts(line)
            if amts:
                return amts[-1]

    # Pass 2: Last standalone TOTAL (not SUB/MERCH/TAX/SAVINGS)
    skip_words = [
        'SUBTOTAL', 'SUB TOTAL', 'SUB-TOTAL', 'MERCHANDISE', 'MERCH',
        'TAX', 'SAVINGS', 'ITEMS SOLD', 'TOTAL ITEMS', 'TOTAL SOLD',
    ]
    last_total = None
    for line in lines:
        upper = line.strip().upper()
        if 'TOTAL' not in upper:
            continue
        if any(w in upper for w in skip_words):
            continue
        amts = _find_amounts(line)
        if amts:
            last_total = amts[-1]

    if last_total is not None:
        return last_total

    # Pass 3: "ORDER $X" as grand total (handles split "ORDER"/"TOTAL" across lines)
    for line in lines:
        stripped = line.strip()
        if re.match(r'^ORDER\s+\$', stripped, re.IGNORECASE):
            amts = _find_amounts(line)
            if amts:
                return amts[-1]

    return None


def _extract_subtotal(text: str) -> Optional[float]:
    """Extract subtotal / merchandise total."""
    for line in text.split('\n'):
        stripped = line.strip()
        if re.match(
            r'(?:SUB\s*-?\s*TOTAL|MERCHANDISE\s+TOTAL|MERCH\.?\s+TOTAL)\s*:?',
            stripped, re.IGNORECASE
        ):
            amts = _find_amounts(line)
            if amts:
                return amts[-1]
    return None


def _extract_tax(text: str) -> Tuple[Optional[float], Optional[str]]:
    """Extract tax amount(s) and label. Sums multiple tax lines.

    Uses finditer to handle merged PDF columns where tax info appears
    mid-line (e.g. "Tax Exempt: No Tax $84.47").
    """
    total_tax = 0.0
    first_label = None

    _TAX_KW = re.compile(
        r'\b(SALES\s+TAX|STATE\s+TAX|COUNTY\s+TAX|CITY\s+TAX|LOCAL\s+TAX|'
        r'TAX|HST|GST|PST|VAT|IVA)\b'
        r'(?:\s+\d+\.?\d*\s*%)?',
        re.IGNORECASE,
    )

    for line in text.split('\n'):
        stripped = line.strip()

        for m in _TAX_KW.finditer(stripped):
            # Check what precedes - exclude "before tax", "pre-tax", etc.
            before_kw = stripped[:m.start()].rstrip()
            if re.search(r'(?:BEFORE|PRE[-\s]?|EXCLUD)', before_kw, re.IGNORECASE):
                continue

            # Check what follows - exclude false positives
            rest = stripped[m.end():].lstrip()
            if re.match(r'(?:ABLE|ID|EXEMPT|RATE|TERMS|COUNTRY|STATE|INCLUDED)',
                        rest, re.IGNORECASE):
                continue

            # Find dollar amount in the remainder of the line after the keyword
            after_keyword = stripped[m.start():]
            amts = _AMT_RE.findall(after_keyword)
            if not amts:
                amts = _AMT_NOSIGN_RE.findall(after_keyword)

            if amts:
                total_tax += _parse_amt(amts[0])
                if first_label is None:
                    first_label = m.group(1).strip()
                break  # One tax capture per line

    if total_tax > 0:
        return round(total_tax, 2), first_label
    return None, None


def _extract_date(text: str) -> Optional[str]:
    """Extract date in YYYY-MM-DD format."""
    lines = text.split('\n')

    # Pass 1: Labeled dates (Date:, Invoice Date:, etc.)
    for line in lines:
        m = re.search(
            r'(?:Invoice\s+Date|Transaction\s+Date|Order\s+Date|'
            r'Receipt\s+Date|Sold\s+Date|Date)\s*:?\s*'
            r'(\d{1,2})[/\-](\d{1,2})[/\-](\d{2,4})',
            line, re.IGNORECASE,
        )
        if m:
            mo, dy, yr = m.group(1), m.group(2), m.group(3)
            if len(yr) == 2:
                yr = '20' + yr
            return f"{yr}-{mo.zfill(2)}-{dy.zfill(2)}"

        # Labeled with month name: Date: Jan 15, 2025
        m = re.search(
            r'(?:Invoice\s+Date|Transaction\s+Date|Order\s+Date|'
            r'Receipt\s+Date|Sold\s+Date|Date)\s*:?\s*'
            r'(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+'
            r'(\d{1,2}),?\s+(\d{4})',
            line, re.IGNORECASE,
        )
        if m:
            mon = _MONTH_MAP.get(m.group(1)[:3].lower(), '01')
            return f"{m.group(3)}-{mon}-{m.group(2).zfill(2)}"

    # Pass 2: ISO format anywhere
    m = re.search(r'(\d{4})-(\d{2})-(\d{2})', text)
    if m:
        return m.group(0)

    # Pass 3: Month name format anywhere in first 15 lines
    for line in lines[:15]:
        m = re.search(
            r'(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+'
            r'(\d{1,2}),?\s+(\d{4})',
            line, re.IGNORECASE,
        )
        if m:
            mon = _MONTH_MAP.get(m.group(1)[:3].lower(), '01')
            return f"{m.group(3)}-{mon}-{m.group(2).zfill(2)}"

    # Pass 4: Unlabeled MM/DD/YYYY in first 15 lines
    for line in lines[:15]:
        m = re.search(r'(\d{1,2})/(\d{1,2})/(\d{2,4})', line)
        if m:
            mo, dy, yr = m.group(1), m.group(2), m.group(3)
            if len(yr) == 2:
                yr = '20' + yr
            return f"{yr}-{mo.zfill(2)}-{dy.zfill(2)}"

    return None


def _extract_bill_id(text: str) -> Optional[str]:
    """Extract invoice/receipt/order number.

    Searches per-line to avoid cross-line false matches
    (e.g. 'INVOICE\\nNotice' matching 'tice' as bill_id).
    """
    # Pass 1: Labeled patterns per line (Invoice #, Receipt No., Order: ...)
    # \b boundaries prevent matching inside words (e.g. "PO" in "Polymer")
    for line in text.split('\n'):
        m = re.search(
            r'\b(?:Invoice|Receipt|Order|PO|Ref|Document|Confirmation|Bill|Trans(?:action)?)\b'
            r'\s*(?:#|No\.?|Number|Num)?\s*:?\s*'
            r'([A-Z0-9][\w\-\.]{3,})',
            line, re.IGNORECASE,
        )
        if m:
            val = m.group(1).strip().rstrip('.')
            # Reject known false positives (words that follow Invoice/Order/etc.)
            if val.lower() in ('date', 'total', 'summary', 'details', 'status',
                               'terms', 'type', 'country', 'state', 'tice',
                               'confirmation', 'placed', 'number'):
                continue
            return val

    # Pass 2: Standalone "No." followed by alphanumeric ID (e.g. "No. H0679-622602")
    for line in text.split('\n'):
        m = re.search(r'\bNo\.\s*([A-Z0-9][\w\-\.]{3,})', line, re.IGNORECASE)
        if m:
            val = m.group(1).strip().rstrip('.')
            if re.match(r'^\d{1,2}$', val):  # Skip page numbers
                continue
            return val

    # Pass 3: Header line with "INVOICE" keyword, bill_id on next line
    # Handles merged column headers like "INVOICE # DATE TOTAL DUE ..."
    # where the actual ID is on the following line
    lines = text.split('\n')
    for i, line in enumerate(lines):
        upper = line.strip().upper()
        if re.search(r'\bINVOICE\b.*#', upper) and i + 1 < len(lines):
            next_line = lines[i + 1].strip()
            m = re.match(r'(\d{4,})', next_line)
            if m:
                return m.group(1)

    return None


def _extract_payment_hints(text: str) -> List[str]:
    """Detect payment method keywords in receipt text."""
    hints = []
    checks = [
        ('Visa', r'\bVISA\b|\bVS\b\s*[\u2022\*•]{2,}'),  # "VISA" or "VS ••••"
        ('Mastercard', r'\bMASTER\s*CARD\b'),
        ('Amex', r'\bAME(?:X|RICAN\s+EXPRESS)\b'),
        ('Discover', r'\bDISCOVER\b'),
        ('Debit', r'\bDEBIT\b'),
        ('Credit', r'\bCREDIT\b'),
        ('Cash', r'\bCASH\b'),
        ('Check', r'\bCHECK\b(?!\s+(?:your|the|this|out|in|for|if|on|back|status|order|current|mark))'),
        ('ACH', r'\bACH\b'),
    ]
    for name, pat in checks:
        if re.search(pat, text, re.IGNORECASE):
            hints.append(name)
    return hints


def _extract_shipping(text: str) -> Optional[float]:
    """Extract shipping/freight/delivery amount from receipt text."""
    for line in text.split('\n'):
        stripped = line.strip()
        m = re.match(
            r'(?:Shipping|Freight|Delivery|Ship\s+to\s+Store)'
            r'(?:\s*(?:&|and)\s*(?:Handling|Delivery))?\s*:?\s*',
            stripped, re.IGNORECASE,
        )
        if m:
            if 'FREE' in stripped.upper() or '$0.00' in stripped:
                return 0.0
            amts = _find_amounts(line)
            if amts:
                return amts[-1]
    return None


def _extract_line_items(
    text: str,
    grand_total: Optional[float],
    subtotal: Optional[float],
    tax_amount: Optional[float],
) -> List[dict]:
    """
    Best-effort extraction of line items.
    Returns list of {description: str, amount: float}.
    """
    items = []

    # Known amounts to exclude (totals, subtotals, tax)
    known = set()
    if grand_total:
        known.add(grand_total)
    if subtotal:
        known.add(subtotal)
    if tax_amount:
        known.add(tax_amount)

    for line in text.split('\n'):
        stripped = line.strip()
        if not stripped or len(stripped) < 4:
            continue

        # Skip metadata lines (totals, tax, headers, etc.)
        if _SKIP_RE.search(stripped):
            continue
        if _HEADER_RE.match(stripped):
            continue

        # Skip lines that are only numbers/symbols (no text)
        if re.match(r'^[\d\s\-\.\$/,%#]+$', stripped):
            continue

        # Find dollar amounts
        amts = _find_amounts(stripped)
        if not amts:
            continue

        # Line total = last amount (rightmost column in tabular layouts)
        line_amount = amts[-1]

        # Skip known totals/subtotals/tax
        if line_amount in known:
            continue

        # Skip if >= grand total (likely a missed total)
        if grand_total and line_amount >= grand_total:
            continue

        # Extract description: text before the first $ sign or first amount
        dollar_pos = stripped.find('$')
        if dollar_pos > 0:
            desc = stripped[:dollar_pos].strip()
        else:
            # No $ sign - find position of first amount match
            m = _AMT_NOSIGN_RE.search(stripped)
            desc = stripped[:m.start()].strip() if m else stripped

        # Clean up description
        desc = re.sub(r'[\.\s\-]+$', '', desc)       # trailing dots/dashes
        desc = re.sub(r'^\d{5,}\s*', '', desc)        # leading SKU (5+ digits)
        desc = re.sub(r'\s{2,}', ' ', desc)           # collapse spaces
        desc = desc.strip()

        if desc and line_amount > 0:
            item = {'description': desc, 'amount': line_amount}

            # Try to detect quantity: "80 x ITEM" or "QTY: 3" at start
            qty_match = re.match(r'^(\d+)\s*[xX@]\s+(.+)', desc)
            if qty_match:
                item['qty_hint'] = int(qty_match.group(1))
                item['description'] = qty_match.group(2).strip()

            items.append(item)

    return items


# ── Confidence computation (shared by general + vendor parsers) ─

def _compute_confidence(meta: dict) -> dict:
    """Compute confidence assessment for any extraction result.

    Grand total is the ANCHOR. We work backwards from it:
      - items + tax + shipping == grand_total  -> HIGH
      - subtotal + tax + shipping == grand_total -> HIGH (cross-validated)
      - items == subtotal -> useful even if grand_total has shipping gap
    """
    grand_total = meta.get('grand_total')
    subtotal = meta.get('subtotal')
    tax_amount = meta.get('tax_amount')
    items_sum = meta.get('items_sum', 0)
    shipping = meta.get('shipping') or 0

    # Cross-validation: subtotal + tax [+ shipping] == grand_total
    cross_valid = False
    if subtotal is not None and tax_amount is not None and grand_total is not None:
        diff_no_ship = abs((subtotal + tax_amount) - grand_total)
        diff_with_ship = abs((subtotal + tax_amount + shipping) - grand_total)
        cross_valid = min(diff_no_ship, diff_with_ship) <= 0.05

    items_match_subtotal = bool(subtotal is not None and abs(items_sum - subtotal) <= 0.10)
    items_match_grand = bool(grand_total is not None and abs(items_sum - grand_total) <= 0.10)

    # Grand-total-first: items + tax [+ shipping] == grand_total
    items_plus_tax_match = False
    if grand_total is not None and tax_amount is not None:
        check = items_sum + tax_amount + shipping
        items_plus_tax_match = abs(check - grand_total) <= 0.10

    gt_confidence = None
    if grand_total is not None:
        if cross_valid or items_match_grand or items_plus_tax_match:
            gt_confidence = 'high'
        elif subtotal or items_match_subtotal:
            gt_confidence = 'medium'
        else:
            gt_confidence = 'low'

    return {
        'grand_total': gt_confidence,
        'cross_validated': cross_valid,
        'items_match_subtotal': items_match_subtotal,
        'items_match_grand': items_match_grand,
        'items_plus_tax_match': items_plus_tax_match,
    }


# ── Main entry point (general regex) ──────────────────────────

def extract_receipt_metadata(text: str) -> dict:
    """
    Extract all available metadata from pdfplumber receipt text.

    Returns:
        {
            'grand_total': float | None,
            'subtotal': float | None,
            'tax_amount': float | None,
            'tax_label': str | None,
            'date': str (YYYY-MM-DD) | None,
            'bill_id': str | None,
            'payment_hints': [str],
            'line_items': [{'description': str, 'amount': float}],
            'items_sum': float,
            'confidence': {
                'grand_total': 'high' | 'medium' | 'low' | None,
                'cross_validated': bool,
                'items_match_subtotal': bool,
                'items_match_grand': bool,
            }
        }
    """
    grand_total = _extract_grand_total(text)
    subtotal = _extract_subtotal(text)
    tax_amount, tax_label = _extract_tax(text)
    shipping = _extract_shipping(text)
    date = _extract_date(text)
    bill_id = _extract_bill_id(text)
    payment_hints = _extract_payment_hints(text)
    line_items = _extract_line_items(text, grand_total, subtotal, tax_amount)

    items_sum = round(sum(i['amount'] for i in line_items), 2) if line_items else 0

    meta = {
        'grand_total': grand_total,
        'subtotal': subtotal,
        'tax_amount': tax_amount,
        'tax_label': tax_label,
        'shipping': shipping,
        'date': date,
        'bill_id': bill_id,
        'payment_hints': payment_hints,
        'line_items': line_items,
        'items_sum': items_sum,
    }
    meta['confidence'] = _compute_confidence(meta)
    return meta


# ── Pass 1: Noise cleaning ──────────────────────────────────────

def _is_noise(line: str) -> bool:
    """Return True if line is definitely noise (no useful receipt data)."""
    # GUARD: never filter lines with financial keywords + dollar amounts
    # (handles merged PDF columns like "875 DURWARD ST Estimated Tax: $12.64")
    if re.search(r'(?:TAX|TOTAL|SUBTOTAL|AMOUNT|BALANCE|SHIPPING)\b.*\$', line, re.IGNORECASE):
        return False
    # Separators
    if re.match(r'^[\-=\*\_\+]{3,}$', line):
        return True
    # Street addresses: "123 Main St" / "456 Commerce Blvd"
    if re.match(
        r'^\d+\s+[A-Z][a-z]+(?:\s+[A-Za-z]+)*\s+'
        r'(?:St|Ave|Blvd|Rd|Dr|Ln|Way|Ct|Pl|Pkwy|Hwy|Street|Avenue|Boulevard|Road|Drive|Lane)\b',
        line, re.IGNORECASE,
    ):
        return True
    # Suite/Apt lines
    if re.match(r'^(?:Suite|Ste|Apt|Unit|Floor|Fl)[\s#]*\d', line, re.IGNORECASE):
        return True
    # City, State ZIP (handles mixed case and ALL CAPS)
    if re.match(r'^[A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+)*,?\s+[A-Z]{2}\s+\d{5}', line):
        return True
    # Standalone phone numbers
    if re.match(r'^\(?\d{3}\)?[\s\-\.]\d{3}[\s\-\.]\d{4}\s*$', line):
        return True
    if re.match(r'^(?:Phone|Tel|Fax)\s*:?\s*\(?\d{3}\)', line, re.IGNORECASE):
        return True
    # URLs and emails
    if re.match(r'^(?:www\.|https?://|[a-z0-9._%+-]+@)', line, re.IGNORECASE):
        return True
    # Social media
    if re.match(r'^(?:Follow\s+us|Like\s+us|@\w)', line, re.IGNORECASE):
        return True
    # Courtesy messages
    if re.match(r'^(?:Thank\s+you|Thanks\s+for|Have\s+a|Welcome\s+to|Please\s+come)',
                line, re.IGNORECASE):
        return True
    if re.match(r'^(?:Valued?\s+customer|We\s+appreciate|Your\s+satisfaction)',
                line, re.IGNORECASE):
        return True
    # Return/refund policy
    if re.match(r'^(?:Return|Refund|Exchange)\s+(?:policy|within|must|items)',
                line, re.IGNORECASE):
        return True
    if re.match(r'^(?:Keep|Save)\s+(?:this\s+)?receipt', line, re.IGNORECASE):
        return True
    # Barcode / PLU (long digit-only strings)
    if re.match(r'^\d{10,}$', line):
        return True
    # Card chip/contactless processing details
    if re.match(r'^(?:AID|TC|TVR|TSI|IAD|CVM)\s*:', line, re.IGNORECASE):
        return True
    if re.match(r'^(?:Entry\s+Method|Chip\s+Read|Contactless|Swiped|Inserted)',
                line, re.IGNORECASE):
        return True
    # Copy labels
    if re.match(r'^(?:CUSTOMER|MERCHANT|CARDHOLDER)\s+COPY', line, re.IGNORECASE):
        return True
    # Page headers
    if re.match(r'^Page\s+\d+\s+(?:of|/)\s+\d+', line, re.IGNORECASE):
        return True
    # CID markers (PDF artifacts)
    if '(cid:' in line:
        return True
    # Continuation / end-of-section markers
    if re.search(r'CONTINUED\s+ON\s+NEXT\s+PAGE', line, re.IGNORECASE):
        return True
    if re.match(r'^END\s+OF\s+(?:ORDER|CARRY|MERCHANDISE|INVOICE)', line, re.IGNORECASE):
        return True
    # Item markdown / asterisk notes
    if re.match(r'^\*+\s*(?:Indicates|Note|All\s)', line, re.IGNORECASE):
        return True
    # Copy labels (Customer Copy, Merchant Copy)
    if re.search(r'(?:Customer|Merchant|Cardholder)\s+Copy', line, re.IGNORECASE):
        return True
    # "Check your order status" type lines
    if re.match(r'^Check\s+your\s+', line, re.IGNORECASE):
        return True
    # Policy/legal notices
    if re.match(r'^[\'"]?The\s+Home\s+Depot\s+reserves', line, re.IGNORECASE):
        return True
    if re.match(r'^(?:Notice\s+of\s+Cancellation|see\s+Exhibit)', line, re.IGNORECASE):
        return True
    # "Invoice summary reflects..." type disclaimer
    if re.match(r'^(?:Invoice|Order)\s+summary\s+reflects', line, re.IGNORECASE):
        return True
    # Standalone "REPRINT" or "DUPLICATE"
    if re.match(r'^(?:REPRINT|DUPLICATE)\s*$', line, re.IGNORECASE):
        return True
    return False


def clean_receipt_text(raw_text: str) -> str:
    """
    Pass 1: Remove known noise from pdfplumber text.
    Returns cleaned text (smaller input for regex Pass 2 or GPT fallback).
    """
    cleaned = []
    prev_blank = False

    for line in raw_text.split('\n'):
        stripped = line.strip()

        # Collapse consecutive blank lines to max 1
        if not stripped:
            if not prev_blank:
                cleaned.append('')
                prev_blank = True
            continue
        prev_blank = False

        if _is_noise(stripped):
            continue

        # Collapse excessive internal whitespace
        cleaned_line = re.sub(r'\s{3,}', '  ', stripped)
        cleaned.append(cleaned_line)

    return '\n'.join(cleaned).strip()


# ── Vendor / Payment matching (no GPT) ─────────────────────────

def fuzzy_match_vendor(text: str, vendors_list: List[str]) -> str:
    """Match receipt text against known vendor names. No GPT needed.

    Strategy:
      1. Exact substring in first 5 lines (case-insensitive)
      2. Any vendor word (4+ chars) appears in first 5 lines
      3. difflib fuzzy match
    """
    from difflib import get_close_matches

    first_lines = ' '.join(text.split('\n')[:5]).upper()

    # Pass 1: Exact substring (e.g. "HOME DEPOT" in "THE HOME DEPOT #4521")
    for vendor in vendors_list:
        if vendor == "Unknown":
            continue
        if vendor.upper() in first_lines:
            return vendor

    # Pass 2: Any significant vendor word present
    for vendor in vendors_list:
        if vendor == "Unknown":
            continue
        for word in vendor.upper().split():
            if len(word) >= 4 and word in first_lines:
                return vendor

    # Pass 3: difflib fuzzy
    matches = get_close_matches(first_lines[:80], vendors_list, n=1, cutoff=0.4)
    if matches and matches[0] != "Unknown":
        return matches[0]

    return "Unknown"


def match_payment_method(hints: List[str], payment_methods_list: List[dict]) -> str:
    """Match extracted payment hints to DB payment methods list.

    Args:
        hints: e.g. ['Visa', 'Credit'] from _extract_payment_hints
        payment_methods_list: [{'id': ..., 'name': 'Debit'}, ...]
    """
    if not payment_methods_list:
        return "Unknown"

    pm_names = [p.get('name', '') for p in payment_methods_list]

    # No hints at all → default to Debit
    if not hints:
        for pm_name in pm_names:
            if 'debit' in pm_name.lower():
                return pm_name
        return "Unknown"

    # Map hint keywords to what the DB name might contain
    hint_keywords = {
        'Visa': ['visa', 'credit'],
        'Mastercard': ['mastercard', 'master', 'credit'],
        'Amex': ['amex', 'american', 'credit'],
        'Discover': ['discover', 'credit'],
        'Debit': ['debit'],
        'Credit': ['credit'],
        'Cash': ['cash'],
        'Check': ['check'],
        'ACH': ['ach', 'transfer'],
    }

    for hint in hints:
        keywords = hint_keywords.get(hint, [hint.lower()])
        for pm_name in pm_names:
            for kw in keywords:
                if kw in pm_name.lower():
                    return pm_name

    # Fallback: always default to Debit (most receipts are card payments)
    for pm_name in pm_names:
        if 'debit' in pm_name.lower():
            return pm_name

    return "Unknown"


# ── Result assembly (regex → scan_receipt output format) ────────

def assemble_scan_result(
    metadata: dict,
    vendors_list: List[str],
    txn_types_list: List[dict],
    payment_methods_list: List[dict],
    original_text: str = "",
) -> dict:
    """
    Build the final scan_receipt-compatible result dict from regex metadata.
    Handles vendor matching, payment matching, and tax distribution.

    Returns dict with keys: expenses, tax_summary, validation
    (same format as GPT-based scan_receipt output).
    """
    # Resolve vendor
    vendor = fuzzy_match_vendor(original_text or "", vendors_list)

    # Resolve payment method
    payment = match_payment_method(
        metadata.get('payment_hints', []), payment_methods_list
    )

    date = metadata.get('date') or ""
    bill_id = metadata.get('bill_id') or ""

    # Build expenses from regex line items (pre-tax amounts)
    expenses = []
    for item in metadata.get('line_items', []):
        qty_hint = item.get('qty_hint')
        desc = item['description']
        if qty_hint:
            desc = f"{qty_hint}x {desc}"

        expenses.append({
            'date': date,
            'bill_id': bill_id,
            'description': desc,
            'vendor': vendor,
            'amount': item['amount'],
            'transaction_type': 'Unknown',
            'payment_method': payment,
            'tax_included': 0,
        })

    # Tax distribution (in code, not GPT)
    tax_amount = metadata.get('tax_amount') or 0
    tax_summary = None
    line_items = metadata.get('line_items', [])
    has_item_tax = metadata.get('item_level_tax', False)

    if tax_amount > 0 and expenses:
        pre_tax_subtotal = sum(e['amount'] for e in expenses)

        if has_item_tax and len(line_items) == len(expenses):
            # Exact per-item tax from vendor-specific parser
            for i, exp in enumerate(expenses):
                item_tax = line_items[i].get('item_tax', 0)
                item_total = line_items[i].get('item_total')
                if item_total is not None:
                    exp['tax_included'] = item_tax
                    exp['amount'] = item_total
                else:
                    exp['tax_included'] = item_tax
                    exp['amount'] = round(exp['amount'] + item_tax, 2)
        elif pre_tax_subtotal > 0:
            # Proportional distribution
            distributed = 0.0
            for i, exp in enumerate(expenses):
                if i == len(expenses) - 1:
                    share = round(tax_amount - distributed, 2)
                else:
                    share = round(tax_amount * (exp['amount'] / pre_tax_subtotal), 2)
                    distributed += share
                exp['tax_included'] = share
                exp['amount'] = round(exp['amount'] + share, 2)

        tax_summary = {
            'total_tax_detected': tax_amount,
            'tax_label': metadata.get('tax_label') or 'Tax',
            'subtotal': round(pre_tax_subtotal, 2),
            'grand_total': metadata.get('grand_total'),
            'distribution': [
                {
                    'description': e['description'],
                    'original_amount': round(e['amount'] - e['tax_included'], 2),
                    'tax_added': e['tax_included'],
                    'final_amount': e['amount'],
                }
                for e in expenses
            ],
        }

    # Validation
    calc_sum = round(sum(e['amount'] for e in expenses), 2)
    invoice_total = metadata.get('grand_total') if metadata.get('grand_total') is not None else calc_sum
    diff = abs(calc_sum - invoice_total)

    validation = {
        'invoice_total': invoice_total,
        'calculated_sum': calc_sum,
        'validation_passed': diff <= 0.02,
        'validation_warning': (
            None if diff <= 0.02
            else f"Calculated sum ${calc_sum:.2f} does not match invoice total ${invoice_total:.2f}"
        ),
    }

    return {
        'expenses': expenses,
        'tax_summary': tax_summary,
        'validation': validation,
    }


# ── Vendor detection ──────────────────────────────────────────

_VENDOR_SIGNATURES = {
    'home_depot': [
        r'HOME\s+DEPOT',
        r'SPECIAL\s+SERVICES\s+CUSTOMER\s+INVOICE',
    ],
    'wayfair': [
        r'WAYFAIR',
        r'wayfair\.com',
    ],
    'sf_transport': [
        r'SF\s+TRANSPORT',
    ],
    'floor_decor': [
        r'FLOOR\s*&\s*DECOR',
        r'flooranddecor\.com',
    ],
    'amazon': [
        r'AMAZON\.COM',
        r'amazon\.com',
    ],
}


def detect_vendor_format(text: str) -> Optional[str]:
    """Detect vendor format from receipt text. Returns vendor key or None."""
    header = text[:600]
    for vendor, patterns in _VENDOR_SIGNATURES.items():
        for pat in patterns:
            if re.search(pat, header, re.IGNORECASE):
                return vendor
    return None


# ── Home Depot vendor parser ─────────────────────────────────

_HD_ITEM_RE = re.compile(
    r'^R(\d{2})\s+'                    # R## reference
    r'(\d{4}-\d{3}-\d{3})\s+'         # SKU (####-###-###)
    r'(\d+\.\d{2})\s+'                # Quantity
    r'([A-Z]{2})\s+'                   # Unit of measure
    r'(.+?)\s+'                        # Description (lazy up to Y $)
    r'Y\s+'                            # Tax indicator
    r'\$(\d[\d,.]*\.\d{2})\s+'         # Unit price
    r'\$(\d[\d,.]*\.\d{2})\*?\s*$',    # Extension (line total)
    re.IGNORECASE,
)


def _parse_home_depot(text: str) -> dict:
    """Parse Home Depot SPECIAL SERVICES CUSTOMER INVOICE format.

    Knows the exact columnar layout: R## SKU QTY UM Description / Y $UNIT $EXT
    """
    lines = text.split('\n')
    meta = {
        'grand_total': None, 'subtotal': None,
        'tax_amount': None, 'tax_label': None,
        'date': None, 'bill_id': None,
        'payment_hints': [], 'line_items': [],
        'items_sum': 0, 'vendor_detected': 'home_depot',
    }

    # Bill ID: "No. H####-######"
    for line in lines:
        m = re.search(r'\bNo\.\s*(H\d{4}-\d{6})', line)
        if m:
            meta['bill_id'] = m.group(1)
            break

    # Date: ISO format in header area (e.g. "2026-02-12 15:14")
    for line in lines[:25]:
        m = re.search(r'(\d{4}-\d{2}-\d{2})', line)
        if m:
            meta['date'] = m.group(1)
            break

    # Payment hints
    meta['payment_hints'] = _extract_payment_hints(text)

    # Line items: R## pattern with multi-line description support
    for idx, line in enumerate(lines):
        m = _HD_ITEM_RE.match(line.strip())
        if not m:
            continue

        qty = float(m.group(3))
        um = m.group(4)
        desc_raw = m.group(5).strip().rstrip('/').strip()
        extension = _parse_amt(m.group(7))

        # Append continuation lines (description wraps to next line, ends with /)
        for next_idx in range(idx + 1, min(idx + 3, len(lines))):
            next_line = lines[next_idx].strip()
            if not next_line or _HD_ITEM_RE.match(next_line):
                break
            if not re.search(r'\$\d', next_line):
                continuation = next_line.rstrip('/').strip()
                if continuation:
                    desc_raw = desc_raw + ' ' + continuation
            else:
                break

        desc = re.sub(r'\s{2,}', ' ', desc_raw)
        # Strip trailing conjunctions from truncated descriptions
        desc = re.sub(r'\s+(?:and|or|with)\s*$', '', desc, flags=re.IGNORECASE)

        item_desc = f"{int(qty)}x {desc}" if qty > 1 else desc
        meta['line_items'].append({
            'description': item_desc,
            'amount': extension,
            'sku': m.group(2),
            'ref': f'R{m.group(1)}',
        })

    # Totals: MERCHANDISE TOTAL, SALES TAX, TOTAL
    for line in lines:
        stripped = line.strip()
        upper = stripped.upper()

        if 'MERCHANDISE TOTAL' in upper:
            amts = _find_amounts(line)
            if amts:
                meta['subtotal'] = amts[-1]
        elif re.match(r'^SALES\s+TAX', upper):
            amts = _find_amounts(line)
            if amts:
                meta['tax_amount'] = amts[-1]
                meta['tax_label'] = 'SALES TAX'
        elif re.match(r'^TOTAL\s', upper) and 'MERCHANDISE' not in upper and 'CHARGES' not in upper:
            amts = _find_amounts(line)
            if amts and amts[-1] > 0:
                meta['grand_total'] = amts[-1]

    meta['items_sum'] = round(sum(i['amount'] for i in meta['line_items']), 2)
    meta['confidence'] = _compute_confidence(meta)
    return meta


# ── Wayfair vendor parser ────────────────────────────────────

_WF_AMOUNTS_RE = re.compile(
    r'\$(\d[\d,.]*\.\d{2})\s+'    # Unit price
    r'(\d+)\s+'                    # Qty
    r'\$(\d[\d,.]*\.\d{2})\s+'    # Item subtotal
    r'\$(\d[\d,.]*\.\d{2})\s+'    # Shipping & delivery
    r'\$(\d[\d,.]*\.\d{2})\s+'    # Per-item tax
    r'\$(\d[\d,.]*\.\d{2})'       # Per-item total (tax-inclusive)
)


def _parse_wayfair(text: str) -> dict:
    """Parse Wayfair invoice format.

    Wayfair uses tabular layout per shipment group:
      Description (may be multi-line)
      $UnitPrice  Qty  $Subtotal  $Shipping  $Tax  $Total
      SKU_CODE (optional, on separate line)
    """
    lines = text.split('\n')
    meta = {
        'grand_total': None, 'subtotal': None,
        'tax_amount': None, 'tax_label': None,
        'date': None, 'bill_id': None,
        'payment_hints': [], 'line_items': [],
        'items_sum': 0, 'vendor_detected': 'wayfair',
        'item_level_tax': True,  # Each item has exact per-item tax
    }

    # Invoice #
    for line in lines:
        m = re.search(r'Invoice\s*#\s*(\d{5,})', line, re.IGNORECASE)
        if m:
            meta['bill_id'] = m.group(1)
            break

    # Order Date: "Order Date Mon DD, YYYY"
    for line in lines:
        m = re.search(
            r'Order\s+Date\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\w*\.?\s+'
            r'(\d{1,2}),?\s+(\d{4})',
            line, re.IGNORECASE,
        )
        if m:
            mon = _MONTH_MAP.get(m.group(1)[:3].lower(), '01')
            meta['date'] = f"{m.group(3)}-{mon}-{m.group(2).zfill(2)}"
            break

    # Invoice Summary totals
    for line in lines:
        stripped = line.strip()
        if re.match(r'Order\s+Total', stripped, re.IGNORECASE):
            amts = _find_amounts(line)
            if amts:
                meta['grand_total'] = amts[-1]
        elif re.match(r'Subtotal\s', stripped, re.IGNORECASE):
            amts = _find_amounts(line)
            if amts:
                meta['subtotal'] = amts[-1]

    # Tax (general extractor handles merged "Tax Exempt: No Tax $X" lines)
    meta['tax_amount'], meta['tax_label'] = _extract_tax(text)

    # Payment hints
    meta['payment_hints'] = _extract_payment_hints(text)

    # Items: find lines with 6 dollar amounts (Wayfair columnar format)
    _WF_SKU_RE = re.compile(r'^[A-Z]{2,}\d{2,}\s*$')
    _WF_STOP_RE = re.compile(
        r'(?:Item\s+Unit|Shipping\s*&|Delivery|Shipped\s+On|Items\s+to\s+be|'
        r'Ship\s+To|Total:|United\s+States|Payments|Invoice\s+Summary|'
        r'Payment\s+Terms|Leo\s+)',
        re.IGNORECASE,
    )

    for i, line in enumerate(lines):
        m = _WF_AMOUNTS_RE.search(line.strip())
        if not m:
            continue

        qty = int(m.group(2))
        item_subtotal = _parse_amt(m.group(3))
        item_shipping = _parse_amt(m.group(4))
        item_tax = _parse_amt(m.group(5))
        item_total = _parse_amt(m.group(6))

        # Check for SKU prefix on same line (e.g. "DZHZ1512 $40.99 ...")
        before_dollar = line.strip()[:line.strip().find('$')].strip()
        sku_on_line = before_dollar if _WF_SKU_RE.match(before_dollar) else None

        # Walk backwards to find description lines
        desc_parts = []
        for j in range(i - 1, max(0, i - 6), -1):
            prev = lines[j].strip()
            if not prev:
                break
            if _WF_STOP_RE.search(prev):
                break
            if _WF_AMOUNTS_RE.search(prev):
                break
            if _WF_SKU_RE.match(prev):
                continue  # Skip standalone SKU lines
            if re.match(r'^Finish:', prev, re.IGNORECASE):
                continue
            desc_parts.insert(0, prev)

        desc = ' '.join(desc_parts) if desc_parts else (sku_on_line or 'Unknown Item')
        if qty > 1:
            desc = f"{qty}x {desc}"

        meta['line_items'].append({
            'description': desc,
            'amount': item_subtotal,        # Pre-tax amount
            'item_tax': item_tax,           # Exact tax per item
            'item_total': item_total,       # Final amount (tax-inclusive)
            'item_shipping': item_shipping, # Per-item shipping
        })

    # Document-level shipping = sum of per-item shipping
    meta['shipping'] = round(sum(i.get('item_shipping', 0) for i in meta['line_items']), 2)
    meta['items_sum'] = round(sum(i['amount'] for i in meta['line_items']), 2)
    meta['confidence'] = _compute_confidence(meta)
    return meta


# ── Sf Transport vendor parser ──────────────────────────────

def _parse_sf_transport(text: str) -> dict:
    """Parse Sf Transport invoice format.

    Header layout:
      INVOICE # DATE TOTAL DUE DUE DATE ENCLOSED
      10910    01/22/2026  $450.00  01/22/2026
    Items:
      DATE DESCRIPTION QTY RATE AMOUNT
      01/21/2026 Box No. 10 Lowboy with dirt 1 450.00 450.00
    Footer:
      BALANCE DUE $450.00
    """
    lines = text.split('\n')
    meta = {
        'grand_total': None, 'subtotal': None,
        'tax_amount': None, 'tax_label': None,
        'shipping': 0, 'date': None, 'bill_id': None,
        'payment_hints': [], 'line_items': [],
        'items_sum': 0, 'vendor_detected': 'sf_transport',
    }

    # Header: merged column "INVOICE # DATE TOTAL DUE ..."
    # Data on next line: "10910 01/22/2026 $450.00 01/22/2026"
    for i, line in enumerate(lines):
        upper = line.strip().upper()
        if 'INVOICE' in upper and 'TOTAL' in upper and 'DUE' in upper:
            for j in range(i + 1, min(i + 3, len(lines))):
                data = lines[j].strip()
                if not data:
                    continue
                m = re.match(
                    r'(\d+)\s+(\d{1,2}/\d{1,2}/\d{4})\s+\$?([\d,]+\.\d{2})',
                    data,
                )
                if m:
                    meta['bill_id'] = m.group(1)
                    parts = m.group(2).split('/')
                    mo, dy, yr = parts[0], parts[1], parts[2]
                    if len(yr) == 2:
                        yr = '20' + yr
                    meta['date'] = f"{yr}-{mo.zfill(2)}-{dy.zfill(2)}"
                    meta['grand_total'] = _parse_amt(m.group(3))
                break
            break

    # BALANCE DUE as confirmation / fallback
    for line in lines:
        if re.match(r'BALANCE\s+DUE', line.strip(), re.IGNORECASE):
            amts = _find_amounts(line)
            if amts:
                if meta['grand_total'] is None:
                    meta['grand_total'] = amts[-1]

    # Items: after "DATE DESCRIPTION QTY RATE AMOUNT" header
    in_items = False
    for line in lines:
        stripped = line.strip()
        upper = stripped.upper()
        if 'DESCRIPTION' in upper and ('QTY' in upper or 'RATE' in upper):
            in_items = True
            continue
        if not in_items:
            continue
        if re.match(r'BALANCE\s+DUE', stripped, re.IGNORECASE):
            break
        if not stripped:
            continue
        # Pattern: MM/DD/YYYY Description [numbers...] Amount
        # Use flexible approach: grab date, then last amount, strip trailing numbers for desc
        m = re.match(r'(\d{1,2}/\d{1,2}/\d{4})\s+(.+)', stripped)
        if m:
            rest = m.group(2)
            all_amts = _AMT_NOSIGN_RE.findall(rest)
            if all_amts:
                amount = _parse_amt(all_amts[-1])
                # Strip trailing numeric columns (qty, rate, amount)
                desc = re.sub(r'(?:\s+[\d,.]+){2,}\s*$', '', rest).strip()
                if not desc:
                    desc = rest[:rest.find(all_amts[0])].strip()
                if desc:
                    meta['line_items'].append({'description': desc, 'amount': amount})

    meta['tax_amount'], meta['tax_label'] = _extract_tax(text)
    meta['payment_hints'] = _extract_payment_hints(text)
    meta['items_sum'] = round(sum(i['amount'] for i in meta['line_items']), 2)
    if not meta['subtotal'] and meta['line_items']:
        meta['subtotal'] = meta['items_sum']
    meta['confidence'] = _compute_confidence(meta)
    return meta


# ── Floor & Decor vendor parser ─────────────────────────────

def _parse_floor_decor(text: str) -> dict:
    """Parse Floor & Decor order confirmation format.

    Totals on page 1:
      SUBTOTAL $1,595.17
      Taxes $123.63
      ORDER $1,718.80
      TOTAL
    Items on page 2 (two-column layout):
      Artisan Frost II 512 $1,479.68
      Pure White 1 $115.49
    """
    lines = text.split('\n')
    meta = {
        'grand_total': None, 'subtotal': None,
        'tax_amount': None, 'tax_label': None,
        'shipping': 0, 'date': None, 'bill_id': None,
        'payment_hints': [], 'line_items': [],
        'items_sum': 0, 'vendor_detected': 'floor_decor',
    }

    # Order Number
    for line in lines:
        m = re.search(r'Order\s+Number:\s*(\d+)', line, re.IGNORECASE)
        if m:
            meta['bill_id'] = m.group(1)
            break

    # Date: "Order Placed: Jan 05, 2026"
    for line in lines:
        m = re.search(
            r'Order\s+Placed:\s*(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\w*\.?\s+'
            r'(\d{1,2}),?\s+(\d{4})',
            line, re.IGNORECASE,
        )
        if m:
            mon = _MONTH_MAP.get(m.group(1)[:3].lower(), '01')
            meta['date'] = f"{m.group(3)}-{mon}-{m.group(2).zfill(2)}"
            break

    # Totals: SUBTOTAL, Taxes, ORDER TOTAL (ORDER $X on one line, TOTAL on next)
    for i, line in enumerate(lines):
        stripped = line.strip()
        upper = stripped.upper()

        if upper.startswith('SUBTOTAL'):
            amts = _find_amounts(line)
            if amts:
                meta['subtotal'] = amts[-1]
        elif re.match(r'TAXES?\b', upper):
            amts = _find_amounts(line)
            if amts:
                meta['tax_amount'] = amts[-1]
                meta['tax_label'] = 'Taxes'
        elif upper.startswith('ORDER') and '$' in line:
            amts = _find_amounts(line)
            if amts:
                # "ORDER $1,718.80" with "TOTAL" on a separate line (PDF layout split)
                meta['grand_total'] = amts[-1]

    # Shipping: "Ship to Store FREE" or shipping amount
    for line in lines:
        if re.search(r'Ship\s+to\s+Store\s+FREE', line, re.IGNORECASE):
            meta['shipping'] = 0
            break
        m = re.search(
            r'(?:Shipping|Ship\s+to\s+Store)\s*:?\s*\$?([\d,]+\.\d{2})',
            line, re.IGNORECASE,
        )
        if m:
            meta['shipping'] = _parse_amt(m.group(1))
            break

    meta['payment_hints'] = _extract_payment_hints(text)

    # Items: "ProductName QTY $Amount" with confirmation on following lines
    for i, line in enumerate(lines):
        stripped = line.strip()
        m = re.match(r'(.+?)\s+(\d+)\s+\$(\d[\d,.]*\.\d{2})\s*$', stripped)
        if not m:
            continue
        desc_raw = m.group(1).strip()
        qty = int(m.group(2))
        amount = _parse_amt(m.group(3))

        if len(desc_raw) < 3 or re.match(r'^\d+$', desc_raw):
            continue
        if any(w in desc_raw.upper() for w in ['TOTAL', 'SUBTOTAL', 'TAX', 'ORDER']):
            continue

        # Confirm: next lines have "piece", "SKU:", or "Size:" -> this is a real item
        is_item = False
        for j in range(i + 1, min(i + 5, len(lines))):
            nxt = lines[j].strip().lower()
            if 'piece' in nxt or 'sku:' in nxt or 'size:' in nxt:
                is_item = True
                break
            if not nxt:
                break
        if not is_item:
            continue

        item_desc = f"{qty}x {desc_raw}" if qty > 1 else desc_raw
        meta['line_items'].append({'description': item_desc, 'amount': amount})

    meta['items_sum'] = round(sum(i['amount'] for i in meta['line_items']), 2)
    meta['confidence'] = _compute_confidence(meta)
    return meta


# ── Amazon vendor parser ────────────────────────────────────

def _parse_amazon(text: str) -> dict:
    """Parse Amazon order confirmation format.

    Layout:
      Order Total: $165.76
      Items Ordered  Price
      2 of: Kwikset Downtown Deadbolt Lock...  $41.41
      ...
      Item(s) Subtotal: $153.12
      Shipping & Handling: $0.00
      Total before tax: $153.12
      Estimated Tax: $12.64
      Grand Total: $165.76
    """
    lines = text.split('\n')
    meta = {
        'grand_total': None, 'subtotal': None,
        'tax_amount': None, 'tax_label': None,
        'shipping': 0, 'date': None, 'bill_id': None,
        'payment_hints': [], 'line_items': [],
        'items_sum': 0, 'vendor_detected': 'amazon',
    }

    for line in lines:
        stripped = line.strip()

        # Grand Total
        if re.match(r'Grand\s+Total', stripped, re.IGNORECASE):
            amts = _find_amounts(line)
            if amts:
                meta['grand_total'] = amts[-1]
        # Order Total (fallback)
        elif re.match(r'Order\s+Total', stripped, re.IGNORECASE) and meta['grand_total'] is None:
            amts = _find_amounts(line)
            if amts:
                meta['grand_total'] = amts[-1]
        # Item(s) Subtotal
        elif re.search(r'Item\(?s?\)?\s+Subtotal', stripped, re.IGNORECASE):
            amts = _find_amounts(line)
            if amts:
                meta['subtotal'] = amts[-1]
        # Estimated Tax
        elif re.search(r'Estimated\s+Tax', stripped, re.IGNORECASE):
            amts = _find_amounts(line)
            if amts:
                meta['tax_amount'] = amts[-1]
                meta['tax_label'] = 'Estimated Tax'
        # Shipping & Handling
        elif re.match(r'Shipping\s*&\s*Handling', stripped, re.IGNORECASE):
            amts = _find_amounts(line)
            if amts:
                meta['shipping'] = amts[-1]

    # Order ID from "Order Placed:" or "Order#"
    for line in lines:
        m = re.search(r'Order\s*#?\s*:?\s*(\d{3}-\d{7}-\d{7})', line)
        if m:
            meta['bill_id'] = m.group(1)
            break

    # Date
    meta['date'] = _extract_date(text)
    meta['payment_hints'] = _extract_payment_hints(text)

    # Items: "N of: Description  $UnitPrice"  (Amazon shows unit price, not line total)
    for line in lines:
        stripped = line.strip()
        m = re.match(r'(\d+)\s+of:\s+(.+?)\s+\$(\d[\d,.]*\.\d{2})\s*$', stripped)
        if m:
            qty = int(m.group(1))
            desc = m.group(2).strip().rstrip('.')
            unit_price = _parse_amt(m.group(3))
            line_total = round(qty * unit_price, 2)
            if qty > 1:
                desc = f"{qty}x {desc}"
            meta['line_items'].append({'description': desc, 'amount': line_total})

    meta['items_sum'] = round(sum(i['amount'] for i in meta['line_items']), 2)
    meta['confidence'] = _compute_confidence(meta)
    return meta


# ── Scoring system ────────────────────────────────────────────

def score_extraction(meta: dict) -> dict:
    """Score extraction quality on 0-100 scale.

    Breakdown (max 100):
      totals     (35): grand_total found (10), cross-validated/high (+25)
      items      (40): found (10), sum matches (20), clean descriptions (10)
      metadata   (25): date (8), bill_id (8), payment (5), shipping known (4)
      penalty    (-N): items mismatch, missing critical data
    """
    score = 0
    breakdown = {}
    conf = meta.get('confidence', {})

    # Totals (max 35) - grand total is the ANCHOR
    totals = 0
    if meta.get('grand_total') is not None:
        totals += 10
    if conf.get('cross_validated'):
        totals += 25
    elif conf.get('items_plus_tax_match'):
        totals += 22  # items+tax+shipping == grand_total (nearly as good)
    elif conf.get('grand_total') == 'high':
        totals += 20
    elif conf.get('grand_total') == 'medium':
        totals += 10
    breakdown['totals'] = totals
    score += totals

    # Items (max 40)
    items_score = 0
    n_items = len(meta.get('line_items', []))
    if n_items > 0:
        items_score += 10
        if conf.get('items_match_subtotal') or conf.get('items_match_grand') or conf.get('items_plus_tax_match'):
            items_score += 20
        # Clean descriptions (more than just a SKU or short code)
        clean = sum(1 for i in meta.get('line_items', []) if len(i.get('description', '')) > 5)
        items_score += min(10, int(10 * clean / max(n_items, 1)))
    breakdown['items'] = items_score
    score += items_score

    # Metadata (max 25)
    meta_score = 0
    if meta.get('date'):
        meta_score += 8
    if meta.get('bill_id'):
        meta_score += 8
    if meta.get('payment_hints'):
        meta_score += 5
    # Shipping known (even if $0 - we confirmed it)
    if meta.get('shipping') is not None:
        meta_score += 4
    breakdown['metadata'] = meta_score
    score += meta_score

    # Penalty
    penalty = 0
    if n_items > 0 and not (conf.get('items_match_subtotal') or conf.get('items_match_grand') or conf.get('items_plus_tax_match')):
        penalty += 10
    breakdown['penalty'] = -penalty
    score -= penalty

    return {'score': max(0, min(100, score)), 'breakdown': breakdown}


# ── Filename hints extraction ─────────────────────────────────

# Map filename keywords to vendor keys (lowercase match)
_FILENAME_VENDOR_MAP = {
    'home depot': 'home_depot',
    'homedepot': 'home_depot',
    'wayfair': 'wayfair',
    'lowes': 'lowes',
    "lowe's": 'lowes',
    'ferguson': 'ferguson',
    'floor & decor': 'floor_decor',
    'floor and decor': 'floor_decor',
    'sf transport': 'sf_transport',
    'amazon': 'amazon',
}


def extract_filename_hints(filename: Optional[str]) -> dict:
    """Extract vendor, date, and total hints from the filename.

    Filenames often contain metadata like:
      "Wayfair - 12-22-2025 - $1,108.41 ON HUB (1).pdf"
      "Order_InvoiceFebruary-12-2026_01_14-PM.pdf"
      "Home Depot Invoice 2026-01-15.pdf"

    Returns:
        {'vendor_hint': str|None, 'date_hint': str|None, 'total_hint': float|None}
    """
    if not filename:
        return {'vendor_hint': None, 'date_hint': None, 'total_hint': None}

    # Strip path and extension
    import os
    basename = os.path.splitext(os.path.basename(filename))[0]
    lower = basename.lower()

    # Vendor detection from filename
    vendor_hint = None
    for keyword, vendor_key in _FILENAME_VENDOR_MAP.items():
        if keyword in lower:
            vendor_hint = vendor_key
            break

    # Total: look for $X,XXX.XX pattern in filename
    total_hint = None
    total_match = _AMT_RE.search(basename)
    if total_match:
        total_hint = _parse_amt(total_match.group(1))

    # Date: try multiple patterns
    date_hint = None

    # Pattern 1: MM-DD-YYYY or MM/DD/YYYY
    dm = re.search(r'(\d{1,2})[-/](\d{1,2})[-/](\d{4})', basename)
    if dm:
        mo, dy, yr = dm.group(1), dm.group(2), dm.group(3)
        if 1 <= int(mo) <= 12 and 1 <= int(dy) <= 31:
            date_hint = f"{yr}-{mo.zfill(2)}-{dy.zfill(2)}"

    # Pattern 2: YYYY-MM-DD
    if not date_hint:
        dm = re.search(r'(\d{4})-(\d{2})-(\d{2})', basename)
        if dm:
            date_hint = f"{dm.group(1)}-{dm.group(2)}-{dm.group(3)}"

    # Pattern 3: MonthName-DD-YYYY (e.g. "February-12-2026")
    if not date_hint:
        dm = re.search(
            r'(January|February|March|April|May|June|July|August|September|October|November|December)'
            r'[-\s](\d{1,2})[-,\s]+(\d{4})',
            basename, re.IGNORECASE,
        )
        if dm:
            mon = _MONTH_MAP.get(dm.group(1)[:3].lower(), '01')
            date_hint = f"{dm.group(3)}-{mon}-{dm.group(2).zfill(2)}"

    return {
        'vendor_hint': vendor_hint,
        'date_hint': date_hint,
        'total_hint': total_hint,
    }


# ── Best extraction orchestrator ──────────────────────────────

_VENDOR_PARSERS = {
    'home_depot': _parse_home_depot,
    'wayfair': _parse_wayfair,
    'sf_transport': _parse_sf_transport,
    'floor_decor': _parse_floor_decor,
    'amazon': _parse_amazon,
}


def extract_best(text: str, filename: Optional[str] = None) -> Tuple[dict, dict]:
    """Run general + vendor-specific extraction, return best-scored result.

    Args:
        text: Cleaned receipt text from pdfplumber.
        filename: Original filename (optional). Used to extract vendor, date,
                  and total hints for cross-validation and gap-filling.

    Returns:
        (best_metadata, scoring_info)
    """
    # Layer 0: Filename hints
    hints = extract_filename_hints(filename)

    # Layer 1: General regex
    general_meta = extract_receipt_metadata(text)
    general_scoring = score_extraction(general_meta)

    # Layer 2: Vendor-specific regex
    # Prefer vendor from text; fall back to filename hint
    vendor = detect_vendor_format(text) or hints['vendor_hint']
    vendor_meta = None
    vendor_scoring = None

    if vendor and vendor in _VENDOR_PARSERS:
        vendor_meta = _VENDOR_PARSERS[vendor](text)
        vendor_scoring = score_extraction(vendor_meta)

    # Pick winner
    info = {
        'general_score': general_scoring['score'],
        'general_breakdown': general_scoring['breakdown'],
        'vendor_detected': vendor,
        'vendor_score': vendor_scoring['score'] if vendor_scoring else None,
        'vendor_breakdown': vendor_scoring['breakdown'] if vendor_scoring else None,
        'winner': 'general',
        'filename_hints': hints,
    }

    if vendor_scoring and vendor_scoring['score'] > general_scoring['score']:
        info['winner'] = 'vendor'
        best = vendor_meta
    else:
        best = general_meta

    # Layer 3: Apply filename hints to fill gaps + cross-validate
    _apply_filename_hints(best, hints)

    return best, info


def _apply_filename_hints(meta: dict, hints: dict) -> None:
    """Merge filename hints into extraction result (mutates meta).

    - Fills missing date from filename
    - Cross-validates grand_total against filename total
    - Adds 'filename_cross_validated' flag to confidence
    """
    # Fill missing date
    if not meta.get('date') and hints.get('date_hint'):
        meta['date'] = hints['date_hint']
        meta.setdefault('_hints_applied', []).append('date_from_filename')

    # Cross-validate grand total with filename total
    fn_total = hints.get('total_hint')
    gt = meta.get('grand_total')
    if fn_total is not None and gt is not None:
        if abs(fn_total - gt) <= 0.05:
            meta.setdefault('confidence', {})['filename_cross_validated'] = True
            meta.setdefault('_hints_applied', []).append('total_confirmed_by_filename')
        else:
            meta.setdefault('confidence', {})['filename_cross_validated'] = False
            meta.setdefault('_hints_applied', []).append(
                f'total_mismatch_filename=${fn_total:.2f}_vs_regex=${gt:.2f}'
            )


# ── CLI test harness ────────────────────────────────────────────

if __name__ == '__main__':
    import pdfplumber
    import time as _time

    if len(sys.argv) < 2:
        print("Usage: python services/receipt_regex.py <path_to_pdf> [--raw] [--clean] [--json]")
        sys.exit(1)

    pdf_path = sys.argv[1]
    show_raw = '--raw' in sys.argv
    show_clean = '--clean' in sys.argv

    t0 = _time.monotonic()

    # ── pdfplumber extraction ───────────────────────────────────
    with pdfplumber.open(pdf_path) as pdf:
        pages_text = []
        for i, page in enumerate(pdf.pages):
            page_text = page.extract_text() or ""
            pages_text.append(page_text)
        text = "\n\n--- PAGE BREAK ---\n\n".join(pages_text)
    t_pdf = _time.monotonic()

    if show_raw:
        print("=" * 60)
        print(f"RAW PDFPLUMBER TEXT ({len(text)} chars)")
        print("=" * 60)
        print(text)

    # ── Pass 1: Clean noise ─────────────────────────────────────
    cleaned = clean_receipt_text(text)
    t_clean = _time.monotonic()

    if show_clean:
        print("\n" + "=" * 60)
        print(f"CLEANED TEXT ({len(cleaned)} chars, {len(text)-len(cleaned)} removed)")
        print("=" * 60)
        print(cleaned)

    # ── Pass 2: Best extraction (general + vendor) ──────────────
    meta, scoring = extract_best(cleaned, filename=pdf_path)
    t_regex = _time.monotonic()

    # ── Filename hints ───────────────────────────────────────────
    hints = scoring.get('filename_hints', {})
    if any(hints.get(k) for k in ('vendor_hint', 'date_hint', 'total_hint')):
        print("\n" + "=" * 60)
        print("FILENAME HINTS")
        print("=" * 60)
        if hints.get('vendor_hint'):
            print(f"  Vendor:  {hints['vendor_hint']}")
        if hints.get('date_hint'):
            print(f"  Date:    {hints['date_hint']}")
        if hints.get('total_hint') is not None:
            print(f"  Total:   ${hints['total_hint']:,.2f}")
        applied = meta.get('_hints_applied', [])
        if applied:
            print(f"  Applied: {', '.join(applied)}")

    # ── Scoring comparison ──────────────────────────────────────
    print("\n" + "=" * 60)
    print("SCORING COMPARISON")
    print("=" * 60)
    v = scoring['vendor_detected']
    print(f"  Vendor detected:  {v or 'none'}")
    print(f"  General score:    {scoring['general_score']}/100  {scoring['general_breakdown']}")
    if scoring['vendor_score'] is not None:
        print(f"  Vendor score:     {scoring['vendor_score']}/100  {scoring['vendor_breakdown']}")
    print(f"  Winner:           {scoring['winner'].upper()}"
          f"{'  (' + v + ' parser)' if scoring['winner'] == 'vendor' else ''}")

    # ── Results ─────────────────────────────────────────────────
    gt = meta['grand_total']
    st = meta['subtotal']
    tx = meta['tax_amount']
    conf = meta['confidence']

    print("\n" + "=" * 60)
    print(f"EXTRACTION RESULTS ({scoring['winner'].upper()})")
    print("=" * 60)

    ship = meta.get('shipping') or 0

    print(f"  Grand Total:  ${gt:,.2f}" if gt is not None else "  Grand Total:  NOT FOUND")
    print(f"  Subtotal:     ${st:,.2f}" if st is not None else "  Subtotal:     NOT FOUND")
    print(f"  Tax:          ${tx:,.2f} ({meta['tax_label']})" if tx is not None else "  Tax:          NOT FOUND")
    if ship > 0:
        print(f"  Shipping:     ${ship:,.2f}")

    if st is not None and tx is not None and gt is not None:
        calc = round(st + tx + ship, 2)
        ok = "MATCH" if abs(calc - gt) <= 0.05 else f"MISMATCH (diff ${abs(calc - gt):.2f})"
        print(f"  Cross-check:  ${st:,.2f} + ${tx:,.2f} + ${ship:,.2f}(ship) = ${calc:,.2f} vs ${gt:,.2f} -> {ok}")

    print(f"  Date:         {meta['date']}" if meta['date'] else "  Date:         NOT FOUND")
    print(f"  Bill ID:      {meta['bill_id']}" if meta['bill_id'] else "  Bill ID:      NOT FOUND")
    print(f"  Payment:      {', '.join(meta['payment_hints'])}" if meta['payment_hints'] else "  Payment:      NO HINTS")

    print(f"\n  Line Items:   {len(meta['line_items'])}")
    print(f"  Items Sum:    ${meta['items_sum']:,.2f}")

    if meta['line_items']:
        print("\n  #   Amount       Description")
        print("  " + "-" * 60)
        for i, item in enumerate(meta['line_items'], 1):
            extras = []
            if 'qty_hint' in item:
                extras.append(f"qty:{item['qty_hint']}")
            if 'item_tax' in item:
                extras.append(f"tax:{item['item_tax']:.2f}")
            if 'sku' in item:
                extras.append(f"sku:{item['sku']}")
            suffix = f" ({', '.join(extras)})" if extras else ""
            print(f"  {i:<3} ${item['amount']:>10,.2f}   {item['description'][:42]}{suffix}")

    # ── Validation ──────────────────────────────────────────────
    isum = meta['items_sum']
    print()
    if st is not None:
        diff = round(isum - st, 2)
        print(f"  Items vs Subtotal:  ${isum:,.2f} vs ${st:,.2f} (diff ${diff:+,.2f})"
              + (" OK" if abs(diff) <= 0.10 else " MISMATCH"))
    if gt is not None:
        diff = round(isum - gt, 2)
        print(f"  Items vs Grand:     ${isum:,.2f} vs ${gt:,.2f} (diff ${diff:+,.2f})"
              + (" OK" if abs(diff) <= 0.10 else " MISMATCH"))

    print(f"\n  Confidence:   {json.dumps(conf)}")

    # ── Decision ────────────────────────────────────────────────
    items_ok = conf.get('items_match_subtotal') or conf.get('items_match_grand') or conf.get('items_plus_tax_match')
    would_skip_gpt = conf['grand_total'] == 'high' and len(meta['line_items']) > 0 and items_ok
    best_score = scoring['vendor_score'] if scoring['winner'] == 'vendor' else scoring['general_score']

    print(f"\n  VERDICT:      {'REGEX OK - SKIP GPT' if would_skip_gpt else 'FALLBACK TO GPT'}"
          f"  (score: {best_score}/100)")

    # ── Timing ──────────────────────────────────────────────────
    total_ms = int((t_regex - t0) * 1000)
    print(f"\n  Timing:")
    print(f"    pdfplumber:  {int((t_pdf - t0) * 1000)}ms")
    print(f"    clean:       {int((t_clean - t_pdf) * 1000)}ms")
    print(f"    regex:       {int((t_regex - t_clean) * 1000)}ms")
    print(f"    TOTAL:       {total_ms}ms")

    # Full JSON for piping
    if '--json' in sys.argv:
        print("\n" + "=" * 60)
        print("FULL JSON")
        print("=" * 60)
        print(json.dumps(meta, indent=2))
