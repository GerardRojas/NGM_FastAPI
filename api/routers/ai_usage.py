"""
AI Usage Router — read-only aggregates over the `ai_usage` ledger.

Backs the IT > "AI Usage" page. All endpoints require a valid session; page
access is governed by role_permissions (module_key 'ai-usage', CEO/COO).

Costs are ESTIMATES from the static price table in api/services/ai_usage.py;
reconcile against the real OpenAI invoice.
"""
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, Query

from api.auth import get_current_user
from api.supabase_client import supabase
from api.services.ai_usage import PRICING

router = APIRouter(prefix="/ai-usage", tags=["ai-usage"])

# Safety cap on rows pulled for in-memory aggregation (table is append-only).
_MAX_ROWS = 50000


def _num(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _fetch_window(days: int) -> List[Dict[str, Any]]:
    since = datetime.now(timezone.utc) - timedelta(days=max(1, days))
    rows = (
        supabase.table("ai_usage")
        .select("created_at, feature, model, input_tokens, output_tokens, total_tokens, cost_usd, success")
        .gte("created_at", since.isoformat())
        .order("created_at", desc=True)
        .range(0, _MAX_ROWS - 1)
        .execute()
        .data
    ) or []
    return rows


@router.get("/summary")
async def summary(days: int = Query(30, ge=1, le=365),
                  current_user: dict = Depends(get_current_user)):
    """Totals + breakdowns (by model, by feature, daily) over the last N days."""
    rows = _fetch_window(days)

    totals = {"calls": 0, "input_tokens": 0, "output_tokens": 0,
              "total_tokens": 0, "cost_usd": 0.0, "failures": 0}
    by_model: Dict[str, Dict[str, float]] = defaultdict(lambda: {"calls": 0, "total_tokens": 0, "cost_usd": 0.0})
    by_feature: Dict[str, Dict[str, float]] = defaultdict(lambda: {"calls": 0, "total_tokens": 0, "cost_usd": 0.0})
    by_day: Dict[str, Dict[str, float]] = defaultdict(lambda: {"calls": 0, "total_tokens": 0, "cost_usd": 0.0})

    for r in rows:
        cost = _num(r.get("cost_usd"))
        tot = int(_num(r.get("total_tokens")))
        totals["calls"] += 1
        totals["input_tokens"] += int(_num(r.get("input_tokens")))
        totals["output_tokens"] += int(_num(r.get("output_tokens")))
        totals["total_tokens"] += tot
        totals["cost_usd"] += cost
        if not r.get("success", True):
            totals["failures"] += 1

        model = r.get("model") or "unknown"
        feature = r.get("feature") or "unknown"
        day = str(r.get("created_at") or "")[:10]

        for bucket, key in ((by_model, model), (by_feature, feature), (by_day, day)):
            bucket[key]["calls"] += 1
            bucket[key]["total_tokens"] += tot
            bucket[key]["cost_usd"] += cost

    def _flatten(bucket: Dict[str, Dict[str, float]], key_name: str, sort_by_key: bool = False):
        items = [
            {key_name: k, "calls": int(v["calls"]),
             "total_tokens": int(v["total_tokens"]), "cost_usd": round(v["cost_usd"], 6)}
            for k, v in bucket.items()
        ]
        if sort_by_key:
            return sorted(items, key=lambda x: x[key_name])
        return sorted(items, key=lambda x: x["cost_usd"], reverse=True)

    totals["cost_usd"] = round(totals["cost_usd"], 6)

    return {
        "range_days": days,
        "totals": totals,
        "by_model": _flatten(by_model, "model"),
        "by_feature": _flatten(by_feature, "feature"),
        "daily": _flatten(by_day, "date", sort_by_key=True),
        "pricing": {m: {"input_per_1m": p[0], "output_per_1m": p[1]} for m, p in PRICING.items()},
        "capped": len(rows) >= _MAX_ROWS,
    }


@router.get("/recent")
async def recent(limit: int = Query(50, ge=1, le=200),
                 current_user: dict = Depends(get_current_user)):
    """Most recent individual calls (for spot-checking)."""
    rows = (
        supabase.table("ai_usage")
        .select("created_at, feature, model, input_tokens, output_tokens, total_tokens, cost_usd, latency_ms, success")
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
        .data
    ) or []
    return {"items": rows}
