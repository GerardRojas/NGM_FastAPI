# api/routers/estimator.py
# ================================
# Estimator API Router
# ================================
# Manages estimates and templates stored in Supabase buckets
#
# Bucket Structure:
# - estimates/
#     {estimate-id}/
#         estimate.ngm                    (JSON with project data, categories, quantities)
#         materials_snapshot.json          (full materials DB at time of save)
#         concepts_snapshot.json           (full concepts DB at time of save)
#         concept_materials_snapshot.json  (concept-material junction at time of save)
#
# - templates/
#     {template-id}/
#         template.ngm              (JSON with structure, NO quantities)
#         materials_snapshot.json
#         concepts_snapshot.json
#         concept_materials_snapshot.json
#         template_meta.json        (name, description, created_at, etc.)
#
# Note: Legacy estimates/templates may have .csv snapshots - backward compatible

import logging
from typing import Dict, Any, Optional, List
from pathlib import Path
from datetime import datetime
import json
import uuid

from fastapi import APIRouter, HTTPException, Body
from pydantic import BaseModel

from api.supabase_client import supabase

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/estimator", tags=["estimator"])

# ============================================
# BUCKET CONFIGURATION
# ============================================

ESTIMATES_BUCKET = "estimates"
TEMPLATES_BUCKET = "templates"

# Legacy file paths (for backward compatibility)
BASE_DIR = Path(__file__).resolve().parents[2]   # .../NGM_API
TEMPLATES_DIR = BASE_DIR / "templates"
NGM_FILE_PATH = TEMPLATES_DIR / "estimate.ngm"

logger.info("[ESTIMATOR] BASE_DIR      = %s", BASE_DIR)
logger.info("[ESTIMATOR] TEMPLATES_DIR = %s", TEMPLATES_DIR)


# ============================================
# PYDANTIC MODELS
# ============================================

class EstimateSaveRequest(BaseModel):
    """Request body for saving an estimate"""
    estimate_id: Optional[str] = None  # If None, generates new ID
    project_name: str
    project: Dict[str, Any]
    categories: List[Dict[str, Any]]
    overhead: Optional[Dict[str, Any]] = None
    materials_snapshot: Optional[List[Dict[str, Any]]] = None
    concepts_snapshot: Optional[List[Dict[str, Any]]] = None
    concept_materials_snapshot: Optional[List[Dict[str, Any]]] = None
    created_from_template: Optional[str] = None


class TemplateSaveRequest(BaseModel):
    """Request body for saving a template"""
    template_name: str
    description: Optional[str] = ""
    project: Dict[str, Any]
    categories: List[Dict[str, Any]]
    overhead: Optional[Dict[str, Any]] = None
    materials_snapshot: Optional[List[Dict[str, Any]]] = None
    concepts_snapshot: Optional[List[Dict[str, Any]]] = None
    concept_materials_snapshot: Optional[List[Dict[str, Any]]] = None


# ============================================
# BUCKET HELPERS
# ============================================

def ensure_bucket_exists(bucket_name: str, public: bool = True) -> bool:
    """Ensures a bucket exists, creates if not. Returns True if successful."""
    try:
        supabase.storage.get_bucket(bucket_name)
        return True
    except Exception as _exc:
        logger.debug("Suppressed: %s", _exc)
        try:
            supabase.storage.create_bucket(
                bucket_name,
                options={"public": public}
            )
            logger.info("[ESTIMATOR] Created bucket: %s", bucket_name)
            return True
        except Exception as e:
            logger.warning("[ESTIMATOR] Bucket creation note for %s: %s", bucket_name, e)
            # Might already exist due to race condition
            return True


def generate_estimate_id(project_name: str) -> str:
    """Generate a unique estimate ID from project name + timestamp"""
    safe_name = "".join(c if c.isalnum() or c in "-_" else "-" for c in project_name.lower())
    safe_name = safe_name[:30]  # Limit length
    timestamp = int(datetime.now().timestamp() * 1000)
    return f"{safe_name}-{timestamp}"


def generate_template_id(template_name: str) -> str:
    """Generate a unique template ID from template name + timestamp"""
    safe_name = "".join(c if c.isalnum() or c in "-_" else "-" for c in template_name.lower())
    safe_name = safe_name[:30]
    timestamp = int(datetime.now().timestamp() * 1000)
    return f"{safe_name}-{timestamp}"



# ============================================
# BUCKET INITIALIZATION
# ============================================

