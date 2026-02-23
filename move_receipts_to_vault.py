# One-time script: Copy receipt files from expenses-receipts bucket
# to the Vault Receipts folder for "1519 Arthur Neal Court"
#
# Usage:
#   cd NGM_API
#   .venv/Scripts/python.exe move_receipts_to_vault.py          # dry-run
#   .venv/Scripts/python.exe move_receipts_to_vault.py --apply  # execute

import os
import sys
import hashlib
import re
import time
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
    print("ERROR: Missing SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY")
    sys.exit(1)

sb = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)

PROJECT_ID = "582cfbde-a1d6-411a-bca8-75f29df6f0d6"
PROJECT_NAME = "1519 Arthur Neal Court"
RECEIPTS_FOLDER_ID = "615a599a-b26e-4863-85cb-807c95fd19ff"
SOURCE_BUCKET = "expenses-receipts"
DEST_BUCKET = "vault"
DEST_PREFIX = f"Projects/{PROJECT_NAME}/Receipts"

DRY_RUN = "--apply" not in sys.argv


def sanitize_filename(name):
    """Remove/replace characters that cause issues in storage paths."""
    # Replace special chars but keep dots, dashes, underscores
    name = re.sub(r'[#$%&{}\\<>*?/!\'":@+`|=]', "_", name)
    # Collapse multiple underscores
    name = re.sub(r"_+", "_", name)
    return name.strip("_")


def get_admin_user_id():
    """Get a valid admin user ID for the uploaded_by field."""
    r = sb.table("users").select("user_id").limit(1).execute()
    if r.data:
        return r.data[0]["user_id"]
    return None


def list_source_files():
    """List all files in the expenses-receipts bucket for this project."""
    files = []
    items = sb.storage.from_(SOURCE_BUCKET).list(PROJECT_ID, {"limit": 200})
    for item in (items or []):
        meta = item.get("metadata") or {}
        if meta.get("size") is not None and meta.get("size") > 0:
            # It's a file (not a folder)
            files.append({
                "name": item["name"],
                "size": meta.get("size", 0),
                "mimetype": meta.get("mimetype", "application/octet-stream"),
                "source_path": f"{PROJECT_ID}/{item['name']}",
            })
        elif item.get("id") is None:
            # It's a subfolder - list its contents too
            subfolder = item["name"]
            subitems = sb.storage.from_(SOURCE_BUCKET).list(
                f"{PROJECT_ID}/{subfolder}", {"limit": 200}
            )
            for si in (subitems or []):
                smeta = si.get("metadata") or {}
                if smeta.get("size") is not None and smeta.get("size") > 0:
                    files.append({
                        "name": f"{subfolder}_{si['name']}",
                        "size": smeta.get("size", 0),
                        "mimetype": smeta.get("mimetype", "application/octet-stream"),
                        "source_path": f"{PROJECT_ID}/{subfolder}/{si['name']}",
                    })
    return files


def main():
    if DRY_RUN:
        print("=== DRY RUN MODE (pass --apply to execute) ===\n")
    else:
        print("=== APPLY MODE - Files will be copied and records created ===\n")

    # Get admin user for uploaded_by
    admin_uid = get_admin_user_id()
    if not admin_uid:
        print("ERROR: No users found in database")
        sys.exit(1)
    print(f"Using admin user: {admin_uid}")

    # List source files
    print(f"\n1. Listing files in {SOURCE_BUCKET}/{PROJECT_ID}/...")
    source_files = list_source_files()
    print(f"   Found {len(source_files)} files")

    if not source_files:
        print("   Nothing to move!")
        sys.exit(0)

    # Check what already exists in vault Receipts folder
    print(f"\n2. Checking existing vault_files in Receipts folder...")
    existing = sb.table("vault_files") \
        .select("name") \
        .eq("parent_id", RECEIPTS_FOLDER_ID) \
        .eq("is_deleted", False) \
        .execute()
    existing_names = {r["name"] for r in (existing.data or [])}
    print(f"   {len(existing_names)} files already in vault Receipts")

    # Filter out duplicates
    new_files = [f for f in source_files if f["name"] not in existing_names]
    skipped = len(source_files) - len(new_files)
    if skipped:
        print(f"   Skipping {skipped} files already in vault")

    print(f"\n3. Files to copy ({len(new_files)}):")
    total_size = 0
    for i, f in enumerate(new_files, 1):
        size_kb = f["size"] / 1024
        total_size += f["size"]
        print(f"   {i:3d}. {f['name']} ({size_kb:.1f} KB, {f['mimetype']})")
    print(f"\n   Total size: {total_size/1024/1024:.2f} MB")

    if DRY_RUN:
        print(f"\n=== DRY RUN: Would copy {len(new_files)} files ({total_size/1024/1024:.2f} MB) ===")
        print("Run with --apply to execute.")
        return

    # Execute the copy
    print(f"\n4. Copying {len(new_files)} files...")
    success = 0
    errors = 0

    for i, f in enumerate(new_files, 1):
        try:
            # Download from source bucket
            file_data = sb.storage.from_(SOURCE_BUCKET).download(f["source_path"])

            if not file_data:
                print(f"   [{i}/{len(new_files)}] SKIP (empty): {f['name']}")
                errors += 1
                continue

            # Compute file hash
            file_hash = hashlib.sha256(file_data).hexdigest()

            # Determine dest filename with _v1 suffix
            base, ext = os.path.splitext(f["name"])
            safe_name = sanitize_filename(base)
            dest_filename = f"{safe_name}_v1{ext}"
            dest_path = f"{DEST_PREFIX}/{dest_filename}"

            # Upload to vault bucket
            sb.storage.from_(DEST_BUCKET).upload(
                dest_path,
                file_data,
                {"content-type": f["mimetype"]},
            )

            # Create vault_files record
            record = {
                "name": f["name"],
                "is_folder": False,
                "parent_id": RECEIPTS_FOLDER_ID,
                "project_id": PROJECT_ID,
                "bucket_path": dest_path,
                "mime_type": f["mimetype"],
                "size_bytes": f["size"],
                "file_hash": file_hash,
                "uploaded_by": admin_uid,
                "is_deleted": False,
            }
            sb.table("vault_files").insert(record).execute()

            print(f"   [{i}/{len(new_files)}] OK: {f['name']}")
            success += 1

            # Small delay to avoid rate limits
            if i % 10 == 0:
                time.sleep(0.5)

        except Exception as e:
            err_msg = str(e)
            if "Duplicate" in err_msg or "already exists" in err_msg.lower():
                print(f"   [{i}/{len(new_files)}] SKIP (already in storage): {f['name']}")
            else:
                print(f"   [{i}/{len(new_files)}] ERROR: {f['name']} -> {err_msg[:100]}")
            errors += 1

    print(f"\nDone! Copied: {success}, Errors/Skipped: {errors}")


if __name__ == "__main__":
    main()
