"""
Pure helpers for the Analytics router (no DB, no request context). Extracted
from routers/analytics.py so that 3500-line file stays focused on endpoints and
these stay independently testable. Behaviour is identical — moved verbatim.
"""
from datetime import date
from typing import Any, Optional


def _safe_float(val) -> float:
    """Convert a value to float, defaulting to 0.0 on failure."""
    try:
        return float(val)
    except (TypeError, ValueError):
        return 0.0


def _round2(val: float) -> float:
    return round(val, 2)


def _parse_csv_list(value: Optional[str]) -> list[str]:
    """Parse a comma- or semicolon-separated query param into a stripped list.

    Drops empty tokens. Returns [] for None / blank input. Multi-value query
    params (?foo=a&foo=b) are not handled here -- the dashboard endpoint uses
    the csv form so a saved view can serialize trivially as a single string.
    """
    if not value:
        return []
    parts = [p.strip() for p in str(value).replace(";", ",").split(",")]
    return [p for p in parts if p]


def _parse_date(value: Optional[str]) -> Optional[date]:
    """Parse a YYYY-MM-DD prefix into a `date`. None on failure / empty input."""
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except (ValueError, TypeError):
        return None


def _in_date_range(txn_date: Optional[str],
                   date_from: Optional[date],
                   date_to: Optional[date]) -> bool:
    """True if *txn_date* (YYYY-MM-DD prefix) falls in [date_from, date_to]."""
    if not date_from and not date_to:
        return True
    if not txn_date or len(txn_date) < 10:
        return False
    try:
        d = date.fromisoformat(txn_date[:10])
    except (ValueError, TypeError):
        return False
    if date_from and d < date_from:
        return False
    if date_to and d > date_to:
        return False
    return True


def _company_pid_list(cset: Optional[set]) -> Optional[list]:
    """Turn a company project-id set into a list for `.in_()` filters. None stays
    None (no scope); an empty set becomes ['__none__'] so the filter matches no
    rows instead of silently returning everything."""
    if cset is None:
        return None
    return sorted(cset) or ["__none__"]


def _filter_workload_team(team: list[dict],
                          owner_ids: Optional[list[str]]) -> list[dict]:
    """Trim the pipeline's get_team_workload() output to the selected owners."""
    if not owner_ids:
        return team
    wanted = {str(o) for o in owner_ids if o}
    return [m for m in team if str(m.get("user_id") or "") in wanted]


def _odv_serialize(row: dict) -> dict:
    return {
        "view_id": str(row.get("view_id", "")),
        "name": row.get("name", ""),
        "filters": row.get("filters") or {},
        "is_default": bool(row.get("is_default")),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


def _odv_normalize_filters(raw: Any) -> dict:
    """
    Whitelist the filter keys we persist so a malformed payload can't leak
    arbitrary jsonb into the table. Lists are coerced to str-lists; dates are
    re-parsed and normalized back to ISO so we never store bad values.
    """
    if not isinstance(raw, dict):
        return {}
    out: dict = {}
    for key in ("project_ids", "owner_ids", "project_status", "task_status"):
        val = raw.get(key)
        if isinstance(val, list):
            out[key] = [str(v) for v in val if v not in (None, "")]
        elif isinstance(val, str):
            out[key] = _parse_csv_list(val)
        else:
            out[key] = []
    for key in ("date_from", "date_to"):
        d = _parse_date(raw.get(key))
        out[key] = d.isoformat() if d else None
    return out
