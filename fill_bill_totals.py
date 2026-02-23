# Fill bills.expected_total by summing expenses linked to each bill.
#
# For bills with $0 expected_total, calculate the sum of all expenses
# that reference that bill_id. Cross-validate with filename hints from vault.
#
# Usage:
#   .venv/Scripts/python.exe fill_bill_totals.py                  # dry-run
#   .venv/Scripts/python.exe fill_bill_totals.py --apply          # execute

import os
import re
import sys
from dotenv import load_dotenv
from supabase import create_client

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from services.receipt_regex import extract_filename_hints

load_dotenv()

sb = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_SERVICE_ROLE_KEY"))

PROJECT_ID = "582cfbde-a1d6-411a-bca8-75f29df6f0d6"
RECEIPTS_FOLDER_ID = "615a599a-b26e-4863-85cb-807c95fd19ff"
DRY_RUN = "--apply" not in sys.argv


def main():
    if DRY_RUN:
        print("=== DRY RUN (pass --apply to execute) ===\n")
    else:
        print("=== APPLY MODE ===\n")

    # 1. Get all expenses for this project
    print("1. Loading expenses for project...")
    exp_resp = sb.table("expenses_manual_COGS") \
        .select("expense_id, bill_id, Amount, vendor_id, TxnDate, LineDescription, status") \
        .eq("project", PROJECT_ID) \
        .execute()
    expenses = exp_resp.data or []
    print(f"   {len(expenses)} total expenses")

    # Group expenses by bill_id
    by_bill = {}
    no_bill = []
    for e in expenses:
        bid = (e.get("bill_id") or "").strip()
        if bid:
            by_bill.setdefault(bid, []).append(e)
        else:
            no_bill.append(e)
    print(f"   {len(by_bill)} unique bill_ids, {len(no_bill)} expenses without bill_id")

    # 2. Load bills
    print("\n2. Loading bills...")
    bills_resp = sb.table("bills") \
        .select("bill_id, expected_total, status, vendor_id, receipt_url") \
        .execute()
    all_bills = {b["bill_id"]: b for b in (bills_resp.data or [])}

    # Filter to project bills needing total
    bills_needing_total = {}
    for bid in by_bill:
        bill = all_bills.get(bid)
        if bill and (not bill.get("expected_total") or bill["expected_total"] == 0):
            bills_needing_total[bid] = bill

    print(f"   {len(bills_needing_total)} bills with $0 expected_total that have linked expenses")

    # 3. Load vendors for display
    vendor_ids = set()
    for e in expenses:
        if e.get("vendor_id"):
            vendor_ids.add(e["vendor_id"])
    v_resp = sb.table("Vendors").select("id, vendor_name").execute()
    vendors = {v["id"]: v["vendor_name"] for v in (v_resp.data or [])}

    # 4. Load vault filenames for cross-validation
    print("\n3. Loading vault filenames for cross-validation...")
    vf_resp = sb.table("vault_files") \
        .select("name") \
        .eq("parent_id", RECEIPTS_FOLDER_ID) \
        .eq("is_folder", False) \
        .eq("is_deleted", False) \
        .execute()
    vault_filenames = [f["name"] for f in (vf_resp.data or [])]

    # Build filename total hints indexed by normalized bill_id
    filename_totals = {}
    for fn in vault_filenames:
        hints = extract_filename_hints(fn)
        if hints.get("total_hint"):
            # Try to find bill_id in filename
            m = re.match(r'^bill_(.+?)_\d{10,}', fn, re.IGNORECASE)
            if m:
                raw_bid = re.sub(r'^[\.\s]+', '', m.group(1).strip().rstrip("_"))
                if raw_bid:
                    filename_totals.setdefault(raw_bid, []).append(hints["total_hint"])
            # Also try matching vendor-date-total files to bill_ids
            # (these don't have bill_ prefix so we can't match directly)

    # Also look for SF Transport invoices: "INVOICE_10508_from_SF Transport...$1,462.85"
    for fn in vault_filenames:
        m = re.match(r'INVOICE[_\s]+(\d+)[_\s]+from.+?\$(\d[\d,.]*\.\d{2})', fn)
        if m:
            bid = m.group(1)
            total = float(m.group(2).replace(",", ""))
            filename_totals.setdefault(bid, []).append(total)

    print(f"   {len(filename_totals)} bill_ids with filename total hints")

    # 5. Calculate totals and prepare updates
    print(f"\n4. Calculating bill totals from expenses...\n")

    updates = []
    for bid in sorted(bills_needing_total.keys()):
        bill = bills_needing_total[bid]
        exps = by_bill[bid]
        exp_total = round(sum(e.get("Amount") or 0 for e in exps), 2)
        exp_count = len(exps)

        # Get vendor name
        vendor_id = exps[0].get("vendor_id") if exps else None
        vendor_name = vendors.get(vendor_id, "?") if vendor_id else "?"

        # Cross-validate with filename hint
        fn_hints = filename_totals.get(bid, [])
        fn_match = None
        if fn_hints:
            for fh in fn_hints:
                if abs(fh - exp_total) <= 0.10:
                    fn_match = "MATCH"
                    break
            if not fn_match:
                fn_match = f"MISMATCH (fn=${fn_hints[0]:,.2f})"

        # Check expense statuses
        statuses = {}
        for e in exps:
            s = e.get("status", "?")
            statuses[s] = statuses.get(s, 0) + 1
        status_str = ", ".join(f"{s}:{c}" for s, c in sorted(statuses.items()))

        updates.append({
            "bill_id": bid,
            "calculated_total": exp_total,
            "expense_count": exp_count,
            "vendor": vendor_name,
            "bill_status": bill.get("status", "?"),
            "expense_statuses": status_str,
            "filename_cross_check": fn_match,
        })

        xv = f" | fn: {fn_match}" if fn_match else ""
        print(f"   {bid:<28} ${exp_total:>10,.2f}  ({exp_count} expenses, {vendor_name[:20]}) [{status_str}]{xv}")

    # 6. Summary
    total_value = sum(u["calculated_total"] for u in updates)
    print(f"\n{'='*60}")
    print("SUMMARY")
    print(f"{'='*60}")
    print(f"  Bills to update:     {len(updates)}")
    print(f"  Total value:         ${total_value:,.2f}")
    print(f"  Avg per bill:        ${total_value / len(updates):,.2f}" if updates else "")

    # Cross-validation stats
    matched = [u for u in updates if u["filename_cross_check"] == "MATCH"]
    mismatched = [u for u in updates if u["filename_cross_check"] and u["filename_cross_check"] != "MATCH"]
    no_fn = [u for u in updates if not u["filename_cross_check"]]
    print(f"\n  Filename cross-check:")
    print(f"    Matched:    {len(matched)}")
    print(f"    Mismatched: {len(mismatched)}")
    print(f"    No hint:    {len(no_fn)}")

    if mismatched:
        print(f"\n  Mismatches:")
        for u in mismatched:
            print(f"    {u['bill_id']:<25} expenses=${u['calculated_total']:,.2f}  {u['filename_cross_check']}")

    if DRY_RUN:
        print(f"\n=== DRY RUN: Would update {len(updates)} bills ===")
        print("Run with --apply to execute.")
        return

    if not updates:
        print("\n  Nothing to update!")
        return

    # 7. Apply
    print(f"\n5. Updating {len(updates)} bills...")
    success = 0
    errors = 0

    for u in updates:
        try:
            sb.table("bills").update({
                "expected_total": u["calculated_total"],
            }).eq("bill_id", u["bill_id"]).execute()
            print(f"   OK: {u['bill_id']} -> ${u['calculated_total']:,.2f}")
            success += 1
        except Exception as e:
            print(f"   ERROR: {u['bill_id']} -> {str(e)[:100]}")
            errors += 1

    print(f"\nDone! Updated: {success}, Errors: {errors}")


if __name__ == "__main__":
    main()