@router.get("/buckets/init")
async def init_buckets():
    """
    Initialize estimates and templates buckets.
    Call this once to ensure buckets exist.
    """
    estimates_ok = ensure_bucket_exists(ESTIMATES_BUCKET, public=True)
    templates_ok = ensure_bucket_exists(TEMPLATES_BUCKET, public=True)

    return {
        "success": estimates_ok and templates_ok,
        "buckets": {
            "estimates": {"name": ESTIMATES_BUCKET, "initialized": estimates_ok},
            "templates": {"name": TEMPLATES_BUCKET, "initialized": templates_ok}
        }
    }


@router.get("/buckets/status")
async def get_buckets_status():
    """Get status of estimator buckets"""
    status = {
        "estimates": {"exists": False, "file_count": 0},
        "templates": {"exists": False, "file_count": 0}
    }

    try:
        # Check estimates bucket
        try:
            supabase.storage.get_bucket(ESTIMATES_BUCKET)
            status["estimates"]["exists"] = True
            files = supabase.storage.from_(ESTIMATES_BUCKET).list()
            status["estimates"]["file_count"] = len(files) if files else 0
        except Exception as _exc:
            logger.debug("Suppressed: %s", _exc)

        # Check templates bucket
        try:
            supabase.storage.get_bucket(TEMPLATES_BUCKET)
            status["templates"]["exists"] = True
            files = supabase.storage.from_(TEMPLATES_BUCKET).list()
            status["templates"]["file_count"] = len(files) if files else 0
        except Exception as _exc:
            logger.debug("Suppressed: %s", _exc)

        return status
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error checking buckets: {str(e)}")


# ============================================
# ESTIMATES ENDPOINTS
# ============================================

@router.get("/estimates")
async def list_estimates():
    """
    List all saved estimates from the estimates bucket.
    Returns folders (each estimate is a folder).
    """
    try:
        ensure_bucket_exists(ESTIMATES_BUCKET)

        files = supabase.storage.from_(ESTIMATES_BUCKET).list()

        # Filter for folders (estimates) - folders have no extension in name
        estimates = []
        for item in files or []:
            name = item.get("name", "")
            if name and "." not in name:
                estimates.append({
                    "id": name,
                    "name": name,
                    "created_at": item.get("created_at"),
                    "updated_at": item.get("updated_at")
                })

        return {"estimates": estimates, "count": len(estimates)}

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error listing estimates: {str(e)}")


@router.get("/estimates/{estimate_id}")
async def get_estimate(estimate_id: str):
    """
    Load a specific estimate by ID.
    Returns the estimate.ngm JSON content.
    """
    try:
        ensure_bucket_exists(ESTIMATES_BUCKET)

        path = f"{estimate_id}/estimate.ngm"

        # Download the file
        response = supabase.storage.from_(ESTIMATES_BUCKET).download(path)

        if not response:
            raise HTTPException(status_code=404, detail=f"Estimate not found: {estimate_id}")

        # Parse JSON
        data = json.loads(response.decode("utf-8"))

        return data

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error loading estimate: {str(e)}")


