# One-time script: Create pending_receipts entries linking vault file hashes
# to bills for "1519 Arthur Neal Court" so receipt badges appear in vault.
#
# Logic: vault file name == filename part of bill.receipt_url
# Then create pending_receipts with file_hash + receipt_url so the
# receipt-status chain works: vault hash -> pending_receipts -> bills -> expenses
#
# Usage:
#   .venv/Scripts/python.exe link_vault_to_bills.py          # dry-run
#   .venv/Scripts/python.exe link_vault_to_bills.py --apply   # execute

import os
import sys
from urllib.parse import unquote
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

sb = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_SERVICE_ROLE_KEY"))

PROJECT_ID = "582cfbde-a1d6-411a-bca8-75f29df6f0d6"
RECEIPTS_FOLDER_ID = "615a599a-b26e-4863-85cb-807c95fd19ff"
DRY_RUN = "--apply" not in sys.argv


def get_admin_user_id():
    r = sb.table("users").select("user_id").limit(1).execute()
    return r.data[0]["user_id"] if r.data else None


def extract_filename(url):
    """Extract the filename from a Supabase storage URL."""
    if not url:
        return None
    # URL: https://...supabase.co/storage/v1/object/public/expenses-receipts/{project_id}/{filename}
    parts = unquote(url).split("/")
    # The filename is the last part
    return parts[-1] if parts else None


