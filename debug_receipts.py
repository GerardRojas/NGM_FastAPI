import os
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

sb = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_SERVICE_ROLE_KEY"))
project_id = "582cfbde-a1d6-411a-bca8-75f29df6f0d6"

# List all expenses for this project with receipt URLs
print("=== ALL expenses for 1519 Arthur Neal Court ===")
r = sb.table("expenses_manual_COGS") \
    .select("expense_id, Amount, receipt_url, LineDescription, vendor_id, bill_id, created_at") \
    .eq("project", project_id) \
    .order("created_at", desc=True) \
    .execute()
for e in (r.data or []):
    receipt = e.get("receipt_url") or "NO RECEIPT"
    desc = (e.get("LineDescription") or "N/A")[:40]
    eid = e["expense_id"][:8]
    print(f"  {eid}.. ${e.get('Amount',0)} | {desc} | ...{receipt[-70:]}")
print(f"Total: {len(r.data or [])}")

# List root of expenses-receipts bucket (general area)
print("\n=== expenses-receipts bucket: ROOT (general) ===")
files = sb.storage.from_("expenses-receipts").list("", {"limit": 50})
for f in (files or []):
    name = f.get("name", "?")
    fid = f.get("id", "?")
    meta = f.get("metadata") or {}
    size = meta.get("size", "folder?")
    print(f"  {name} (id: {fid}, size: {size})")
print(f"Total: {len(files or [])}")

# List project subfolder in expenses-receipts
print(f"\n=== expenses-receipts bucket: {project_id}/ ===")
files2 = sb.storage.from_("expenses-receipts").list(project_id, {"limit": 50})
for f in (files2 or []):
    name = f.get("name", "?")
    meta = f.get("metadata") or {}
    size = meta.get("size", "folder?")
    print(f"  {name} (size: {size})")
print(f"Total: {len(files2 or [])}")

# List vault bucket root
print("\n=== vault bucket: ROOT ===")
files3 = sb.storage.from_("vault").list("", {"limit": 30})
for f in (files3 or []):
    name = f.get("name", "?")
    meta = f.get("metadata") or {}
    size = meta.get("size", "folder?")
    print(f"  {name} (size: {size})")
print(f"Total: {len(files3 or [])}")

# List vault bucket - Projects folder
print("\n=== vault bucket: Projects/ ===")
try:
    files4 = sb.storage.from_("vault").list("Projects", {"limit": 30})
    for f in (files4 or []):
        name = f.get("name", "?")
        meta = f.get("metadata") or {}
        size = meta.get("size", "folder?")
        print(f"  {name} (size: {size})")
    print(f"Total: {len(files4 or [])}")
except Exception as e:
    print(f"  Error: {e}")

# List vault bucket - Global folder
print("\n=== vault bucket: Global/ ===")
try:
    files5 = sb.storage.from_("vault").list("Global", {"limit": 30})
    for f in (files5 or []):
        name = f.get("name", "?")
        meta = f.get("metadata") or {}
        size = meta.get("size", "folder?")
        print(f"  {name} (size: {size})")
    print(f"Total: {len(files5 or [])}")
except Exception as e:
    print(f"  Error: {e}")

# List pending-expenses bucket
print("\n=== pending-expenses bucket: ROOT ===")
files6 = sb.storage.from_("pending-expenses").list("", {"limit": 50})
for f in (files6 or []):
    name = f.get("name", "?")
    meta = f.get("metadata") or {}
    size = meta.get("size", "folder?")
    print(f"  {name} (size: {size})")
print(f"Total: {len(files6 or [])}")

# Check if there's a general/common folder in pending-expenses
print(f"\n=== pending-expenses bucket: {project_id}/ ===")
try:
    files7 = sb.storage.from_("pending-expenses").list(project_id, {"limit": 50})
    for f in (files7 or []):
        name = f.get("name", "?")
        meta = f.get("metadata") or {}
        size = meta.get("size", "folder?")
        print(f"  {name} (size: {size})")
    print(f"Total: {len(files7 or [])}")
except Exception as e:
    print(f"  Error: {e}")
