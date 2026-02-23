# Upload local files to vault Receipts folder, skipping duplicates by hash.
#
# Compares SHA-256 of local files against existing vault file hashes.
# Only uploads files that don't already exist in vault.
#
# Usage:
#   .venv/Scripts/python.exe upload_missing_to_vault.py "C:\Users\germa\Desktop\MiCarpeta"
#   .venv/Scripts/python.exe upload_missing_to_vault.py "C:\Users\germa\Desktop\MiCarpeta" --apply

import os
import sys
import hashlib
import re
import time
import mimetypes
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

sb = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_SERVICE_ROLE_KEY"))

PROJECT_ID = "582cfbde-a1d6-411a-bca8-75f29df6f0d6"
PROJECT_NAME = "1519 Arthur Neal Court"
RECEIPTS_FOLDER_ID = "615a599a-b26e-4863-85cb-807c95fd19ff"
DEST_BUCKET = "vault"
DEST_PREFIX = f"Projects/{PROJECT_NAME}/Receipts"

DRY_RUN = "--apply" not in sys.argv

ALLOWED_EXTENSIONS = {
    ".pdf", ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tiff", ".tif",
    ".webp", ".heic", ".doc", ".docx", ".xls", ".xlsx", ".csv", ".txt",
}


def get_admin_user_id():
    r = sb.table("users").select("user_id").limit(1).execute()
    return r.data[0]["user_id"] if r.data else None


def sha256_file(filepath):
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def sanitize_filename(name):
    name = re.sub(r'[#$%&{}\\<>*?/!\'":@+`|=]', "_", name)
    name = re.sub(r"_+", "_", name)
    return name.strip("_")


MAGIC_SIGNATURES = {
    b"\x89PNG": ("image/png", ".png"),
    b"\xff\xd8\xff": ("image/jpeg", ".jpg"),
    b"%PDF": ("application/pdf", ".pdf"),
    b"GIF8": ("image/gif", ".gif"),
    b"BM": ("image/bmp", ".bmp"),
    b"II\x2a\x00": ("image/tiff", ".tif"),
    b"MM\x00\x2a": ("image/tiff", ".tif"),
}


def detect_type_by_magic(filepath):
    """Detect file type by reading magic bytes. Returns (mime, ext) or (None, None)."""
    try:
        with open(filepath, "rb") as f:
            header = f.read(8)
        for sig, (mime, ext) in MAGIC_SIGNATURES.items():
            if header.startswith(sig):
                return mime, ext
    except Exception:
        pass
    return None, None


def get_mime_type(filepath):
    mt, _ = mimetypes.guess_type(filepath)
    if mt:
        return mt
    # Fallback: detect by magic bytes
    magic_mime, _ = detect_type_by_magic(filepath)
    return magic_mime or "application/octet-stream"


