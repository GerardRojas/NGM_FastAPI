# One-time script: Normalize receipt URLs that contain special characters (#, etc.)
# in their filenames. Downloads files from Supabase Storage, reuploads with
# clean filenames, updates DB URLs, and deletes the old files.
#
# Usage:
#   cd NGM_API
#   .venv/Scripts/python.exe normalize_receipt_urls.py              # dry-run
#   .venv/Scripts/python.exe normalize_receipt_urls.py --execute     # apply changes

import os
import sys
import re
import time
from urllib.parse import unquote
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
    print("ERROR: Missing SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY in .env")
    sys.exit(1)

sb = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)

KNOWN_BUCKETS = ("expenses-receipts", "pending-expenses", "vault")
BAD_CHARS_PATTERN = re.compile(r'[#$%&{}\\<>*?/!\'":@+`|=]')
DRY_RUN = "--execute" not in sys.argv


def sanitize_filename(name):
    """Remove/replace characters that cause issues in storage paths."""
    name = re.sub(r'[#$%&{}\\<>*?/!\'":@+`|=]', "_", name)
    name = re.sub(r"_+", "_", name)
    return name.strip("_")


def parse_receipt_url(url):
    """Extract bucket name and file path from a Supabase Storage public URL.
    Returns (bucket, filepath) or (None, None) if not parseable.
    """
    if not url or not isinstance(url, str):
        return None, None

    # Strip URL fragment (everything after #)
    clean_url = url.split("#")[0]

    for bucket in KNOWN_BUCKETS:
        marker = f"/object/public/{bucket}/"
        if marker in clean_url:
            path = clean_url.split(marker, 1)[1].split("?")[0]
            # URL-decode (e.g., %23 -> #)
            path = unquote(path)
            return bucket, path

    return None, None


def filename_needs_fix(filepath):
    """Check if the filename portion of a path contains bad characters."""
    if not filepath:
        return False
    filename = filepath.rsplit("/", 1)[-1]
    return bool(BAD_CHARS_PATTERN.search(filename))


def build_public_url(bucket, filepath):
    """Build a Supabase Storage public URL from bucket and filepath."""
    return f"{SUPABASE_URL}/storage/v1/object/public/{bucket}/{filepath}"


def scan_expenses():
    """Find all expenses with receipt_url containing bad chars in filename."""
    print("Scanning expenses_manual_COGS for broken receipt_urls...")
    bad_rows = []
    page_size = 1000
    offset = 0

    while True:
        resp = sb.table("expenses_manual_COGS") \
            .select("expense_id, receipt_url") \
            .not_.is_("receipt_url", "null") \
            .range(offset, offset + page_size - 1) \
            .execute()

        batch = resp.data or []
        if not batch:
            break

        for row in batch:
            url = row.get("receipt_url", "")
            bucket, path = parse_receipt_url(url)
            if path and filename_needs_fix(path):
                bad_rows.append({
                    "table": "expenses_manual_COGS",
                    "id_col": "expense_id",
                    "id_val": row["expense_id"],
                    "receipt_url": url,
                    "bucket": bucket,
                    "filepath": path,
                })

        offset += page_size
        if len(batch) < page_size:
            break

    print(f"  Found {len(bad_rows)} expenses with bad receipt_url filenames")
    return bad_rows


def scan_bills():
    """Find all bills with receipt_url containing bad chars in filename."""
    print("Scanning bills for broken receipt_urls...")
    bad_rows = []

    resp = sb.table("bills") \
        .select("bill_id, receipt_url") \
        .not_.is_("receipt_url", "null") \
        .execute()

    for row in (resp.data or []):
        url = row.get("receipt_url", "")
        bucket, path = parse_receipt_url(url)
        if path and filename_needs_fix(path):
            bad_rows.append({
                "table": "bills",
                "id_col": "bill_id",
                "id_val": row["bill_id"],
                "receipt_url": url,
                "bucket": bucket,
                "filepath": path,
            })

    print(f"  Found {len(bad_rows)} bills with bad receipt_url filenames")
    return bad_rows


