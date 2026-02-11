# services/receipt_scanner.py
# ================================
# Shared Receipt Scanning & Categorization Service
# ================================
# Used by both the human flow (expenses.py /parse-receipt)
# and the agent flow (pending_receipts.py /agent-process).
#
# Functions:
#   extract_text_from_pdf(file_content, min_chars) -> (bool, str)
#   scan_receipt(file_content, file_type, model, correction_context) -> dict
#   auto_categorize(stage, expenses) -> list[dict]

from api.supabase_client import supabase
from openai import OpenAI
from typing import Optional
import base64
import io
import json
import os
import platform
import re

import pdfplumber
from pdf2image import convert_from_bytes


# ====== PDF TEXT EXTRACTION ======

def extract_text_from_pdf(file_content: bytes, min_chars: int = 100) -> tuple:
    """
    Extract text from a PDF using pdfplumber.

    Args:
        file_content: PDF file bytes
        min_chars: Minimum characters to consider extraction successful

    Returns:
        (success: bool, text_or_error: str)
    """
    try:
        print("[PDF-TEXT] Intentando extraer texto con pdfplumber...")

        with pdfplumber.open(io.BytesIO(file_content)) as pdf:
            all_text = []
            total_pages = len(pdf.pages)
            print(f"[PDF-TEXT] PDF tiene {total_pages} pagina(s)")

            for i, page in enumerate(pdf.pages):
                page_text = page.extract_text() or ""
                all_text.append(page_text)
                char_count = len(page_text.strip())
                print(f"[PDF-TEXT] Pagina {i+1}: {char_count} caracteres extraidos")

            combined_text = "\n\n--- PAGE BREAK ---\n\n".join(all_text)
            total_chars = len(combined_text.strip())

            print(f"[PDF-TEXT] Total caracteres extraidos: {total_chars}")

            if total_chars < min_chars:
                print(f"[PDF-TEXT] FALLBACK: Texto insuficiente ({total_chars} < {min_chars})")
                return False, f"Texto insuficiente: {total_chars} caracteres"

            meaningful_text = combined_text.replace(" ", "").replace("\n", "").replace("\t", "")
            if len(meaningful_text) < min_chars:
                print(f"[PDF-TEXT] FALLBACK: Texto no significativo ({len(meaningful_text)} chars utiles)")
                return False, "Texto no significativo (solo espacios/newlines)"

            print(f"[PDF-TEXT] EXITO: Texto extraido correctamente ({total_chars} chars)")
            return True, combined_text

    except Exception as e:
        print(f"[PDF-TEXT] ERROR: {str(e)}")
        return False, f"Error pdfplumber: {str(e)}"


# ====== HELPERS ======

def _get_openai_client():
    """Get OpenAI client. Raises RuntimeError if key not configured."""
    openai_api_key = os.getenv("OPENAI_API_KEY")
    if not openai_api_key or openai_api_key == "your-openai-api-key-here":
        raise RuntimeError("OpenAI API key not configured")
    return OpenAI(api_key=openai_api_key)


def _get_poppler_path():
    """Get poppler path for PDF conversion on Windows."""
    if platform.system() == "Windows":
        return r'C:\poppler\poppler-24.08.0\Library\bin'
    return None


def _convert_pdf_to_images(file_content: bytes):
    """Convert PDF bytes to list of base64-encoded PNG strings."""
    poppler_path = _get_poppler_path()
    images = convert_from_bytes(file_content, dpi=300, poppler_path=poppler_path)
    if not images:
        raise ValueError("Could not convert PDF to image")

    base64_images = []
    for img in images:
        buffer = io.BytesIO()
        img.save(buffer, format='PNG')
        buffer.seek(0)
        base64_images.append(base64.b64encode(buffer.getvalue()).decode('utf-8'))

    return base64_images


def _parse_json_response(result_text: str) -> dict:
    """Parse JSON from OpenAI response, handling markdown code blocks."""
    try:
        return json.loads(result_text)
    except json.JSONDecodeError:
        pass

    # Try markdown code block
    json_match = re.search(r'```json\s*(.*?)\s*```', result_text, re.DOTALL)
    if json_match:
        return json.loads(json_match.group(1))

    # Try any JSON object
    json_match = re.search(r'\{.*\}', result_text, re.DOTALL)
    if json_match:
        return json.loads(json_match.group(0))

    raise RuntimeError(f"OpenAI returned invalid JSON: {result_text[:500]}")


