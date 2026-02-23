# Scan receipt PDFs for bill numbers using regex extraction (super-fast mode).
#
# Downloads receipts from expenses and bills, extracts text with pdfplumber,
# runs receipt_regex to find bill_id. Only targets expenses/bills that need it.
#
# Usage:
#   .venv/Scripts/python.exe scan_receipts_for_bills.py

import io
import os
import re
import sys
import time
from urllib.parse import unquote
from dotenv import load_dotenv
from supabase import create_client

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from services.receipt_regex import extract_best, clean_receipt_text

load_dotenv()

sb = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_SERVICE_ROLE_KEY"))
PROJECT_ID = "582cfbde-a1d6-411a-bca8-75f29df6f0d6"


def download_from_storage(url):
    """Download file from Supabase storage given a full or partial URL. Returns bytes or None."""
    if not url:
        return None
    try:
        # Extract bucket and path from URL
        # URLs look like: https://xxx.supabase.co/storage/v1/object/public/expenses-receipts/...
        # or: /storage/v1/object/public/expenses-receipts/...
        # or just: expenses-receipts/path/to/file

        if "expenses-receipts/" in url:
            path = url.split("expenses-receipts/", 1)[1]
            path = unquote(path)
            data = sb.storage.from_("expenses-receipts").download(path)
            return data
        elif "vault/" in url:
            path = url.split("vault/", 1)[1]
            path = unquote(path)
            data = sb.storage.from_("vault").download(path)
            return data
        else:
            return None
    except Exception as e:
        return None


def extract_text_from_pdf(pdf_bytes):
    """Extract text from PDF using pdfplumber. Returns text or None if scanned."""
    try:
        import pdfplumber
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            pages = []
            for page in pdf.pages:
                t = page.extract_text() or ""
                pages.append(t)
            text = "\n\n".join(pages)
            if len(text.strip()) < 30:
                return None
            return text
    except Exception:
        return None