def migrate_file(row, index, total):
    """Download file, reupload with clean name, update DB, delete old file."""
    bucket = row["bucket"]
    old_path = row["filepath"]
    table = row["table"]
    id_col = row["id_col"]
    id_val = row["id_val"]

    # Build clean path
    parts = old_path.rsplit("/", 1)
    folder = parts[0] if len(parts) > 1 else ""
    old_filename = parts[-1]

    base, ext = os.path.splitext(old_filename)
    clean_base = sanitize_filename(base)
    clean_filename = f"{clean_base}{ext}"

    if clean_filename == old_filename:
        print(f"  [{index}/{total}] SKIP (already clean): {old_filename}")
        return "skip"

    clean_path = f"{folder}/{clean_filename}" if folder else clean_filename
    new_url = build_public_url(bucket, clean_path)

    if DRY_RUN:
        print(f"  [{index}/{total}] WOULD FIX: {old_filename} -> {clean_filename}")
        print(f"           Table: {table}.{id_col} = {id_val}")
        print(f"           New URL: {new_url[:100]}...")
        return "dry"

    try:
        # 1. Download from old path
        file_data = sb.storage.from_(bucket).download(old_path)
        if not file_data:
            print(f"  [{index}/{total}] ERROR (download empty): {old_filename}")
            return "error"

        # 2. Upload to clean path
        # Detect content type from extension
        ext_lower = ext.lower()
        content_types = {
            ".pdf": "application/pdf",
            ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
            ".png": "image/png",
            ".gif": "image/gif",
            ".webp": "image/webp",
        }
        content_type = content_types.get(ext_lower, "application/octet-stream")

        sb.storage.from_(bucket).upload(
            clean_path,
            file_data,
            {"content-type": content_type, "upsert": "true"},
        )

        # 3. Update DB record
        sb.table(table) \
            .update({"receipt_url": new_url}) \
            .eq(id_col, id_val) \
            .execute()

        # 4. Delete old file
        sb.storage.from_(bucket).remove([old_path])

        print(f"  [{index}/{total}] OK: {old_filename} -> {clean_filename}")
        return "ok"

    except Exception as e:
        err_msg = str(e)
        if "already exists" in err_msg.lower() or "Duplicate" in err_msg:
            # File already exists at clean path - just update DB and delete old
            try:
                sb.table(table) \
                    .update({"receipt_url": new_url}) \
                    .eq(id_col, id_val) \
                    .execute()
                sb.storage.from_(bucket).remove([old_path])
                print(f"  [{index}/{total}] OK (existed): {old_filename} -> {clean_filename}")
                return "ok"
            except Exception as e2:
                print(f"  [{index}/{total}] ERROR (cleanup): {old_filename} -> {str(e2)[:100]}")
                return "error"
        else:
            print(f"  [{index}/{total}] ERROR: {old_filename} -> {err_msg[:100]}")
            return "error"


def main():
    if DRY_RUN:
        print("=" * 60)
        print("  DRY RUN MODE (pass --execute to apply changes)")
        print("=" * 60)
    else:
        print("=" * 60)
        print("  EXECUTE MODE - Files will be renamed and DB updated")
        print("=" * 60)

    print()

    # 1. Scan for broken URLs
    expense_rows = scan_expenses()
    bill_rows = scan_bills()
    all_rows = expense_rows + bill_rows

    if not all_rows:
        print("\nNo broken receipt URLs found. Nothing to do!")
        return

    # Deduplicate by (bucket, filepath) to avoid migrating same file twice
    seen_files = {}
    unique_rows = []
    duplicate_db_updates = []

    for row in all_rows:
        key = (row["bucket"], row["filepath"])
        if key not in seen_files:
            seen_files[key] = row
            unique_rows.append(row)
        else:
            # Same file referenced by multiple rows - just need DB update
            duplicate_db_updates.append(row)

    print(f"\nTotal broken URLs: {len(all_rows)}")
    print(f"  Unique files to rename: {len(unique_rows)}")
    print(f"  Additional DB updates (same file): {len(duplicate_db_updates)}")

    # 2. Migrate unique files
    print(f"\n--- Migrating {len(unique_rows)} files ---")
    stats = {"ok": 0, "skip": 0, "error": 0, "dry": 0}

    for i, row in enumerate(unique_rows, 1):
        result = migrate_file(row, i, len(unique_rows))
        stats[result] = stats.get(result, 0) + 1

        # Rate limit
        if i % 5 == 0:
            time.sleep(0.3)

    # 3. Update duplicate DB references (same file, different row)
    if duplicate_db_updates:
        print(f"\n--- Updating {len(duplicate_db_updates)} duplicate DB references ---")
        for i, row in enumerate(duplicate_db_updates, 1):
            old_path = row["filepath"]
            parts = old_path.rsplit("/", 1)
            folder = parts[0] if len(parts) > 1 else ""
            old_filename = parts[-1]
            base, ext = os.path.splitext(old_filename)
            clean_base = sanitize_filename(base)
            clean_filename = f"{clean_base}{ext}"
            clean_path = f"{folder}/{clean_filename}" if folder else clean_filename
            new_url = build_public_url(row["bucket"], clean_path)

            if DRY_RUN:
                print(f"  [{i}/{len(duplicate_db_updates)}] WOULD UPDATE: "
                      f"{row['table']}.{row['id_col']} = {row['id_val']}")
            else:
                try:
                    sb.table(row["table"]) \
                        .update({"receipt_url": new_url}) \
                        .eq(row["id_col"], row["id_val"]) \
                        .execute()
                    print(f"  [{i}/{len(duplicate_db_updates)}] OK: "
                          f"{row['table']}.{row['id_col']} = {row['id_val']}")
                    stats["ok"] += 1
                except Exception as e:
                    print(f"  [{i}/{len(duplicate_db_updates)}] ERROR: {str(e)[:100]}")
                    stats["error"] += 1

    # Summary
    print(f"\n{'=' * 60}")
    if DRY_RUN:
        print(f"DRY RUN COMPLETE")
        print(f"  Would migrate: {stats['dry']} files")
        print(f"  Would update: {len(duplicate_db_updates)} additional DB rows")
        print(f"  Already clean: {stats['skip']}")
        print(f"\nRun with --execute to apply changes.")
    else:
        print(f"MIGRATION COMPLETE")
        print(f"  Migrated: {stats['ok']}")
        print(f"  Skipped: {stats['skip']}")
        print(f"  Errors: {stats['error']}")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
