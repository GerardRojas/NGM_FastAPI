"""
Unified report engine — single source of truth for Budget vs Actuals and
P&L COGS computation.

This is a faithful Python port of the frontend's richer engine
(apps/hub-vite/src/features/budget-vs-actuals/services.ts `buildReport` /
`buildCategories`). Both the dedicated web pages' logic and Art's PDF handlers
should produce identical numbers and classification by going through this.

Richer than the legacy Art `process_report_data`:
  - Phase-B direct classification: a row's own (subcategory_id, cost_type) wins
    over the account overlay.
  - overlay-by-name bridge: rescues QBO budgets whose account_id lives in a
    different id space than expenses/overlay.
  - category ordering by the estimator's sort_order (not alphabetical).

Output shape is snake_case and identical to the legacy Art `process_report_data`
return, so the existing reportlab PDF generators consume it unchanged:
  { "rows": [...], "categories": [...], "totals": {...} }
"""

import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from api.supabase_client import supabase

logger = logging.getLogger(__name__)

TYPE_ORDER = ["material", "labor", "external_service", "change_order", "other_expenses"]
TYPE_LABEL = {
    "material": "Material",
    "labor": "Labor",
    "external_service": "External Service",
    "change_order": "Change Order",
    "other_expenses": "Other Expenses",
}
UNCATEGORIZED = "Uncategorized"

# Sentinels mirroring the TS Number.MAX_SAFE_INTEGER ranking.
_RANK_UNCATEGORIZED = 1 << 62
_RANK_UNRANKED = (1 << 62) - 1