def main():
    print("1. Loading expenses and bills...\n")

    # Load all expenses for project
    r = sb.table("expenses_manual_COGS") \
        .select("expense_id, bill_id, Amount, TxnDate, LineDescription, vendor_id, receipt_url, status") \
        .eq("project", PROJECT_ID) \
        .execute()
    expenses = r.data or []

    # Load bills
    b_resp = sb.table("bills") \
        .select("bill_id, receipt_url, status, expected_total, vendor_id") \
        .execute()
    all_bills = {b["bill_id"]: b for b in (b_resp.data or [])}

    # Load vendors
    v_resp = sb.table("Vendors").select("id, vendor_name").execute()
    vendors = {v["id"]: v["vendor_name"] for v in (v_resp.data or [])}

    # ---- Collect all unique receipt URLs to scan ----
    # Source 1: Expenses with receipt_url (especially those without bill_id)
    # Source 2: Bills with receipt_url
    urls_to_scan = {}  # url -> {source, expense/bill info}

    no_bill_expenses = [e for e in expenses if not (e.get("bill_id") or "").strip()]
    with_bill_expenses = [e for e in expenses if (e.get("bill_id") or "").strip()]

    print(f"   Expenses: {len(expenses)} total, {len(no_bill_expenses)} without bill_id")

    # Expenses WITHOUT bill_id that HAVE receipt_url
    exp_with_receipt = [e for e in no_bill_expenses if e.get("receipt_url")]
    print(f"   Expenses without bill_id + with receipt: {len(exp_with_receipt)}")

    for e in exp_with_receipt:
        url = e["receipt_url"]
        if url not in urls_to_scan:
            urls_to_scan[url] = {"source": "expense_no_bill", "expense": e}

    # Bills with receipt_url (project bills)
    project_bill_ids = set()
    for e in expenses:
        bid = (e.get("bill_id") or "").strip()
        if bid:
            project_bill_ids.add(bid)

    bills_with_receipt = {bid: all_bills[bid] for bid in project_bill_ids
                          if bid in all_bills and all_bills[bid].get("receipt_url")}
    print(f"   Project bills with receipt: {len(bills_with_receipt)}")

    for bid, bill in bills_with_receipt.items():
        url = bill["receipt_url"]
        if url not in urls_to_scan:
            urls_to_scan[url] = {"source": "bill", "bill": bill, "bill_id": bid}

    # Also scan expenses WITH bill_id that have receipt_url (for cross-validation)
    exp_with_bill_and_receipt = [e for e in with_bill_expenses if e.get("receipt_url")]
    print(f"   Expenses with bill_id + receipt: {len(exp_with_bill_and_receipt)} (for validation)")
    # Skip these for now - focus on what we need

    print(f"\n   Total unique URLs to scan: {len(urls_to_scan)}")

    # ---- Download and scan ----
    print(f"\n2. Downloading and scanning {len(urls_to_scan)} receipts...\n")

    results = {
        "text_pdf": [],      # PDF with extractable text
        "scanned_pdf": [],   # PDF but scanned/image
        "image": [],         # Image file (PNG, JPG)
        "failed": [],        # Download failed
    }

    for i, (url, info) in enumerate(urls_to_scan.items(), 1):
        source = info["source"]

        # Determine file type from URL
        fn = url.rsplit("/", 1)[-1] if "/" in url else url
        fn_lower = fn.lower()
        is_image = any(fn_lower.endswith(ext) for ext in [".png", ".jpg", ".jpeg", ".gif", ".bmp"])

        if is_image:
            results["image"].append({"url": url, "info": info, "filename": fn})
            continue

        # Download
        data = download_from_storage(url)
        if not data:
            results["failed"].append({"url": url, "info": info, "filename": fn})
            if i % 20 == 0:
                print(f"   [{i}/{len(urls_to_scan)}] ...")
            continue

        # Check if it's a PDF (might be image even without extension)
        is_pdf = data[:4] == b"%PDF"
        is_img_bytes = data[:4] in [b"\x89PNG", b"\xff\xd8\xff\xe0", b"\xff\xd8\xff\xe1"]

        if is_img_bytes:
            results["image"].append({"url": url, "info": info, "filename": fn})
            continue

        if not is_pdf:
            results["image"].append({"url": url, "info": info, "filename": fn})
            continue

        # Try extracting text
        text = extract_text_from_pdf(data)
        if not text:
            results["scanned_pdf"].append({"url": url, "info": info, "filename": fn})
            continue

        # TEXT PDF! Run regex extraction
        cleaned = clean_receipt_text(text)
        meta, scoring = extract_best(cleaned, filename=fn)

        bill_id = meta.get("bill_id")
        grand_total = meta.get("grand_total")
        date = meta.get("date")
        confidence = (meta.get("confidence") or {}).get("grand_total", "?")
        items = meta.get("line_items", [])

        result = {
            "url": url, "info": info, "filename": fn,
            "text_len": len(text),
            "bill_id": bill_id,
            "grand_total": grand_total,
            "date": date,
            "confidence": confidence,
            "items_count": len(items),
            "text_preview": text[:200].replace("\n", " | "),
        }
        results["text_pdf"].append(result)

        # Show progress
        source_tag = f"[{source}]"
        bid_tag = f"bill_id={bill_id}" if bill_id else "no_bill_id"
        total_tag = f"${grand_total:,.2f}" if grand_total else "$?"
        print(f"   [{i}/{len(urls_to_scan)}] TEXT PDF! {source_tag:<20} {fn[:35]:<35} {bid_tag:<25} {total_tag}")

        if i % 15 == 0:
            time.sleep(0.3)

    # ---- Summary ----
    print(f"\n{'='*70}")
    print("SCAN RESULTS")
    print(f"{'='*70}")
    print(f"\n  Text PDFs (extractable):  {len(results['text_pdf'])}")
    print(f"  Scanned PDFs (no text):   {len(results['scanned_pdf'])}")
    print(f"  Images (PNG/JPG):         {len(results['image'])}")
    print(f"  Download failed:          {len(results['failed'])}")

    if results["text_pdf"]:
        print(f"\n{'='*70}")
        print(f"TEXT PDFs WITH DATA ({len(results['text_pdf'])})")
        print(f"{'='*70}")

        with_bill = [r for r in results["text_pdf"] if r["bill_id"]]
        without_bill = [r for r in results["text_pdf"] if not r["bill_id"]]

        if with_bill:
            print(f"\n  --- With bill_id extracted ({len(with_bill)}) ---")
            for r in with_bill:
                src = r["info"]["source"]
                gt = f"${r['grand_total']:,.2f}" if r["grand_total"] else "$?"
                print(f"    [{src:<15}] bill_id={r['bill_id']:<20} {gt:>12} | {r['filename'][:40]}")
                print(f"    {'':19} text: {r['text_preview'][:80]}...")

        if without_bill:
            print(f"\n  --- Without bill_id ({len(without_bill)}) ---")
            for r in without_bill:
                src = r["info"]["source"]
                gt = f"${r['grand_total']:,.2f}" if r["grand_total"] else "$?"
                print(f"    [{src:<15}] {gt:>12} ({r['items_count']} items) | {r['filename'][:40]}")
                print(f"    {'':19} text: {r['text_preview'][:80]}...")
    else:
        print("\n  No text PDFs found - all receipts are scanned images.")

    # Show breakdown by source
    print(f"\n{'='*70}")
    print("BY FILE TYPE (from expenses without bill_id)")
    print(f"{'='*70}")
    from_exp = [r for cat in results.values() for r in cat if r.get("info", {}).get("source") == "expense_no_bill"]
    exp_text = [r for r in results["text_pdf"] if r["info"]["source"] == "expense_no_bill"]
    exp_scanned = [r for r in results["scanned_pdf"] if r["info"]["source"] == "expense_no_bill"]
    exp_image = [r for r in results["image"] if r["info"]["source"] == "expense_no_bill"]
    print(f"  From {len(exp_with_receipt)} expenses without bill_id that have receipt_url:")
    print(f"    Text PDFs:    {len(exp_text)}")
    print(f"    Scanned PDFs: {len(exp_scanned)}")
    print(f"    Images:       {len(exp_image)}")

    print(f"\n{'='*70}")
    print("BY FILE TYPE (from bills)")
    print(f"{'='*70}")
    bill_text = [r for r in results["text_pdf"] if r["info"]["source"] == "bill"]
    bill_scanned = [r for r in results["scanned_pdf"] if r["info"]["source"] == "bill"]
    bill_image = [r for r in results["image"] if r["info"]["source"] == "bill"]
    bill_failed = [r for r in results["failed"] if r["info"]["source"] == "bill"]
    print(f"  From {len(bills_with_receipt)} bills with receipt_url:")
    print(f"    Text PDFs:    {len(bill_text)}")
    print(f"    Scanned PDFs: {len(bill_scanned)}")
    print(f"    Images:       {len(bill_image)}")
    print(f"    Failed DL:    {len(bill_failed)}")


if __name__ == "__main__":
    main()
