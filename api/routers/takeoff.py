# api/routers/takeoff.py
# ================================
# Takeoff Module API Router
# ================================
# CRUD for construction plan images, calibration data,
# and measurement annotations (line, area, count).

import logging
from typing import Optional, List
from datetime import datetime
import json

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from api.auth import get_current_user
from api.supabase_client import supabase

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/takeoff", tags=["Takeoff"])


# ============================================
# PYDANTIC MODELS
# ============================================

class PlanPageCreate(BaseModel):
    page_number: int = 1
    image_url: str
    image_width: int
    image_height: int
    thumbnail_url: Optional[str] = None


class PlanCreate(BaseModel):
    id: Optional[str] = None
    filename: str
    project_id: Optional[str] = None
    pages: List[PlanPageCreate] = []


class CalibrationUpdate(BaseModel):
    point1_x: float
    point1_y: float
    point2_x: float
    point2_y: float
    known_distance: float
    unit: str  # ft, in, m, cm, mm


class MeasurementPoint(BaseModel):
    x: float
    y: float


class MeasurementCreate(BaseModel):
    id: Optional[str] = None
    plan_id: str
    page_number: int = 1
    type: str  # line, area, count
    label: Optional[str] = ""
    points: List[MeasurementPoint] = []
    value: float = 0
    unit: str = "ft"
    color: Optional[str] = None


class MeasurementUpdate(BaseModel):
    label: Optional[str] = None
    value: Optional[float] = None


# ============================================
# PLANS
# ============================================

@router.post("/plans")
async def create_plan(body: PlanCreate, user=Depends(get_current_user)):
    """Create a plan record with its pages."""
    try:
        plan_data = {
            "filename": body.filename,
            "created_by": user.get("uid", ""),
        }
        if body.id:
            plan_data["id"] = body.id
        if body.project_id:
            plan_data["project_id"] = body.project_id

        result = supabase.table("takeoff_plans").insert(plan_data).execute()
        if not result.data:
            raise HTTPException(status_code=500, detail="Failed to create plan")

        plan = result.data[0]
        plan_id = plan["id"]

        # Insert pages
        pages_out = []
        for pg in body.pages:
            page_data = {
                "plan_id": plan_id,
                "page_number": pg.page_number,
                "image_url": pg.image_url,
                "image_width": pg.image_width,
                "image_height": pg.image_height,
                "thumbnail_url": pg.thumbnail_url,
            }
            pg_result = supabase.table("takeoff_plan_pages").insert(page_data).execute()
            if pg_result.data:
                pages_out.append(pg_result.data[0])

        plan["pages"] = pages_out
        return plan

    except HTTPException:
        raise
    except Exception as e:
        logger.error("[TAKEOFF] create_plan error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/plans")
async def list_plans(project_id: Optional[str] = None, user=Depends(get_current_user)):
    """List all plans, optionally filtered by project."""
    try:
        query = supabase.table("takeoff_plans").select("*").order("created_at", desc=True)
        if project_id:
            query = query.eq("project_id", project_id)

        result = query.execute()
        plans = result.data or []

        # Fetch pages for each plan
        plan_ids = [p["id"] for p in plans]
        if plan_ids:
            pages_result = supabase.table("takeoff_plan_pages") \
                .select("*") \
                .in_("plan_id", plan_ids) \
                .order("page_number") \
                .execute()
            pages_by_plan = {}
            for pg in (pages_result.data or []):
                pid = pg["plan_id"]
                if pid not in pages_by_plan:
                    pages_by_plan[pid] = []
                # Reconstruct calibration object if present
                if pg.get("cal_p1_x") is not None:
                    pg["calibration"] = {
                        "point1": {"x": pg["cal_p1_x"], "y": pg["cal_p1_y"]},
                        "point2": {"x": pg["cal_p2_x"], "y": pg["cal_p2_y"]},
                        "known_distance": pg["cal_distance"],
                        "unit": pg["cal_unit"],
                    }
                else:
                    pg["calibration"] = None
                pages_by_plan[pid].append(pg)

            for plan in plans:
                plan["pages"] = pages_by_plan.get(plan["id"], [])
        else:
            for plan in plans:
                plan["pages"] = []

        return plans

    except Exception as e:
        logger.error("[TAKEOFF] list_plans error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/plans/{plan_id}")
async def get_plan(plan_id: str, user=Depends(get_current_user)):
    """Get a single plan with pages and measurements."""
    try:
        result = supabase.table("takeoff_plans").select("*").eq("id", plan_id).execute()
        if not result.data:
            raise HTTPException(status_code=404, detail="Plan not found")

        plan = result.data[0]

        # Pages
        pages_result = supabase.table("takeoff_plan_pages") \
            .select("*").eq("plan_id", plan_id).order("page_number").execute()
        pages = pages_result.data or []
        for pg in pages:
            if pg.get("cal_p1_x") is not None:
                pg["calibration"] = {
                    "point1": {"x": pg["cal_p1_x"], "y": pg["cal_p1_y"]},
                    "point2": {"x": pg["cal_p2_x"], "y": pg["cal_p2_y"]},
                    "known_distance": pg["cal_distance"],
                    "unit": pg["cal_unit"],
                }
            else:
                pg["calibration"] = None
        plan["pages"] = pages

        # Measurements
        meas_result = supabase.table("takeoff_measurements") \
            .select("*").eq("plan_id", plan_id).order("created_at").execute()
        plan["measurements"] = meas_result.data or []

        return plan

    except HTTPException:
        raise
    except Exception as e:
        logger.error("[TAKEOFF] get_plan error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/plans/{plan_id}")