def _fetch_lookup_data():
    """Fetch vendors, transaction types, and payment methods from DB."""
    vendors_resp = supabase.table("Vendors").select("vendor_name").execute()
    vendors_list = [v.get("vendor_name") for v in (vendors_resp.data or []) if v.get("vendor_name")]
    if "Unknown" not in vendors_list:
        vendors_list.append("Unknown")

    txn_types_resp = supabase.table("txn_types").select("TnxType_id, TnxType_name").execute()
    txn_types_list = [
        {"id": t.get("TnxType_id"), "name": t.get("TnxType_name")}
        for t in (txn_types_resp.data or []) if t.get("TnxType_name")
    ]

    payment_resp = supabase.table("paymet_methods").select("id, payment_method_name").execute()
    payment_methods_list = [
        {"id": p.get("id"), "name": p.get("payment_method_name")}
        for p in (payment_resp.data or []) if p.get("payment_method_name")
    ]

    return vendors_list, txn_types_list, payment_methods_list


# ====== PROMPTS ======

def _build_vision_prompt(vendors_list, txn_types_list, payment_methods_list, page_count_hint=""):
    """Build the main OCR prompt for vision mode."""
    return f"""You are an expert at extracting expense data from receipts, invoices, and bills.
{page_count_hint}
Analyze this receipt/invoice and extract ALL expense items in JSON format.

AVAILABLE VENDORS (you MUST match to one of these, or use "Unknown"):
{json.dumps(vendors_list, indent=2)}

AVAILABLE TRANSACTION TYPES (you MUST match to one of these by name, or use "Unknown"):
{json.dumps(txn_types_list, indent=2)}

AVAILABLE PAYMENT METHODS (you MUST match to one of these by name, or use "Unknown"):
{json.dumps(payment_methods_list, indent=2)}

IMPORTANT RULES:

1. ALWAYS USE LINE TOTALS - CRITICAL:
   - For each line item, ALWAYS use the LINE TOTAL (extended/calculated amount), NOT the unit price
   - Common column names for line totals: EXTENSION, EXT, AMOUNT, LINE TOTAL, TOTAL, SUBTOTAL (per line)
   - The line total is typically the RIGHTMOST dollar amount on each line
   - Examples:
     * "QTY: 80, PRICE EACH: $1.84, EXTENSION: $147.20" -> amount is $147.20
     * "2 x $5.00 = $10.00" -> amount is $10.00
     * "Widget (3 @ $25.00) ... $75.00" -> amount is $75.00
     * "Service charge ..... $150.00" -> amount is $150.00
   - NEVER use unit prices like "PRICE EACH", "UNIT PRICE", "per each", "@ $X.XX each"

2. DOCUMENT STRUCTURE - Adapt to ANY format:

   A) SIMPLE RECEIPTS (grocery stores, restaurants, retail):
      - Usually single page with items listed vertically
      - Look for: item name followed by price on the same line
      - Total at the bottom

   B) ITEMIZED INVOICES (contractors, services):
      - May have: Description, Quantity, Rate, Amount columns
      - Use the AMOUNT column (rightmost), not Rate

   C) COMPLEX MULTI-SECTION INVOICES (Home Depot, Lowe's, supply stores):
      - May have multiple sections: "CARRY OUT", "DELIVERY #1", "DELIVERY #2", etc.
      - May span multiple pages: "Page 1 of 2", "Continued on next page"
      - Extract items from ALL sections and ALL pages
      - Don't stop at section subtotals (MERCHANDISE TOTAL) - continue to find all items
      - The GRAND TOTAL at the end covers everything

   D) STATEMENTS/SUMMARIES:
      - May show only totals per category
      - Extract each category as a line item if no detail available

3. Extract EVERY line item as a separate expense (don't combine items)

4. For each item, extract:
   - date: Transaction date in YYYY-MM-DD format (look for: Date, Invoice Date, Transaction Date, or use document date)
   - bill_id: Invoice/Bill/Receipt number - extract from: "Invoice #", "Invoice No.", "Bill #", "Receipt #", "Ref #", "PO #", "Order #", "Transaction ID", "Document #", "Confirmation #", or any similar reference number at the top of the document. This is typically the same for all items in one receipt/invoice.
   - description: Item description (include quantity if shown, e.g., "3x Lumber 2x4", "Labor - 4 hours")
   - vendor: Match to AVAILABLE VENDORS list using partial/fuzzy matching. If not found, use "Unknown"
   - amount: The LINE TOTAL as a number (no currency symbols) - NOT the unit price!
   - category: Expense category (e.g., "Materials", "Labor", "Office Supplies", "Food & Beverage", "Transportation", "Utilities", "Equipment Rental", etc.)
   - transaction_type: Match to AVAILABLE TRANSACTION TYPES by document type. If uncertain, use "Unknown"
   - payment_method: Match to AVAILABLE PAYMENT METHODS by payment indicators on receipt. If uncertain, use "Unknown"

5. TAX DISTRIBUTION - CRITICAL:
   - If the receipt shows Sales Tax, Tax, HST, GST, VAT, IVA, or similar tax amounts, DO NOT create a separate tax line item
   - Instead, DISTRIBUTE the tax proportionally across all product/service line items based on each item's percentage of the subtotal
   - Example: Subtotal $100, Item A $60 (60%), Item B $40 (40%), Tax $8:
     * Item A final = $60 + ($8 x 0.60) = $64.80
     * Item B final = $40 + ($8 x 0.40) = $43.20
   - The sum of all final amounts MUST equal the receipt's TOTAL (including tax)
   - Add "tax_included" field to each item showing the tax amount added to it
   - NOTE: Even if tax rate shows 0%, check for actual tax line amounts

   TAX-INCLUSIVE DETECTION - Use these criteria to determine if prices already include tax:
   a) If there is NO separate "Tax" / "Sales Tax" / "VAT" line on the receipt, prices are likely tax-inclusive. In this case set tax_included=0 for all items and use the amounts as-is.
   b) If the receipt shows a SUBTOTAL + TAX LINE + GRAND TOTAL structure, the line item prices are PRE-TAX. You MUST add the distributed tax to each item so the amounts sum to the GRAND TOTAL (not the subtotal).
   c) If item prices already look like they include tax (e.g., the sum of line items already equals the grand total), do NOT add tax again. Set tax_included=0.
   d) SELF-CHECK: After computing all amounts, verify SUM(all expense amounts) == GRAND TOTAL on receipt. If your sum equals the SUBTOTAL instead of the GRAND TOTAL, you forgot to distribute the tax - go back and fix it.

6. FEES ARE LINE ITEMS (not distributed):
   - These are NOT taxes and should be separate line items:
     * Delivery Fee, Shipping, Freight
     * Service Fee, Convenience Fee, Processing Fee
     * Handling Fee, Restocking Fee
     * Tip, Gratuity
     * Environmental fees (CA LUMBER FEE, recycling fee, etc.)
     * Fuel surcharge
   - Only actual TAX amounts (Sales Tax, VAT, GST, HST) get distributed

7. SINGLE TOTAL FALLBACK:
   - If the receipt shows only ONE total with no itemization, create ONE expense with that total amount
   - Use the vendor name or document title as the description

8. Use the currency shown on the receipt (default to USD if not specified)

9. CRITICAL: vendor, transaction_type, and payment_method MUST exactly match one from their respective lists, or use "Unknown"

VALIDATION - MANDATORY (do this BEFORE returning your response):
1. Find the GRAND TOTAL / TOTAL DUE / AMOUNT DUE shown on the receipt - this is "invoice_total"
2. Calculate the arithmetic sum of all your expense amounts - this is "calculated_sum"
3. Compare them:
   - If they match (within $0.02 tolerance), set "validation_passed" to true
   - If they DON'T match, set "validation_passed" to false and include a "validation_warning" message
4. The "invoice_total" must be the EXACT value printed on the receipt, not your calculation
5. CRITICAL TAX SELF-CHECK: If calculated_sum equals the SUBTOTAL (pre-tax) instead of the GRAND TOTAL, you have NOT distributed the tax. Go back, recalculate each item's amount with its proportional tax share, and try again. The final amounts MUST sum to the GRAND TOTAL.
6. If there is a tax line on the receipt, the sum of all "tax_included" values across items must equal the total tax amount shown on the receipt (within $0.02).

Return ONLY valid JSON in this exact format:
{{
  "expenses": [
    {{
      "date": "2025-01-17",
      "bill_id": "INV-12345",
      "description": "Item name or description",
      "vendor": "Exact vendor name from VENDORS list or Unknown",
      "amount": 45.99,
      "category": "Category name",
      "transaction_type": "Exact name from TRANSACTION TYPES list or Unknown",
      "payment_method": "Exact name from PAYMENT METHODS list or Unknown",
      "tax_included": 3.45
    }}
  ],
  "tax_summary": {{
    "total_tax_detected": 8.00,
    "tax_label": "Sales Tax",
    "subtotal": 100.00,
    "grand_total": 108.00,
    "distribution": [
      {{"description": "Item A", "original_amount": 60.00, "tax_added": 4.80, "final_amount": 64.80}},
      {{"description": "Item B", "original_amount": 40.00, "tax_added": 3.20, "final_amount": 43.20}}
    ]
  }},
  "validation": {{
    "invoice_total": 108.00,
    "calculated_sum": 108.00,
    "validation_passed": true,
    "validation_warning": null
  }}
}}

IMPORTANT:
- If NO tax was detected on the receipt, set "tax_summary" to null
- The "tax_included" field in each expense should be the tax amount added to that specific item (0 if no tax was distributed to it, like for fees)
- The "invoice_total" MUST be the exact total shown on the receipt/invoice document
- If validation fails, explain in "validation_warning" why the numbers don't match (e.g., "Calculated sum $105.00 does not match invoice total $108.00 - possible missing item or rounding issue")
- REMEMBER: Each expense "amount" should be the FINAL amount (with tax distributed). The sum of ALL "amount" fields = invoice_total.

DO NOT include any text before or after the JSON. ONLY return the JSON object."""


