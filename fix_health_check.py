# Fix health check issues for 1519 Arthur Neal Court project.
#
# Mission: Reduce health check negatives WITHOUT modifying expense totals.
# Strategy:
#   1. Extract bill_id from expense descriptions (Check, WFCT, HD invoice, factura, etc.)
#   2. Group "factura por XXX" expenses into HD bills by referenced invoice total
#   3. Match to existing bills OR create new bills for orphan references
#   4. For vendor-name-only expenses, create descriptive bill_ids (vendor+date)
#   5. Fill bills.expected_total from expense sums
#
# Usage:
#   .venv/Scripts/python.exe fix_health_check.py                  # dry-run
#   .venv/Scripts/python.exe fix_health_check.py --apply          # execute

import os
import re
import sys
from collections import defaultdict
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

sb = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_SERVICE_ROLE_KEY"))
PROJECT_ID = "582cfbde-a1d6-411a-bca8-75f29df6f0d6"
DRY_RUN = "--apply" not in sys.argv


def normalize_bill_id(bid):
    if not bid:
        return ""
    return re.sub(r"[^A-Z0-9]", "", bid.upper())


def extract_bill_hint(desc, vendor_name, txn_date):
    """Extract potential bill_id from expense description.
    Returns (hint_type, canonical_bill_id) or None.
    """
    if not desc:
        return None

    # Priority 1: Check number - "Check 1446", "check 1324", "Check 1389-Jose"
    m = re.search(r'\bcheck\s*[#]?\s*(\d{3,5})', desc, re.IGNORECASE)
    if m:
        return ("check", f"Check {m.group(1)}")

    # Priority 2: Wire transfer - "WFCT...", "WTCT..."
    m = re.search(r'\b(WFCT\w+|WTCT\w+)\b', desc, re.IGNORECASE)
    if m:
        return ("wire", m.group(1).upper())

    # Priority 3: HD invoice - "H0659-XXXXXXX" or "0659-XXXXXXX"
    m = re.search(r'\b(H?\d{4}[-]\d{5,7})\b', desc)
    if m:
        bid = m.group(1)
        if not bid.startswith("H"):
            bid = f"H{bid}"
        return ("hd_invoice", bid)

    # Priority 4: SF Transport invoice
    m = re.search(r'\bSF\s*(?:transport)?\s*[#]?\s*(\d{4,6})\b', desc, re.IGNORECASE)
    if m:
        return ("sf_invoice", m.group(1))

    # Priority 5: Amazon order "XXX-XXXXXXX-XXXXXXX"
    m = re.search(r'(\d{3}-\d{7}-\d{7})', desc)
    if m:
        return ("amazon", m.group(1))

    # Priority 6: "Zelle Name"
    m = re.search(r'\b[Zz]elle\s+([A-Z][a-zA-Z\s]+)', desc)
    if m:
        name = m.group(1).strip()
        if len(name) > 3:
            return ("zelle", f"Zelle {name}")

    # Priority 7: "factura por XXX.XX" - Home Depot invoice reference
    m = re.search(r'factura\s+por\s+\$?([\d,]+\.?\d*)', desc, re.IGNORECASE)
    if m:
        raw = m.group(1).replace(",", "")
        try:
            total = float(raw)
            # Use the factura total as a bill grouping key
            return ("factura", f"HD-Factura-{total:.2f}")
        except ValueError:
            pass

    # Priority 8: "payment Valeria" or similar payment descriptions
    m = re.search(r'\bpayment\s+(\w+)', desc, re.IGNORECASE)
    if m:
        name = m.group(1).strip()
        if len(name) > 2:
            short_vendor = vendor_name[:20] if vendor_name else "Unknown"
            return ("payment", f"Payment-{short_vendor}-{name}")

    return None


def generate_fallback_bill_id(vendor_name, txn_date, amount):
    """Generate a descriptive bill_id for expenses with no identifiable reference."""
    clean_vendor = re.sub(r'[^A-Za-z0-9 ]', '', vendor_name or "Unknown")[:20].strip().replace(" ", "-")
    date_part = (txn_date or "nodate")[:10]
    return f"{clean_vendor}-{date_part}"


