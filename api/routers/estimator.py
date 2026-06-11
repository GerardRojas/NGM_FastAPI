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

from fastapi import APIRouter, HTTPException, Body, Query, Depends
from api.auth import require_internal
from pydantic import BaseModel

from api.supabase_client import supabase

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(require_internal)], prefix="/estimator", tags=["estimator"])

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
    branch_id: Optional[str] = None    # If None, saves the main branch
    project_name: str
    project: Dict[str, Any]
    categories: List[Dict[str, Any]]
    overhead: Optional[Dict[str, Any]] = None
    materials_snapshot: Optional[List[Dict[str, Any]]] = None
    concepts_snapshot: Optional[List[Dict[str, Any]]] = None
    concept_materials_snapshot: Optional[List[Dict[str, Any]]] = None
    created_from_template: Optional[str] = None
    company_id: Optional[str] = None  # Owning workspace; stamped into the manifest


class BranchCreateRequest(BaseModel):
    """Request body for creating a new branch of an estimate.

    A `variation` is a full independent copy of the source branch (default).
    A `change_order` starts empty: only the project metadata is carried over,
    `categories` and `overhead` are reset. `empty=True` forces the empty
    behaviour even for `variation` (rarely useful, but explicit)."""
    name: str
    based_on: Optional[str] = None        # branch_id to copy from; defaults to main
    kind: Optional[str] = "variation"     # "variation" | "change_order"
    empty: Optional[bool] = False
    # Optional CO bookkeeping (only meaningful when kind == "change_order").
    status: Optional[str] = None          # "pending" | "approved" | "rejected"


class BranchUpdateRequest(BaseModel):
    """PATCH body — every field is optional; only those set get updated."""
    name: Optional[str] = None
    status: Optional[str] = None          # "pending" | "approved" | "rejected"
    kind: Optional[str] = None            # "variation" | "change_order"


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
# ESTIMATE METADATA HELPERS
# ============================================