def _build_text_prompt(vendors_list, txn_types_list, payment_methods_list, extracted_text):
    """Build the OCR prompt for pdfplumber text mode."""
    return f"""You are an expert at extracting expense data from receipts, invoices, and bills.

Below is the TEXT extracted from a receipt/invoice PDF. Analyze it and extract ALL expense items in JSON format.

AVAILABLE VENDORS (you MUST match to one of these, or use "Unknown"):
{json.dumps(vendors_list, indent=2)}

AVAILABLE TRANSACTION TYPES (you MUST match to one of these by name, or use "Unknown"):
{json.dumps(txn_types_list, indent=2)}

AVAILABLE PAYMENT METHODS (you MUST match to one of these by name, or use "Unknown"):
{json.dumps(payment_methods_list, indent=2)}

IMPORTANT RULES:

1. ALWAYS USE LINE TOTALS - CRITICAL:
   - For each line item, ALWAYS use the LINE TOTAL (extended/calculated amount), NOT the unit price
   - Common column names for line totals: EXTENSION, EXT, AMOUNT, LINE TOTAL, TOTAL, SUBTOTAL (per line)
   - The line total is typically the LARGEST dollar amount associated with each item
   - Examples:
     * "QTY: 80, PRICE EACH: $1.84, EXTENSION: $147.20" -> amount is $147.20
     * "2 x $5.00 = $10.00" -> amount is $10.00
     * "Widget (3 @ $25.00) ... $75.00" -> amount is $75.00
     * "Artisan Frost 512 pieces $1,479.68 ... $2.89/piece" -> amount is $1,479.68 (NOT $2.89)
     * "Item Name 1 $115.49 piece $115.49/piece" -> amount is $115.49
   - NEVER use: "PRICE EACH", "UNIT PRICE", "/piece", "/each", "@ $X.XX each"
   - If you see both a total AND a per-unit price, ALWAYS use the total (larger amount)

2. DOCUMENT STRUCTURE - Adapt to ANY format:
   A) SIMPLE RECEIPTS: item name followed by price on same line
   B) ITEMIZED INVOICES: Use the AMOUNT column (rightmost), not Rate
   C) COMPLEX MULTI-SECTION INVOICES (Home Depot, Lowe's):
      - May have multiple sections: "CARRY OUT", "DELIVERY #1", "DELIVERY #2"
      - Extract items from ALL sections
      - Don't stop at section subtotals (MERCHANDISE TOTAL)
   D) STATEMENTS: Extract each category as a line item

3. Extract EVERY line item as a separate expense (don't combine items)

4. For each item, extract:
   - date: Transaction date in YYYY-MM-DD format
   - bill_id: Invoice/Receipt number (same for all items in one receipt)
   - description: Item description (include quantity if shown, e.g., "80x PGT2 Pipe Grip Tie")
   - vendor: Match to AVAILABLE VENDORS list. If not found, use "Unknown"
   - amount: The LINE TOTAL as a number (no currency symbols) - NOT the unit price!
   - category: Expense category (Materials, Labor, Office Supplies, etc.)
   - transaction_type: Match to AVAILABLE TRANSACTION TYPES or use "Unknown"
   - payment_method: Match to AVAILABLE PAYMENT METHODS or use "Unknown"

5. TAX DISTRIBUTION - CRITICAL:
   - If the receipt shows Sales Tax, DO NOT create a separate tax line item
   - DISTRIBUTE the tax proportionally across all product/service line items
   - Calculate each item's percentage of the subtotal, then apply that % to the tax
   - Example: Subtotal $1595.17, Item A $1479.68, Item B $115.49, Tax $123.63:
     * Item A: $1479.68 / $1595.17 = 92.76% -> tax = $123.63 x 0.9276 = $114.68
     * Item B: $115.49 / $1595.17 = 7.24% -> tax = $123.63 x 0.0724 = $8.95
     * Item A final amount = $1479.68 + $114.68 = $1594.36
     * Item B final amount = $115.49 + $8.95 = $124.44
     * Total = $1594.36 + $124.44 = $1718.80 (matches invoice!)
   - PRECISION: Round tax_included to 2 decimal places
   - The sum of all "amount" fields MUST equal the receipt's GRAND TOTAL exactly
   - Add "tax_included" field to each item showing the tax amount added to it

   TAX-INCLUSIVE DETECTION - Use these criteria:
   a) If there is NO separate "Tax" / "Sales Tax" / "VAT" line on the receipt, prices are likely tax-inclusive. Set tax_included=0 for all items and use amounts as-is.
   b) If the receipt shows SUBTOTAL + TAX LINE + GRAND TOTAL, line item prices are PRE-TAX. You MUST add distributed tax so amounts sum to GRAND TOTAL (not subtotal).
   c) If item prices already sum to the grand total, do NOT add tax again. Set tax_included=0.
   d) SELF-CHECK: After computing all amounts, verify SUM(all expense amounts) == GRAND TOTAL. If your sum equals SUBTOTAL instead, you forgot to distribute tax - go back and fix it.

6. FEES ARE LINE ITEMS (not distributed):
   - These are NOT taxes and should be separate line items:
     * Delivery Fee, Shipping, Freight
     * Service Fee, Convenience Fee
     * Environmental fees (CA LUMBER FEE, recycling fee, etc.)
     * Tip, Gratuity
   - Only actual TAX amounts (Sales Tax, VAT, GST) get distributed

7. SINGLE TOTAL FALLBACK:
   - If the receipt shows only ONE total with no itemization, create ONE expense

8. CRITICAL: vendor, transaction_type, payment_method MUST exactly match one from their lists, or use "Unknown"

VALIDATION - MANDATORY (do this BEFORE returning your response):
1. Find the GRAND TOTAL / TOTAL DUE shown on the receipt - this is "invoice_total"
2. Calculate the sum of all your expense amounts - this is "calculated_sum"
3. Compare them:
   - If they match (within $0.02 tolerance), set "validation_passed" to true
   - If they DON'T match, set "validation_passed" to false and include "validation_warning"
4. CRITICAL TAX SELF-CHECK: If calculated_sum equals the SUBTOTAL (pre-tax) instead of the GRAND TOTAL, you have NOT distributed the tax. Go back, recalculate each item's amount with its proportional tax share, and try again.
5. If there is a tax line on the receipt, the sum of all "tax_included" values must equal the total tax amount shown (within $0.02).

Return ONLY valid JSON in this exact format:
{{
  "expenses": [
    {{
      "date": "2025-01-17",
      "bill_id": "INV-12345",
      "description": "Item name or description",
      "vendor": "Exact vendor name from list or Unknown",
      "amount": 45.99,
      "category": "Category name",
      "transaction_type": "Exact name from list or Unknown",
      "payment_method": "Exact name from list or Unknown",
      "tax_included": 3.45
    }}
  ],
  "tax_summary": {{
    "total_tax_detected": 8.00,
    "tax_label": "Sales Tax",
    "subtotal": 100.00,
    "grand_total": 108.00,
    "distribution": [
      {{"description": "Item A", "original_amount": 60.00, "tax_added": 4.80, "final_amount": 64.80}},
      {{"description": "Item B", "original_amount": 40.00, "tax_added": 3.20, "final_amount": 43.20}}
    ]
  }},
  "validation": {{
    "invoice_total": 108.00,
    "calculated_sum": 108.00,
    "validation_passed": true,
    "validation_warning": null
  }}
}}

IMPORTANT:
- If NO tax was detected, set "tax_summary" to null
- The "tax_included" field should be the tax added to that item (0 for fees)
- The "invoice_total" MUST be the exact total shown on the receipt
- If validation fails, explain in "validation_warning" why
- REMEMBER: Each expense "amount" should be the FINAL amount (with tax distributed). The sum of ALL "amount" fields = invoice_total.

DO NOT include any text before or after the JSON. ONLY return the JSON object.

--- RECEIPT TEXT START ---
{extracted_text}
--- RECEIPT TEXT END ---"""