def main():
    # Parse folder argument
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        print("Usage: upload_missing_to_vault.py <FOLDER_PATH> [--apply]")
        print("Example: upload_missing_to_vault.py \"C:\\Users\\germa\\Desktop\\Receipts\"")
        sys.exit(1)

    folder_path = args[0]
    if not os.path.isdir(folder_path):
        print(f"ERROR: '{folder_path}' is not a valid directory")
        sys.exit(1)

    if DRY_RUN:
        print("=== DRY RUN (pass --apply to execute) ===\n")
    else:
        print("=== APPLY MODE ===\n")

    admin_uid = get_admin_user_id()
    print(f"Project: {PROJECT_NAME}")
    print(f"Source folder: {folder_path}")
    print(f"Target: Vault > Receipts ({RECEIPTS_FOLDER_ID})\n")

    # 1. Scan local files
    print("1. Scanning local files...")
    local_files = []
    for entry in os.listdir(folder_path):
        filepath = os.path.join(folder_path, entry)
        if not os.path.isfile(filepath):
            continue
        ext = os.path.splitext(entry)[1].lower()
        name = entry
        mime = None

        if ext not in ALLOWED_EXTENSIONS:
            # No recognized extension - try magic bytes detection
            magic_mime, magic_ext = detect_type_by_magic(filepath)
            if magic_mime and magic_ext:
                name = entry + magic_ext
                mime = magic_mime
                print(f"   AUTO-DETECT: {entry} -> {magic_mime} (added {magic_ext})")
            else:
                print(f"   SKIP (unsupported): {entry}")
                continue

        size = os.path.getsize(filepath)
        file_hash = sha256_file(filepath)
        local_files.append({
            "name": name,
            "path": filepath,
            "size": size,
            "hash": file_hash,
            "mime": mime or get_mime_type(filepath),
        })
    print(f"   {len(local_files)} eligible files found")

    if not local_files:
        print("   Nothing to upload!")
        sys.exit(0)

    # 2. Get existing vault hashes
    print("\n2. Loading existing vault file hashes...")
    vf_resp = sb.table("vault_files") \
        .select("file_hash, name") \
        .eq("parent_id", RECEIPTS_FOLDER_ID) \
        .eq("is_folder", False) \
        .eq("is_deleted", False) \
        .execute()
    existing_hashes = {r["file_hash"] for r in (vf_resp.data or []) if r.get("file_hash")}
    existing_names = {r["name"] for r in (vf_resp.data or [])}
    print(f"   {len(existing_hashes)} unique hashes in vault")

    # 3. Compare
    print("\n3. Comparing...")
    new_files = []
    duplicates = []
    for f in local_files:
        if f["hash"] in existing_hashes:
            duplicates.append(f)
        else:
            new_files.append(f)

    print(f"   {len(duplicates)} duplicates (already in vault by hash)")
    print(f"   {len(new_files)} new files to upload")

    if duplicates:
        print("\n   Duplicates skipped:")
        for d in duplicates[:10]:
            print(f"     - {d['name']} ({d['size']/1024:.1f} KB)")
        if len(duplicates) > 10:
            print(f"     ... and {len(duplicates) - 10} more")

    if not new_files:
        print("\n   All files already exist in vault!")
        sys.exit(0)

    # 4. Show new files
    total_size = sum(f["size"] for f in new_files)
    print(f"\n4. Files to upload ({len(new_files)}, {total_size/1024/1024:.2f} MB):")
    for i, f in enumerate(new_files, 1):
        print(f"   {i:3d}. {f['name']} ({f['size']/1024:.1f} KB, {f['mime']})")

    if DRY_RUN:
        print(f"\n=== DRY RUN: Would upload {len(new_files)} files ({total_size/1024/1024:.2f} MB) ===")
        print("Run with --apply to execute.")
        return

    # 5. Upload
    print(f"\n5. Uploading {len(new_files)} files...")
    success = 0
    errors = 0

    for i, f in enumerate(new_files, 1):
        try:
            with open(f["path"], "rb") as fh:
                file_data = fh.read()

            # Build destination path
            base, ext = os.path.splitext(f["name"])
            safe_name = sanitize_filename(base)
            # Avoid name collision
            dest_filename = f"{safe_name}_v1{ext}"
            if dest_filename in existing_names:
                dest_filename = f"{safe_name}_{int(time.time())}_v1{ext}"
            dest_path = f"{DEST_PREFIX}/{dest_filename}"

            # Upload to vault bucket
            sb.storage.from_(DEST_BUCKET).upload(
                dest_path,
                file_data,
                {"content-type": f["mime"]},
            )

            # Create vault_files record
            record = {
                "name": f["name"],
                "is_folder": False,
                "parent_id": RECEIPTS_FOLDER_ID,
                "project_id": PROJECT_ID,
                "bucket_path": dest_path,
                "mime_type": f["mime"],
                "size_bytes": f["size"],
                "file_hash": f["hash"],
                "uploaded_by": admin_uid,
                "is_deleted": False,
            }
            sb.table("vault_files").insert(record).execute()

            existing_hashes.add(f["hash"])
            existing_names.add(dest_filename)
            print(f"   [{i}/{len(new_files)}] OK: {f['name']}")
            success += 1

            if i % 10 == 0:
                time.sleep(0.5)

        except Exception as e:
            err = str(e)
            if "Duplicate" in err or "already exists" in err.lower():
                print(f"   [{i}/{len(new_files)}] SKIP (storage conflict): {f['name']}")
            else:
                print(f"   [{i}/{len(new_files)}] ERROR: {f['name']} -> {err[:120]}")
            errors += 1

    print(f"\nDone! Uploaded: {success}, Errors/Skipped: {errors}")


if __name__ == "__main__":
    main()
