"""
Categories re-arch — PHASE 1 tool: parse flat accounts into the Category ->
Subcategory + cost_type overlay, and prove PARITY against existing expenses.

NON-DESTRUCTIVE by default (dry-run): reads `accounts` + `expenses_manual_COGS`,
parses each account name "{Subcategory} {Type}" (e.g. "Cabinets Material"),
prints the proposed hierarchy + a parity report, and writes review CSVs.
Nothing is written to the DB unless you pass --apply.

Usage:
    py build_category_map.py                      # dry-run + parity report + CSVs
    py build_category_map.py --grouping grouping.csv   # use team's subcat->category map
    py build_category_map.py --grouping grouping.csv --apply   # write the overlay tables

grouping.csv (filled by finance during curation, header required):
    subcategory,category,cost_code
    Cabinets,Carpentry,COGS-Materials
    ...
If --grouping is omitted, each subcategory becomes its own category (1:1) and
cost_code is left blank — valid but flagged reviewed=false for later curation.

Plan: apps/hub-vite/src/features/accounts/CATEGORIES_REARCH_PLAN.md (run the SQL
sql/categories_rearch_phase1.sql first).
"""

import argparse
import csv
import os
import sys
from collections import defaultdict

from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

# --- CONFIG: trailing-token -> cost_type. Longest match wins. EDIT to match -----
# the real labels in your accounts list (see open question #2 in the plan).
TYPE_SYNONYMS = [
    ("external services", "external_service"),
    ("external service", "external_service"),
    ("subcontractor", "external_service"),
    ("subcontract", "external_service"),
    ("services", "external_service"),
    ("service", "external_service"),
    ("materials", "material"),
    ("material", "material"),
    ("labour", "labor"),
    ("labor", "labor"),
    ("mano de obra", "labor"),
    ("change order", "change_order"),
    ("other expenses", "other_expenses"),
    ("other expense", "other_expenses"),
]
# Match the longest phrase first so "external service" beats "service".
TYPE_SYNONYMS.sort(key=lambda kv: len(kv[0]), reverse=True)


def parse_account(name):
    """('Cabinets Material') -> ('Cabinets', 'material'). Returns (subcat, cost_type)
    or (name, None) when no type token is found (needs human review)."""
    raw = (name or "").strip()
    low = raw.lower()
    for phrase, ctype in TYPE_SYNONYMS:
        # token must be the trailing word(s), separated by a space
        if low == phrase:
            return (raw, ctype)  # whole name is just a type -> ambiguous subcat
        if low.endswith(" " + phrase):
            sub = raw[: len(raw) - len(phrase)].strip(" -–·")
            return (sub or raw, ctype)
    return (raw, None)


