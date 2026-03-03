# Fix script: Normalize receipt URLs with # in filenames.
#
# Problem: When bill_id starts with # (e.g., #10910), the upload created filenames
# like "bill_#10910_timestamp.pdf". The # acts as a URL fragment separator in HTTP,
# so the file was stored as just "bill_" (truncated). The DB has the full URL with #,
# but the file is inaccessible.
#
# Fix: For each broken URL, find the most recent clean copy of the file in storage
# (uploaded after the frontend sanitization fix), and update the DB to point to it.
# If no clean copy exists, set receipt_url = null.
#
# Usage:
#   cd NGM_API
#   .venv/Scripts/python.exe normalize_receipt_urls.py              # dry-run
#   .venv/Scripts/python.exe normalize_receipt_urls.py --execute     # apply changes

import os
import sys
import re
import time
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
    print("ERROR: Missing SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY in .env")
    sys.exit(1)

sb = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)

BUCKET = "expenses-receipts"
DRY_RUN = "--execute" not in sys.argv


def build_public_url(folder, filename):
    """Build a Supabase Storage public URL."""
    return f"{SUPABASE_URL}/storage/v1/object/public/{BUCKET}/{folder}/{filename}"


def find_clean_file(folder, bill_id_clean):
    """Find the most recent clean file matching bill_{clean_id}_timestamp.ext in storage."""
    prefix = f"bill_{bill_id_clean}_"
    try:
        files = sb.storage.from_(BUCKET).list(folder, {"limit": 1000})
    except Exception as e:
        print(f"    ERROR listing folder {folder}: {e}")
        return None

    matches = []
    for f in (files or []):
        meta = f.get("metadata") or {}
        if meta.get("size") is None or meta.get("size", 0) <= 0:
            continue
        if f["name"].startswith(prefix):
            matches.append(f)

    if not matches:
        return None

    # Return the most recent one (highest timestamp in filename)
    matches.sort(key=lambda f: f["name"], reverse=True)
    return matches[0]["name"]