@router.post("/estimates")
async def save_estimate(request: EstimateSaveRequest):
    """
    Save an estimate to the estimates bucket.
    Creates a folder with:
    - estimate.ngm (main JSON file)
    - materials_snapshot.json (optional)
    - concepts_snapshot.json (optional)
    - concept_materials_snapshot.json (optional)
    """
    try:
        ensure_bucket_exists(ESTIMATES_BUCKET)

        # Generate or use provided ID
        estimate_id = request.estimate_id or generate_estimate_id(request.project_name)

        # Prepare main estimate data
        estimate_data = {
            "estimate_id": estimate_id,
            "project_name": request.project_name,
            "project": request.project,
            "categories": request.categories,
            "overhead": request.overhead or {"percentage": 0, "amount": 0},
            "created_from_template": request.created_from_template,
            "created_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat(),
            "version": "1.0"
        }

        # Upload main estimate file
        estimate_json = json.dumps(estimate_data, indent=2)
        supabase.storage.from_(ESTIMATES_BUCKET).upload(
            path=f"{estimate_id}/estimate.ngm",
            file=estimate_json.encode("utf-8"),
            file_options={"content-type": "application/json", "upsert": "true"}
        )

        # Upload materials snapshot if provided (JSON format)
        if request.materials_snapshot:
            snapshot_json = json.dumps(request.materials_snapshot)
            supabase.storage.from_(ESTIMATES_BUCKET).upload(
                path=f"{estimate_id}/materials_snapshot.json",
                file=snapshot_json.encode("utf-8"),
                file_options={"content-type": "application/json", "upsert": "true"}
            )

        # Upload concepts snapshot if provided (JSON format)
        if request.concepts_snapshot:
            snapshot_json = json.dumps(request.concepts_snapshot)
            supabase.storage.from_(ESTIMATES_BUCKET).upload(
                path=f"{estimate_id}/concepts_snapshot.json",
                file=snapshot_json.encode("utf-8"),
                file_options={"content-type": "application/json", "upsert": "true"}
            )

        # Upload concept_materials snapshot if provided (JSON format)
        if request.concept_materials_snapshot:
            snapshot_json = json.dumps(request.concept_materials_snapshot)
            supabase.storage.from_(ESTIMATES_BUCKET).upload(
                path=f"{estimate_id}/concept_materials_snapshot.json",
                file=snapshot_json.encode("utf-8"),
                file_options={"content-type": "application/json", "upsert": "true"}
            )

        return {
            "success": True,
            "estimate_id": estimate_id,
            "message": "Estimate saved successfully"
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error saving estimate: {str(e)}")


@router.delete("/estimates/{estimate_id}")
async def delete_estimate(estimate_id: str):
    """
    Delete an estimate and all its files.
    """
    try:
        ensure_bucket_exists(ESTIMATES_BUCKET)

        # List files in the estimate folder
        files = supabase.storage.from_(ESTIMATES_BUCKET).list(estimate_id)

        if not files:
            raise HTTPException(status_code=404, detail=f"Estimate not found: {estimate_id}")

        # Delete all files in the folder
        file_paths = [f"{estimate_id}/{f['name']}" for f in files]
        supabase.storage.from_(ESTIMATES_BUCKET).remove(file_paths)

        return {
            "success": True,
            "estimate_id": estimate_id,
            "files_deleted": len(file_paths)
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error deleting estimate: {str(e)}")


# ============================================
# TEMPLATES ENDPOINTS
# ============================================

@router.get("/templates")
async def list_templates():
    """
    List all saved templates from the templates bucket.
    """
    try:
        ensure_bucket_exists(TEMPLATES_BUCKET)

        files = supabase.storage.from_(TEMPLATES_BUCKET).list()

        # Filter for folders (templates) - folders have id=None in Supabase storage
        templates = []
        for item in files or []:
            name = item.get("name", "")
            if name and "." not in name:
                templates.append({
                    "id": name,
                    "name": name,
                    "created_at": item.get("created_at"),
                    "updated_at": item.get("updated_at")
                })

        return {"templates": templates, "count": len(templates)}

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error listing templates: {str(e)}")


@router.get("/templates/{template_id}")
async def get_template(template_id: str):
    """
    Load a specific template by ID.
    Returns the template.ngm JSON content.
    """
    try:
        ensure_bucket_exists(TEMPLATES_BUCKET)

        path = f"{template_id}/template.ngm"

        # Download the file
        response = supabase.storage.from_(TEMPLATES_BUCKET).download(path)

        if not response:
            raise HTTPException(status_code=404, detail=f"Template not found: {template_id}")

        # Parse JSON
        data = json.loads(response.decode("utf-8"))

        return data

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error loading template: {str(e)}")


@router.get("/templates/{template_id}/meta")
async def get_template_meta(template_id: str):
    """
    Get template metadata (name, description, created_at).
    """
    try:
        ensure_bucket_exists(TEMPLATES_BUCKET)

        path = f"{template_id}/template_meta.json"

        response = supabase.storage.from_(TEMPLATES_BUCKET).download(path)

        if not response:
            # Return basic info from folder name
            return {
                "template_id": template_id,
                "name": template_id.replace("-", " ").title(),
                "description": ""
            }

        data = json.loads(response.decode("utf-8"))
        return data

    except Exception as e:
        # Return basic info on error
        return {
            "template_id": template_id,
            "name": template_id.replace("-", " ").title(),
            "description": ""
        }


@router.post("/templates")
async def save_template(request: TemplateSaveRequest):
    """
    Save a template to the templates bucket.
    Creates a folder with:
    - template.ngm (main JSON file - NO quantities)
    - template_meta.json (name, description)
    - materials_snapshot.json (optional)
    - concepts_snapshot.json (optional)
    - concept_materials_snapshot.json (optional)
    """
    try:
        ensure_bucket_exists(TEMPLATES_BUCKET)

        # Generate template ID
        template_id = generate_template_id(request.template_name)

        # Prepare template metadata
        meta_data = {
            "template_id": template_id,
            "name": request.template_name,
            "description": request.description or "",
            "created_at": datetime.now().isoformat()
        }

        # Prepare main template data (should already have quantities cleared)
        template_data = {
            "template_id": template_id,
            "template_name": request.template_name,
            "project": request.project,
            "categories": request.categories,
            "overhead": request.overhead or {"percentage": 0, "amount": 0},
            "template_meta": meta_data,
            "created_at": datetime.now().isoformat(),
            "version": "1.0"
        }

        # Upload main template file
        template_json = json.dumps(template_data, indent=2)
        supabase.storage.from_(TEMPLATES_BUCKET).upload(
            path=f"{template_id}/template.ngm",
            file=template_json.encode("utf-8"),
            file_options={"content-type": "application/json", "upsert": "true"}
        )

        # Upload metadata file
        meta_json = json.dumps(meta_data, indent=2)
        supabase.storage.from_(TEMPLATES_BUCKET).upload(
            path=f"{template_id}/template_meta.json",
            file=meta_json.encode("utf-8"),
            file_options={"content-type": "application/json", "upsert": "true"}
        )

        # Upload materials snapshot if provided (JSON format)
        if request.materials_snapshot:
            snapshot_json = json.dumps(request.materials_snapshot)
            supabase.storage.from_(TEMPLATES_BUCKET).upload(
                path=f"{template_id}/materials_snapshot.json",
                file=snapshot_json.encode("utf-8"),
                file_options={"content-type": "application/json", "upsert": "true"}
            )

        # Upload concepts snapshot if provided (JSON format)
        if request.concepts_snapshot:
            snapshot_json = json.dumps(request.concepts_snapshot)
            supabase.storage.from_(TEMPLATES_BUCKET).upload(
                path=f"{template_id}/concepts_snapshot.json",
                file=snapshot_json.encode("utf-8"),
                file_options={"content-type": "application/json", "upsert": "true"}
            )

        # Upload concept_materials snapshot if provided (JSON format)
        if request.concept_materials_snapshot:
            snapshot_json = json.dumps(request.concept_materials_snapshot)
            supabase.storage.from_(TEMPLATES_BUCKET).upload(
                path=f"{template_id}/concept_materials_snapshot.json",
                file=snapshot_json.encode("utf-8"),
                file_options={"content-type": "application/json", "upsert": "true"}
            )

        return {
            "success": True,
            "template_id": template_id,
            "message": "Template saved successfully"
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error saving template: {str(e)}")


@router.delete("/templates/{template_id}")
async def delete_template(template_id: str):
    """
    Delete a template and all its files.
    """
    try:
        ensure_bucket_exists(TEMPLATES_BUCKET)

        # List files in the template folder
        files = supabase.storage.from_(TEMPLATES_BUCKET).list(template_id)

        if not files:
            raise HTTPException(status_code=404, detail=f"Template not found: {template_id}")

        # Delete all files in the folder
        file_paths = [f"{template_id}/{f['name']}" for f in files]
        supabase.storage.from_(TEMPLATES_BUCKET).remove(file_paths)

        return {
            "success": True,
            "template_id": template_id,
            "files_deleted": len(file_paths)
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error deleting template: {str(e)}")


# ============================================
# LEGACY ENDPOINTS (for backward compatibility)
# ============================================

@router.get("/base-structure")
async def get_base_structure() -> Dict[str, Any]:
    """
    Legacy endpoint: Load base structure from local file.
    TODO: Migrate to loading from a default template in bucket.
    """
    try:
        text = NGM_FILE_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise HTTPException(
            status_code=500,
            detail=f"File not found: {NGM_FILE_PATH}"
        ) from None

    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise HTTPException(
            status_code=500,
            detail=f"Invalid JSON in estimate.ngm: {e}"
        ) from e

    if not isinstance(data, dict):
        raise HTTPException(
            status_code=500,
            detail="estimate.ngm must be a JSON object"
        )

    return data


@router.post("/save")
async def save_estimate_legacy(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Legacy endpoint: Save estimate to local file.
    TODO: Migrate to using bucket storage.
    """
    if "categories" not in payload:
        raise HTTPException(
            status_code=400,
            detail="Missing 'categories' in payload"
        )

    TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)

    try:
        NGM_FILE_PATH.write_text(
            json.dumps(payload, indent=2),
            encoding="utf-8",
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error saving estimate.ngm at {NGM_FILE_PATH}: {e}"
        ) from e

    return {"status": "ok", "path": str(NGM_FILE_PATH)}
