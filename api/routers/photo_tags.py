"""
Photo Tags — NGM Cam global tag catalog + per-photo assignments.

A single company-wide list of tags (managed from "Manage Tags" on web), each
with a display color, attached to vault photos through a join table. Powers
filtering/sorting of the NGM Cam gallery on web and mobile.

Tables (see sql/photo_tags.sql):
  photo_tags       — the catalog (the Manage Tags list)
  photo_file_tags  — links a vault file (photo) to a tag
"""

from fastapi import APIRouter, HTTPException, Query, Depends
from pydantic import BaseModel, Field
from typing import Optional, List

from api.supabase_client import supabase
from api.auth import get_current_user

router = APIRouter(prefix="/photo-tags", tags=["Photo Tags"])

DEFAULT_COLOR = "#6b7280"


# ====== PYDANTIC MODELS ======

class TagCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=60)
    color: Optional[str] = None


class TagUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=60)
    color: Optional[str] = None


class AssignmentsSet(BaseModel):
    file_id: str = Field(..., min_length=1)
    tag_ids: List[str] = Field(default_factory=list)


# ====== CATALOG (Manage Tags) ======

@router.get("")
def list_tags(current_user: dict = Depends(get_current_user)):
    """The global tag catalog, alphabetical."""
    try:
        res = (
            supabase.table("photo_tags")
            .select("id, name, color, created_at")
            .order("name")
            .execute()
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Query failed: {e}")
    return {"tags": res.data or []}


@router.post("", status_code=201)
def create_tag(payload: TagCreate, current_user: dict = Depends(get_current_user)):
    """Add a tag to the catalog. Names are unique case-insensitively."""
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Tag name is required")

    try:
        # Case-insensitive duplicate guard (no wildcards => exact ci match).
        existing = supabase.table("photo_tags").select("id").ilike("name", name).execute()
        if existing.data:
            raise HTTPException(status_code=409, detail="A tag with that name already exists")

        row = {
            "name": name,
            "color": (payload.color or DEFAULT_COLOR).strip() or DEFAULT_COLOR,
            "created_by": current_user.get("user_id"),
        }
        res = supabase.table("photo_tags").insert(row).execute()
        if not res.data:
            raise HTTPException(status_code=500, detail="Create failed")
        return res.data[0]
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error creating tag: {e}")


@router.patch("/{tag_id}")
def update_tag(tag_id: str, payload: TagUpdate, current_user: dict = Depends(get_current_user)):
    """Rename and/or recolor a catalog tag."""
    updates: dict = {}
    if payload.name is not None:
        name = payload.name.strip()
        if not name:
            raise HTTPException(status_code=400, detail="Tag name cannot be empty")
        try:
            clash = (
                supabase.table("photo_tags")
                .select("id")
                .ilike("name", name)
                .neq("id", tag_id)
                .execute()
            )
            if clash.data:
                raise HTTPException(status_code=409, detail="A tag with that name already exists")
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Query failed: {e}")
        updates["name"] = name
    if payload.color is not None:
        updates["color"] = payload.color.strip() or DEFAULT_COLOR
    if not updates:
        raise HTTPException(status_code=400, detail="Nothing to update")

    try:
        res = supabase.table("photo_tags").update(updates).eq("id", tag_id).execute()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Update failed: {e}")
    if not res.data:
        raise HTTPException(status_code=404, detail="Tag not found")
    return res.data[0]


@router.delete("/{tag_id}")
def delete_tag(tag_id: str, current_user: dict = Depends(get_current_user)):
    """Delete a catalog tag. Its photo assignments cascade away (FK)."""
    try:
        supabase.table("photo_tags").delete().eq("id", tag_id).execute()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Delete failed: {e}")
    return {"ok": True, "id": tag_id}


# ====== ASSIGNMENTS (photo <-> tag) ======

@router.get("/assignments")
def get_assignments(
    file_ids: str = Query(..., description="Comma-separated vault file IDs"),
    current_user: dict = Depends(get_current_user),
):
    """
    Tag assignments for a set of photos. Result: { assignments: [{file_id, tag_id}] }.
    The frontend maps these onto the photos it already has loaded.
    """
    ids = [fid.strip() for fid in file_ids.split(",") if fid.strip()]
    if not ids:
        return {"assignments": []}
    try:
        res = (
            supabase.table("photo_file_tags")
            .select("file_id, tag_id")
            .in_("file_id", ids)
            .execute()
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Query failed: {e}")
    return {"assignments": res.data or []}


@router.put("/assignments")
def set_assignments(payload: AssignmentsSet, current_user: dict = Depends(get_current_user)):
    """Replace the full set of tags on one photo (last-write-wins)."""
    user_id = current_user.get("user_id")
    tag_ids = [t for t in {str(t).strip() for t in payload.tag_ids} if t]

    try:
        # Clear then re-insert the desired set.
        supabase.table("photo_file_tags").delete().eq("file_id", payload.file_id).execute()
        if tag_ids:
            rows = [
                {"file_id": payload.file_id, "tag_id": tid, "created_by": user_id}
                for tid in tag_ids
            ]
            supabase.table("photo_file_tags").insert(rows).execute()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Save failed: {e}")
    return {"file_id": payload.file_id, "tag_ids": tag_ids}