def main():
    if DRY_RUN:
        print("=" * 60)
        print("  DRY RUN MODE (pass --execute to apply changes)")
        print("=" * 60)
    else:
        print("=" * 60)
        print("  EXECUTE MODE - DB URLs will be updated")
        print("=" * 60)

    print()

    # 1. Find all bills with # in receipt_url
    print("Scanning bills table for receipt_urls containing #...")
    resp = sb.table("bills") \
        .select("bill_id, receipt_url") \
        .like("receipt_url", "%#%") \
        .execute()

    broken_bills = resp.data or []
    print(f"  Found {len(broken_bills)} bills with # in receipt_url")

    # 2. Find all expenses with # in receipt_url
    print("Scanning expenses for receipt_urls containing #...")
    resp2 = sb.table("expenses_manual_COGS") \
        .select("expense_id, bill_id, receipt_url") \
        .like("receipt_url", "%#%") \
        .execute()

    broken_expenses = resp2.data or []
    print(f"  Found {len(broken_expenses)} expenses with # in receipt_url")

    if not broken_bills and not broken_expenses:
        print("\nNo broken URLs found. Nothing to do!")
        return

    stats = {"fixed": 0, "nulled": 0, "error": 0}

    # 3. Fix bills
    if broken_bills:
        print(f"\n--- Fixing {len(broken_bills)} bills ---")

    for i, bill in enumerate(broken_bills, 1):
        bill_id = bill["bill_id"]
        old_url = bill["receipt_url"]

        # Extract folder (project ID) from URL
        marker = f"/object/public/{BUCKET}/"
        if marker not in old_url.split("#")[0]:
            print(f"  [{i}/{len(broken_bills)}] SKIP (can't parse URL): {bill_id}")
            continue

        path_part = old_url.split("#")[0].split(marker, 1)[1].split("?")[0]
        folder = path_part.rstrip("/")  # This is the folder up to the truncation point
        # The folder might include a partial filename like "PROJECT_ID/bill_"
        # We need just the project folder
        if "/" in folder:
            folder = folder.rsplit("/", 1)[0]

        # Clean the bill_id (remove #)
        bill_id_clean = re.sub(r'[#$%&{}\\<>*?/!\'":@+`|=]', '', bill_id)

        # Find clean file in storage
        clean_filename = find_clean_file(folder, bill_id_clean)

        if clean_filename:
            new_url = build_public_url(folder, clean_filename)
            if DRY_RUN:
                print(f"  [{i}/{len(broken_bills)}] WOULD FIX: bill {bill_id}")
                print(f"    Old: ...{old_url[-60:]}")
                print(f"    New: ...{new_url[-60:]}")
            else:
                try:
                    sb.table("bills") \
                        .update({"receipt_url": new_url}) \
                        .eq("bill_id", bill_id) \
                        .execute()
                    print(f"  [{i}/{len(broken_bills)}] FIXED: bill {bill_id} -> {clean_filename}")
                    stats["fixed"] += 1
                except Exception as e:
                    print(f"  [{i}/{len(broken_bills)}] ERROR: {bill_id} -> {str(e)[:100]}")
                    stats["error"] += 1
        else:
            if DRY_RUN:
                print(f"  [{i}/{len(broken_bills)}] WOULD NULL: bill {bill_id} (no clean file found)")
            else:
                try:
                    sb.table("bills") \
                        .update({"receipt_url": None}) \
                        .eq("bill_id", bill_id) \
                        .execute()
                    print(f"  [{i}/{len(broken_bills)}] NULLED: bill {bill_id} (no clean file)")
                    stats["nulled"] += 1
                except Exception as e:
                    print(f"  [{i}/{len(broken_bills)}] ERROR: {bill_id} -> {str(e)[:100]}")
                    stats["error"] += 1

    # 4. Fix expenses (same approach)
    if broken_expenses:
        print(f"\n--- Fixing {len(broken_expenses)} expenses ---")

    for i, exp in enumerate(broken_expenses, 1):
        expense_id = exp["expense_id"]
        old_url = exp["receipt_url"]
        exp_bill_id = exp.get("bill_id") or ""

        marker = f"/object/public/{BUCKET}/"
        if marker not in old_url.split("#")[0]:
            print(f"  [{i}/{len(broken_expenses)}] SKIP (can't parse URL): {expense_id}")
            continue

        path_part = old_url.split("#")[0].split(marker, 1)[1].split("?")[0]
        folder = path_part.rstrip("/")
        if "/" in folder:
            folder = folder.rsplit("/", 1)[0]

        bill_id_clean = re.sub(r'[#$%&{}\\<>*?/!\'":@+`|=]', '', exp_bill_id) if exp_bill_id else None

        clean_filename = None
        if bill_id_clean:
            clean_filename = find_clean_file(folder, bill_id_clean)

        if clean_filename:
            new_url = build_public_url(folder, clean_filename)
            if DRY_RUN:
                print(f"  [{i}/{len(broken_expenses)}] WOULD FIX: expense {expense_id[:12]}...")
            else:
                try:
                    sb.table("expenses_manual_COGS") \
                        .update({"receipt_url": new_url}) \
                        .eq("expense_id", expense_id) \
                        .execute()
                    print(f"  [{i}/{len(broken_expenses)}] FIXED: expense {expense_id[:12]}...")
                    stats["fixed"] += 1
                except Exception as e:
                    print(f"  [{i}/{len(broken_expenses)}] ERROR: {expense_id[:12]}... -> {str(e)[:100]}")
                    stats["error"] += 1
        else:
            if DRY_RUN:
                print(f"  [{i}/{len(broken_expenses)}] WOULD NULL: expense {expense_id[:12]}... (no clean file)")
            else:
                try:
                    sb.table("expenses_manual_COGS") \
                        .update({"receipt_url": None}) \
                        .eq("expense_id", expense_id) \
                        .execute()
                    print(f"  [{i}/{len(broken_expenses)}] NULLED: expense {expense_id[:12]}...")
                    stats["nulled"] += 1
                except Exception as e:
                    print(f"  [{i}/{len(broken_expenses)}] ERROR: {expense_id[:12]}... -> {str(e)[:100]}")
                    stats["error"] += 1

    # 5. Clean up orphaned "bill_" file(s)
    print("\n--- Checking for orphaned 'bill_' truncated files ---")
    folders_to_check = set()
    for bill in broken_bills:
        old_url = bill["receipt_url"]
        marker = f"/object/public/{BUCKET}/"
        if marker in old_url.split("#")[0]:
            path_part = old_url.split("#")[0].split(marker, 1)[1].split("?")[0]
            folder = path_part.rstrip("/")
            if "/" in folder:
                folder = folder.rsplit("/", 1)[0]
            folders_to_check.add(folder)

    for folder in folders_to_check:
        try:
            files = sb.storage.from_(BUCKET).list(folder, {"limit": 1000})
            orphans = [f for f in (files or []) if f["name"] == "bill_" and (f.get("metadata") or {}).get("size")]
            for orphan in orphans:
                if DRY_RUN:
                    print(f"  WOULD DELETE orphan: {folder}/bill_ ({orphan.get('metadata', {}).get('size', '?')} bytes)")
                else:
                    try:
                        sb.storage.from_(BUCKET).remove([f"{folder}/bill_"])
                        print(f"  DELETED orphan: {folder}/bill_")
                    except Exception as e:
                        print(f"  WARN: Could not delete {folder}/bill_: {e}")
        except Exception as e:
            print(f"  WARN: Could not check folder {folder}: {e}")

    # Summary
    print(f"\n{'=' * 60}")
    if DRY_RUN:
        total = len(broken_bills) + len(broken_expenses)
        print(f"DRY RUN COMPLETE - {total} broken URLs found")
        print(f"\nRun with --execute to apply changes.")
    else:
        print(f"MIGRATION COMPLETE")
        print(f"  Fixed (pointed to clean file): {stats['fixed']}")
        print(f"  Nulled (no clean file found): {stats['nulled']}")
        print(f"  Errors: {stats['error']}")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
