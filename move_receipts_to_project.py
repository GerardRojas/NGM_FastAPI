# One-time script: Move vault receipts linked to "1519 Arthur Neal Court"
# from the global vault folder to the project's Receipts folder.
#
# Usage:
#   cd NGM_API
#   python move_receipts_to_project.py          # dry-run (preview only)
#   python move_receipts_to_project.py --apply  # actually move files

import os
import sys
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
    print("ERROR: Missing SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY in .env")
    sys.exit(1)

supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)

PROJECT_NAME = "1519 Arthur Neal Court"
DRY_RUN = "--apply" not in sys.argv


def main():
    if DRY_RUN:
        print("=== DRY RUN MODE (pass --apply to execute) ===\n")
    else:
        print("=== APPLY MODE - Changes will be written to DB ===\n")

    # 1. Find the project
    print(f"1. Looking for project: '{PROJECT_NAME}'...")
    resp = supabase.table("projects") \
        .select("project_id, project_name") \
        .ilike("project_name", f"%{PROJECT_NAME}%") \
        .execute()

    if not resp.data:
        print(f"   ERROR: Project '{PROJECT_NAME}' not found")
        sys.exit(1)

    project = resp.data[0]
    project_id = project["project_id"]
    print(f"   Found: {project['project_name']} (ID: {project_id})")

    # 2. Find the project's "Receipts" folder in vault
    print("\n2. Looking for 'Receipts' folder in project vault...")
    resp = supabase.table("vault_files") \
        .select("id, name") \
        .eq("project_id", project_id) \
        .eq("is_folder", True) \
        .eq("is_deleted", False) \
        .ilike("name", "Receipts") \
        .execute()

    if not resp.data:
        print("   ERROR: No 'Receipts' folder found for this project in vault")
        print("   You may need to create it first via the Vault UI")
        sys.exit(1)

    receipts_folder = resp.data[0]
    receipts_folder_id = receipts_folder["id"]
    print(f"   Found: '{receipts_folder['name']}' folder (ID: {receipts_folder_id})")

    # 3. Find pending_receipts linked to this project that have vault_file_ids
    print("\n3. Finding pending_receipts linked to this project with vault files...")
    resp = supabase.table("pending_receipts") \
        .select("id, file_name, vault_file_id, status, file_hash") \
        .eq("project_id", project_id) \
        .not_.is_("vault_file_id", "null") \
        .execute()

    if not resp.data:
        print("   No pending_receipts with vault_file_id found for this project")
        # Fall through to also check vault files directly
        pending_vault_ids = set()
    else:
        pending_vault_ids = {r["vault_file_id"] for r in resp.data}
        print(f"   Found {len(resp.data)} pending_receipts with vault links:")
        for r in resp.data:
            print(f"     - {r['file_name']} (status: {r['status']}, vault_id: {r['vault_file_id']})")

    # 4. Also check vault_files in global area that might match by file_hash
    print("\n4. Checking for vault files in global area (project_id IS NULL)...")

    # Get file hashes from pending_receipts for this project
    resp_hashes = supabase.table("pending_receipts") \
        .select("file_hash") \
        .eq("project_id", project_id) \
        .not_.is_("file_hash", "null") \
        .execute()

    project_hashes = {r["file_hash"] for r in resp_hashes.data} if resp_hashes.data else set()

    # Find global vault files (no project_id) that are NOT folders
    resp_global = supabase.table("vault_files") \
        .select("id, name, parent_id, project_id, file_hash, mime_type, size_bytes") \
        .is_("project_id", "null") \
        .eq("is_folder", False) \
        .eq("is_deleted", False) \
        .execute()

    if not resp_global.data:
        print("   No files found in global vault")
    else:
        print(f"   Found {len(resp_global.data)} total files in global vault")

    # Identify files to move:
    # a) Files whose ID is in pending_vault_ids
    # b) Files whose hash matches project hashes
    files_to_move = []
    seen_ids = set()

    if resp_global.data:
        for f in resp_global.data:
            should_move = False
            reason = ""

            if f["id"] in pending_vault_ids:
                should_move = True
                reason = "vault_file_id match in pending_receipts"
            elif f.get("file_hash") and f["file_hash"] in project_hashes:
                should_move = True
                reason = "file_hash match with project receipts"

            if should_move and f["id"] not in seen_ids:
                files_to_move.append({**f, "reason": reason})
                seen_ids.add(f["id"])

    # Also check: vault files that ARE in vault_file_id set but might already have a project_id
    if pending_vault_ids:
        resp_linked = supabase.table("vault_files") \
            .select("id, name, parent_id, project_id, file_hash, mime_type, size_bytes") \
            .in_("id", list(pending_vault_ids)) \
            .eq("is_deleted", False) \
            .execute()

        if resp_linked.data:
            for f in resp_linked.data:
                if f["id"] not in seen_ids:
                    is_global = f.get("project_id") is None
                    if is_global:
                        files_to_move.append({**f, "reason": "vault_file_id in pending_receipts (direct lookup)"})
                        seen_ids.add(f["id"])
                    else:
                        print(f"   SKIP (already in project): {f['name']} (project_id: {f['project_id']})")

    if not files_to_move:
        print("\n   No files to move! All receipts are already in the correct location.")
        sys.exit(0)

    # 5. Show summary and move
    print(f"\n5. Files to move to project Receipts folder ({len(files_to_move)}):")
    print(f"   Target: '{receipts_folder['name']}' (ID: {receipts_folder_id})")
    print(f"   Project: {project['project_name']} (ID: {project_id})")
    print()

    for i, f in enumerate(files_to_move, 1):
        size_kb = (f.get("size_bytes") or 0) / 1024
        print(f"   {i}. {f['name']}")
        print(f"      Type: {f.get('mime_type', 'unknown')} | Size: {size_kb:.1f} KB")
        print(f"      Reason: {f['reason']}")
        print(f"      Current parent_id: {f.get('parent_id')} | project_id: {f.get('project_id')}")

    if DRY_RUN:
        print(f"\n=== DRY RUN: Would move {len(files_to_move)} files ===")
        print("Run with --apply to execute the move.")
        return

    # Execute the move
    print(f"\n6. Moving {len(files_to_move)} files...")
    success = 0
    errors = 0

    for f in files_to_move:
        try:
            resp = supabase.table("vault_files") \
                .update({
                    "parent_id": receipts_folder_id,
                    "project_id": project_id
                }) \
                .eq("id", f["id"]) \
                .execute()

            if resp.data:
                print(f"   MOVED: {f['name']}")
                success += 1
            else:
                print(f"   WARN: No data returned for {f['name']} (may have been deleted)")
                errors += 1
        except Exception as e:
            print(f"   ERROR moving {f['name']}: {e}")
            errors += 1

    print(f"\nDone! Moved: {success}, Errors: {errors}")