def _build_correction_prompt(correction_context, vendors_list, txn_types_list, payment_methods_list):
    """Build the correction prompt for 2nd pass validation."""
    items_json = json.dumps(correction_context.get("items", []), indent=2)
    invoice_total = correction_context.get("invoice_total", 0)
    calculated_sum = correction_context.get("calculated_sum", 0)
    difference = round(abs(invoice_total - calculated_sum), 2)

    return f"""You are an expert at verifying and correcting expense data extracted from receipts.

A fast OCR pass already extracted the items below from this receipt, but the amounts DO NOT add up correctly.

KNOWN INVOICE TOTAL (printed on the receipt): ${invoice_total:.2f}
OCR EXTRACTED SUM: ${calculated_sum:.2f}
DIFFERENCE: ${difference:.2f}

ITEMS EXTRACTED BY OCR (may have errors in amounts):
{items_json}

YOUR TASK:
1. Look at the receipt image carefully
2. Compare each OCR item amount against what is actually printed on the receipt
3. Find which amount(s) are wrong and correct them
4. Make sure the corrected amounts sum to exactly ${invoice_total:.2f}
5. If the OCR missed an item entirely, add it
6. If the OCR added a phantom item, remove it

AVAILABLE VENDORS: {json.dumps(vendors_list)}
AVAILABLE TRANSACTION TYPES: {json.dumps([t["name"] for t in txn_types_list])}
AVAILABLE PAYMENT METHODS: {json.dumps([p["name"] for p in payment_methods_list])}

TAX RULES:
- If the receipt shows a separate tax line, distribute tax proportionally across items
- Each expense "amount" = final amount WITH tax included
- The sum of ALL "amount" fields MUST equal the invoice total (${invoice_total:.2f})

Return ONLY valid JSON in this exact format:
{{
  "expenses": [
    {{
      "date": "YYYY-MM-DD",
      "bill_id": "invoice number",
      "description": "item description",
      "vendor": "vendor name from list or Unknown",
      "amount": 0.00,
      "category": "category",
      "transaction_type": "from list or Unknown",
      "payment_method": "from list or Unknown",
      "tax_included": 0.00
    }}
  ],
  "tax_summary": {{
    "total_tax_detected": 0.00,
    "tax_label": "Sales Tax",
    "subtotal": 0.00,
    "grand_total": {invoice_total:.2f},
    "distribution": []
  }},
  "validation": {{
    "invoice_total": {invoice_total:.2f},
    "calculated_sum": 0.00,
    "validation_passed": true,
    "validation_warning": null,
    "corrections_made": "describe what you corrected"
  }}
}}

DO NOT include any text before or after the JSON. ONLY return the JSON object."""