def _num(value: Any) -> float:
    try:
        if value is None or value == "":
            return 0.0
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _str(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _norm_name(value: Any) -> str:
    """Normalized account/category name — the bridge key between budgets and
    expenses (which live in different id spaces). Lowercased, whitespace-collapsed."""
    return " ".join(_str(value).lower().split())


# Estimate-pushed budgets carry the estimator's category code as a name prefix
# (e.g. "C1 Plans & Permits", "C12 Exterior Finishes"). Strip a single leading
# short code token so the name can bridge to the NGM category taxonomy. Kept
# conservative — only a leading "C1 ", "C12 ", "12 ", "01-100 " style token —
# and the result is only ever USED when it exact-matches a real category name,
# so an over-strip can't invent a wrong mapping.
_CODE_PREFIX_RE = re.compile(r"^\s*[A-Za-z]{0,2}\d+(?:[.\-]\d+)*[\s.):\-]+")


def _strip_code(value: Any) -> str:
    return _CODE_PREFIX_RE.sub("", _str(value)).strip()


# Curated synonyms for local category names that appear in some imported
# estimates but aren't in the NGM category taxonomy. Keyed by normalized,
# code-stripped name -> canonical NGM category name. Small + explicit on purpose;
# extend as new imported estimates surface new local names. (Left out
# deliberately: generic "finishes materials" — too ambiguous to map blindly.)
_CATEGORY_ALIASES = {
    "texture": "Drywall",
    "drywall texture": "Drywall",
    "stucco": "Exterior Finishes",
    "plumbing material": "Rough Plumbing",
    "electrical exits": "Rough Electrical",
    "electrical material": "Rough Electrical",
    "windows & glass doors- material & labor": "Windows",
}


def _is_authorized(expense: Dict[str, Any]) -> bool:
    """Canonical 'authorized' definition shared everywhere: status == 'auth'."""
    return _str(expense.get("status")) == "auth"


def _all(table: str, cols: str) -> List[Dict[str, Any]]:
    """Paginated select-all (1000-row blocks)."""
    rows: List[Dict[str, Any]] = []
    start = 0
    while True:
        batch = supabase.table(table).select(cols).range(start, start + 999).execute().data or []
        rows += batch
        if len(batch) < 1000:
            break
        start += 1000
    return rows


def fetch_category_tree() -> Tuple[List[str], Dict[str, str], Dict[str, str]]:
    """Returns (order, subcategory_index, category_names):
      - order: normalized category names in the estimator's sort_order, used to
        order the report's categories the same way the estimator lists them.
      - subcategory_index: subcategory_id -> category name, the bridge that lets
        rows classify by their direct (subcategory_id, cost_type) triple.
      - category_names: normalized category name -> canonical name, the bridge
        that lets a row whose NAME is a category (e.g. estimate-pushed budgets
        like "C1 Plans & Permits") classify into that category by name.
    Empty fallbacks mean the report uses the overlay-by-account path exclusively."""
    order: List[str] = []
    subcategory_index: Dict[str, str] = {}
    category_names: Dict[str, str] = {}
    try:
        cats = _all("categories", "id, name, sort_order")
        cats.sort(key=lambda c: (c.get("sort_order") or 0, _str(c.get("name")).lower()))
        cat_name_by_id: Dict[str, str] = {}
        for c in cats:
            name = _str(c.get("name"))
            cat_name_by_id[c.get("id")] = name
            if name:
                order.append(_norm_name(name))
                category_names.setdefault(_norm_name(name), name)
        for s in _all("subcategories", "id, category_id, name"):
            sid = _str(s.get("id"))
            cat_name = cat_name_by_id.get(s.get("category_id"))
            if sid and cat_name:
                subcategory_index[sid] = cat_name
    except Exception as e:
        logger.warning("[REPORT] category tree unavailable: %s", e)
    return order, subcategory_index, category_names


def build_report(
    budgets: List[Dict[str, Any]],
    expenses: List[Dict[str, Any]],
    accounts: List[Dict[str, Any]],
    overlay: Optional[Dict[str, Dict[str, str]]] = None,
    category_order: Optional[List[str]] = None,
    subcategory_index: Optional[Dict[str, str]] = None,
    category_names: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """Group budgets and authorized expenses by account (flat `rows`) AND by the
    Category -> cost_type hierarchy (`categories`). For P&L pass budgets=[]."""
    overlay = overlay or {}
    category_order = category_order or []
    subcategory_index = subcategory_index or {}
    category_names = category_names or {}

    def account_name(account_id: Any, provided_name: Any) -> str:
        name = _str(provided_name)
        if name:
            return name
        aid = _str(account_id)
        if aid:
            for acc in accounts:
                if _str(acc.get("account_id") or acc.get("id")) == aid:
                    return _str(acc.get("Name") or acc.get("account_name")) or "Unknown Account"
        return "Unknown Account"

    def key_of(account_id: Any, name: str) -> str:
        return _str(account_id) or f"name:{name}"

    budgets_by_account: Dict[str, float] = {}
    expenses_by_account: Dict[str, float] = {}
    budgets_by_id: Dict[str, float] = {}
    expenses_by_id: Dict[str, float] = {}
    id_name: Dict[str, str] = {}
    id_subcategory_id: Dict[str, str] = {}
    id_cost_type: Dict[str, str] = {}

    def record_classification(key: str, subcategory_id: Any, cost_type: Any) -> None:
        sid = _str(subcategory_id)
        ct = _str(cost_type)
        if sid and key not in id_subcategory_id:
            id_subcategory_id[key] = sid
        if ct and key not in id_cost_type:
            id_cost_type[key] = ct

    for budget in budgets:
        name = account_name(budget.get("account_id"), budget.get("account_name"))
        amount = _num(budget.get("amount_sum"))
        budgets_by_account[name] = budgets_by_account.get(name, 0.0) + amount
        key = key_of(budget.get("account_id"), name)
        budgets_by_id[key] = budgets_by_id.get(key, 0.0) + amount
        id_name[key] = name
        record_classification(key, budget.get("subcategory_id"), budget.get("cost_type"))

    for expense in expenses:
        if not _is_authorized(expense):
            continue
        name = account_name(expense.get("account_id"), expense.get("account_name"))
        amount = _num(expense.get("Amount") if expense.get("Amount") is not None else expense.get("amount"))
        expenses_by_account[name] = expenses_by_account.get(name, 0.0) + amount
        key = key_of(expense.get("account_id"), name)
        expenses_by_id[key] = expenses_by_id.get(key, 0.0) + amount
        id_name[key] = name
        record_classification(key, expense.get("subcategory_id"), expense.get("cost_type"))

    def account_number(name: str) -> int:
        for acc in accounts:
            if _str(acc.get("Name") or acc.get("account_name")) == name:
                num = acc.get("AcctNum")
                return int(num) if num not in (None, "") else 99999
        return 99999

    rows: List[Dict[str, Any]] = []
    for name in set(list(budgets_by_account.keys()) + list(expenses_by_account.keys())):
        budget_amount = budgets_by_account.get(name, 0.0)
        actual_amount = expenses_by_account.get(name, 0.0)
        rows.append({
            "account": name,
            "account_number": account_number(name),
            "budget": round(budget_amount, 2),
            "actual": round(actual_amount, 2),
            "balance": round(budget_amount - actual_amount, 2),
            "percent_of_budget": round((actual_amount / budget_amount * 100) if budget_amount > 0 else 0, 2),
        })
    rows.sort(key=lambda r: (r["account_number"], r["account"]))

    # Bridge the overlay (keyed by internal account UUID) to normalized account
    # names so QBO budgets (account_id in a different id space) resolve into the
    # same categories as the expenses they contrast with.
    overlay_by_name: Dict[str, Dict[str, str]] = {}
    for account_id, val in overlay.items():
        info = next((a for a in accounts if _str(a.get("account_id") or a.get("id")) == account_id), None)
        nm = _norm_name((info or {}).get("Name") or (info or {}).get("account_name"))
        if nm and nm not in overlay_by_name:
            overlay_by_name[nm] = val

    categories = _build_categories(
        budgets_by_id, expenses_by_id, id_name, overlay, overlay_by_name,
        category_order, id_subcategory_id, id_cost_type, subcategory_index,
        category_names,
    )

    total_budget = sum(r["budget"] for r in rows)
    total_actual = sum(r["actual"] for r in rows)
    return {
        "rows": rows,
        "categories": categories,
        "totals": {
            "budget": round(total_budget, 2),
            "actual": round(total_actual, 2),
            "balance": round(total_budget - total_actual, 2),
            "percent_of_budget": round((total_actual / total_budget * 100) if total_budget > 0 else 0, 2),
        },
    }


def _build_categories(
    budgets_by_id: Dict[str, float],
    expenses_by_id: Dict[str, float],
    id_name: Dict[str, str],
    overlay: Dict[str, Dict[str, str]],
    overlay_by_name: Dict[str, Dict[str, str]],
    category_order: List[str],
    id_subcategory_id: Dict[str, str],
    id_cost_type: Dict[str, str],
    subcategory_index: Dict[str, str],
    category_names: Dict[str, str],
) -> List[Dict[str, Any]]:
    groups: Dict[str, Dict[str, Dict[str, Any]]] = {}

    for key in set(list(budgets_by_id.keys()) + list(expenses_by_id.keys())):
        # Classification source priority:
        #   1. Direct (subcategory_id, cost_type) on the row — Phase B producers.
        #   2. Overlay by raw account_id (matches expenses + dual-write source).
        #   3. Overlay by account name (rescues QBO budgets in a different id space).
        #   4. Name IS a category (rescues estimate-pushed budgets whose name is
        #      the category itself, e.g. "C1 Plans & Permits" — exact match on the
        #      raw or code-stripped name against the category taxonomy).
        # Anything unresolved lands in Uncategorized so totals reconcile.
        direct_sub = id_subcategory_id.get(key)
        direct_cost_type = id_cost_type.get(key)
        direct_hit = subcategory_index.get(direct_sub) if direct_sub else None
        ov = overlay.get(key) or overlay_by_name.get(_norm_name(id_name.get(key)))
        raw_name = id_name.get(key, "")
        stripped_norm = _norm_name(_strip_code(raw_name))
        name_cat = (
            category_names.get(_norm_name(raw_name))
            or category_names.get(stripped_norm)
            or category_names.get(_norm_name(_CATEGORY_ALIASES.get(stripped_norm, "")))
        )

        if direct_hit and direct_cost_type:
            category = direct_hit or UNCATEGORIZED
            cost_type = direct_cost_type
            line_key = cost_type or "_"
            label = TYPE_LABEL.get(cost_type, cost_type or "—")
        elif ov:
            category = ov["category"]
            cost_type = ov.get("cost_type", "")
            line_key = cost_type or "_"
            label = TYPE_LABEL.get(cost_type, cost_type or "—")
        elif name_cat:
            category = name_cat
            cost_type = id_cost_type.get(key, "") or ""
            line_key = cost_type or "_"
            label = TYPE_LABEL.get(cost_type, cost_type or "—")
        else:
            category = UNCATEGORIZED
            cost_type = ""
            label = id_name.get(key, "Unknown Account")
            line_key = f"acct:{label}"

        line = groups.setdefault(category, {}).setdefault(
            line_key, {"label": label, "cost_type": cost_type, "budget": 0.0, "actual": 0.0})
        line["budget"] += budgets_by_id.get(key, 0.0)
        line["actual"] += expenses_by_id.get(key, 0.0)

    cats: List[Dict[str, Any]] = []
    for name, lines in groups.items():
        line_list = []
        for a in lines.values():
            b, ac = a["budget"], a["actual"]
            line_list.append({
                "label": a["label"], "cost_type": a["cost_type"],
                "budget": round(b, 2), "actual": round(ac, 2),
                "balance": round(b - ac, 2),
                "percent_of_budget": round((ac / b * 100) if b > 0 else 0, 2),
            })
        line_list.sort(key=lambda l: (
            TYPE_ORDER.index(l["cost_type"]) if l["cost_type"] in TYPE_ORDER else 99,
            l["label"].lower()))
        b = sum(l["budget"] for l in line_list)
        ac = sum(l["actual"] for l in line_list)
        cats.append({
            "name": name, "budget": round(b, 2), "actual": round(ac, 2),
            "balance": round(b - ac, 2),
            "percent_of_budget": round((ac / b * 100) if b > 0 else 0, 2),
            "lines": line_list,
        })

    def rank_of(name: str) -> int:
        if name == UNCATEGORIZED:
            return _RANK_UNCATEGORIZED
        try:
            i = category_order.index(_norm_name(name))
        except ValueError:
            i = -1
        return _RANK_UNRANKED if i == -1 else i

    cats.sort(key=lambda c: (rank_of(c["name"]), c["name"].lower()))
    return cats
