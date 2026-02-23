# Fill missing bill data by processing vault PDFs with regex extraction (no GPT).
#
# Downloads text-embedded PDFs from vault Receipts folder, runs pdfplumber +
# receipt_regex extraction, and matches extracted data to existing bills to fill
# expected_total and validate bill_id.
#
# Usage:
#   .venv/Scripts/python.exe fill_bills_from_pdf.py                  # dry-run
#   .venv/Scripts/python.exe fill_bills_from_pdf.py --apply          # execute updates
#   .venv/Scripts/python.exe fill_bills_from_pdf.py --verbose        # show extraction details
#   .venv/Scripts/python.exe fill_bills_from_pdf.py --apply --verbose

import io
import os
import re
import sys
import time
from urllib.parse import unquote
from dotenv import load_dotenv
from supabase import create_client

# Add project root so we can import services
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from services.receipt_regex import (
    extract_best,
    clean_receipt_text,
    fuzzy_match_vendor,
)

load_dotenv()

sb = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_SERVICE_ROLE_KEY"))

PROJECT_ID = "582cfbde-a1d6-411a-bca8-75f29df6f0d6"
RECEIPTS_FOLDER_ID = "615a599a-b26e-4863-85cb-807c95fd19ff"
DRY_RUN = "--apply" not in sys.argv
VERBOSE = "--verbose" in sys.argv

IMAGE_MIMES = {"image/png", "image/jpeg", "image/gif", "image/bmp", "image/tiff", "image/webp"}


def normalize_bill_id(bill_id):
    if not bill_id:
        return ""
    return re.sub(r"[^A-Z0-9]", "", bill_id.upper())


def download_vault_file(bucket_path):
    """Download file from vault bucket. Returns bytes or None."""
    try:
        data = sb.storage.from_("vault").download(bucket_path)
        return data
    except Exception as e:
        return None


def extract_text_from_pdf(pdf_bytes):
    """Extract text from PDF using pdfplumber. Returns text or None if scanned/image."""
    try:
        import pdfplumber
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            pages_text = []
            for page in pdf.pages:
                page_text = page.extract_text() or ""
                pages_text.append(page_text)
            text = "\n\n--- PAGE BREAK ---\n\n".join(pages_text)

            stripped = text.strip()
            if len(stripped) < 30:
                return None  # Likely scanned/image PDF

            return text
    except Exception:
        return None