# ====== MAIN FUNCTIONS ======

def scan_receipt(
    file_content: bytes,
    file_type: str,
    model: str = "fast",
    correction_context: Optional[dict] = None,
) -> dict:
    """
    Core receipt scanning logic. Extracts line items from a receipt image/PDF.

    Args:
        file_content: Raw file bytes (image or PDF)
        file_type: MIME type (e.g. "image/jpeg", "application/pdf")
        model: "fast" (gpt-4o-mini) or "heavy" (gpt-4o)
        correction_context: Optional dict for correction pass (2nd pass)

    Returns:
        {
            "expenses": [{date, bill_id, description, vendor, amount, category,
                          transaction_type, payment_method, tax_included}, ...],
            "tax_summary": {...} or None,
            "validation": {invoice_total, calculated_sum, validation_passed, ...},
            "extraction_method": "pdfplumber" | "vision" | "vision_direct" | "correction",
            "model_used": "fast" | "heavy"
        }

    Raises:
        ValueError: Invalid file type or empty content
        RuntimeError: OpenAI API failure, invalid response
    """
    # Validate
    allowed_types = ["image/jpeg", "image/jpg", "image/png", "image/webp", "image/gif", "application/pdf"]
    if file_type not in allowed_types:
        raise ValueError(f"Invalid file type. Allowed: JPG, PNG, WebP, GIF, PDF. Got: {file_type}")
    if len(file_content) > 20 * 1024 * 1024:
        raise ValueError("File too large. Maximum size is 20MB.")

    client = _get_openai_client()
    openai_model = "gpt-4o" if model == "heavy" else "gpt-4o-mini"
    print(f"[SCAN-RECEIPT] Using model: {openai_model} (requested: {model})")

    # Fetch lookup data
    vendors_list, txn_types_list, payment_methods_list = _fetch_lookup_data()

    # Handle correction context
    if correction_context:
        print(f"[SCAN-RECEIPT] CORRECTION MODE: invoice_total={correction_context.get('invoice_total')}, "
              f"calculated_sum={correction_context.get('calculated_sum')}, "
              f"items={len(correction_context.get('items', []))}")
        openai_model = "gpt-4o"
        model = "heavy"

    # Determine extraction mode
    use_text_mode = False
    extraction_method = "vision"
    extracted_text = ""
    base64_images = []
    media_type = file_type

    # HEAVY MODE: Direct vision with gpt-4o
    if model == "heavy":
        print(f"[SCAN-RECEIPT] MODO HEAVY: Directo a Vision (gpt-4o)")
        extraction_method = "vision_direct"

        if file_type == "application/pdf":
            try:
                base64_images = _convert_pdf_to_images(file_content)
                media_type = "image/png"
                print(f"[SCAN-RECEIPT] PDF convertido a {len(base64_images)} imagen(es) para Vision")
            except Exception as pdf_error:
                raise ValueError(f"Error processing PDF: {str(pdf_error)}")
        else:
            base64_images = [base64.b64encode(file_content).decode('utf-8')]
            print(f"[SCAN-RECEIPT] Imagen lista para Vision")

    # FAST MODE: pdfplumber -> Vision fallback
    else:
        print(f"[SCAN-RECEIPT] MODO FAST: pdfplumber -> Vision")

        if file_type == "application/pdf":
            print(f"[SCAN-RECEIPT] Archivo PDF detectado")
            print(f"[SCAN-RECEIPT] Intentando pdfplumber...")
            text_success, text_result = extract_text_from_pdf(file_content)

            if text_success:
                use_text_mode = True
                extraction_method = "pdfplumber"
                extracted_text = text_result
                print(f"[SCAN-RECEIPT] EXITO pdfplumber - {len(extracted_text)} caracteres")
            else:
                print(f"[SCAN-RECEIPT] pdfplumber fallo ({text_result}), usando Vision...")
                extraction_method = "vision"
                try:
                    base64_images = _convert_pdf_to_images(file_content)
                    media_type = "image/png"
                    print(f"[SCAN-RECEIPT] PDF convertido a {len(base64_images)} imagen(es) para Vision")
                except Exception as pdf_error:
                    raise ValueError(f"Error processing PDF: {str(pdf_error)}")
        else:
            print(f"[SCAN-RECEIPT] Imagen detectada ({file_type}) - usando Vision")
            extraction_method = "vision"
            base64_images = [base64.b64encode(file_content).decode('utf-8')]

    # Build prompt
    if correction_context:
        use_text_mode = False
        extraction_method = "correction"
        prompt = _build_correction_prompt(correction_context, vendors_list, txn_types_list, payment_methods_list)
    elif use_text_mode:
        prompt = _build_text_prompt(vendors_list, txn_types_list, payment_methods_list, extracted_text)
    else:
        page_count_hint = ""
        if len(base64_images) > 1:
            page_count_hint = f"\n\nIMPORTANT: This document has {len(base64_images)} pages. Analyze ALL pages and combine the data from all of them into a single response. The images are provided in page order (Page 1, Page 2, etc.).\n"
        prompt = _build_vision_prompt(vendors_list, txn_types_list, payment_methods_list, page_count_hint)

    # Call OpenAI
    if use_text_mode:
        print(f"[SCAN-RECEIPT] Enviando texto a OpenAI ({len(extracted_text)} chars)...")
        response = client.chat.completions.create(
            model=openai_model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=4000,
            temperature=0.1
        )
        print(f"[SCAN-RECEIPT] Respuesta recibida de OpenAI (modo texto)")
    else:
        message_content = [{"type": "text", "text": prompt}]
        for base64_img in base64_images:
            message_content.append({
                "type": "image_url",
                "image_url": {
                    "url": f"data:{media_type};base64,{base64_img}",
                    "detail": "high"
                }
            })

        print(f"[SCAN-RECEIPT] Enviando {len(base64_images)} imagen(es) a OpenAI Vision API...")
        response = client.chat.completions.create(
            model=openai_model,
            messages=[{"role": "user", "content": message_content}],
            max_tokens=4000,
            temperature=0.1
        )
        print(f"[SCAN-RECEIPT] Respuesta recibida de OpenAI (modo vision)")

    # Parse response
    result_text = response.choices[0].message.content.strip()
    parsed_data = _parse_json_response(result_text)

    # Validate structure
    if "expenses" not in parsed_data or not isinstance(parsed_data["expenses"], list):
        raise RuntimeError("OpenAI response missing 'expenses' array")

    for expense in parsed_data["expenses"]:
        if not all(key in expense for key in ["date", "description", "amount"]):
            raise RuntimeError("Some expenses are missing required fields (date, description, amount)")

    print(f"[SCAN-RECEIPT] COMPLETADO - metodo: {extraction_method}, items: {len(parsed_data['expenses'])}")

    return {
        "expenses": parsed_data["expenses"],
        "tax_summary": parsed_data.get("tax_summary"),
        "validation": parsed_data.get("validation"),
        "extraction_method": extraction_method,
        "model_used": model,
    }


