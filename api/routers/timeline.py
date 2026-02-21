"""
Timeline Manager Router
Handles project phases and milestones for Gantt-style timeline views.
"""

import logging
import csv
import io
import re
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, Depends, UploadFile, File, Query
from pydantic import BaseModel

from api.auth import get_current_user
from api.supabase_client import supabase

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/timeline", tags=["Timeline"])


# ================================
# MODELS
# ================================

class PhaseCreate(BaseModel):
    phase_name: str
    phase_order: int = 0
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    status: Optional[str] = "pending"
    progress_pct: Optional[float] = 0
    color: Optional[str] = "#3ecf8e"
    notes: Optional[str] = None


class PhaseUpdate(BaseModel):
    phase_name: Optional[str] = None
    phase_order: Optional[int] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    actual_start: Optional[str] = None
    actual_end: Optional[str] = None
    status: Optional[str] = None
    progress_pct: Optional[float] = None
    color: Optional[str] = None
    notes: Optional[str] = None


class MilestoneCreate(BaseModel):
    milestone_name: str
    due_date: Optional[str] = None
    phase_id: Optional[str] = None
    status: Optional[str] = "pending"
    notes: Optional[str] = None


class MilestoneUpdate(BaseModel):
    milestone_name: Optional[str] = None
    due_date: Optional[str] = None
    completed_date: Optional[str] = None
    phase_id: Optional[str] = None
    status: Optional[str] = None
    notes: Optional[str] = None


# ================================
# PHASES
# ================================

@router.get("/projects/{project_id}/phases")
async def list_phases(
    project_id: str,
    current_user: dict = Depends(get_current_user),
):
    """List all phases for a project, ordered by phase_order."""
    try:
        result = (
            supabase
            .table("project_phases")
            .select("*")
            .eq("project_id", project_id)
            .order("phase_order")
            .execute()
        )
        return result.data or []

    except Exception as e:
        logger.error("Error listing phases for project %s: %s", project_id, e)
        raise HTTPException(status_code=500, detail=f"Error listing phases: {e}")


@router.post("/projects/{project_id}/phases", status_code=201)
async def create_phase(
    project_id: str,
    payload: PhaseCreate,
    current_user: dict = Depends(get_current_user),
):
    """Create a new phase for a project."""
    try:
        data = payload.dict(exclude_none=True)
        data["project_id"] = project_id

        result = (
            supabase
            .table("project_phases")
            .insert(data)
            .execute()
        )

        if not result.data:
            raise HTTPException(status_code=500, detail="Phase insert returned no data")

        return {"message": "Phase created", "data": result.data[0]}

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error creating phase for project %s: %s", project_id, e)
        raise HTTPException(status_code=500, detail=f"Error creating phase: {e}")


@router.patch("/phases/{phase_id}")
async def update_phase(
    phase_id: str,
    payload: PhaseUpdate,
    current_user: dict = Depends(get_current_user),
):
    """Update phase fields (only provided fields are changed)."""
    try:
        update_data = {k: v for k, v in payload.dict().items() if v is not None}

        if not update_data:
            raise HTTPException(status_code=400, detail="No fields to update")

        # Validate status if provided
        valid_statuses = {"pending", "in_progress", "completed", "delayed"}
        if "status" in update_data and update_data["status"] not in valid_statuses:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid status. Must be one of: {', '.join(sorted(valid_statuses))}",
            )

        # Validate progress_pct range
        if "progress_pct" in update_data:
            pct = update_data["progress_pct"]
            if pct < 0 or pct > 100:
                raise HTTPException(status_code=400, detail="progress_pct must be between 0 and 100")

        update_data["updated_at"] = datetime.utcnow().isoformat()

        result = (
            supabase
            .table("project_phases")
            .update(update_data)
            .eq("phase_id", phase_id)
            .execute()
        )

        if not result.data:
            raise HTTPException(status_code=404, detail="Phase not found")

        return {"message": "Phase updated", "data": result.data[0]}

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error updating phase %s: %s", phase_id, e)
        raise HTTPException(status_code=500, detail=f"Error updating phase: {e}")


