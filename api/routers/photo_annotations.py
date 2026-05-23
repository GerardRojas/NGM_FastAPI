"""
Photo Annotations — NGM Cam non-destructive markup.

Editable vector overlays (arrows, lines, rectangles, ellipses, freehand, text)
drawn on top of a vault photo. The original image is never modified; shapes are
stored as JSON (normalized 0..1 coordinates) and rendered as an SVG overlay on
the frontend. One shared annotation doc per photo (file_id).
"""

from fastapi import APIRouter, HTTPException, Query, Depends
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any

from api.supabase_client import supabase
from api.auth import get_current_user

router = APIRouter(prefix="/photo-annotations", tags=["Photo Annotations"])


# ====== PYDANTIC MODELS ======

class AnnotationsSave(BaseModel):
    file_id: str = Field(..., min_length=1)
    project_id: Optional[str] = None
    shapes: List[Dict[str, Any]] = Field(default_factory=list)


# ====== ENDPOINTS ======

@router.get("")
def get_annotations(
    file_id: str = Query(...),
    current_user: dict = Depends(get_current_user),
):
    """Get the annotation doc (shapes) for a single photo."""
    try:
        res = (
            supabase.table("photo_annotations")
            .select("file_id, project_id, shapes, updated_by, updated_at")
            .eq("file_id", file_id)
            .execute()
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Query failed: {e}")

    if not res.data:
        return {"file_id": file_id, "shapes": []}
    return res.data[0]


@router.get("/counts")
def annotation_counts(
    file_ids: str = Query(..., description="Comma-separated vault file IDs"),
    current_user: dict = Depends(get_current_user),
):
    """
    Return shape counts per file_id for batch badge rendering.
    Files without annotations are omitted. Result: { file_id: shape_count }.
    """
    ids = [fid.strip() for fid in file_ids.split(",") if fid.strip()]
    if not ids:
        return {"counts": {}}

    try:
        res = (
            supabase.table("photo_annotations")
            .select("file_id, shapes")
            .in_("file_id", ids)
            .execute()
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Query failed: {e}")

    counts: Dict[str, int] = {}
    for row in res.data or []:
        shapes = row.get("shapes") or []
        if isinstance(shapes, list) and shapes:
            counts[str(row["file_id"])] = len(shapes)
    return {"counts": counts}


@router.put("")
def save_annotations(
    payload: AnnotationsSave,
    current_user: dict = Depends(get_current_user),
):
    """
    Upsert the annotation doc for a photo (collaborative last-write-wins).
    Saving an empty shapes array deletes the doc so badges disappear.
    """
    user_id = current_user.get("user_id")

    # Empty shapes -> remove the doc entirely.
    if not payload.shapes:
        try:
            supabase.table("photo_annotations").delete().eq("file_id", payload.file_id).execute()
            return {"file_id": payload.file_id, "shapes": []}
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Delete failed: {e}")

    row = {
        "file_id": payload.file_id,
        "project_id": payload.project_id,
        "shapes": payload.shapes,
        "updated_by": user_id,
    }

    try:
        # Check existence, then update or insert (avoids silent upsert no-ops).
        existing = (
            supabase.table("photo_annotations")
            .select("id")
            .eq("file_id", payload.file_id)
            .execute()
        )
        if existing.data:
            res = (
                supabase.table("photo_annotations")
                .update(row)
                .eq("file_id", payload.file_id)
                .execute()
            )
        else:
            res = supabase.table("photo_annotations").insert(row).execute()

        if not res.data:
            raise HTTPException(status_code=500, detail="Save failed")
        return res.data[0]

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error saving annotations: {e}")
