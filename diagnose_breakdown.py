# Diagnose bills: which have proper expense breakdowns vs single line items?
# For single-line bills, check if they match a bill with a similar ID that HAS breakdown.
# Also identify missing fields across all expenses.

import os
import re
import sys
from collections import defaultdict
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

sb = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_SERVICE_ROLE_KEY"))
PROJECT_ID = "582cfbde-a1d6-411a-bca8-75f29df6f0d6"


def normalize_bill_id(bid):
    if not bid:
        return ""
    return re.sub(r"[^A-Z0-9]", "", bid.upper())


def main():
    # 1. Load all expenses
    print("1. Loading all expenses for project...\n")
    r = sb.table("expenses_manual_COGS") \
        .select("expense_id, bill_id, Amount, TxnDate, LineDescription, vendor_id, account_id, payment_type, status, receipt_url") \
        .eq("project", PROJECT_ID) \
        .execute()
    expenses = r.data or []
    print(f"   {len(expenses)} total expenses\n")

    # 2. Load vendors
    v_resp = sb.table("Vendors").select("id, vendor_name").execute()
    vendors = {v["id"]: v["vendor_name"] for v in (v_resp.data or [])}

    # 3. Load accounts
    a_resp = sb.table("accounts").select("account_id, Name").execute()
    accounts = {a["account_id"]: a["Name"] for a in (a_resp.data or [])}

    # 4. Group by bill_id
    by_bill = defaultdict(list)
    no_bill = []
    for e in expenses:
        bid = (e.get("bill_id") or "").strip()
        if bid:
            by_bill[bid].append(e)
        else:
            no_bill.append(e)

    # 5. Classify bills
    single_line = {}   # bill_id -> [1 expense]
    multi_line = {}    # bill_id -> [N expenses]

    for bid, exps in by_bill.items():
        if len(exps) == 1:
            single_line[bid] = exps
        else:
            multi_line[bid] = exps

    print(f"{'='*70}")
    print("BILL BREAKDOWN ANALYSIS")
    print(f"{'='*70}")
    print(f"  Bills with 1 expense (single line):    {len(single_line)}")
    print(f"  Bills with 2+ expenses (breakdown):    {len(multi_line)}")
    print(f"  Expenses without bill_id:              {len(no_bill)}")

    # 6. Analyze missing fields across ALL expenses
    print(f"\n{'='*70}")
    print("MISSING FIELDS ANALYSIS (all expenses)")
    print(f"{'='*70}")

    field_stats = {
        "vendor_id": 0,
        "account_id": 0,
        "TxnDate": 0,
        "Amount": 0,
        "payment_type": 0,
        "bill_id": 0,
    }
    expenses_with_issues = []

    for e in expenses:
        missing = []
        if not e.get("vendor_id"): missing.append("vendor")
        if not e.get("account_id"): missing.append("account")
        if not e.get("TxnDate"): missing.append("date")
        if not e.get("Amount") and e.get("Amount") != 0: missing.append("amount")
        if not e.get("payment_type"): missing.append("payment")
        bid = (e.get("bill_id") or "").strip()
        if not bid: missing.append("bill_id")

        for f in missing:
            if f in field_stats:
                field_stats[f] += 1

        if missing:
            expenses_with_issues.append({"expense": e, "missing": missing})

    for field, count in sorted(field_stats.items(), key=lambda x: -x[1]):
        pct = count / len(expenses) * 100
        print(f"  Missing {field:<15}: {count:>4} ({pct:.1f}%)")

    print(f"\n  Expenses with at least 1 missing field: {len(expenses_with_issues)}")

    # 7. Single-line bills detail
    print(f"\n{'='*70}")
    print(f"SINGLE-LINE BILLS ({len(single_line)})")
    print(f"{'='*70}")

    # Categorize single-line bills
    single_checks = []     # Check payments (labor)
    single_invoices = []   # HD/vendor invoices
    single_other = []

    for bid, exps in sorted(single_line.items()):
        e = exps[0]
        amt = e.get("Amount") or 0
        desc = (e.get("LineDescription") or "")[:60]
        vendor = vendors.get(e.get("vendor_id"), "?")
        account = accounts.get(e.get("account_id"), "?")
        status = e.get("status", "?")
        date = e.get("TxnDate") or "?"

        # Missing fields for this expense
        missing = []
        if not e.get("vendor_id"): missing.append("vendor")
        if not e.get("account_id"): missing.append("account")
        if not e.get("TxnDate"): missing.append("date")
        if not e.get("payment_type"): missing.append("payment")

        info = {
            "bid": bid, "amt": amt, "desc": desc, "vendor": vendor,
            "account": account, "status": status, "date": date,
            "missing": missing, "expense": e,
        }

        if bid.lower().startswith("check") or bid.lower().startswith("wfct") or bid.lower().startswith("wtct"):
            single_checks.append(info)
        elif re.match(r'^H\d{4}-\d+', bid) or re.match(r'^\d{4,}$', bid):
            single_invoices.append(info)
        else:
            single_other.append(info)

    # Show checks (labor - usually expected to be single line)
    print(f"\n  --- Checks/Zelle/Wire ({len(single_checks)}) - Labor (expected single line) ---")
    checks_with_missing = [c for c in single_checks if c["missing"]]
    print(f"      With missing fields: {len(checks_with_missing)}")
    if checks_with_missing:
        for c in checks_with_missing[:10]:
            print(f"      {c['bid']:<25} ${c['amt']:>10,.2f} | {c['vendor']:<20} | missing: {', '.join(c['missing'])}")

    # Show invoices (HD, SF Transport, etc. - SHOULD have breakdown)
    print(f"\n  --- Vendor Invoices ({len(single_invoices)}) - Should have breakdown ---")
    invoices_with_missing = [c for c in single_invoices if c["missing"]]
    print(f"      With missing fields: {len(invoices_with_missing)}")
    print()
    for c in single_invoices:
        flag = " *** MISSING: " + ", ".join(c["missing"]) if c["missing"] else ""
        print(f"      {c['bid']:<25} ${c['amt']:>10,.2f} | {c['vendor']:<20} | {c['account']:<20} | {c['desc'][:35]}{flag}")

    # Show other
    print(f"\n  --- Other ({len(single_other)}) ---")
    for c in single_other:
        flag = " *** MISSING: " + ", ".join(c["missing"]) if c["missing"] else ""
        print(f"      {c['bid']:<25} ${c['amt']:>10,.2f} | {c['vendor']:<20} | {c['account']:<20} | {c['desc'][:35]}{flag}")

    # 8. Multi-line bills summary
    print(f"\n{'='*70}")
    print(f"MULTI-LINE BILLS ({len(multi_line)}) - Properly broken down")
    print(f"{'='*70}")

    multi_with_missing = {}
    for bid, exps in sorted(multi_line.items()):
        vendor = vendors.get(exps[0].get("vendor_id"), "?")
        total = round(sum(e.get("Amount") or 0 for e in exps), 2)
        missing_any = []
        for e in exps:
            m = []
            if not e.get("vendor_id"): m.append("vendor")
            if not e.get("account_id"): m.append("account")
            if not e.get("TxnDate"): m.append("date")
            if not e.get("payment_type"): m.append("payment")
            if m:
                missing_any.append(m)

        if missing_any:
            multi_with_missing[bid] = {"exps": exps, "missing": missing_any, "vendor": vendor, "total": total}

        flag = f" | {len(missing_any)} expenses missing fields" if missing_any else ""
        print(f"   {bid:<28} ${total:>10,.2f}  ({len(exps)} items, {vendor[:20]}){flag}")

    # 9. Expenses without bill_id
    print(f"\n{'='*70}")
    print(f"NO BILL_ID ({len(no_bill)})")
    print(f"{'='*70}")
    for e in no_bill[:20]:
        amt = e.get("Amount") or 0
        desc = (e.get("LineDescription") or "")[:50]
        vendor = vendors.get(e.get("vendor_id"), "?")
        status = e.get("status", "?")
        receipt = "has_receipt" if e.get("receipt_url") else "no_receipt"
        missing = []
        if not e.get("vendor_id"): missing.append("vendor")
        if not e.get("account_id"): missing.append("account")
        if not e.get("TxnDate"): missing.append("date")
        if not e.get("payment_type"): missing.append("payment")
        flag = f" MISSING: {', '.join(missing)}" if missing else ""
        print(f"   ${amt:>10,.2f} | {vendor:<20} | {status:<8} | {receipt} | {desc}{flag}")

    # 10. Summary: safe auto-fill opportunities
    print(f"\n{'='*70}")
    print("SAFE AUTO-FILL OPPORTUNITIES")
    print(f"{'='*70}")

    # Count expenses in multi-line bills that are missing payment_type
    # (can inherit from sibling expenses in same bill)
    multi_missing_payment = 0
    multi_missing_account = 0
    multi_can_inherit = 0
    for bid, info in multi_with_missing.items():
        exps = info["exps"]
        # Check if some siblings have the field
        has_payment = [e for e in exps if e.get("payment_type")]
        has_account = [e for e in exps if e.get("account_id")]
        for m_list in info["missing"]:
            if "payment" in m_list:
                multi_missing_payment += 1
                if has_payment:
                    multi_can_inherit += 1
            if "account" in m_list:
                multi_missing_account += 1

    print(f"\n  Multi-line bills with missing fields: {len(multi_with_missing)}")
    print(f"  Expenses missing payment in multi-line: {multi_missing_payment} (can inherit: {multi_can_inherit})")
    print(f"  Expenses missing account in multi-line: {multi_missing_account}")

    # Single-line invoices that should have breakdown
    print(f"\n  Single-line vendor invoices (need breakdown): {len(single_invoices)}")
    print(f"  Single-line checks/labor (OK as single): {len(single_checks)}")


if __name__ == "__main__":
    main()
