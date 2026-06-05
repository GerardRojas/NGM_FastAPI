"""
RRULE-lite — server-side mirror of apps/hub-vite/src/features/calendar/rrule.ts.

Supports the subset used by the calendar module:
    FREQ=DAILY|WEEKLY|MONTHLY
    INTERVAL=N
    BYDAY=MO,TU,...      (WEEKLY only)
    UNTIL=YYYYMMDDTHHMMSSZ or ISO 8601
    COUNT=N

Public API:
    parse_rrule(text)                    -> dict | None
    expand_occurrences(rule, master_start, window_start, window_end) -> list[datetime]

All datetimes are timezone-aware (UTC). The caller passes tz-aware Pythons in,
and gets tz-aware Pythons back. Cap of 366 occurrences applied to bound work.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

_WEEKDAY_TO_NUM = {"SU": 6, "MO": 0, "TU": 1, "WE": 2, "TH": 3, "FR": 4, "SA": 5}
# In Python, weekday() returns 0=Mon..6=Sun. We use that consistently.

_HARD_CAP = 366


def parse_rrule(text: Optional[str]) -> Optional[Dict[str, Any]]:
    if not text:
        return None
    parts = [p.strip() for p in text.split(";") if p.strip()]
    if not parts:
        return None

    freq: Optional[str] = None
    interval = 1
    byday: List[int] = []
    until: Optional[datetime] = None
    count: Optional[int] = None

    for part in parts:
        if "=" not in part:
            continue
        key, val = part.split("=", 1)
        key = key.strip().upper()
        val = val.strip()
        if key == "FREQ":
            f = val.upper()
            if f in ("DAILY", "WEEKLY", "MONTHLY"):
                freq = f
        elif key == "INTERVAL":
            try:
                n = int(val)
                if n > 0:
                    interval = n
            except ValueError:
                pass
        elif key == "BYDAY":
            byday = [
                _WEEKDAY_TO_NUM[d.upper()]
                for d in (s.strip() for s in val.split(","))
                if d.upper() in _WEEKDAY_TO_NUM
            ]
        elif key == "UNTIL":
            until = _parse_ics_date(val)
        elif key == "COUNT":
            try:
                n = int(val)
                if n > 0:
                    count = n
            except ValueError:
                pass

    if not freq:
        return None
    return {"freq": freq, "interval": interval, "byday": byday, "until": until, "count": count}


def expand_occurrences(
    rule: Dict[str, Any],
    master_start: datetime,
    window_start: datetime,
    window_end: datetime,
) -> List[datetime]:
    if not rule:
        return []
    if window_end < window_start:
        return []
    if master_start.tzinfo is None or window_start.tzinfo is None or window_end.tzinfo is None:
        raise ValueError("expand_occurrences requires tz-aware datetimes")

    out: List[datetime] = []
    limit = min(_HARD_CAP, rule.get("count") or _HARD_CAP)
    until_cap = window_end
    rule_until = rule.get("until")
    if rule_until is not None and rule_until < until_cap:
        until_cap = rule_until

    interval = int(rule.get("interval") or 1)
    freq = rule["freq"]

    def push(dt: datetime) -> None:
        if dt >= window_start and dt <= until_cap:
            out.append(dt)

    if freq == "DAILY":
        cur = master_start
        i = 0
        while cur <= until_cap and len(out) < limit and i < _HARD_CAP:
            push(cur)
            cur = cur + timedelta(days=interval)
            i += 1
        return out

    if freq == "WEEKLY":
        # Anchor to start of master's week (Monday-anchored to match Python weekday()).
        anchor = master_start - timedelta(days=master_start.weekday())
        anchor = anchor.replace(hour=master_start.hour, minute=master_start.minute, second=0, microsecond=0)
        days = sorted(rule.get("byday") or []) or [master_start.weekday()]
        week_idx = 0
        emitted = 0
        while emitted < limit and week_idx < _HARD_CAP:
            week_start = anchor + timedelta(weeks=week_idx * interval)
            if week_start > until_cap:
                break
            for dow in days:
                occ = week_start + timedelta(days=dow)
                if occ < master_start:
                    continue
                if occ > until_cap:
                    break
                push(occ)
                emitted += 1
                if emitted >= limit:
                    break
            week_idx += 1
        return out

    if freq == "MONTHLY":
        day = master_start.day
        hour = master_start.hour
        minute = master_start.minute
        i = 0
        while i < _HARD_CAP and len(out) < limit:
            year = master_start.year
            month = master_start.month + i * interval
            year += (month - 1) // 12
            month = ((month - 1) % 12) + 1
            last_day = _last_day_of_month(year, month)
            occ = datetime(
                year, month, min(day, last_day), hour, minute, 0,
                tzinfo=master_start.tzinfo,
            )
            if occ > until_cap:
                break
            push(occ)
            i += 1
        return out

    return out


def next_occurrence(
    rule: Dict[str, Any],
    master_start: datetime,
    after: datetime,
    horizon_days: int = 60,
) -> Optional[datetime]:
    """Find the first occurrence strictly after `after` within `horizon_days`.
    Returns None if no occurrence falls in the window."""
    window_end = after + timedelta(days=horizon_days)
    for occ in expand_occurrences(rule, master_start, after, window_end):
        if occ > after:
            return occ
    return None


def _parse_ics_date(value: str) -> Optional[datetime]:
    if not value:
        return None
    if "-" in value:
        try:
            v = value.replace("Z", "+00:00")
            d = datetime.fromisoformat(v)
            if d.tzinfo is None:
                d = d.replace(tzinfo=timezone.utc)
            return d
        except ValueError:
            return None
    m = re.match(r"^(\d{4})(\d{2})(\d{2})(?:T(\d{2})(\d{2})(\d{2})Z?)?$", value)
    if not m:
        return None
    y, mo, d, h, mi, s = m.groups()
    return datetime(
        int(y), int(mo), int(d),
        int(h or 0), int(mi or 0), int(s or 0),
        tzinfo=timezone.utc,
    )


def _last_day_of_month(year: int, month: int) -> int:
    if month == 12:
        first_next = datetime(year + 1, 1, 1)
    else:
        first_next = datetime(year, month + 1, 1)
    return (first_next - timedelta(days=1)).day