@router.delete("/phases/{phase_id}")
async def delete_phase(
    phase_id: str,
    current_user: dict = Depends(get_current_user),
):
    """Delete a phase (milestones referencing it will have phase_id set to NULL)."""
    try:
        result = (
            supabase
            .table("project_phases")
            .delete()
            .eq("phase_id", phase_id)
            .execute()
        )

        if not result.data:
            raise HTTPException(status_code=404, detail="Phase not found")

        return {"message": "Phase deleted"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error deleting phase %s: %s", phase_id, e)
        raise HTTPException(status_code=500, detail=f"Error deleting phase: {e}")


# ================================
# MILESTONES
# ================================

@router.get("/projects/{project_id}/milestones")
async def list_milestones(
    project_id: str,
    current_user: dict = Depends(get_current_user),
):
    """List all milestones for a project, ordered by due_date."""
    try:
        result = (
            supabase
            .table("project_milestones")
            .select("*")
            .eq("project_id", project_id)
            .order("due_date")
            .execute()
        )
        return result.data or []

    except Exception as e:
        logger.error("Error listing milestones for project %s: %s", project_id, e)
        raise HTTPException(status_code=500, detail=f"Error listing milestones: {e}")


@router.post("/projects/{project_id}/milestones", status_code=201)
async def create_milestone(
    project_id: str,
    payload: MilestoneCreate,
    current_user: dict = Depends(get_current_user),
):
    """Create a new milestone for a project."""
    try:
        data = payload.dict(exclude_none=True)
        data["project_id"] = project_id

        result = (
            supabase
            .table("project_milestones")
            .insert(data)
            .execute()
        )

        if not result.data:
            raise HTTPException(status_code=500, detail="Milestone insert returned no data")

        return {"message": "Milestone created", "data": result.data[0]}

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error creating milestone for project %s: %s", project_id, e)
        raise HTTPException(status_code=500, detail=f"Error creating milestone: {e}")


@router.patch("/milestones/{milestone_id}")
async def update_milestone(
    milestone_id: str,
    payload: MilestoneUpdate,
    current_user: dict = Depends(get_current_user),
):
    """Update milestone fields (only provided fields are changed)."""
    try:
        update_data = {k: v for k, v in payload.dict().items() if v is not None}

        if not update_data:
            raise HTTPException(status_code=400, detail="No fields to update")

        # Validate status if provided
        valid_statuses = {"pending", "completed", "overdue"}
        if "status" in update_data and update_data["status"] not in valid_statuses:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid status. Must be one of: {', '.join(sorted(valid_statuses))}",
            )

        update_data["updated_at"] = datetime.utcnow().isoformat()

        result = (
            supabase
            .table("project_milestones")
            .update(update_data)
            .eq("milestone_id", milestone_id)
            .execute()
        )

        if not result.data:
            raise HTTPException(status_code=404, detail="Milestone not found")

        return {"message": "Milestone updated", "data": result.data[0]}

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error updating milestone %s: %s", milestone_id, e)
        raise HTTPException(status_code=500, detail=f"Error updating milestone: {e}")


