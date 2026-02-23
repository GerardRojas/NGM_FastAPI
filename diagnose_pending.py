# Diagnose pending expenses and bills for 1519 Arthur Neal Court
# Shows: pending expenses missing fields, bills with receipt PDFs, matching opportunities

import os
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

sb = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_SERVICE_ROLE_KEY"))
PROJECT_ID = "582cfbde-a1d6-411a-bca8-75f29df6f0d6"

print("=== 1. PENDING EXPENSES (missing fields) ===\n")
r = sb.table("expenses_manual_COGS") \
    .select("expense_id, Amount, TxnDate, LineDescription, vendor_id, bill_id, account_id, receipt_url, status, payment_type") \
    .eq("project", PROJECT_ID) \
    .eq("status", "pending") \
    .execute()
pending = r.data or []
print(f"Total pending expenses: {len(pending)}\n")

missing_bill = []
missing_vendor = []
missing_account = []
missing_date = []
missing_amount = []

for e in pending:
    eid = e["expense_id"][:8]
    amt = e.get("Amount", 0)
    desc = (e.get("LineDescription") or "N/A")[:50]
    bid = (e.get("bill_id") or "").strip()
    vid = e.get("vendor_id")
    aid = e.get("account_id")
    dt = e.get("TxnDate")

    fields_missing = []
    if not bid:
        fields_missing.append("bill_id")
        missing_bill.append(e)
    if not vid:
        fields_missing.append("vendor")
        missing_vendor.append(e)
    if not aid:
        fields_missing.append("account")
        missing_account.append(e)
    if not dt:
        fields_missing.append("date")
        missing_date.append(e)
    if not amt and amt != 0:
        fields_missing.append("amount")
        missing_amount.append(e)

    if fields_missing:
        print(f"  {eid}.. ${amt or 0:>10.2f} | {desc}")
        print(f"         missing: {', '.join(fields_missing)}")
        if bid:
            print(f"         bill_id: {bid}")

print(f"\nSummary:")
print(f"  Missing bill_id:  {len(missing_bill)}")
print(f"  Missing vendor:   {len(missing_vendor)}")
print(f"  Missing account:  {len(missing_account)}")
print(f"  Missing date:     {len(missing_date)}")
print(f"  Missing amount:   {len(missing_amount)}")

print(f"\n=== 2. ALL BILLS for this project ===\n")
# Bills don't have project_id directly, they use split_projects or are linked via expenses
# Get bills referenced by expenses for this project
bills_resp = sb.table("bills") \
    .select("bill_id, receipt_url, status, expected_total, vendor_id") \
    .execute()
all_bills = bills_resp.data or []
print(f"Total bills in system: {len(all_bills)}")

# Also get bills referenced by this project's expenses
exp_bill_ids = set()
all_exp = sb.table("expenses_manual_COGS") \
    .select("bill_id") \
    .eq("project", PROJECT_ID) \
    .not_.is_("bill_id", "null") \
    .execute()
for e in (all_exp.data or []):
    bid = (e.get("bill_id") or "").strip()
    if bid:
        exp_bill_ids.add(bid)
print(f"Bills referenced by project expenses: {len(exp_bill_ids)}")

# Show bills with receipt URLs
bills_with_pdf = [b for b in all_bills if b.get("receipt_url") and b["bill_id"] in exp_bill_ids]
print(f"Bills with receipt PDFs (project-related): {len(bills_with_pdf)}\n")

for b in bills_with_pdf[:20]:
    url = b["receipt_url"]
    ext = url.rsplit(".", 1)[-1].lower() if "." in url else "?"
    print(f"  {b['bill_id']:<30} ${b.get('expected_total') or 0:>10.2f}  ({b['status']}, .{ext})")

if len(bills_with_pdf) > 20:
    print(f"  ... and {len(bills_with_pdf) - 20} more")

print(f"\n=== 3. PENDING EXPENSES WITH bill_id (have bill but still pending) ===\n")
pending_with_bill = [e for e in pending if (e.get("bill_id") or "").strip()]
print(f"Pending expenses that DO have a bill_id: {len(pending_with_bill)}")
for e in pending_with_bill[:10]:
    eid = e["expense_id"][:8]
    amt = e.get("Amount", 0)
    desc = (e.get("LineDescription") or "N/A")[:40]
    bid = e.get("bill_id", "").strip()
    vid = e.get("vendor_id")
    aid = e.get("account_id")
    missing = []
    if not vid: missing.append("vendor")
    if not aid: missing.append("account")
    print(f"  {eid}.. ${amt or 0:>10.2f} | bill={bid} | missing: {', '.join(missing) or 'none (other issue?)'}")

print(f"\n=== 4. PENDING EXPENSES WITHOUT bill_id ===\n")
pending_no_bill = [e for e in pending if not (e.get("bill_id") or "").strip()]
print(f"Pending expenses WITHOUT bill_id: {len(pending_no_bill)}")
for e in pending_no_bill[:20]:
    eid = e["expense_id"][:8]
    amt = e.get("Amount", 0)
    desc = (e.get("LineDescription") or "N/A")[:50]
    vid = e.get("vendor_id")
    dt = e.get("TxnDate")
    receipt = (e.get("receipt_url") or "")
    print(f"  {eid}.. ${amt or 0:>10.2f} | {dt or 'no-date'} | {desc}")
    if receipt:
        print(f"         receipt: ...{receipt[-60:]}")

# Get vendor names for context
if pending:
    vendor_ids = list({e["vendor_id"] for e in pending if e.get("vendor_id")})
    if vendor_ids:
        print(f"\n=== 5. VENDORS referenced by pending expenses ===\n")
        v_resp = sb.table("Vendors") \
            .select("vendor_id, name") \
            .in_("vendor_id", vendor_ids) \
            .execute()
        for v in (v_resp.data or []):
            count = sum(1 for e in pending if e.get("vendor_id") == v["vendor_id"])
            print(f"  {v['name']:<30} ({count} pending expenses)")
