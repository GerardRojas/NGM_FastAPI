import os
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

sb = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_SERVICE_ROLE_KEY"))
project_id = "582cfbde-a1d6-411a-bca8-75f29df6f0d6"

# Check existing vault files to see bucket_path format
print("=== Existing vault_files with bucket_path ===")
r = sb.table("vault_files") \
    .select("id, name, bucket_path, project_id, parent_id, mime_type, size_bytes, file_hash") \
    .eq("is_folder", False) \
    .eq("is_deleted", False) \
    .limit(10) \
    .execute()
for f in (r.data or []):
    print(f"  name: {f['name']}")
    print(f"  bucket_path: {f.get('bucket_path')}")
    print(f"  project_id: {f.get('project_id')}")
    print(f"  parent_id: {f.get('parent_id')}")
    print(f"  mime_type: {f.get('mime_type')}")
    print(f"  size_bytes: {f.get('size_bytes')}")
    print(f"  file_hash: {f.get('file_hash')}")
    print()

# List vault storage for this project
print("=== vault bucket: Projects/1519 Arthur Neal Court/ ===")
try:
    files = sb.storage.from_("vault").list("Projects/1519 Arthur Neal Court", {"limit": 30})
    for f in (files or []):
        name = f.get("name", "?")
        meta = f.get("metadata") or {}
        size = meta.get("size", "folder?")
        print(f"  {name} (size: {size})")
    print(f"Total: {len(files or [])}")
except Exception as ex:
    print(f"  Error: {ex}")

print("\n=== vault bucket: Projects/1519 Arthur Neal Court/Receipts/ ===")
try:
    files = sb.storage.from_("vault").list("Projects/1519 Arthur Neal Court/Receipts", {"limit": 30})
    for f in (files or []):
        name = f.get("name", "?")
        meta = f.get("metadata") or {}
        size = meta.get("size", "folder?")
        print(f"  {name} (size: {size})")
    print(f"Total: {len(files or [])}")
except Exception as ex:
    print(f"  Error: {ex}")

print("\n=== vault bucket: Projects/1519 Arthur Neal Court/Reports/ ===")
try:
    files = sb.storage.from_("vault").list("Projects/1519 Arthur Neal Court/Reports", {"limit": 30})
    for f in (files or []):
        name = f.get("name", "?")
        meta = f.get("metadata") or {}
        size = meta.get("size", "folder?")
        print(f"  {name} (size: {size})")
    print(f"Total: {len(files or [])}")
except Exception as ex:
    print(f"  Error: {ex}")

# List the 50 files in expenses-receipts for this project (full details)
print(f"\n=== expenses-receipts/{project_id}/ (all files) ===")
files = sb.storage.from_("expenses-receipts").list(project_id, {"limit": 100})
total_size = 0
for f in (files or []):
    name = f.get("name", "?")
    meta = f.get("metadata") or {}
    size = meta.get("size", 0)
    mime = meta.get("mimetype", "?")
    total_size += size if isinstance(size, int) else 0
    # Check if it's a subfolder
    if f.get("id") is None:
        print(f"  [FOLDER] {name}")
        # List subfolder
        subfiles = sb.storage.from_("expenses-receipts").list(f"{project_id}/{name}", {"limit": 50})
        for sf in (subfiles or []):
            sname = sf.get("name", "?")
            smeta = sf.get("metadata") or {}
            ssize = smeta.get("size", 0)
            smime = smeta.get("mimetype", "?")
            total_size += ssize if isinstance(ssize, int) else 0
            print(f"    {sname} ({smime}, {ssize} bytes)")
    else:
        print(f"  {name} ({mime}, {size} bytes)")
print(f"Total files: {len(files or [])}, Total size: {total_size/1024:.1f} KB")