def _to_num(value: Any) -> Optional[float]:
    """Coerce a value to float, stripping $/commas. Returns None if not numeric."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).replace("$", "").replace(",", ""))
    except (TypeError, ValueError):
        return None


def _first(row: Dict[str, Any], keys: List[str]) -> Any:
    """First defined/non-empty value among the given keys (legacy field fallbacks)."""
    for key in keys:
        val = row.get(key)
        if val is not None and val != "":
            return val
    return None


def compute_subtotal(data: Dict[str, Any]) -> float:
    """Construction cost (before overhead): stored value wins, else the sum of
    category rollups. Mirrors services.ts normalizeEstimate."""
    subtotal = _to_num(data.get("subtotal"))
    if subtotal is not None:
        return subtotal
    subtotal = 0.0
    for cat in (data.get("categories") or []):
        cat_total = _to_num(cat.get("total_cost") if isinstance(cat, dict) else None)
        if cat_total is not None:
            subtotal += cat_total
            continue
        for sub in (cat.get("subcategories") or []):
            sub_total = _to_num(sub.get("total_cost"))
            if sub_total is not None:
                subtotal += sub_total
                continue
            for item in (sub.get("items") or []):
                subtotal += _to_num(_first(item, ["total", "total_cost", "subtotal"])) or 0.0
    return round(subtotal, 2)


def compute_grand_total(data: Dict[str, Any]) -> Optional[float]:
    """Resolve an estimate's grand total, mirroring the frontend rollup
    (services.ts normalizeEstimate): stored value wins, else subtotal + overhead.
    """
    subtotal = compute_subtotal(data)

    # Overhead amount: itemized (sum of items) or flat amount.
    overhead = data.get("overhead") or {}
    if isinstance(overhead.get("items"), list):
        oh_amount = sum((_to_num(it.get("amount")) or 0.0) for it in overhead["items"])
    else:
        oh_amount = _to_num(overhead.get("amount")) or 0.0

    stored_grand = _to_num(_first(data, ["grand_total", "total"]))
    return stored_grand if stored_grand is not None else round(subtotal + oh_amount, 2)


def extract_estimate_meta(estimate_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
    """Build a board-card summary from an estimate.ngm payload."""
    project = data.get("project")
    project_label = None
    project_type = None
    if isinstance(project, dict):
        project_label = _first(project, ["client", "name", "address", "project_name"])
        project_type = _first(project, ["project_type", "construction_type"])
    elif isinstance(project, str):
        project_label = project
    return {
        "id": estimate_id,
        "name": str(_first(data, ["project_name"]) or estimate_id),
        "created_at": data.get("created_at"),
        "updated_at": data.get("updated_at"),
        "subtotal": compute_subtotal(data),
        "grand_total": compute_grand_total(data),
        "project": project_label,
        "project_type": project_type,
    }


# ============================================
# BRANCH HELPERS
# ============================================
# An estimate folder holds variations ("branches") of the same project, e.g.
# "Sin appliances". Each branch is a full independent copy:
#   {estimate_id}/branches/{branch_id}.ngm   (canonical per-branch content)
#   {estimate_id}/branches.json              (manifest: which branch is main + card metadata)
#   {estimate_id}/estimate.ngm               (mirror of the MAIN branch, for legacy HTML reads)

MAIN_BRANCH_ID = "main"

# Branch kinds. "variation" = full alternate (e.g. "Without appliances").
# "change_order" = additive scope; starts empty, has a status (pending/approved/
# rejected) and rolls into the contract value only when approved.
BRANCH_KIND_VARIATION = "variation"
BRANCH_KIND_CHANGE_ORDER = "change_order"
BRANCH_KINDS = {BRANCH_KIND_VARIATION, BRANCH_KIND_CHANGE_ORDER}

CO_STATUSES = {"pending", "approved", "rejected"}


def _branch_path(estimate_id: str, branch_id: str) -> str:
    return f"{estimate_id}/branches/{branch_id}.ngm"


def _manifest_path(estimate_id: str) -> str:
    return f"{estimate_id}/branches.json"


def _download_json(path: str) -> Optional[Dict[str, Any]]:
    try:
        blob = supabase.storage.from_(ESTIMATES_BUCKET).download(path)
        if blob:
            return json.loads(blob.decode("utf-8"))
    except Exception as exc:
        logger.debug("[ESTIMATOR] download miss %s: %s", path, exc)
    return None


def _upload_json(path: str, data: Any) -> None:
    payload = json.dumps(data, indent=2).encode("utf-8")
    supabase.storage.from_(ESTIMATES_BUCKET).upload(
        path=path,
        file=payload,
        file_options={"content-type": "application/json", "upsert": "true"},
    )


def generate_branch_id(name: str) -> str:
    safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in (name or "branch").lower())[:30]
    return f"{safe}-{int(datetime.now().timestamp() * 1000)}"


def find_branch(manifest: Dict[str, Any], branch_id: str) -> Optional[Dict[str, Any]]:
    for b in manifest.get("branches", []):
        if b.get("id") == branch_id:
            return b
    return None


def ensure_manifest(estimate_id: str) -> Dict[str, Any]:
    """Return the branches manifest, lazily migrating legacy estimates that
    predate branching into a single 'main' branch. Also backfills `kind` on
    pre-CO entries so consumers can rely on it being present."""
    manifest = _download_json(_manifest_path(estimate_id))
    if manifest and isinstance(manifest.get("branches"), list) and manifest["branches"]:
        # Backfill kind on existing entries that predate the CO field.
        dirty = False
        for entry in manifest["branches"]:
            if "kind" not in entry:
                entry["kind"] = BRANCH_KIND_VARIATION
                dirty = True
        if dirty:
            try:
                _upload_json(_manifest_path(estimate_id), manifest)
            except Exception as exc:
                logger.debug("[ESTIMATOR] kind backfill upload skipped for %s: %s", estimate_id, exc)
        return manifest

    data = _download_json(f"{estimate_id}/estimate.ngm") or {}
    now_iso = datetime.now().isoformat()
    created = data.get("created_at") or now_iso
    updated = data.get("updated_at") or now_iso
    meta = extract_estimate_meta(estimate_id, data)
    manifest = {
        "main_branch_id": MAIN_BRANCH_ID,
        "project_name": meta["name"],
        "project": meta["project"],
        "project_type": meta["project_type"],
        "subtotal": meta["subtotal"],
        "created_at": created,
        "updated_at": updated,
        "branches": [{
            "id": MAIN_BRANCH_ID,
            "name": "Main",
            "kind": BRANCH_KIND_VARIATION,
            "created_at": created,
            "updated_at": updated,
            "grand_total": meta["grand_total"],
            "based_on": None,
        }],
    }
    _upload_json(_manifest_path(estimate_id), manifest)
    return manifest


def read_branch_content(estimate_id: str, branch_id: str, manifest: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Load a branch's .ngm. The main branch falls back to the root estimate.ngm
    when no per-branch file exists yet (legacy / lazily-migrated estimates)."""
    data = _download_json(_branch_path(estimate_id, branch_id))
    if data is None and branch_id == manifest.get("main_branch_id"):
        data = _download_json(f"{estimate_id}/estimate.ngm")
    return data


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
async def list_estimates(
    company_id: Optional[str] = Query(None, description="Scope the board to the active workspace. Estimates with no company (legacy) are shown in all workspaces."),
):
    """
    List all saved estimates from the estimates bucket.
    Returns folders (each estimate is a folder). When company_id is given, the
    board is scoped to that workspace's estimates (plus untagged/shared ones).
    """
    try:
        ensure_bucket_exists(ESTIMATES_BUCKET)

        files = supabase.storage.from_(ESTIMATES_BUCKET).list()

        # Filter for folders (estimates) - folders have no extension in name.
        # Prefer the lightweight branches.json for board metadata (dates, grand
        # total, project, branch count); fall back to opening estimate.ngm for
        # legacy estimates not yet migrated. Folder metadata is unreliable for
        # pseudo-folders, so it's only the last resort.
        estimates = []
        for item in files or []:
            name = item.get("name", "")
            if not name or "." in name:
                continue
            summary = {
                "id": name,
                "name": name,
                "created_at": item.get("created_at"),
                "updated_at": item.get("updated_at"),
                "subtotal": None,
                "grand_total": None,
                "project": None,
                "project_type": None,
                "branch_count": 1,
                "variation_count": 0,
                "change_order_count": 0,
                "review_status": "none",
            }
            est_company = None
            try:
                manifest = _download_json(_manifest_path(name))
                if manifest and isinstance(manifest.get("branches"), list) and manifest["branches"]:
                    est_company = manifest.get("company_id")
                    main = find_branch(manifest, manifest.get("main_branch_id")) or manifest["branches"][0]
                    branches = manifest["branches"]
                    co_count = sum(1 for b in branches if b.get("kind") == BRANCH_KIND_CHANGE_ORDER)
                    main_id = manifest.get("main_branch_id")
                    # Variations = every non-main, non-CO branch. Pre-CO entries
                    # default to "variation" so this stays correct historically.
                    var_count = sum(
                        1 for b in branches
                        if b.get("id") != main_id and b.get("kind") != BRANCH_KIND_CHANGE_ORDER
                    )
                    # Card-level review state: surface the most actionable across
                    # branches (a branch awaiting approval outranks an approved one)
                    # so the board flags "Pending approval".
                    _review_rank = {"under_review": 4, "changes_requested": 3, "approved": 2, "rejected": 1, "none": 0}
                    review_status = "none"
                    for b in branches:
                        rs = b.get("review_status") or "none"
                        if _review_rank.get(rs, 0) > _review_rank.get(review_status, 0):
                            review_status = rs
                    summary.update({
                        "name": manifest.get("project_name") or name,
                        "created_at": manifest.get("created_at") or summary["created_at"],
                        "updated_at": manifest.get("updated_at") or summary["updated_at"],
                        "subtotal": manifest.get("subtotal"),
                        "grand_total": main.get("grand_total"),
                        "project": manifest.get("project"),
                        "project_type": manifest.get("project_type"),
                        "branch_count": len(branches),
                        "variation_count": var_count,
                        "change_order_count": co_count,
                        "review_status": review_status,
                    })
                else:
                    data = _download_json(f"{name}/estimate.ngm")
                    if data:
                        summary.update(extract_estimate_meta(name, data))
                        summary["id"] = name  # keep folder id as the canonical key
            except Exception as _exc:
                logger.debug("[ESTIMATOR] list meta skip for %s: %s", name, _exc)
            # Workspace scope: drop estimates that belong to a different company.
            # Untagged/legacy estimates (no company_id) stay visible everywhere.
            if company_id and est_company and est_company != company_id:
                continue
            estimates.append(summary)

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
        is_new = not request.estimate_id

        now_iso = datetime.now().isoformat()

        # Resolve which branch this save targets. Brand-new estimates skip the
        # manifest read (nothing exists yet) and write the main branch.
        manifest = None if is_new else _download_json(_manifest_path(estimate_id))
        main_id = (manifest or {}).get("main_branch_id", MAIN_BRANCH_ID)
        branch_id = request.branch_id or main_id
        is_main = branch_id == main_id

        # Preserve the original creation date on updates (only set it once),
        # per branch. The main branch falls back to the root estimate.ngm.
        prior = _download_json(_branch_path(estimate_id, branch_id))
        if prior is None and is_main:
            prior = _download_json(f"{estimate_id}/estimate.ngm")
        created_at = (prior or {}).get("created_at") or now_iso

        # Prepare estimate data
        estimate_data = {
            "estimate_id": estimate_id,
            "branch_id": branch_id,
            "project_name": request.project_name,
            "project": request.project,
            "categories": request.categories,
            "overhead": request.overhead or {"percentage": 0, "amount": 0},
            "created_from_template": request.created_from_template,
            "created_at": created_at,
            "updated_at": now_iso,
            "version": "1.0"
        }

        # Write the canonical per-branch file, and mirror the main branch to the
        # root estimate.ngm so legacy HTML clients keep reading it.
        _upload_json(_branch_path(estimate_id, branch_id), estimate_data)
        if is_main:
            _upload_json(f"{estimate_id}/estimate.ngm", estimate_data)

        # Refresh the manifest entry for this branch (+ folder metadata from main).
        grand_total = compute_grand_total(estimate_data)
        if manifest is None:
            manifest = {"main_branch_id": main_id, "branches": []}
        # Tag the estimate with the owning workspace so the board can scope it.
        if request.company_id:
            manifest["company_id"] = request.company_id
        if is_main:
            meta = extract_estimate_meta(estimate_id, estimate_data)
            manifest["project_name"] = meta["name"]
            manifest["project"] = meta["project"]
            manifest["project_type"] = meta["project_type"]
            manifest["subtotal"] = meta["subtotal"]
            manifest["created_at"] = manifest.get("created_at") or created_at
            manifest["updated_at"] = now_iso
        entry = find_branch(manifest, branch_id)
        if entry is None:
            entry = {
                "id": branch_id,
                "name": "Main" if is_main else branch_id,
                "kind": BRANCH_KIND_VARIATION,
                "based_on": None,
                "created_at": created_at,
            }
            manifest["branches"].append(entry)
        # Older entries written before kind existed get backfilled here.
        if "kind" not in entry:
            entry["kind"] = BRANCH_KIND_VARIATION
        entry["grand_total"] = grand_total
        entry["created_at"] = entry.get("created_at") or created_at
        entry["updated_at"] = now_iso
        _upload_json(_manifest_path(estimate_id), manifest)

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
            "branch_id": branch_id,
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

        # Delete top-level files in the folder
        file_paths = [f"{estimate_id}/{f['name']}" for f in files]

        # Also delete nested per-branch files under branches/
        try:
            branch_files = supabase.storage.from_(ESTIMATES_BUCKET).list(f"{estimate_id}/branches")
            file_paths += [f"{estimate_id}/branches/{f['name']}" for f in (branch_files or [])]
        except Exception as _exc:
            logger.debug("[ESTIMATOR] no branches subfolder for %s: %s", estimate_id, _exc)

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
# BRANCH ENDPOINTS
# ============================================