def debug():
    """Debug: show all vault data related to this project."""
    print("=== DEBUG: Investigating vault structure ===\n")

    # Find project
    resp = supabase.table("projects") \
        .select("project_id, project_name") \
        .ilike("project_name", f"%{PROJECT_NAME}%") \
        .execute()
    if not resp.data:
        print("Project not found")
        return
    project_id = resp.data[0]["project_id"]
    print(f"Project: {resp.data[0]['project_name']} ({project_id})\n")

    # All vault folders for this project
    print("--- Project vault folders ---")
    resp = supabase.table("vault_files") \
        .select("id, name, parent_id, is_folder") \
        .eq("project_id", project_id) \
        .eq("is_folder", True) \
        .eq("is_deleted", False) \
        .execute()
    for f in (resp.data or []):
        print(f"  Folder: {f['name']} (id: {f['id']}, parent: {f['parent_id']})")

    # All vault files for this project
    print("\n--- Project vault files ---")
    resp = supabase.table("vault_files") \
        .select("id, name, parent_id, is_folder, mime_type, size_bytes") \
        .eq("project_id", project_id) \
        .eq("is_folder", False) \
        .eq("is_deleted", False) \
        .execute()
    for f in (resp.data or []):
        size_kb = (f.get("size_bytes") or 0) / 1024
        print(f"  File: {f['name']} ({size_kb:.1f} KB, parent: {f['parent_id']})")
    print(f"  Total: {len(resp.data or [])} files")

    # All global vault folders (project_id IS NULL)
    print("\n--- Global vault folders ---")
    resp = supabase.table("vault_files") \
        .select("id, name, parent_id, is_folder") \
        .is_("project_id", "null") \
        .eq("is_folder", True) \
        .eq("is_deleted", False) \
        .execute()
    for f in (resp.data or []):
        print(f"  Folder: {f['name']} (id: {f['id']}, parent: {f['parent_id']})")

    # All global vault files
    print("\n--- Global vault files ---")
    resp = supabase.table("vault_files") \
        .select("id, name, parent_id, is_folder, mime_type, size_bytes") \
        .is_("project_id", "null") \
        .eq("is_folder", False) \
        .eq("is_deleted", False) \
        .execute()
    for f in (resp.data or []):
        size_kb = (f.get("size_bytes") or 0) / 1024
        print(f"  File: {f['name']} ({size_kb:.1f} KB, parent: {f['parent_id']})")
    print(f"  Total: {len(resp.data or [])} files")

    # Pending receipts for this project
    print("\n--- Pending receipts for this project ---")
    resp = supabase.table("pending_receipts") \
        .select("id, file_name, status, vault_file_id, file_hash, file_url") \
        .eq("project_id", project_id) \
        .execute()
    for r in (resp.data or []):
        print(f"  Receipt: {r['file_name']} (status: {r['status']}, vault_id: {r.get('vault_file_id')}, hash: {r.get('file_hash', 'N/A')[:16]}...)")
    print(f"  Total: {len(resp.data or [])} receipts")

    # Check ALL vault files (any project)
    print("\n--- ALL vault files (across all projects) ---")
    resp = supabase.table("vault_files") \
        .select("id, name, parent_id, project_id, is_folder, mime_type, size_bytes") \
        .eq("is_folder", False) \
        .eq("is_deleted", False) \
        .limit(50) \
        .execute()
    for f in (resp.data or []):
        size_kb = (f.get("size_bytes") or 0) / 1024
        print(f"  File: {f['name']} ({size_kb:.1f} KB, project: {f.get('project_id', 'GLOBAL')}, parent: {f['parent_id']})")
    print(f"  Total shown: {len(resp.data or [])} files")

    # Check ALL vault folders
    print("\n--- ALL vault folders ---")
    resp = supabase.table("vault_files") \
        .select("id, name, parent_id, project_id, is_folder") \
        .eq("is_folder", True) \
        .eq("is_deleted", False) \
        .execute()
    for f in (resp.data or []):
        proj_label = f.get("project_id") or "GLOBAL"
        print(f"  Folder: {f['name']} (id: {f['id']}, project: {proj_label}, parent: {f['parent_id']})")

    # Check expenses for this project
    print("\n--- Expenses for this project (with receipt_url) ---")
    resp = supabase.table("expenses_manual_COGS") \
        .select("expense_id, description, amount, receipt_url, category") \
        .eq("project_id", project_id) \
        .not_.is_("receipt_url", "null") \
        .limit(30) \
        .execute()
    for e in (resp.data or []):
        url = (e.get("receipt_url") or "N/A")
        print(f"  Expense: {e.get('description', 'N/A')} ${e.get('amount', 0)} | cat: {e.get('category', 'N/A')}")
        print(f"    URL: {url}")
    print(f"  Total with receipts: {len(resp.data or [])}")

    # Check bills for this project
    print("\n--- Bills for this project ---")
    resp = supabase.table("bills") \
        .select("bill_id, total_amount, receipt_url, status") \
        .eq("project_id", project_id) \
        .limit(30) \
        .execute()
    for b in (resp.data or []):
        url = (b.get("receipt_url") or "N/A")
        print(f"  Bill: ${b.get('total_amount', 0)} (status: {b.get('status')}) | url: {url}")
    print(f"  Total: {len(resp.data or [])} bills")

    # List Supabase Storage buckets
    print("\n--- Supabase Storage: listing available buckets ---")
    try:
        buckets = supabase.storage.list_buckets()
        for b in (buckets or []):
            print(f"  Bucket: {b.name} (id: {b.id}, public: {b.public})")
    except Exception as e:
        print(f"  Error listing buckets: {e}")

    # List root of receipts bucket
    print("\n--- Supabase Storage: 'receipts' bucket root ---")
    try:
        files = supabase.storage.from_("receipts").list("", {"limit": 50})
        for f in (files or []):
            print(f"  {f.get('name', '?')} (id: {f.get('id', '?')})")
        print(f"  Total in root: {len(files or [])}")
    except Exception as e:
        print(f"  Error: {e}")


if __name__ == "__main__":
    if "--debug" in sys.argv:
        debug()
    else:
        main()