def main():
    if DRY_RUN:
        print("=== DRY RUN (pass --apply to execute) ===\n")
    else:
        print("=== APPLY MODE ===\n")

    admin_uid = get_admin_user_id()
    print(f"Admin user: {admin_uid}\n")

    # 1. Get all vault files in Receipts folder (with file_hash)
    print("1. Loading vault files from Receipts folder...")
    vf_resp = sb.table("vault_files") \
        .select("id, name, file_hash, mime_type, size_bytes") \
        .eq("parent_id", RECEIPTS_FOLDER_ID) \
        .eq("is_folder", False) \
        .eq("is_deleted", False) \
        .execute()
    vault_files = vf_resp.data or []
    print(f"   {len(vault_files)} vault files")

    # Build map: filename -> vault file
    vault_by_name = {}
    for vf in vault_files:
        vault_by_name[vf["name"]] = vf

    # 2. Get all bills whose receipt_url contains this project_id
    print("\n2. Loading bills with receipt_url matching this project...")
    bills_resp = sb.table("bills") \
        .select("bill_id, receipt_url, status, expected_total") \
        .like("receipt_url", f"%{PROJECT_ID}%") \
        .execute()
    bills = bills_resp.data or []
    print(f"   {len(bills)} bills with receipt_url for this project")

    # 3. Match vault files to bills by filename
    print("\n3. Matching vault files to bills...")
    matches = []
    unmatched_bills = []
    matched_vault_names = set()

    for bill in bills:
        bill_filename = extract_filename(bill["receipt_url"])
        if not bill_filename:
            continue

        vf = vault_by_name.get(bill_filename)
        if vf and vf.get("file_hash"):
            matches.append({
                "vault_file": vf,
                "bill": bill,
                "bill_filename": bill_filename,
            })
            matched_vault_names.add(bill_filename)
        else:
            unmatched_bills.append({"bill": bill, "filename": bill_filename})

    print(f"   {len(matches)} matches found")
    print(f"   {len(unmatched_bills)} bills without vault match")

    # 4. Also check expenses with receipt_url (direct attachment, no bill)
    print("\n4. Checking expenses with direct receipt_url...")
    exp_resp = sb.table("expenses_manual_COGS") \
        .select("expense_id, receipt_url, bill_id, auth_status, status") \
        .eq("project", PROJECT_ID) \
        .not_.is_("receipt_url", "null") \
        .execute()
    direct_expenses = [e for e in (exp_resp.data or []) if not e.get("bill_id")]
    billed_expenses = [e for e in (exp_resp.data or []) if e.get("bill_id")]
    print(f"   {len(billed_expenses)} expenses via bills, {len(direct_expenses)} direct")

    # Match direct expenses to vault files
    direct_matches = []
    for exp in direct_expenses:
        exp_filename = extract_filename(exp["receipt_url"])
        if exp_filename and exp_filename in vault_by_name and exp_filename not in matched_vault_names:
            vf = vault_by_name[exp_filename]
            if vf.get("file_hash"):
                direct_matches.append({
                    "vault_file": vf,
                    "expense": exp,
                    "exp_filename": exp_filename,
                })
                matched_vault_names.add(exp_filename)
    print(f"   {len(direct_matches)} direct expense matches")

    # 5. Check existing pending_receipts to avoid duplicates
    print("\n5. Checking existing pending_receipts...")
    existing_hashes = set()
    if matches or direct_matches:
        all_hashes = [m["vault_file"]["file_hash"] for m in matches] + \
                     [m["vault_file"]["file_hash"] for m in direct_matches]
        if all_hashes:
            pr_resp = sb.table("pending_receipts") \
                .select("file_hash") \
                .eq("project_id", PROJECT_ID) \
                .in_("file_hash", all_hashes) \
                .execute()
            existing_hashes = {r["file_hash"] for r in (pr_resp.data or [])}
    print(f"   {len(existing_hashes)} already exist")

    # Filter out existing
    new_bill_matches = [m for m in matches if m["vault_file"]["file_hash"] not in existing_hashes]
    new_direct_matches = [m for m in direct_matches if m["vault_file"]["file_hash"] not in existing_hashes]
    skipped = (len(matches) + len(direct_matches)) - (len(new_bill_matches) + len(new_direct_matches))
    if skipped:
        print(f"   Skipping {skipped} (already have pending_receipts)")

    # 6. Show what will be created
    total_new = len(new_bill_matches) + len(new_direct_matches)
    print(f"\n6. Pending receipts to create: {total_new}")

    if new_bill_matches:
        print(f"\n   --- Via Bills ({len(new_bill_matches)}) ---")
        for i, m in enumerate(new_bill_matches[:20], 1):
            vf = m["vault_file"]
            b = m["bill"]
            print(f"   {i:3d}. {vf['name']}")
            print(f"        hash: {vf['file_hash'][:24]}... -> bill ${b.get('expected_total',0)} ({b['status']})")

        if len(new_bill_matches) > 20:
            print(f"   ... and {len(new_bill_matches) - 20} more")

    if new_direct_matches:
        print(f"\n   --- Via Direct Expenses ({len(new_direct_matches)}) ---")
        for i, m in enumerate(new_direct_matches[:10], 1):
            vf = m["vault_file"]
            exp = m["expense"]
            auth = exp.get("auth_status") or exp.get("status")
            print(f"   {i:3d}. {vf['name']} -> expense auth={auth}")

    unmatched_vault = [n for n in vault_by_name if n not in matched_vault_names]
    if unmatched_vault:
        print(f"\n   {len(unmatched_vault)} vault files with no bill/expense match (no badge)")

    if total_new == 0:
        print("\n   Nothing to create!")
        return

    if DRY_RUN:
        print(f"\n=== DRY RUN: Would create {total_new} pending_receipts ===")
        print("Run with --apply to execute.")
        return

    # 7. Create pending_receipts
    print(f"\n7. Creating {total_new} pending_receipts...")
    success = 0
    errors = 0

    for m in new_bill_matches:
        vf = m["vault_file"]
        b = m["bill"]
        try:
            record = {
                "project_id": PROJECT_ID,
                "file_name": vf["name"],
                "file_url": b["receipt_url"],
                "file_type": vf.get("mime_type", "application/octet-stream"),
                "file_size": vf.get("size_bytes"),
                "status": "linked",
                "file_hash": vf["file_hash"],
                "vault_file_id": vf["id"],
                "uploaded_by": admin_uid,
                "file_url": b["receipt_url"],
            }
            sb.table("pending_receipts").insert(record).execute()
            print(f"   OK: {vf['name']}")
            success += 1
        except Exception as e:
            print(f"   ERROR: {vf['name']} -> {str(e)[:100]}")
            errors += 1

    for m in new_direct_matches:
        vf = m["vault_file"]
        exp = m["expense"]
        try:
            record = {
                "project_id": PROJECT_ID,
                "file_name": vf["name"],
                "file_url": exp["receipt_url"],
                "file_type": vf.get("mime_type", "application/octet-stream"),
                "file_size": vf.get("size_bytes"),
                "status": "linked",
                "file_hash": vf["file_hash"],
                "vault_file_id": vf["id"],
                "uploaded_by": admin_uid,
                "file_url": exp["receipt_url"],
            }
            sb.table("pending_receipts").insert(record).execute()
            print(f"   OK: {vf['name']} (direct)")
            success += 1
        except Exception as e:
            print(f"   ERROR: {vf['name']} -> {str(e)[:100]}")
            errors += 1

    print(f"\nDone! Created: {success}, Errors: {errors}")


if __name__ == "__main__":
    main()
