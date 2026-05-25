"""
Phase 1 grouping generator: resolve every account to a CANONICAL category (from
templates/estimate.ngm) + cost_type, then emit the inputs build_category_map.py
consumes (grouping.csv, accounts_overrides.csv) plus an audit (account_map.csv).
Read-only — writes CSVs only. Decisions baked in per the team's curation.
"""
import os, re, json, csv, difflib
from collections import Counter
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()
sb = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_SERVICE_ROLE_KEY"))

SYN = [("external services", "external_service"), ("external service", "external_service"),
       ("subcontractor", "external_service"), ("subcontract", "external_service"),
       ("services", "external_service"), ("service", "external_service"),
       ("materials", "material"), ("material", "material"),
       ("labour", "labor"), ("labor", "labor"), ("mano de obra", "labor"),
       ("change order", "change_order"), ("other expenses", "other_expenses"), ("other expense", "other_expenses")]
SYN.sort(key=lambda kv: len(kv[0]), reverse=True)

def parse(name):
    raw = (name or "").strip(); low = raw.lower()
    for ph, ct in SYN:
        if low == ph: return raw, ct
        if low.endswith(" " + ph): return raw[: len(raw) - len(ph)].strip(" -–·") or raw, ct
    return raw, None

def norm(s): return re.sub(r"[^a-z0-9]", "", (s or "").lower())

# Curation decisions (team-approved): subcategory(lower) -> canonical category.
SPECIALS_CATEGORY = {
    "carpet": "Flooring", "pool": "Exterior Structures", "raise foundation": "Rough Structure",
    "design meterials": "Plans & Permits", "finish details": "Interior Trim", "pad": "Rough Structure",
    "202 preparation": "Preparation", "206 plans and permits": "Plans & Permits",
    "198": "Other Expenses", "211 hot mop shower pan": "Waterproofing",
    "other utility connections": "Underground",
    # Group B -> Other Expenses
    "cost of goods sold": "Other Expenses", "cost of labor - cogs": "Other Expenses",
    "equipment rental - cogs": "Other Expenses", "freight in - cogs": "Other Expenses",
    "supplies & materials - cogs": "Other Expenses", "utilities": "Other Expenses",
    "advertising & marketing": "Other Expenses", "office expenses": "Other Expenses",
    "crew meals": "Other Expenses", "disposition": "Other Expenses",
    "profit projected": "Other Expenses", "subcontractor": "Other Expenses", "change order": "Other Expenses",
}
# Type-less accounts that need a non-default cost_type (by account Name).
MANUAL_TYPE = {
    "Hauling and Dump": "external_service", "Appliances": "material", "Clearing": "external_service",
    "Rough Electrical": "external_service", "Rough Framing": "external_service", "Rough Grading": "external_service",
    "Finish Details": "labor", "Trenching": "external_service", "Electrical Complements": "material",
    "ROW Work": "external_service", "Supplies & materials - COGS": "material", "Freight in - COGS": "material",
    "PAD": "material", "211 Hot Mop Shower Pan": "material", "Design Meterials": "material",
    "Raise Foundation": "material", "Other Utility Connections": "external_service",
}
GUESSED = set(SPECIALS_CATEGORY) | {k.lower() for k in MANUAL_TYPE}  # reviewed=false candidates

tree = json.load(open("templates/estimate.ngm", encoding="utf-8"))["categories"]
idx, subs, normkeys = {}, [], []
for c in tree:
    for s in c.get("subcategories") or []:
        idx[norm(s["name"])] = c["name"]; subs.append((norm(s["name"]), c["name"])); normkeys.append(norm(s["name"]))

def match_cat(sub):
    n = norm(sub)
    if n in idx: return idx[n]
    best = None
    for ns, cat in subs:
        if len(ns) >= 4 and (ns in n or n in ns):
            if best is None or len(ns) > len(best[0]): best = (ns, cat)
    if best: return best[1]
    cm = difflib.get_close_matches(n, normkeys, n=1, cutoff=0.82)
    return idx[cm[0]] if cm else None

accts, st = [], 0
while True:
    b = sb.table("accounts").select("account_id, Name").range(st, st + 999).execute().data or []
    accts += b
    if len(b) < 1000: break
    st += 1000

rows = []   # (account_id, name, subcat, category, cost_type, flag)
for a in accts:
    name = a.get("Name"); sub, ct = parse(name)
    sl = sub.lower()
    cat = SPECIALS_CATEGORY.get(sl) or match_cat(sub) or sub
    if ct is None:
        ct = MANUAL_TYPE.get(name) or ("other_expenses" if cat == "Other Expenses" else None)
    flag = "review" if (sl in GUESSED or name in MANUAL_TYPE) else "ok"
    rows.append((a.get("account_id"), name, sub, cat, ct, flag))

# grouping.csv: distinct subcategory -> category
seen = {}
for _, _, sub, cat, _, _ in rows:
    seen.setdefault(sub.lower(), (sub, cat))
with open("grouping.csv", "w", newline="", encoding="utf-8") as fh:
    w = csv.writer(fh); w.writerow(["subcategory", "category", "cost_code"])
    for sub, cat in sorted(seen.values()): w.writerow([sub, cat, ""])

# accounts_overrides.csv: accounts whose cost_type was assigned manually (parser gave None)
with open("accounts_overrides.csv", "w", newline="", encoding="utf-8") as fh:
    w = csv.writer(fh); w.writerow(["account_id", "subcategory", "cost_type"])
    for aid, name, sub, cat, ct, _ in rows:
        _, parsed_ct = parse(name)
        if parsed_ct is None and ct is not None:
            w.writerow([aid, sub, ct])

# account_map.csv: full audit
with open("account_map.csv", "w", newline="", encoding="utf-8") as fh:
    w = csv.writer(fh); w.writerow(["account_id", "name", "category", "subcategory", "cost_type", "flag"])
    for aid, name, sub, cat, ct, flag in sorted(rows, key=lambda r: (r[3], r[2])):
        w.writerow([aid, name, cat, sub, ct or "", flag])

cats = Counter(r[3] for r in rows)
unmapped = [r for r in rows if r[4] is None]
print(f"accounts: {len(rows)} | categories: {len(cats)} | unmapped (no cost_type, no spend): {len(unmapped)}")
print("--- categories (account count) ---")
for c, n in sorted(cats.items(), key=lambda x: -x[1]):
    print(f"  {n:>3}  {c}")
print(f"\nflagged review (best-guess): {sum(1 for r in rows if r[5]=='review')}")
print("wrote grouping.csv, accounts_overrides.csv, account_map.csv")