@router.get("/estimates/{estimate_id}/branches")
async def get_branches(estimate_id: str):
    """List the branches (variations) of an estimate. Lazily migrates legacy
    estimates into a single 'main' branch."""
    try:
        ensure_bucket_exists(ESTIMATES_BUCKET)
        return ensure_manifest(estimate_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error listing branches: {str(e)}")


@router.get("/estimates/{estimate_id}/branches/{branch_id}")
async def get_branch(estimate_id: str, branch_id: str):
    """Load a specific branch's estimate content (.ngm)."""
    try:
        ensure_bucket_exists(ESTIMATES_BUCKET)
        manifest = ensure_manifest(estimate_id)
        if not find_branch(manifest, branch_id):
            raise HTTPException(status_code=404, detail=f"Branch not found: {branch_id}")
        data = read_branch_content(estimate_id, branch_id, manifest)
        if data is None:
            raise HTTPException(status_code=404, detail=f"Branch content not found: {branch_id}")
        return data
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error loading branch: {str(e)}")


@router.post("/estimates/{estimate_id}/branches")
async def create_branch(estimate_id: str, request: BranchCreateRequest):
    """Create a new branch. By default it's a `variation` — a full independent
    copy of the source branch. When `kind="change_order"` (or `empty=True`) it
    starts empty: the source's project metadata is carried over but
    `categories` and `overhead` are reset, so the user can build the CO's
    scope from scratch."""
    try:
        ensure_bucket_exists(ESTIMATES_BUCKET)
        manifest = ensure_manifest(estimate_id)

        kind = (request.kind or BRANCH_KIND_VARIATION).strip().lower()
        if kind not in BRANCH_KINDS:
            raise HTTPException(status_code=400, detail=f"Invalid branch kind: {kind}")
        is_co = kind == BRANCH_KIND_CHANGE_ORDER
        empty = bool(request.empty) or is_co

        source_id = request.based_on or manifest["main_branch_id"]
        if not find_branch(manifest, source_id):
            raise HTTPException(status_code=404, detail=f"Source branch not found: {source_id}")
        source = read_branch_content(estimate_id, source_id, manifest)
        if source is None:
            raise HTTPException(status_code=404, detail=f"Source branch content not found: {source_id}")

        branch_id = generate_branch_id(request.name)
        now_iso = datetime.now().isoformat()

        if empty:
            # Carry only project metadata; reset scope so the user starts at $0.
            new_data: Dict[str, Any] = {
                "estimate_id": estimate_id,
                "branch_id": branch_id,
                "branch_name": request.name,
                "project_name": source.get("project_name") or request.name,
                "project": source.get("project") or {},
                "categories": [],
                "overhead": {"percentage": 0, "amount": 0},
                "created_from_template": source.get("created_from_template"),
                "created_at": now_iso,
                "updated_at": now_iso,
                "version": source.get("version") or "1.0",
            }
        else:
            new_data = dict(source)
            new_data["estimate_id"] = estimate_id
            new_data["branch_id"] = branch_id
            new_data["branch_name"] = request.name
            new_data["created_at"] = now_iso
            new_data["updated_at"] = now_iso
        _upload_json(_branch_path(estimate_id, branch_id), new_data)

        entry: Dict[str, Any] = {
            "id": branch_id,
            "name": request.name,
            "kind": kind,
            "based_on": source_id,
            "created_at": now_iso,
            "updated_at": now_iso,
            "grand_total": compute_grand_total(new_data),
        }
        if is_co:
            status = (request.status or "pending").strip().lower()
            if status not in CO_STATUSES:
                raise HTTPException(status_code=400, detail=f"Invalid CO status: {status}")
            entry["status"] = status
        manifest["branches"].append(entry)
        _upload_json(_manifest_path(estimate_id), manifest)

        return {"success": True, "branch_id": branch_id, "manifest": manifest}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error creating branch: {str(e)}")


@router.patch("/estimates/{estimate_id}/branches/{branch_id}")
async def update_branch(estimate_id: str, branch_id: str, request: BranchUpdateRequest):
    """Update mutable manifest fields on a branch (name, status, kind). Every
    field is optional; only those provided in the body get written. Validates
    `status` (only for change_orders) and `kind` against the allowed sets."""
    try:
        ensure_bucket_exists(ESTIMATES_BUCKET)
        manifest = ensure_manifest(estimate_id)
        entry = find_branch(manifest, branch_id)
        if not entry:
            raise HTTPException(status_code=404, detail=f"Branch not found: {branch_id}")

        changed = False
        if request.name is not None:
            new_name = request.name.strip()
            if not new_name:
                raise HTTPException(status_code=400, detail="Branch name cannot be empty")
            entry["name"] = new_name
            changed = True

        if request.kind is not None:
            kind = request.kind.strip().lower()
            if kind not in BRANCH_KINDS:
                raise HTTPException(status_code=400, detail=f"Invalid branch kind: {kind}")
            entry["kind"] = kind
            # Switching away from change_order drops a stale status; switching
            # in seeds a sensible default so the UI has something to render.
            if kind != BRANCH_KIND_CHANGE_ORDER:
                entry.pop("status", None)
            elif "status" not in entry:
                entry["status"] = "pending"
            changed = True

        if request.status is not None:
            status = request.status.strip().lower()
            if status not in CO_STATUSES:
                raise HTTPException(status_code=400, detail=f"Invalid CO status: {status}")
            if entry.get("kind") != BRANCH_KIND_CHANGE_ORDER:
                raise HTTPException(status_code=400, detail="status only applies to change_order branches")
            entry["status"] = status
            changed = True

        if changed:
            entry["updated_at"] = datetime.now().isoformat()
            _upload_json(_manifest_path(estimate_id), manifest)
        return {"success": True, "manifest": manifest}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error updating branch: {str(e)}")


@router.post("/estimates/{estimate_id}/branches/{branch_id}/set-main")
async def set_main_branch(estimate_id: str, branch_id: str):
    """Promote a branch to be the main one. Mirrors its content to the root
    estimate.ngm (legacy reads) and records it in the manifest."""
    try:
        ensure_bucket_exists(ESTIMATES_BUCKET)
        manifest = ensure_manifest(estimate_id)
        if not find_branch(manifest, branch_id):
            raise HTTPException(status_code=404, detail=f"Branch not found: {branch_id}")

        old_main = manifest.get("main_branch_id")
        data = read_branch_content(estimate_id, branch_id, manifest)
        if data is None:
            raise HTTPException(status_code=404, detail=f"Branch content not found: {branch_id}")

        # Guarantee the outgoing main keeps its own per-branch file.
        if old_main and old_main != branch_id:
            old_data = read_branch_content(estimate_id, old_main, manifest)
            if old_data is not None:
                _upload_json(_branch_path(estimate_id, old_main), old_data)

        # Mirror the new main to the root estimate.ngm.
        _upload_json(f"{estimate_id}/estimate.ngm", data)

        manifest["main_branch_id"] = branch_id
        meta = extract_estimate_meta(estimate_id, data)
        manifest["project_name"] = meta["name"]
        manifest["project"] = meta["project"]
        manifest["project_type"] = meta["project_type"]
        manifest["subtotal"] = meta["subtotal"]
        manifest["updated_at"] = datetime.now().isoformat()
        _upload_json(_manifest_path(estimate_id), manifest)

        return {"success": True, "manifest": manifest}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error promoting branch: {str(e)}")


@router.delete("/estimates/{estimate_id}/branches/{branch_id}")
async def delete_branch(estimate_id: str, branch_id: str):
    """Delete a branch. The main branch cannot be deleted (promote another first)."""
    try:
        ensure_bucket_exists(ESTIMATES_BUCKET)
        manifest = ensure_manifest(estimate_id)
        if branch_id == manifest.get("main_branch_id"):
            raise HTTPException(status_code=400, detail="Cannot delete the main branch. Set another branch as main first.")
        if not find_branch(manifest, branch_id):
            raise HTTPException(status_code=404, detail=f"Branch not found: {branch_id}")

        try:
            supabase.storage.from_(ESTIMATES_BUCKET).remove([_branch_path(estimate_id, branch_id)])
        except Exception as _exc:
            logger.debug("[ESTIMATOR] branch file remove note %s: %s", branch_id, _exc)

        manifest["branches"] = [b for b in manifest["branches"] if b.get("id") != branch_id]
        _upload_json(_manifest_path(estimate_id), manifest)

        return {"success": True, "manifest": manifest}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error deleting branch: {str(e)}")


# ============================================
# BRANCH REVIEW + CARÁTULA
# ----------------------------------------------------------------------------
# Any branch (a variation, a change order, a budget version) can be sent to
# review with its own persisted carátula. Review state lives on the manifest
# branch entry; the carátula snapshot is a separate JSON blob per branch.
# ============================================

REVIEW_STATUSES = {"under_review", "approved", "rejected", "changes_requested"}


def _caratula_path(estimate_id: str, branch_id: str) -> str:
    return f"{estimate_id}/branches/{branch_id}/caratula.json"


class CaratulaSaveRequest(BaseModel):
    caratula: Dict[str, Any]                          # SheetDocument snapshot


class SendToReviewRequest(BaseModel):
    caratula: Optional[Dict[str, Any]] = None         # persist this snapshot on send
    reviewer_user_ids: Optional[List[str]] = None     # explicit reviewers; else role-resolved
    note: Optional[str] = None


class ReviewDecisionRequest(BaseModel):
    decision: str                                     # approved | rejected | changes_requested
    note: Optional[str] = None


class MarkPromotedRequest(BaseModel):
    """Stamp a branch as promoted to a project's budget. Records the estimate ⇄
    project ⇄ budget link on the branch so it is traceable from the estimator
    side (the budget rows carry the reverse link via source_estimate_id)."""
    project_id: str
    budget_batch_id: Optional[str] = None             # batch returned by /budgets/import


@router.put("/estimates/{estimate_id}/branches/{branch_id}/caratula")
async def save_branch_caratula(estimate_id: str, branch_id: str, request: CaratulaSaveRequest):
    """Persist a generated carátula (cover/export snapshot) for one branch so it
    can be viewed later (e.g. by a reviewer) independent of live edits."""
    try:
        ensure_bucket_exists(ESTIMATES_BUCKET)
        manifest = ensure_manifest(estimate_id)
        entry = find_branch(manifest, branch_id)
        if not entry:
            raise HTTPException(status_code=404, detail=f"Branch not found: {branch_id}")
        now_iso = datetime.now().isoformat()
        _upload_json(_caratula_path(estimate_id, branch_id), {
            "branch_id": branch_id, "updated_at": now_iso, "document": request.caratula,
        })
        entry["has_caratula"] = True
        entry["caratula_updated_at"] = now_iso
        _upload_json(_manifest_path(estimate_id), manifest)
        return {"success": True, "updated_at": now_iso}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error saving carátula: {str(e)}")


@router.get("/estimates/{estimate_id}/branches/{branch_id}/caratula")
async def get_branch_caratula(estimate_id: str, branch_id: str):
    """Return the stored carátula snapshot for a branch (404 if none)."""
    data = _download_json(_caratula_path(estimate_id, branch_id))
    if data is None:
        raise HTTPException(status_code=404, detail="No carátula stored for this branch")
    return data


@router.post("/estimates/{estimate_id}/branches/{branch_id}/send-to-review")
async def send_branch_to_review(estimate_id: str, branch_id: str, request: SendToReviewRequest):
    """Send a branch to review: mark it under_review, record reviewers + timestamp,
    and persist its carátula when provided. Reviewers default to every user whose
    role has can_review_estimates (CEO/COO always). Also creates the review task
    (best-effort) so the reviewer sees the carátula + a link to this branch."""
    try:
        ensure_bucket_exists(ESTIMATES_BUCKET)
        manifest = ensure_manifest(estimate_id)
        entry = find_branch(manifest, branch_id)
        if not entry:
            raise HTTPException(status_code=404, detail=f"Branch not found: {branch_id}")

        # Resolve reviewers: explicit override, else role-based. Lazy import keeps
        # the routers decoupled / avoids any import cycle.
        reviewer_ids = request.reviewer_user_ids
        if not reviewer_ids:
            try:
                from api.routers.permissions import resolve_estimate_reviewer_user_ids
                reviewer_ids = resolve_estimate_reviewer_user_ids()
            except Exception as exc:
                logger.warning("[ESTIMATOR] reviewer resolve failed: %s", exc)
                reviewer_ids = []

        now_iso = datetime.now().isoformat()
        if request.caratula is not None:
            _upload_json(_caratula_path(estimate_id, branch_id), {
                "branch_id": branch_id, "updated_at": now_iso, "document": request.caratula,
            })
            entry["has_caratula"] = True
            entry["caratula_updated_at"] = now_iso

        entry["review_status"] = "under_review"
        entry["review_sent_at"] = now_iso
        entry["review_reviewer_ids"] = reviewer_ids
        if request.note is not None:
            entry["review_note"] = request.note
        entry["updated_at"] = now_iso
        _upload_json(_manifest_path(estimate_id), manifest)

        # Create the review task for the resolved reviewers. Forward-compatible:
        # this no-ops cleanly until the pipeline helper lands (Phase 3).
        task_id = None
        try:
            from api.routers.pipeline import create_estimate_review_task
            task_id = create_estimate_review_task(
                estimate_id=estimate_id, branch=entry, manifest=manifest,
                reviewer_ids=reviewer_ids, note=request.note,
            )
            if task_id:
                entry["review_task_id"] = task_id
                _upload_json(_manifest_path(estimate_id), manifest)
        except Exception as exc:
            logger.warning("[ESTIMATOR] review task create skipped: %s", exc)

        return {"success": True, "manifest": manifest, "reviewer_ids": reviewer_ids, "task_id": task_id}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error sending branch to review: {str(e)}")


def apply_branch_review_decision(estimate_id: str, branch_id: str, decision: str, note=None):
    """Record a reviewer's decision on a branch and fan out the approval side-effects.

    Shared by the in-estimator decision endpoint AND the dashboard "Approve" flow
    (pipeline.approve_task), so a reviewer approving from either place produces the
    same result: the branch is stamped, the review task advances (approved -> Done),
    and on approval the branch hands off in parallel to Budgets (Costs) and to
    Coordination (Send estimate). Returns the updated manifest.
    """
    ensure_bucket_exists(ESTIMATES_BUCKET)
    decision = (decision or "").strip().lower()
    if decision not in {"approved", "rejected", "changes_requested"}:
        raise HTTPException(status_code=400, detail=f"Invalid decision: {decision}")
    manifest = ensure_manifest(estimate_id)
    entry = find_branch(manifest, branch_id)
    if not entry:
        raise HTTPException(status_code=404, detail=f"Branch not found: {branch_id}")

    now_iso = datetime.now().isoformat()
    entry["review_status"] = decision
    entry["review_decided_at"] = now_iso
    if note is not None:
        entry["review_note"] = note
    if entry.get("kind") == BRANCH_KIND_CHANGE_ORDER and decision in ("approved", "rejected"):
        entry["status"] = decision
    entry["updated_at"] = now_iso
    _upload_json(_manifest_path(estimate_id), manifest)

    # Mirror the decision onto the review task so the pipeline board advances
    # itself (approved -> Done, changes_requested -> Resubmittal Needed,
    # rejected -> Done). Best-effort; never blocks the decision.
    try:
        from api.routers.pipeline import set_estimate_review_task_status
        set_estimate_review_task_status(estimate_id, entry, decision)
    except Exception as exc:
        logger.warning("[ESTIMATOR] review task status sync skipped: %s", exc)

    # On approval, fan out the two parallel handoffs. Best-effort, idempotent:
    #  - Costs/Budgets: import the approved estimate into Budgets.
    #  - Coordination: send the approved estimate to the client.
    if decision == "approved":
        try:
            from api.routers.pipeline import create_estimate_to_budget_task
            create_estimate_to_budget_task(estimate_id, entry, manifest)
        except Exception as exc:
            logger.warning("[ESTIMATOR] budget handoff task skipped: %s", exc)
        try:
            from api.routers.pipeline import create_send_estimate_task
            create_send_estimate_task(estimate_id, entry, manifest)
        except Exception as exc:
            logger.warning("[ESTIMATOR] send-estimate handoff task skipped: %s", exc)

    return manifest


@router.post("/estimates/{estimate_id}/branches/{branch_id}/review-decision")
async def decide_branch_review(estimate_id: str, branch_id: str, request: ReviewDecisionRequest):
    """Record a reviewer's decision on a branch. For change_order branches the
    approved/rejected decision is mirrored onto the CO status so the Contract
    Value rollup stays consistent."""
    try:
        manifest = apply_branch_review_decision(estimate_id, branch_id, request.decision, note=request.note)
        return {"success": True, "manifest": manifest}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error recording review decision: {str(e)}")


@router.post("/estimates/{estimate_id}/branches/{branch_id}/mark-promoted")
async def mark_branch_promoted(estimate_id: str, branch_id: str, request: MarkPromotedRequest):
    """Record that a branch was promoted to a project's budget (the
    estimate→project→budget link). Called by the frontend right after a
    successful POST /budgets/import so the estimator can show "Promoted to
    project X" and stop re-prompting. Idempotent: re-promoting just overwrites
    the stamp with the latest project/batch."""
    try:
        ensure_bucket_exists(ESTIMATES_BUCKET)
        manifest = ensure_manifest(estimate_id)
        entry = find_branch(manifest, branch_id)
        if not entry:
            raise HTTPException(status_code=404, detail=f"Branch not found: {branch_id}")
        now_iso = datetime.now().isoformat()
        entry["promoted_to_project_id"] = request.project_id
        entry["promoted_budget_batch_id"] = request.budget_batch_id
        entry["promoted_at"] = now_iso
        entry["updated_at"] = now_iso

        # Keep the estimate's workspace aligned with the project it was promoted
        # into. The estimator board scopes by the manifest's company_id while
        # Budgets/Projects scope by projects.source_company; promotion is the
        # moment the project (the destination workspace) becomes authoritative,
        # so mirror it here. Without this the same deal can show under company A
        # in Estimates and company B in Budgets. Best-effort: never block the
        # promotion stamp on the lookup.
        try:
            proj = (
                supabase.table("projects")
                .select("source_company")
                .eq("project_id", request.project_id)
                .single()
                .execute()
            )
            src_company = (proj.data or {}).get("source_company")
            if src_company and manifest.get("company_id") != src_company:
                logger.info(
                    "[ESTIMATOR] aligning estimate %s company_id %s -> %s (project %s)",
                    estimate_id, manifest.get("company_id"), src_company, request.project_id,
                )
                manifest["company_id"] = src_company
        except Exception as exc:
            logger.warning("[ESTIMATOR] company sync on promote skipped: %s", exc)

        _upload_json(_manifest_path(estimate_id), manifest)
        return {"success": True, "manifest": manifest}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error marking branch promoted: {str(e)}")


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
            folder = item.get("name", "")
            if not folder or "." in folder:
                continue
            # Prefer the friendly name/description from template_meta.json; the
            # folder id ("spearhead-adu-estimate-1780989508130") is the canonical
            # key but an unreadable label. Fall back to the folder name if the
            # meta is missing or unreadable so the template still lists.
            display_name = folder
            description = None
            try:
                blob = supabase.storage.from_(TEMPLATES_BUCKET).download(f"{folder}/template_meta.json")
                if blob:
                    meta = json.loads(blob.decode("utf-8"))
                    display_name = (meta.get("name") or "").strip() or folder
                    description = meta.get("description")
            except Exception as _exc:
                logger.debug("[ESTIMATOR] template meta skip for %s: %s", folder, _exc)
            templates.append({
                "id": folder,
                "name": display_name,
                "description": description,
                "created_at": item.get("created_at"),
                "updated_at": item.get("updated_at"),
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