async def delete_plan(plan_id: str, user=Depends(get_current_user)):
    """Delete a plan and all its pages/measurements (cascades)."""
    try:
        # Delete from storage
        try:
            files = supabase.storage.from_("takeoff-plans").list(plan_id)
            if files:
                paths = [f"{plan_id}/{f['name']}" for f in files]
                if paths:
                    supabase.storage.from_("takeoff-plans").remove(paths)
        except Exception as e:
            logger.warning("[TAKEOFF] Storage cleanup error: %s", e)

        # Delete from DB (cascades to pages and measurements via FK)
        supabase.table("takeoff_plans").delete().eq("id", plan_id).execute()
        return {"success": True}

    except Exception as e:
        logger.error("[TAKEOFF] delete_plan error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


# ============================================
# CALIBRATION
# ============================================

@router.patch("/plans/{plan_id}/pages/{page_number}/calibration")
async def update_calibration(
    plan_id: str,
    page_number: int,
    body: CalibrationUpdate,
    user=Depends(get_current_user)
):
    """Save or update calibration data for a plan page."""
    try:
        result = supabase.table("takeoff_plan_pages") \
            .update({
                "cal_p1_x": body.point1_x,
                "cal_p1_y": body.point1_y,
                "cal_p2_x": body.point2_x,
                "cal_p2_y": body.point2_y,
                "cal_distance": body.known_distance,
                "cal_unit": body.unit,
            }) \
            .eq("plan_id", plan_id) \
            .eq("page_number", page_number) \
            .execute()

        if not result.data:
            raise HTTPException(status_code=404, detail="Page not found")

        return result.data[0]

    except HTTPException:
        raise
    except Exception as e:
        logger.error("[TAKEOFF] update_calibration error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


# ============================================
# MEASUREMENTS
# ============================================

@router.post("/measurements")
async def create_measurement(body: MeasurementCreate, user=Depends(get_current_user)):
    """Create a measurement annotation."""
    try:
        points_json = [{"x": p.x, "y": p.y} for p in body.points]

        data = {
            "plan_id": body.plan_id,
            "page_number": body.page_number,
            "type": body.type,
            "label": body.label or "",
            "points": json.dumps(points_json),
            "value": body.value,
            "unit": body.unit,
            "color": body.color,
            "created_by": user.get("uid", ""),
        }

        result = supabase.table("takeoff_measurements").insert(data).execute()
        if not result.data:
            raise HTTPException(status_code=500, detail="Failed to create measurement")

        meas = result.data[0]
        # Parse points back to list
        if isinstance(meas.get("points"), str):
            meas["points"] = json.loads(meas["points"])
        return meas

    except HTTPException:
        raise
    except Exception as e:
        logger.error("[TAKEOFF] create_measurement error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/measurements")
async def list_measurements(
    plan_id: str,
    page_number: Optional[int] = None,
    user=Depends(get_current_user)
):
    """List measurements for a plan, optionally filtered by page."""
    try:
        query = supabase.table("takeoff_measurements") \
            .select("*") \
            .eq("plan_id", plan_id) \
            .order("created_at")

        if page_number is not None:
            query = query.eq("page_number", page_number)

        result = query.execute()
        measurements = result.data or []

        # Parse points JSON
        for m in measurements:
            if isinstance(m.get("points"), str):
                m["points"] = json.loads(m["points"])

        return measurements

    except Exception as e:
        logger.error("[TAKEOFF] list_measurements error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/measurements/{meas_id}")
async def update_measurement(
    meas_id: str,
    body: MeasurementUpdate,
    user=Depends(get_current_user)
):
    """Update measurement label or value."""
    try:
        update_data = {}
        if body.label is not None:
            update_data["label"] = body.label
        if body.value is not None:
            update_data["value"] = body.value

        if not update_data:
            raise HTTPException(status_code=400, detail="Nothing to update")

        result = supabase.table("takeoff_measurements") \
            .update(update_data) \
            .eq("id", meas_id) \
            .execute()

        if not result.data:
            raise HTTPException(status_code=404, detail="Measurement not found")

        meas = result.data[0]
        if isinstance(meas.get("points"), str):
            meas["points"] = json.loads(meas["points"])
        return meas

    except HTTPException:
        raise
    except Exception as e:
        logger.error("[TAKEOFF] update_measurement error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/measurements/{meas_id}")
async def delete_measurement(meas_id: str, user=Depends(get_current_user)):
    """Delete a measurement."""
    try:
        supabase.table("takeoff_measurements").delete().eq("id", meas_id).execute()
        return {"success": True}
    except Exception as e:
        logger.error("[TAKEOFF] delete_measurement error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))