def auto_categorize(stage: str, expenses: list) -> list:
    """
    Core auto-categorization logic. Categorizes expense descriptions using GPT-4o.

    Args:
        stage: Construction stage (e.g. "Framing", "Rough Plumbing")
        expenses: List of {"rowIndex": int, "description": str}

    Returns:
        List of {"rowIndex", "account_id", "account_name", "confidence", "reasoning", "warning"}

    Raises:
        ValueError: If stage or expenses empty
        RuntimeError: OpenAI failure, missing accounts
    """
    if not stage or not expenses:
        raise ValueError("Missing stage or expenses")

    # Fetch accounts
    accounts_resp = supabase.table("accounts").select("account_id, Name, AcctNum").execute()
    accounts = accounts_resp.data or []
    if not accounts:
        raise RuntimeError("No accounts found in database")

    client = _get_openai_client()

    # Build accounts list (exclude Labor accounts)
    accounts_list = []
    for acc in accounts:
        acc_name = acc.get("Name", "")
        if "Labor" in acc_name:
            continue
        accounts_list.append({
            "account_id": acc.get("account_id"),
            "name": acc_name,
            "number": acc.get("AcctNum")
        })

    # Build prompt
    prompt = f"""You are an expert construction accountant specializing in categorizing expenses.

CONSTRUCTION STAGE: {stage}

AVAILABLE ACCOUNTS:
{json.dumps(accounts_list, indent=2)}

EXPENSE DESCRIPTIONS TO CATEGORIZE:
{json.dumps([{"rowIndex": e["rowIndex"], "description": e["description"]} for e in expenses], indent=2)}

INSTRUCTIONS:
1. For each expense description, determine the MOST APPROPRIATE account from the available accounts list.
2. Consider the construction stage when categorizing. For example:
   - "Wood stud" in "Framing" stage -> likely "Lumber & Materials" or similar
   - "Wood stud" in "Roof" stage -> likely "Roofing Materials" or similar
   - Same material can have different categorizations based on stage
3. Calculate a confidence score (0-100) based on:
   - How well the description matches the account (50% weight)
   - How appropriate the stage is for this account (30% weight)
   - How specific/detailed the description is (20% weight)
4. ONLY use account_id values from the provided accounts list - do NOT invent accounts
5. If no good match exists, use the most general/appropriate account with confidence <60

SPECIAL RULES - VERY IMPORTANT:
- POWER TOOLS (drills, saws, grinders, nail guns, etc.) are CAPITAL ASSETS and should NOT be categorized in COGS accounts.
   - If you detect a power tool (the tool itself, not consumables), set confidence to 0 and add "WARNING: Power tool - not a COGS expense" in reasoning
   - Consumables FOR power tools (drill bits, saw blades, nails, etc.) ARE valid COGS and should be categorized normally

- BEVERAGES & REFRESHMENTS (water bottles, energy drinks, coffee, sports drinks, etc.) should be categorized under "Base Materials" account
   - These are considered crew provisions and ARE valid construction expenses

Return ONLY valid JSON in this format:
{{
  "categorizations": [
    {{
      "rowIndex": 0,
      "account_id": "exact-account-id-from-list",
      "account_name": "exact-account-name-from-list",
      "confidence": 85,
      "reasoning": "Brief explanation of why this account was chosen",
      "warning": "Optional warning message for special cases like power tools"
    }}
  ]
}}

IMPORTANT:
- Match rowIndex from input to output
- Use EXACT account_id and Name from the accounts list
- Confidence must be 0-100
- Be conservative with confidence scores - better to under-estimate than over-estimate
- DO NOT include any text before or after the JSON"""

    # Call OpenAI
    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {
                "role": "system",
                "content": "You are a construction accounting expert. You always return valid JSON with accurate account categorizations."
            },
            {
                "role": "user",
                "content": prompt
            }
        ],
        temperature=0.3,
        max_tokens=2000
    )

    # Parse response
    result_text = response.choices[0].message.content.strip()
    parsed_data = _parse_json_response(result_text)

    # Validate
    if "categorizations" not in parsed_data or not isinstance(parsed_data["categorizations"], list):
        raise RuntimeError("OpenAI response missing 'categorizations' array")

    for cat in parsed_data["categorizations"]:
        required_fields = ["rowIndex", "account_id", "account_name", "confidence"]
        if not all(field in cat for field in required_fields):
            raise RuntimeError(f"Categorization missing required fields: {cat}")
        if not (0 <= cat["confidence"] <= 100):
            cat["confidence"] = max(0, min(100, cat["confidence"]))

    return parsed_data["categorizations"]