def load_grouping(path):
    """subcategory(lower) -> (category, cost_code)."""
    mapping = {}
    if not path:
        return mapping
    with open(path, newline="", encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            sub = (row.get("subcategory") or "").strip()
            if not sub:
                continue
            mapping[sub.lower()] = (
                (row.get("category") or "").strip() or sub,
                (row.get("cost_code") or "").strip() or None,
            )
    return mapping


def load_overrides(path):
    """account_id -> (subcategory|None, cost_type|None) for type-less accounts."""
    m = {}
    if not path:
        return m
    valid = {"material", "labor", "external_service", "change_order", "other_expenses"}
    with open(path, newline="", encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            aid = (row.get("account_id") or "").strip()
            if not aid:
                continue
            ct = (row.get("cost_type") or "").strip().lower() or None
            if ct and ct not in valid:
                raise SystemExit(f"override for {aid}: invalid cost_type '{ct}' (use {sorted(valid)})")
            m[aid] = ((row.get("subcategory") or "").strip() or None, ct)
    return m


def fetch_all(sb, table, columns):
    """Page through a table (Supabase caps at 1000 rows/request)."""
    rows, start, page = [], 0, 1000
    while True:
        resp = sb.table(table).select(columns).range(start, start + page - 1).execute()
        batch = resp.data or []
        rows.extend(batch)
        if len(batch) < page:
            break
        start += page
    return rows


def to_num(v):
    try:
        return float(str(v).replace("$", "").replace(",", "")) if v not in (None, "") else 0.0
    except (TypeError, ValueError):
        return 0.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--grouping", help="CSV: subcategory,category,cost_code")
    ap.add_argument("--overrides", help="CSV: account_id,subcategory,cost_type (for type-less accounts)")
    ap.add_argument("--apply", action="store_true", help="write the overlay tables (default: dry-run)")
    args = ap.parse_args()

    sb = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_SERVICE_ROLE_KEY"))
    grouping = load_grouping(args.grouping)
    overrides = load_overrides(args.overrides)

    accounts = fetch_all(sb, "accounts", "account_id, Name, AcctNum, is_cogs")
    print(f"accounts: {len(accounts)}")

    # Parse every account into (subcategory, cost_type, category, cost_code).
    # Per-account overrides (from --overrides) win over the name parser — that's
    # how the type-less accounts get a manual cost_type/subcategory.
    parsed = {}          # account_id -> dict
    unparseable = []     # accounts still without a type after overrides
    subcats = {}         # subcat_lower -> {name, category, cost_code}
    for a in accounts:
        aid = a.get("account_id")
        sub, ctype = parse_account(a.get("Name"))
        ov_sub, ov_type = overrides.get(aid, (None, None))
        if ov_sub:
            sub = ov_sub
        if ov_type:
            ctype = ov_type
        cat, code = grouping.get(sub.lower(), (sub, None))  # default 1:1 category
        if ctype is None:
            unparseable.append(a)
        parsed[aid] = {"name": a.get("Name"), "subcategory": sub, "cost_type": ctype,
                       "category": cat, "cost_code": code}
        subcats.setdefault(sub.lower(), {"name": sub, "category": cat, "cost_code": code})

    categories = sorted({s["category"] for s in subcats.values()})
    cogs_unp = [a for a in unparseable if a.get("is_cogs")]
    print(f"-> {len(categories)} categories, {len(subcats)} subcategories, "
          f"{len(unparseable)} accounts need review (no type token): "
          f"{len(cogs_unp)} COGS (need a cost_type) + {len(unparseable) - len(cogs_unp)} non-COGS (likely overhead/out of scope)")

    # ---- PARITY against expenses ------------------------------------------------
    expenses = fetch_all(sb, "expenses_manual_COGS", "account_id, Amount")
    by_account = defaultdict(float)
    for e in expenses:
        by_account[e.get("account_id")] += to_num(e.get("Amount"))
    grand = sum(by_account.values())

    mapped_total, unmapped_total, unmapped_accounts = 0.0, 0.0, []
    no_account_total = 0.0   # expenses with NULL/blank account_id in the source —
                             # never had a classification; not a parity regression.
    by_cat = defaultdict(float)
    by_subcat_type = defaultdict(float)
    by_code = defaultdict(float)
    for aid, amount in by_account.items():
        if not aid:
            no_account_total += amount
            continue
        p = parsed.get(aid)
        if not p or p["cost_type"] is None:
            unmapped_total += amount
            unmapped_accounts.append((aid, amount))
            continue
        mapped_total += amount
        by_cat[p["category"]] += amount
        by_subcat_type[(p["subcategory"], p["cost_type"])] += amount
        by_code[p["cost_code"] or "(unassigned)"] += amount

    print("\n=== PARITY REPORT ===")
    print(f"expenses rows:            {len(expenses)}")
    print(f"distinct accounts used:   {len(by_account)}")
    print(f"grand total (expenses):   {grand:,.2f}")
    print(f"  mapped:                 {mapped_total:,.2f}")
    print(f"  no account in source:   {no_account_total:,.2f}  (pre-existing, not a regression)")
    print(f"  UNMAPPED (breaks parity):{unmapped_total:,.2f}  across {len(unmapped_accounts)} accounts")
    conserved = abs(grand - (mapped_total + unmapped_total + no_account_total)) < 0.01
    print(f"conservation (sum check): {'OK' if conserved else 'FAIL'}")
    print(f"parity GATE: {'PASS' if unmapped_total < 0.01 and conserved else 'FAIL - resolve review list first'}")

    if unmapped_accounts:
        print("\n-- accounts with spend but no type token (fix TYPE_SYNONYMS or rename) --")
        id_to_name = {a['account_id']: a.get('Name') for a in accounts}
        for aid, amt in sorted(unmapped_accounts, key=lambda x: -x[1])[:50]:
            print(f"   {amt:>14,.2f}  {id_to_name.get(aid, aid)}")

    # ---- write review CSVs (always, for curation) -------------------------------
    with open("subcategories_review.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["subcategory", "category", "cost_code"])
        for s in sorted(subcats.values(), key=lambda x: x["name"].lower()):
            w.writerow([s["name"], s["category"], s["cost_code"] or ""])
    # Doubles as the --overrides template: fill cost_type (and tweak subcategory).
    with open("accounts_unparseable.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["account_id", "name", "is_cogs", "subcategory", "cost_type"])
        for a in sorted(unparseable, key=lambda x: (not x.get("is_cogs"), (x.get("Name") or "").lower())):
            w.writerow([a.get("account_id"), a.get("Name"), a.get("is_cogs"), a.get("Name") or "", ""])
    print("\nwrote subcategories_review.csv (assign category + cost_code) and "
          "accounts_unparseable.csv (fill cost_type, then pass as --overrides)")

    if not args.apply:
        print("\nDRY-RUN: nothing written to the DB. Re-run with --apply once parity GATE = PASS.")
        return

    if unmapped_total >= 0.01 or not conserved:
        print("\nRefusing --apply: parity GATE failed. Resolve the review list first.")
        sys.exit(1)

    # ---- APPLY: upsert categories -> subcategories -> account_category_map ------
    print("\nAPPLY: writing overlay tables...")
    cat_ids = {}
    for name in categories:
        row = sb.table("categories").upsert({"name": name}, on_conflict="name").execute().data[0]
        cat_ids[name] = row["id"]
    sub_ids = {}
    for s in subcats.values():
        row = sb.table("subcategories").upsert(
            {"category_id": cat_ids[s["category"]], "name": s["name"]},
            on_conflict="category_id,name").execute().data[0]
        sub_ids[s["name"].lower()] = row["id"]
    written = 0
    for aid, p in parsed.items():
        if p["cost_type"] is None:
            continue
        sb.table("account_category_map").upsert({
            "account_id": aid,
            "subcategory_id": sub_ids[p["subcategory"].lower()],
            "cost_type": p["cost_type"],
            "reviewed": bool(args.grouping),
            "source": "auto",
        }, on_conflict="account_id").execute()
        written += 1
    print(f"done: {len(cat_ids)} categories, {len(sub_ids)} subcategories, {written} account maps.")


if __name__ == "__main__":
    main()