@router.delete("/milestones/{milestone_id}")
async def delete_milestone(
    milestone_id: str,
    current_user: dict = Depends(get_current_user),
):
    """Delete a milestone."""
    try:
        result = (
            supabase
            .table("project_milestones")
            .delete()
            .eq("milestone_id", milestone_id)
            .execute()
        )

        if not result.data:
            raise HTTPException(status_code=404, detail="Milestone not found")

        return {"message": "Milestone deleted"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error deleting milestone %s: %s", milestone_id, e)
        raise HTTPException(status_code=500, detail=f"Error deleting milestone: {e}")


# ================================
# SUMMARY
# ================================

@router.get("/projects/{project_id}/summary")
async def project_timeline_summary(
    project_id: str,
    current_user: dict = Depends(get_current_user),
):
    """
    Lightweight summary for the Projects tab:
    - Phase counts by status
    - Milestone counts by status
    - Phase list with key fields
    - Next 5 upcoming milestones
    - Overall progress percentage
    """
    try:
        # Fetch phases
        phases_resp = (
            supabase
            .table("project_phases")
            .select("phase_id, phase_name, status, progress_pct, start_date, end_date, phase_order")
            .eq("project_id", project_id)
            .order("phase_order")
            .execute()
        )
        phases = phases_resp.data or []

        # Fetch milestones
        milestones_resp = (
            supabase
            .table("project_milestones")
            .select("milestone_name, due_date, status, phase_id")
            .eq("project_id", project_id)
            .order("due_date")
            .execute()
        )
        milestones = milestones_resp.data or []

        # Phase counts
        total_phases = len(phases)
        completed_phases = sum(1 for p in phases if p.get("status") == "completed")
        in_progress_phases = sum(1 for p in phases if p.get("status") == "in_progress")

        # Build phase_id → phase_name map for milestone enrichment
        phase_name_map: dict[str, str] = {}
        for p in phases:
            pid = p.get("phase_id") or p.get("id") or ""
            if pid:
                phase_name_map[str(pid)] = p.get("phase_name", "")

        # Milestone counts
        total_milestones = len(milestones)
        completed_milestones = sum(1 for m in milestones if m.get("status") == "completed")
        overdue_milestones = sum(1 for m in milestones if m.get("status") == "overdue")

        # Upcoming milestones (next 5 not completed) — enrich with phase_name
        upcoming_milestones = []
        for m in milestones:
            if m.get("status") != "completed":
                enriched = dict(m)
                pid = str(m.get("phase_id") or "")
                enriched["phase_name"] = phase_name_map.get(pid, "")
                upcoming_milestones.append(enriched)
                if len(upcoming_milestones) >= 5:
                    break

        # Overall progress (average of phase progress_pct)
        if total_phases > 0:
            progress_values = [float(p.get("progress_pct") or 0) for p in phases]
            overall_progress_pct = round(sum(progress_values) / total_phases, 2)
        else:
            overall_progress_pct = 0

        return {
            "total_phases": total_phases,
            "completed_phases": completed_phases,
            "in_progress_phases": in_progress_phases,
            "total_milestones": total_milestones,
            "completed_milestones": completed_milestones,
            "overdue_milestones": overdue_milestones,
            "phases": phases,
            "upcoming_milestones": upcoming_milestones,
            "overall_progress_pct": overall_progress_pct,
        }

    except Exception as e:
        logger.error("Error building timeline summary for project %s: %s", project_id, e)
        raise HTTPException(status_code=500, detail=f"Error building timeline summary: {e}")


# ================================
# IMPORT — MS Project (XML / CSV)
# ================================

PHASE_COLORS = [
    "#3ecf8e", "#6366f1", "#f59e0b", "#ef4444",
    "#06b6d4", "#8b5cf6", "#ec4899", "#10b981",
]


def _status_from_pct(pct: float) -> str:
    """Derive status string from percent-complete value."""
    if pct >= 100:
        return "completed"
    if pct > 0:
        return "in_progress"
    return "pending"


def _parse_date(raw: str | None) -> str | None:
    """
    Try to extract a YYYY-MM-DD date from various formats.
    Returns None when the value is empty or unparseable.
    """
    if not raw:
        return None
    raw = raw.strip()
    if not raw:
        return None

    # ISO datetime — just take first 10 chars
    if re.match(r"^\d{4}-\d{2}-\d{2}", raw):
        return raw[:10]

    # Try common date formats
    for fmt in ("%m/%d/%Y", "%d/%m/%Y", "%m/%d/%y", "%d/%m/%y",
                "%Y/%m/%d", "%m-%d-%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue

    return None


def _strip_ns(xml_bytes: bytes) -> bytes:
    """Remove XML namespace declarations so element tags are plain names."""
    # Remove xmlns="..." (default namespace) — keeps the document parseable
    return re.sub(rb'\s+xmlns="[^"]+"', b"", xml_bytes, count=1)


def _parse_xml(content: bytes) -> tuple[list[dict], list[dict]]:
    """
    Parse MS Project XML into (phases_list, milestones_list).
    Each dict is ready for DB insert (minus project_id and color).
    """
    cleaned = _strip_ns(content)
    root = ET.fromstring(cleaned)

    tasks_el = root.find("Tasks")
    if tasks_el is None:
        return [], []

    phases: list[dict] = []
    milestones: list[dict] = []
    # Map UID -> index in phases list (for linking milestones)
    uid_to_phase_idx: dict[str, int] = {}
    last_summary_uid: str | None = None
    phase_order = 0

    for task in tasks_el.findall("Task"):
        uid = (task.findtext("UID") or "").strip()
        name = (task.findtext("Name") or "").strip()
        outline = int(task.findtext("OutlineLevel") or "0")
        is_summary = (task.findtext("Summary") or "0").strip() == "1"
        is_milestone = (task.findtext("Milestone") or "0").strip() == "1"
        pct_raw = task.findtext("PercentComplete") or "0"
        start_raw = task.findtext("Start")
        finish_raw = task.findtext("Finish")

        if outline == 0:
            # Project-root row — skip
            continue

        if not name:
            continue

        try:
            pct = float(pct_raw)
        except ValueError:
            pct = 0.0

        start_date = _parse_date(start_raw)
        end_date = _parse_date(finish_raw)
        status = _status_from_pct(pct)

        if is_milestone:
            milestones.append({
                "milestone_name": name,
                "due_date": end_date or start_date,
                "status": "completed" if pct >= 100 else "pending",
                "completed_date": (end_date or start_date) if pct >= 100 else None,
                "notes": None,
                "_parent_uid": last_summary_uid,
            })
        else:
            # Summary tasks and regular subtasks both become phases
            phase_order += 1
            phases.append({
                "phase_name": name,
                "phase_order": phase_order,
                "start_date": start_date,
                "end_date": end_date,
                "status": status,
                "progress_pct": pct,
                "notes": None,
            })
            uid_to_phase_idx[uid] = len(phases) - 1

            if is_summary:
                last_summary_uid = uid

    # Attach _parent_phase_idx to milestones so caller can link after insert
    for ms in milestones:
        parent_uid = ms.pop("_parent_uid", None)
        if parent_uid and parent_uid in uid_to_phase_idx:
            ms["_parent_phase_idx"] = uid_to_phase_idx[parent_uid]
        else:
            ms["_parent_phase_idx"] = None

    return phases, milestones


def _find_col(headers: list[str], *candidates: str) -> int | None:
    """Return index of the first header matching any candidate (case-insensitive)."""
    lower_headers = [h.lower().strip() for h in headers]
    for c in candidates:
        cl = c.lower()
        for idx, h in enumerate(lower_headers):
            if cl in h:
                return idx
    return None


def _truthy(val: str | None) -> bool:
    """Check if a CSV cell value represents a truthy flag."""
    if not val:
        return False
    return val.strip().lower() in ("1", "yes", "true", "si", "sí")


def _parse_csv(content: str) -> tuple[list[dict], list[dict]]:
    """
    Parse MS Project CSV export into (phases_list, milestones_list).
    """
    # Detect delimiter
    first_line = content.split("\n", 1)[0]
    delimiter = ";" if first_line.count(";") > first_line.count(",") else ","

    reader = csv.reader(io.StringIO(content), delimiter=delimiter)
    rows = list(reader)

    if len(rows) < 2:
        return [], []

    headers = rows[0]

    col_name = _find_col(headers, "name", "task name", "task_name", "nombre")
    col_start = _find_col(headers, "start", "inicio")
    col_finish = _find_col(headers, "finish", "end", "fin")
    col_pct = _find_col(headers, "% complete", "percent_complete", "percent complete", "%complete", "% completado")
    col_outline = _find_col(headers, "outline level", "outline_level", "nivel")
    col_milestone = _find_col(headers, "milestone", "hito")
    col_summary = _find_col(headers, "summary", "resumen")
    col_notes = _find_col(headers, "notes", "notas")

    if col_name is None:
        raise ValueError("Cannot find 'Name' column in CSV headers")

    phases: list[dict] = []
    milestones: list[dict] = []
    last_summary_row_idx: int | None = None
    uid_to_phase_idx: dict[int, int] = {}
    phase_order = 0

    for row_idx, row in enumerate(rows[1:], start=1):
        if len(row) <= col_name:
            continue

        name = row[col_name].strip()
        if not name:
            continue

        # Outline level
        outline = 0
        if col_outline is not None and col_outline < len(row):
            try:
                outline = int(row[col_outline].strip())
            except ValueError:
                outline = 1

        if outline == 0:
            continue

        # Percent complete
        pct = 0.0
        if col_pct is not None and col_pct < len(row):
            raw_pct = row[col_pct].strip().replace("%", "")
            try:
                pct = float(raw_pct)
            except ValueError:
                pct = 0.0

        # Dates
        start_date = _parse_date(row[col_start] if col_start is not None and col_start < len(row) else None)
        end_date = _parse_date(row[col_finish] if col_finish is not None and col_finish < len(row) else None)

        # Flags
        is_milestone = False
        if col_milestone is not None and col_milestone < len(row):
            is_milestone = _truthy(row[col_milestone])

        is_summary = False
        if col_summary is not None and col_summary < len(row):
            is_summary = _truthy(row[col_summary])

        # Notes
        notes = None
        if col_notes is not None and col_notes < len(row):
            notes = row[col_notes].strip() or None

        status = _status_from_pct(pct)

        if is_milestone:
            milestones.append({
                "milestone_name": name,
                "due_date": end_date or start_date,
                "status": "completed" if pct >= 100 else "pending",
                "completed_date": (end_date or start_date) if pct >= 100 else None,
                "notes": notes,
                "_parent_phase_idx": uid_to_phase_idx.get(last_summary_row_idx),
            })
        else:
            phase_order += 1
            phases.append({
                "phase_name": name,
                "phase_order": phase_order,
                "start_date": start_date,
                "end_date": end_date,
                "status": status,
                "progress_pct": pct,
                "notes": notes,
            })
            uid_to_phase_idx[row_idx] = len(phases) - 1

            if is_summary:
                last_summary_row_idx = row_idx

    # Strip internal key from milestones that had no parent
    for ms in milestones:
        if "_parent_phase_idx" not in ms:
            ms["_parent_phase_idx"] = None

    return phases, milestones


@router.post("/projects/{project_id}/import")
async def import_timeline(
    project_id: str,
    file: UploadFile = File(...),
    mode: str = Query("replace", pattern="^(replace|append)$"),
    current_user: dict = Depends(get_current_user),
):
    """
    Import phases and milestones from a Microsoft Project export file.
    Supported formats: .xml (MS Project XML) and .csv.
    Mode: 'replace' deletes existing data first; 'append' adds to it.
    """
    filename = (file.filename or "").lower()
    if not (filename.endswith(".xml") or filename.endswith(".csv")):
        raise HTTPException(
            status_code=400,
            detail="Unsupported file format. Please upload an .xml or .csv file exported from Microsoft Project.",
        )

    # ── Read file content ──────────────────────────────────────────
    raw_bytes = await file.read()
    try:
        content_str = raw_bytes.decode("utf-8")
    except UnicodeDecodeError:
        content_str = raw_bytes.decode("latin-1")

    # ── Parse ──────────────────────────────────────────────────────
    try:
        if filename.endswith(".xml"):
            phases, milestones = _parse_xml(raw_bytes)
        else:
            phases, milestones = _parse_csv(content_str)
    except Exception as exc:
        logger.error("Failed to parse imported file %s: %s", file.filename, exc)
        raise HTTPException(
            status_code=400,
            detail=f"Failed to parse file: {exc}",
        )

    if not phases and not milestones:
        raise HTTPException(
            status_code=400,
            detail="No phases or milestones found in the uploaded file. Check that the file is a valid MS Project export.",
        )

    # ── Assign colors to phases ────────────────────────────────────
    for idx, phase in enumerate(phases):
        phase["color"] = PHASE_COLORS[idx % len(PHASE_COLORS)]
        phase["project_id"] = project_id

    # ── Replace mode: delete existing data ─────────────────────────
    try:
        if mode == "replace":
            supabase.table("project_milestones").delete().eq("project_id", project_id).execute()
            supabase.table("project_phases").delete().eq("project_id", project_id).execute()
    except Exception as exc:
        logger.error("Error deleting existing timeline data for project %s: %s", project_id, exc)
        raise HTTPException(status_code=500, detail=f"Error clearing existing timeline data: {exc}")

    # ── Insert phases ──────────────────────────────────────────────
    inserted_phases: list[dict] = []
    try:
        if phases:
            result = supabase.table("project_phases").insert(phases).execute()
            inserted_phases = result.data or []
    except Exception as exc:
        logger.error("Error inserting phases for project %s: %s", project_id, exc)
        raise HTTPException(status_code=500, detail=f"Error inserting phases: {exc}")

    # ── Insert milestones (link to parent phases) ──────────────────
    milestones_to_insert: list[dict] = []
    for ms in milestones:
        parent_idx = ms.pop("_parent_phase_idx", None)
        ms["project_id"] = project_id

        # Link to the newly-inserted phase if possible
        if parent_idx is not None and parent_idx < len(inserted_phases):
            ms["phase_id"] = inserted_phases[parent_idx].get("phase_id")
        else:
            ms["phase_id"] = None

        milestones_to_insert.append(ms)

    inserted_milestones: list[dict] = []
    try:
        if milestones_to_insert:
            result = supabase.table("project_milestones").insert(milestones_to_insert).execute()
            inserted_milestones = result.data or []
    except Exception as exc:
        logger.error("Error inserting milestones for project %s: %s", project_id, exc)
        raise HTTPException(status_code=500, detail=f"Error inserting milestones: {exc}")

    action = "replaced" if mode == "replace" else "appended"
    return {
        "message": f"Timeline imported successfully ({action})",
        "phases_imported": len(inserted_phases),
        "milestones_imported": len(inserted_milestones),
    }
