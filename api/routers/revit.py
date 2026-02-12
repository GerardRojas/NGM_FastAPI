"""
Router for NGM Revit ecosystem data.
Serves definitions, templates, materials, registry, and manifest schema
from the revit_data submodule (NGM_REVIT repo).
Also handles build manifest CRUD (stored in Supabase).
"""
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from typing import Optional
from pathlib import Path
from datetime import datetime
import json

from api.supabase_client import supabase

router = APIRouter(prefix="/revit", tags=["revit"])

# Path to revit_data submodule (relative to project root)
REVIT_DATA = Path(__file__).resolve().parent.parent.parent / "revit_data"


def _read_json(relative_path: str):
    """Read a JSON file from the revit_data submodule."""
    filepath = REVIT_DATA / relative_path
    if not filepath.exists():
        raise HTTPException(status_code=404, detail="File not found: {}".format(relative_path))
    with open(filepath, "r", encoding="utf-8") as f:
        return json.load(f)


# ========================================
# Registry (ecosystem overview)
# ========================================

@router.get("/registry")
async def get_registry():
    """Full ecosystem registry: scripts, families, definitions index."""
    return _read_json("registry.json")


# ========================================
# Definitions
# ========================================

DEFINITION_TYPES = [
    "wall_types",
    "floor_types",
    "view_templates",
    "sheet_layouts",
    "graphic_styles",
    "naming_conventions",
    "shared_parameters",
]

@router.get("/definitions")
async def list_definitions():
    """List all available definition types with metadata."""
    registry = _read_json("registry.json")
    return registry.get("definitions_index", {})


@router.get("/definitions/{def_type}")
async def get_definition(def_type: str):
    """Get a specific definition file content."""
    if def_type not in DEFINITION_TYPES:
        raise HTTPException(
            status_code=400,
            detail="Invalid definition type. Valid: {}".format(", ".join(DEFINITION_TYPES))
        )
    return _read_json("definitions/{}.json".format(def_type))


# ========================================
# Templates
# ========================================

@router.get("/templates")
async def list_templates():
    """List available project templates."""
    registry = _read_json("registry.json")
    return registry.get("templates_index", {})


@router.get("/templates/{project_type}")
async def get_template(project_type: str):
    """Get a specific project template."""
    valid = ["residential", "commercial", "industrial"]
    if project_type not in valid:
        raise HTTPException(
            status_code=400,
            detail="Invalid project type. Valid: {}".format(", ".join(valid))
        )
    return _read_json("templates/{}.json".format(project_type))


# ========================================
# Materials
# ========================================

@router.get("/materials-map")
async def get_materials_map():
    """Get the Revit <-> NGM material mapping."""
    return _read_json("materials/material_map.json")


# ========================================
# Manifest Schema
# ========================================

@router.get("/manifest-schema")
async def get_manifest_schema():
    """Get the build manifest JSON schema."""
    return _read_json("manifests/_schema.json")


# ========================================
# Build Manifests (DB-backed CRUD)
# ========================================

class ManifestCreate(BaseModel):
    project_id: Optional[str] = None
    name: str
    project_type: str
    manifest: dict

class ManifestUpdate(BaseModel):
    name: Optional[str] = None
    manifest: Optional[dict] = None


@router.post("/manifests")
async def create_manifest(body: ManifestCreate):
    """Save a generated build manifest to DB."""
    try:
        row = {
            "name": body.name,
            "project_type": body.project_type,
            "manifest": body.manifest,
            "project_id": body.project_id,
        }
        res = supabase.table("build_manifests").insert(row).execute()
        if res.data:
            return res.data[0]
        raise HTTPException(status_code=500, detail="Failed to create manifest")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/manifests")
async def list_manifests(
    project_id: Optional[str] = Query(None),
    limit: int = Query(20, ge=1, le=100),
):
    """List saved manifests, optionally filtered by project."""
    try:
        q = supabase.table("build_manifests").select("id, name, project_type, project_id, created_at, updated_at").order("created_at", desc=True).limit(limit)
        if project_id:
            q = q.eq("project_id", project_id)
        res = q.execute()
        return res.data or []
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/manifests/{manifest_id}")
async def get_manifest(manifest_id: str):
    """Get a specific manifest by ID."""
    try:
        res = supabase.table("build_manifests").select("*").eq("id", manifest_id).execute()
        if not res.data:
            raise HTTPException(status_code=404, detail="Manifest not found")
        return res.data[0]
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/manifests/{manifest_id}")
async def update_manifest(manifest_id: str, body: ManifestUpdate):
    """Update a saved manifest."""
    try:
        updates = {k: v for k, v in body.dict().items() if v is not None}
        if not updates:
            raise HTTPException(status_code=400, detail="No fields to update")
        updates["updated_at"] = datetime.utcnow().isoformat()
        res = supabase.table("build_manifests").update(updates).eq("id", manifest_id).execute()
        if res.data:
            return res.data[0]
        raise HTTPException(status_code=404, detail="Manifest not found")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/manifests/{manifest_id}")
async def delete_manifest(manifest_id: str):
    """Delete a saved manifest."""
    try:
        res = supabase.table("build_manifests").delete().eq("id", manifest_id).execute()
        return {"deleted": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
