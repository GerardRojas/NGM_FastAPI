"""
OCR Metrics Router
Endpoints for retrieving aggregated OCR extraction metrics
across receipt_scanner, Daneel, and Andrew agents.
"""

import logging
from fastapi import APIRouter, Query
from typing import Optional
from datetime import datetime, timezone, timedelta

from api.supabase_client import supabase

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ocr-metrics", tags=["ocr-metrics"])


@router.get("/summary")
async def get_ocr_summary(
    days: int = Query(30, ge=1, le=365, description="Lookback window in days"),
    project_id: Optional[str] = Query(None, description="Filter by project"),
):
    """
    Aggregated OCR metrics summary for the dashboard widget.
    Returns breakdown by agent and extraction method.
    """
    empty_response = {
        "period_days": days,
        "total_scans": 0,
        "success_rate": 0,
        "pdfplumber_count": 0,
        "vision_count": 0,
        "error_count": 0,
        "pdfplumber_pct": 0,
        "vision_pct": 0,
        "avg_confidence": None,
        "tax_detected_count": 0,
        "match_types": {"total": 0, "subtotal": 0, "mismatch": 0, "none": 0},
        "by_agent": {},
        "by_method": {},
        "by_agent_method": {},
    }

    try:
        since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

        query = supabase.table("ocr_metrics") \
            .select("*") \
            .gte("created_at", since) \
            .order("created_at", desc=True)

        if project_id:
            query = query.eq("project_id", project_id)

        result = query.execute()
        rows = result.data or []
    except Exception as e:
        logger.error(f"[ocr-metrics] Failed to query ocr_metrics table: {e}")
        return empty_response

    # Aggregate
    total = len(rows)
    if total == 0:
        return empty_response

    by_agent = {}
    by_method = {}
    by_agent_method = {}
    success_count = 0
    fail_count = 0
    tax_detected_count = 0
    total_confidence = 0
    confidence_count = 0
    match_types = {"total": 0, "subtotal": 0, "mismatch": 0, "none": 0}

    for row in rows:
        agent = row.get("agent", "unknown")
        method = row.get("extraction_method", "unknown")
        success = row.get("success", True)

        by_agent[agent] = by_agent.get(agent, 0) + 1
        by_method[method] = by_method.get(method, 0) + 1

        key = f"{agent}_{method}"
        by_agent_method[key] = by_agent_method.get(key, 0) + 1

        if success:
            success_count += 1
        else:
            fail_count += 1

        if row.get("tax_detected"):
            tax_detected_count += 1

        conf = row.get("confidence")
        if conf is not None:
            total_confidence += conf
            confidence_count += 1

        mt = row.get("total_match_type")
        if mt in match_types:
            match_types[mt] += 1
        elif mt and mt != "none":
            match_types["mismatch"] = match_types.get("mismatch", 0) + 1

    pdfplumber_count = by_method.get("pdfplumber", 0)
    vision_count = sum(v for k, v in by_method.items() if k in ("vision", "vision_direct"))
    error_count = fail_count

    return {
        "period_days": days,
        "total_scans": total,
        "success_rate": round(success_count / total * 100, 1) if total else 0,
        "pdfplumber_count": pdfplumber_count,
        "vision_count": vision_count,
        "error_count": error_count,
        "pdfplumber_pct": round(pdfplumber_count / total * 100, 1) if total else 0,
        "vision_pct": round(vision_count / total * 100, 1) if total else 0,
        "avg_confidence": round(total_confidence / confidence_count, 1) if confidence_count else None,
        "tax_detected_count": tax_detected_count,
        "match_types": match_types,
        "by_agent": by_agent,
        "by_method": by_method,
        "by_agent_method": by_agent_method,
    }


@router.get("/recent")
async def get_recent_metrics(
    limit: int = Query(50, ge=1, le=200),
    agent: Optional[str] = Query(None),
    method: Optional[str] = Query(None),
):
    """
    Recent OCR metric entries for detailed inspection.
    """
    try:
        query = supabase.table("ocr_metrics") \
            .select("*") \
            .order("created_at", desc=True) \
            .limit(limit)

        if agent:
            query = query.eq("agent", agent)
        if method:
            query = query.eq("extraction_method", method)

        result = query.execute()
        return {"items": result.data or [], "count": len(result.data or [])}
    except Exception as e:
        logger.error(f"[ocr-metrics] Failed to query recent metrics: {e}")
        return {"items": [], "count": 0}