def main():
    if DRY_RUN:
        print("=== DRY RUN (pass --apply to execute) ===\n")
    else:
        print("=== APPLY MODE ===\n")

    # 1. Get vault PDF files from Receipts folder
    print("1. Loading vault files from Receipts folder...")
    vf_resp = sb.table("vault_files") \
        .select("id, name, bucket_path, mime_type, file_hash, size_bytes") \
        .eq("parent_id", RECEIPTS_FOLDER_ID) \
        .eq("is_folder", False) \
        .eq("is_deleted", False) \
        .execute()
    vault_files = vf_resp.data or []
    print(f"   {len(vault_files)} total vault files")

    # Separate PDFs from images
    pdf_files = [f for f in vault_files if (f.get("mime_type") or "").lower() == "application/pdf"]
    image_files = [f for f in vault_files if (f.get("mime_type") or "").lower() in IMAGE_MIMES]
    other_files = [f for f in vault_files if f not in pdf_files and f not in image_files]
    print(f"   {len(pdf_files)} PDFs, {len(image_files)} images, {len(other_files)} other")

    # 2. Load bills for this project
    print("\n2. Loading bills...")
    # Get bill_ids referenced by expenses for this project
    exp_resp = sb.table("expenses_manual_COGS") \
        .select("bill_id") \
        .eq("project", PROJECT_ID) \
        .not_.is_("bill_id", "null") \
        .execute()
    project_bill_ids = set()
    for e in (exp_resp.data or []):
        bid = (e.get("bill_id") or "").strip()
        if bid:
            project_bill_ids.add(bid)

    bills_resp = sb.table("bills") \
        .select("bill_id, receipt_url, status, expected_total, vendor_id") \
        .execute()
    all_bills = {b["bill_id"]: b for b in (bills_resp.data or [])}

    # Build normalized bill lookup
    norm_bills = {}
    for bid, bill in all_bills.items():
        nb = normalize_bill_id(bid)
        if nb:
            norm_bills[nb] = bill

    bills_needing_total = {bid: all_bills[bid] for bid in project_bill_ids
                           if bid in all_bills and (not all_bills[bid].get("expected_total")
                                                    or all_bills[bid]["expected_total"] == 0)}
    print(f"   {len(project_bill_ids)} bills referenced by expenses")
    print(f"   {len(bills_needing_total)} bills with $0 expected_total")

    # 3. Load vendors
    print("\n3. Loading vendors...")
    v_resp = sb.table("Vendors").select("id, vendor_name").execute()
    vendors_map = {v["id"]: v["vendor_name"] for v in (v_resp.data or [])}
    vendors_list = list(vendors_map.values())
    print(f"   {len(vendors_list)} vendors loaded")

    # 4. Process ALL vault files (extract from filenames + PDF text)
    print(f"\n4. Processing {len(vault_files)} vault files...\n")

    # Import filename hints extractor
    from services.receipt_regex import extract_filename_hints

    stats = {
        "extracted": [],       # Successfully extracted data
        "text_ok": 0,          # PDF text extraction worked
        "filename_only": 0,    # Only filename hints available
        "no_data": 0,          # No useful data extracted
    }

    # Also extract bill_id from "bill_XXXXX_timestamp.ext" naming pattern
    _BILL_PREFIX_RE = re.compile(
        r'^bill_(.+?)_\d{10,}',   # bill_{bill_id}_{timestamp}
        re.IGNORECASE,
    )

    for i, vf in enumerate(vault_files, 1):
        name = vf["name"]
        mime = (vf.get("mime_type") or "").lower()
        bucket_path = vf.get("bucket_path")

        # --- Layer 1: Filename hints (always available) ---
        hints = extract_filename_hints(name)
        fn_total = hints.get("total_hint")
        fn_date = hints.get("date_hint")
        fn_vendor = hints.get("vendor_hint")

        # Extract bill_id from "bill_XXXXX_timestamp" pattern
        fn_bill_id = None
        bm = _BILL_PREFIX_RE.match(name)
        if bm:
            raw_bid = bm.group(1).strip().rstrip("_")
            # Clean up: "H0659-1136389" stays, ". H0659-1136389" -> "H0659-1136389"
            raw_bid = re.sub(r'^[\.\s]+', '', raw_bid)
            if raw_bid:
                fn_bill_id = raw_bid

        # --- Layer 2: PDF text extraction (only for PDFs) ---
        text_total = None
        text_bill_id = None
        text_date = None
        text_vendor = None
        text_confidence = None
        text_items = []

        if mime == "application/pdf" and bucket_path:
            pdf_bytes = download_vault_file(bucket_path)
            if pdf_bytes:
                raw_text = extract_text_from_pdf(pdf_bytes)
                if raw_text:
                    cleaned = clean_receipt_text(raw_text)
                    meta, scoring = extract_best(cleaned, filename=name)
                    text_total = meta.get("grand_total")
                    text_bill_id = meta.get("bill_id")
                    text_date = meta.get("date")
                    text_vendor = fuzzy_match_vendor(cleaned, vendors_list)
                    text_confidence = (meta.get("confidence") or {}).get("grand_total")
                    text_items = meta.get("line_items", [])
                    if text_total:
                        stats["text_ok"] += 1

        # --- Merge: prefer text extraction, fallback to filename ---
        grand_total = text_total or fn_total
        bill_id = text_bill_id or fn_bill_id
        date = text_date or fn_date
        vendor = text_vendor if (text_vendor and text_vendor != "Unknown") else fn_vendor
        confidence = text_confidence or ("filename" if fn_total else None)
        source = "text" if text_total else ("filename" if fn_total else "none")

        if not grand_total and not bill_id:
            stats["no_data"] += 1
            if VERBOSE:
                print(f"   [{i}/{len(vault_files)}] SKIP (no data): {name[:50]}")
            continue

        if source == "filename":
            stats["filename_only"] += 1

        # Try to match to existing bill
        norm_ext = normalize_bill_id(bill_id) if bill_id else ""
        matched_bill = norm_bills.get(norm_ext) if norm_ext else None

        result = {
            "vault_file": vf,
            "grand_total": grand_total,
            "extracted_bill_id": bill_id,
            "extracted_date": date,
            "extracted_vendor": vendor,
            "confidence": confidence or "unknown",
            "source": source,
            "items_count": len(text_items),
            "matched_bill": matched_bill,
            "matched_bill_id": matched_bill["bill_id"] if matched_bill else None,
            "bill_needs_total": (matched_bill["bill_id"] in bills_needing_total) if matched_bill else False,
        }
        stats["extracted"].append(result)

        if grand_total:
            bill_tag = f"-> bill {matched_bill['bill_id']}" if matched_bill else "(no bill match)"
            needs = " [FILL]" if result["bill_needs_total"] else ""
            src = f"[{source}]"
            print(f"   [{i}/{len(vault_files)}] {src:<10} {name[:38]:<38} "
                  f"${grand_total:>10,.2f}  {bill_tag}{needs}")
        elif bill_id and not grand_total:
            bill_tag = f"-> bill {matched_bill['bill_id']}" if matched_bill else "(no bill match)"
            print(f"   [{i}/{len(vault_files)}] [bill_id]  {name[:38]:<38} "
                  f"{'$?':>11}  bill={bill_id}  {bill_tag}")

        if i % 15 == 0:
            time.sleep(0.3)

    # 5. Summary
    extracted = stats["extracted"]
    with_total = [r for r in extracted if r["grand_total"] and r["grand_total"] > 0]
    with_bill_match = [r for r in with_total if r["matched_bill"]]
    can_update = [r for r in with_bill_match if r["bill_needs_total"]]

    print(f"\n{'='*60}")
    print("SUMMARY")
    print(f"{'='*60}")
    print(f"  Total files processed:      {len(vault_files)}")
    print(f"  Data extracted:             {len(extracted)}")
    print(f"    via PDF text:             {stats['text_ok']}")
    print(f"    via filename only:        {stats['filename_only']}")
    print(f"  No useful data:             {stats['no_data']}")
    print(f"\n  With grand_total:           {len(with_total)}")
    print(f"  Matched to existing bill:   {len(with_bill_match)}")
    print(f"  Can fill $0 expected_total: {len(can_update)}")

    if not with_total:
        print("\n  No totals extracted!")
        return

    # Confidence breakdown
    high = [r for r in with_total if r["confidence"] == "high"]
    medium = [r for r in with_total if r["confidence"] == "medium"]
    low = [r for r in with_total if r["confidence"] == "low"]
    print(f"\n  Confidence: HIGH={len(high)}, MEDIUM={len(medium)}, LOW={len(low)}")

    # Show all extractions
    print(f"\n{'='*60}")
    print("ALL EXTRACTIONS")
    print(f"{'='*60}")

    for conf_level in ["high", "medium", "low", "unknown"]:
        group = [r for r in with_total if r["confidence"] == conf_level]
        if not group:
            continue
        print(f"\n  --- {conf_level.upper()} confidence ({len(group)}) ---")
        for r in sorted(group, key=lambda x: x["grand_total"], reverse=True):
            name = r["vault_file"]["name"][:35]
            gt = r["grand_total"]
            bid = r["extracted_bill_id"] or "?"
            vendor = r["extracted_vendor"][:18] if r["extracted_vendor"] else "?"
            matched = r["matched_bill_id"] or "-"
            needs = " [FILL]" if r["bill_needs_total"] else ""
            print(f"    {name:<35} ${gt:>10,.2f}  bill={bid:<18} vendor={vendor:<18} db_match={matched}{needs}")

    # Show what will be updated
    if can_update:
        total_value = sum(r["grand_total"] for r in can_update)
        print(f"\n{'='*60}")
        print(f"BILLS TO UPDATE ({len(can_update)}, total ${total_value:,.2f})")
        print(f"{'='*60}")
        for r in can_update:
            bid = r["matched_bill_id"]
            gt = r["grand_total"]
            conf = r["confidence"]
            name = r["vault_file"]["name"][:40]
            print(f"    {bid:<28} <- ${gt:>10,.2f}  (conf={conf}, from: {name})")

    if DRY_RUN:
        if can_update:
            print(f"\n=== DRY RUN: Would update {len(can_update)} bills with expected_total ===")
        else:
            print(f"\n=== DRY RUN: No bills to update (0 matched to $0 bills) ===")
        print("Run with --apply to execute.")
        return

    if not can_update:
        print("\n  No bills to update.")
        return

    # 6. Apply updates
    print(f"\n6. Updating {len(can_update)} bills...")
    success = 0
    errors = 0

    for r in can_update:
        bid = r["matched_bill_id"]
        try:
            sb.table("bills").update({
                "expected_total": r["grand_total"],
            }).eq("bill_id", bid).execute()
            print(f"   OK: {bid} -> ${r['grand_total']:,.2f}")
            success += 1
        except Exception as e:
            print(f"   ERROR: {bid} -> {str(e)[:100]}")
            errors += 1

    print(f"\nDone! Updated: {success}, Errors: {errors}")


if __name__ == "__main__":
    main()