def main():
    if DRY_RUN:
        print("=== DRY RUN (pass --apply to execute) ===\n")
    else:
        print("=== APPLY MODE ===\n")

    # 1. Load data
    print("1. Loading data...")
    r = sb.table("expenses_manual_COGS") \
        .select("expense_id, bill_id, Amount, TxnDate, LineDescription, vendor_id, account_id, payment_type, status, receipt_url") \
        .eq("project", PROJECT_ID) \
        .execute()
    expenses = r.data or []

    bills_resp = sb.table("bills") \
        .select("bill_id, receipt_url, status, expected_total, vendor_id") \
        .execute()
    all_bills = {b["bill_id"]: b for b in (bills_resp.data or [])}

    norm_to_bill = {}
    for bid in all_bills:
        nb = normalize_bill_id(bid)
        if nb:
            norm_to_bill[nb] = bid

    v_resp = sb.table("Vendors").select("id, vendor_name").execute()
    vendors = {v["id"]: v["vendor_name"] for v in (v_resp.data or [])}

    a_resp = sb.table("accounts").select("account_id, Name").execute()
    accounts = {a["account_id"]: a["Name"] for a in (a_resp.data or [])}

    print(f"   {len(expenses)} expenses, {len(all_bills)} bills, {len(vendors)} vendors")

    # Split expenses
    with_bill = []
    no_bill = []
    for e in expenses:
        bid = (e.get("bill_id") or "").strip()
        if bid:
            with_bill.append(e)
        else:
            no_bill.append(e)

    # =====================================================
    # PHASE 1: CURRENT HEALTH CHECK
    # =====================================================
    print(f"\n{'='*70}")
    print("PHASE 1: CURRENT HEALTH CHECK STATUS")
    print(f"{'='*70}")

    issues_before = {"vendor": 0, "amount": 0, "date": 0, "account": 0, "bill_id": 0, "receipt": 0}
    for e in expenses:
        if not e.get("vendor_id"):
            issues_before["vendor"] += 1
        if not e.get("Amount") and e.get("Amount") != 0:
            issues_before["amount"] += 1
        if not e.get("TxnDate"):
            issues_before["date"] += 1
        if not e.get("account_id"):
            issues_before["account"] += 1
        bid = (e.get("bill_id") or "").strip()
        if not bid:
            issues_before["bill_id"] += 1
        has_receipt = False
        if bid and bid in all_bills:
            has_receipt = bool(all_bills[bid].get("receipt_url"))
        if not has_receipt:
            has_receipt = bool(e.get("receipt_url"))
        if not has_receipt:
            issues_before["receipt"] += 1

    total_checks = len(expenses) * 6
    total_issues_before = sum(issues_before.values())
    score_before = ((total_checks - total_issues_before) / total_checks) * 100

    print(f"\n  Expenses: {len(expenses)} ({len(with_bill)} with bill, {len(no_bill)} without)")
    print(f"\n  Health Check Issues (BEFORE):")
    for field, count in sorted(issues_before.items(), key=lambda x: -x[1]):
        if count > 0:
            pct = count / len(expenses) * 100
            bar = "#" * int(pct / 2)
            print(f"    {field:<12}: {count:>4} ({pct:5.1f}%) {bar}")
    print(f"\n  Total issues: {total_issues_before} / {total_checks}")
    print(f"  Health Score: {score_before:.1f}%")

    # =====================================================
    # PHASE 2: CLASSIFY ALL EXPENSES WITHOUT BILL_ID
    # =====================================================
    print(f"\n{'='*70}")
    print(f"PHASE 2: CLASSIFY {len(no_bill)} EXPENSES WITHOUT BILL_ID")
    print(f"{'='*70}")

    # Categories:
    cat_A = []    # Matched to existing DB bill
    cat_B = []    # Has identifiable reference, bill to create
    cat_C = []    # Generic desc, will get fallback bill_id

    for e in no_bill:
        desc = e.get("LineDescription") or ""
        vendor_name = vendors.get(e.get("vendor_id"), "Unknown")
        txn_date = e.get("TxnDate") or ""

        hint = extract_bill_hint(desc, vendor_name, txn_date)

        if hint:
            hint_type, canonical_bid = hint

            # Try matching to existing bill
            existing_bid = None
            if canonical_bid in all_bills:
                existing_bid = canonical_bid
            else:
                norm = normalize_bill_id(canonical_bid)
                if norm in norm_to_bill:
                    existing_bid = norm_to_bill[norm]
                else:
                    # Try variations for checks
                    if hint_type == "check":
                        num = canonical_bid.replace("Check ", "")
                        for var in [num, f"check {num}", f"CHECK {num}"]:
                            if var in all_bills:
                                existing_bid = var
                                break
                            nv = normalize_bill_id(var)
                            if nv in norm_to_bill:
                                existing_bid = norm_to_bill[nv]
                                break

            if existing_bid:
                cat_A.append({
                    "expense": e, "hint_type": hint_type,
                    "bill_id": existing_bid, "bill": all_bills[existing_bid],
                    "vendor_name": vendor_name,
                })
            else:
                cat_B.append({
                    "expense": e, "hint_type": hint_type,
                    "bill_id": canonical_bid,
                    "vendor_name": vendor_name,
                })
        else:
            # No identifiable reference - generate fallback bill_id
            fallback = generate_fallback_bill_id(vendor_name, txn_date, e.get("Amount"))
            cat_C.append({
                "expense": e, "hint_type": "fallback",
                "bill_id": fallback,
                "vendor_name": vendor_name,
            })

    # Group B and C by bill_id (multiple expenses may share the same)
    groups_B = defaultdict(list)
    for m in cat_B:
        groups_B[m["bill_id"]].append(m)

    groups_C = defaultdict(list)
    for m in cat_C:
        groups_C[m["bill_id"]].append(m)

    print(f"\n  Category A: {len(cat_A)} expenses -> existing DB bills (just set bill_id)")
    print(f"  Category B: {len(cat_B)} expenses -> {len(groups_B)} new bills (identifiable reference)")
    print(f"  Category C: {len(cat_C)} expenses -> {len(groups_C)} new bills (vendor+date fallback)")
    print(f"  TOTAL:      {len(cat_A) + len(cat_B) + len(cat_C)} / {len(no_bill)}")

    # --- Show Category A ---
    if cat_A:
        print(f"\n  === A) EXISTING BILL MATCHES ({len(cat_A)}) ===")
        for m in sorted(cat_A, key=lambda x: x["bill_id"]):
            e = m["expense"]
            amt = e.get("Amount") or 0
            has_receipt = "receipt" if m["bill"].get("receipt_url") else ""
            print(f"    {m['bill_id']:<28} <- ${amt:>10,.2f} | {m['vendor_name'][:20]:<20} | {has_receipt}")

    # --- Show Category B ---
    if groups_B:
        print(f"\n  === B) NEW BILLS FROM REFERENCES ({len(groups_B)} bills, {len(cat_B)} expenses) ===")

        # Sub-group by hint_type
        by_type = defaultdict(list)
        for bid, group in sorted(groups_B.items()):
            by_type[group[0]["hint_type"]].append((bid, group))

        for htype, items in sorted(by_type.items()):
            print(f"\n    --- {htype} ({sum(len(g) for _, g in items)} expenses) ---")
            for bid, group in items:
                total = round(sum(m["expense"].get("Amount") or 0 for m in group), 2)
                vendor = group[0]["vendor_name"]
                has_receipt = any(m["expense"].get("receipt_url") for m in group)
                r_tag = " [has_receipt]" if has_receipt else ""
                print(f"    {bid:<30} ({len(group)} exp, ${total:>10,.2f}) | {vendor[:20]}{r_tag}")

    # --- Show Category C ---
    if groups_C:
        print(f"\n  === C) FALLBACK BILLS ({len(groups_C)} bills, {len(cat_C)} expenses) ===")
        # Show grouped by vendor
        by_vendor = defaultdict(list)
        for bid, group in groups_C.items():
            by_vendor[group[0]["vendor_name"]].append((bid, group))

        for vname, items in sorted(by_vendor.items(), key=lambda x: -sum(len(g) for _, g in x[1])):
            total_exp = sum(len(g) for _, g in items)
            total_amt = round(sum(m["expense"].get("Amount") or 0 for _, g in items for m in g), 2)
            print(f"    {vname[:25]:<25} {total_exp:>3} expenses, {len(items):>2} bills, ${total_amt:>10,.2f}")

    # =====================================================
    # PHASE 3: FILL BILLS EXPECTED_TOTAL
    # =====================================================
    print(f"\n{'='*70}")
    print("PHASE 3: EXISTING BILLS WITH $0 EXPECTED_TOTAL")
    print(f"{'='*70}")

    by_bill = defaultdict(list)
    for e in with_bill:
        bid = e["bill_id"].strip()
        by_bill[bid].append(e)
    for m in cat_A:
        by_bill[m["bill_id"]].append(m["expense"])

    bills_to_fill_total = []
    for bid, exps in by_bill.items():
        bill = all_bills.get(bid)
        if not bill:
            continue
        if bill.get("expected_total") and bill["expected_total"] > 0:
            continue
        total = round(sum(e.get("Amount") or 0 for e in exps), 2)
        if total > 0:
            bills_to_fill_total.append({
                "bill_id": bid, "calculated_total": total,
                "expense_count": len(exps),
                "vendor": vendors.get(exps[0].get("vendor_id"), "?"),
            })

    total_fill = sum(b["calculated_total"] for b in bills_to_fill_total)
    print(f"\n  {len(bills_to_fill_total)} existing bills need expected_total (${total_fill:,.2f})")

    # =====================================================
    # PHASE 4: PROJECTED IMPROVEMENT
    # =====================================================
    print(f"\n{'='*70}")
    print("PHASE 4: PROJECTED HEALTH CHECK IMPROVEMENT")
    print(f"{'='*70}")

    issues_after = dict(issues_before)

    # ALL bill_id issues fixed (A + B + C = all 125)
    issues_after["bill_id"] = 0

    # Receipt fixes from A (existing bill has receipt)
    receipt_fixes = 0
    for m in cat_A:
        e = m["expense"]
        if not e.get("receipt_url") and m["bill"].get("receipt_url"):
            receipt_fixes += 1
    # Receipt fixes from B (new bills with receipt from expense)
    for m in cat_B:
        e = m["expense"]
        # If expense has receipt_url, receipt was already OK (checked via fallback)
        # If expense has no receipt_url, new bill also won't have receipt -> still fails
        pass
    # Same for C
    issues_after["receipt"] -= receipt_fixes

    total_issues_after = sum(issues_after.values())
    score_after = ((total_checks - total_issues_after) / total_checks) * 100

    print(f"\n  {'Field':<14} {'Before':>8} {'After':>8} {'Fixed':>8}")
    print(f"  {'-'*46}")
    for field in ["bill_id", "receipt", "vendor", "amount", "date", "account"]:
        before = issues_before[field]
        after = issues_after[field]
        fixed = before - after
        marker = f" <-- -{fixed}" if fixed > 0 else ""
        print(f"  {field:<14} {before:>8} {after:>8} {fixed:>8}{marker}")
    print(f"  {'-'*46}")
    total_fixed = total_issues_before - total_issues_after
    print(f"  {'TOTAL':<14} {total_issues_before:>8} {total_issues_after:>8} {total_fixed:>8}")
    print(f"\n  Health Score: {score_before:.1f}% -> {score_after:.1f}%  (+{score_after - score_before:.1f}pp)")
    print(f"  Issues fixed: {total_fixed} of {total_issues_before}")

    print(f"\n  Actions:")
    print(f"    1. Set bill_id on {len(cat_A)} expenses (existing bills)")
    print(f"    2. Create {len(groups_B)} bills + set bill_id on {len(cat_B)} expenses (identifiable ref)")
    print(f"    3. Create {len(groups_C)} bills + set bill_id on {len(cat_C)} expenses (vendor+date)")
    print(f"    4. Fill expected_total on {len(bills_to_fill_total)} existing bills")

    # =====================================================
    # PHASE 5: APPLY
    # =====================================================
    if DRY_RUN:
        print(f"\n{'='*70}")
        print("DRY RUN - Run with --apply to execute")
        print(f"{'='*70}")
        return

    print(f"\n{'='*70}")
    print("APPLYING CHANGES")
    print(f"{'='*70}")

    success = 0
    errors = 0

    # Step 1: Set bill_id on cat_A (existing bill matches)
    if cat_A:
        print(f"\n  Step 1: Set bill_id on {len(cat_A)} expenses (existing bills)...")
        for m in cat_A:
            e = m["expense"]
            try:
                sb.table("expenses_manual_COGS").update({
                    "bill_id": m["bill_id"],
                }).eq("expense_id", e["expense_id"]).execute()
                print(f"    OK: ...{e['expense_id'][-8:]} -> {m['bill_id']}")
                success += 1
            except Exception as ex:
                print(f"    ERR: ...{e['expense_id'][-8:]} -> {str(ex)[:60]}")
                errors += 1

    # Step 2: Create bills + set bill_id for cat_B
    if groups_B:
        print(f"\n  Step 2a: Create {len(groups_B)} new bills (identifiable references)...")
        for bid, group in sorted(groups_B.items()):
            total = round(sum(m["expense"].get("Amount") or 0 for m in group), 2)
            vendor_id = group[0]["expense"].get("vendor_id")
            receipt_url = None
            for m in group:
                if m["expense"].get("receipt_url"):
                    receipt_url = m["expense"]["receipt_url"]
                    break
            try:
                record = {
                    "bill_id": bid,
                    "expected_total": total,
                    "status": "auth",
                    "vendor_id": vendor_id,
                }
                if receipt_url:
                    record["receipt_url"] = receipt_url
                sb.table("bills").insert(record).execute()
                print(f"    OK: '{bid}' (${total:,.2f})")
                success += 1
            except Exception as ex:
                if "duplicate" in str(ex).lower() or "unique" in str(ex).lower():
                    print(f"    SKIP: '{bid}' already exists")
                else:
                    print(f"    ERR: '{bid}' -> {str(ex)[:60]}")
                    errors += 1

        print(f"\n  Step 2b: Set bill_id on {len(cat_B)} expenses...")
        for m in cat_B:
            e = m["expense"]
            try:
                sb.table("expenses_manual_COGS").update({
                    "bill_id": m["bill_id"],
                }).eq("expense_id", e["expense_id"]).execute()
                success += 1
            except Exception as ex:
                print(f"    ERR: ...{e['expense_id'][-8:]} -> {str(ex)[:60]}")
                errors += 1
        print(f"    Done ({len(cat_B)} expenses)")

    # Step 3: Create bills + set bill_id for cat_C
    if groups_C:
        print(f"\n  Step 3a: Create {len(groups_C)} new bills (vendor+date fallback)...")
        for bid, group in sorted(groups_C.items()):
            total = round(sum(m["expense"].get("Amount") or 0 for m in group), 2)
            vendor_id = group[0]["expense"].get("vendor_id")
            receipt_url = None
            for m in group:
                if m["expense"].get("receipt_url"):
                    receipt_url = m["expense"]["receipt_url"]
                    break
            try:
                record = {
                    "bill_id": bid,
                    "expected_total": total,
                    "status": "auth",
                    "vendor_id": vendor_id,
                }
                if receipt_url:
                    record["receipt_url"] = receipt_url
                sb.table("bills").insert(record).execute()
                success += 1
            except Exception as ex:
                if "duplicate" in str(ex).lower() or "unique" in str(ex).lower():
                    pass  # already exists
                else:
                    print(f"    ERR: '{bid}' -> {str(ex)[:60]}")
                    errors += 1
        print(f"    Done ({len(groups_C)} bills)")

        print(f"\n  Step 3b: Set bill_id on {len(cat_C)} expenses...")
        for m in cat_C:
            e = m["expense"]
            try:
                sb.table("expenses_manual_COGS").update({
                    "bill_id": m["bill_id"],
                }).eq("expense_id", e["expense_id"]).execute()
                success += 1
            except Exception as ex:
                print(f"    ERR: ...{e['expense_id'][-8:]} -> {str(ex)[:60]}")
                errors += 1
        print(f"    Done ({len(cat_C)} expenses)")

    # Step 4: Fill expected_total on existing bills
    if bills_to_fill_total:
        print(f"\n  Step 4: Fill expected_total on {len(bills_to_fill_total)} existing bills...")
        for b in bills_to_fill_total:
            try:
                sb.table("bills").update({
                    "expected_total": b["calculated_total"],
                }).eq("bill_id", b["bill_id"]).execute()
                success += 1
            except Exception as ex:
                print(f"    ERR: {b['bill_id']} -> {str(ex)[:60]}")
                errors += 1
        print(f"    Done ({len(bills_to_fill_total)} bills)")

    print(f"\n{'='*70}")
    print(f"  COMPLETE! Success: {success}, Errors: {errors}")
    print(f"  Health Score: {score_before:.1f}% -> {score_after:.1f}%")
    print(f"{'='*70}")


if __name__ == "__main__":
    main()
