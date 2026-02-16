# api/services/vault_service.py
# ================================
# Vault Service - Business Logic
# ================================
# Handles file/folder CRUD, versioning, chunked uploads,
# search, and duplicate detection for the Vault module.

import hashlib
import logging
import os
import shutil
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from api.supabase_client import supabase

logger = logging.getLogger(__name__)

VAULT_BUCKET = "vault"
CHUNK_TEMP_DIR = os.path.join(os.environ.get("TEMP", "/tmp"), "vault_uploads")


# ============================================
# Helpers
# ============================================

def _safe_filename(name: str) -> str:
    """Sanitize filename for storage path."""
    return "".join(c if c.isalnum() or c in ".-_ " else "_" for c in name).strip()


def _resolve_folder_path(parent_id: Optional[str]) -> str:
    """
    Walk up parent_id chain to build a human-readable folder path.
    Returns '' for root level, or 'FolderA/SubFolder' for nested.
    """
    if not parent_id:
        return ""
    parts = []
    current_id = parent_id
    visited = set()
    while current_id and current_id not in visited:
        visited.add(current_id)
        result = (
            supabase.table("vault_files")
            .select("name, parent_id")
            .eq("id", current_id)
            .limit(1)
            .execute()
        )
        if not result.data:
            break
        row = result.data[0]
        parts.append(_safe_filename(row["name"]))
        current_id = row.get("parent_id")
    parts.reverse()
    return "/".join(parts)


def _resolve_project_name(project_id: Optional[str]) -> str:
    """Resolve project UUID to human-readable name for storage path."""
    if not project_id:
        return ""
    try:
        result = (
            supabase.table("projects")
            .select("project_name")
            .eq("project_id", project_id)
            .limit(1)
            .execute()
        )
        if result.data:
            return _safe_filename(result.data[0]["project_name"])
    except Exception:
        pass
    return project_id  # fallback to UUID


def _storage_path(project_id: Optional[str], file_id: str, version: int, ext: str,
                  parent_id: Optional[str] = None, filename: str = "") -> str:
    """
    Generate human-readable storage path for rclone mount compatibility.
    Format: Global/FolderA/SubFolder/filename_v1.ext
        or: Projects/ProjectName/FolderA/filename_v1.ext
    """
    # Root prefix
    if project_id:
        project_name = _resolve_project_name(project_id)
        prefix = f"Projects/{project_name}"
    else:
        prefix = "Global"

    # Folder hierarchy
    folder_path = _resolve_folder_path(parent_id)

    # Filename with version suffix
    safe_name = _safe_filename(filename or file_id)
    base_name, _ = os.path.splitext(safe_name)
    versioned_name = f"{base_name}_v{version}{ext}"

    # Build full path
    parts = [prefix]
    if folder_path:
        parts.append(folder_path)
    parts.append(versioned_name)
    return "/".join(parts)


def _get_extension(filename: str) -> str:
    """Extract file extension including the dot."""
    _, ext = os.path.splitext(filename)
    return ext.lower() if ext else ""


def _compute_hash(data: bytes) -> str:
    """SHA-256 hash of file content."""
    return hashlib.sha256(data).hexdigest()


def _ensure_bucket():
    """Ensure the vault bucket exists."""
    try:
        supabase.storage.get_bucket(VAULT_BUCKET)
    except Exception:
        try:
            supabase.storage.create_bucket(VAULT_BUCKET, options={"public": True})
            logger.info("[Vault] Created bucket: %s", VAULT_BUCKET)
        except Exception as e:
            logger.warning("[Vault] Bucket creation note: %s", e)


# ============================================
# Folder Operations
# ============================================

def create_folder(
    name: str,
    parent_id: Optional[str],
    project_id: Optional[str],
    user_id: str,
) -> Dict[str, Any]:
    """Create a virtual folder."""
    row = {
        "name": name,
        "is_folder": True,
        "parent_id": parent_id,
        "project_id": project_id,
        "uploaded_by": user_id,
    }
    result = supabase.table("vault_files").insert(row).execute()
    return result.data[0] if result.data else {}


DEFAULT_PROJECT_FOLDERS = [
    "Plans",
    "Archviz",
    "Budgets",
    "Contracts",
    "Documents",
    "Photos",
    "Reports",
    "Permits",
    "Receipts",
]


def create_default_folders(project_id: str) -> List[Dict[str, Any]]:
    """Create the default folder structure for a new project."""
    created = []
    for name in DEFAULT_PROJECT_FOLDERS:
        row = {
            "name": name,
            "is_folder": True,
            "parent_id": None,
            "project_id": project_id,
            "uploaded_by": None,
        }
        result = supabase.table("vault_files").insert(row).execute()
        if result.data:
            created.append(result.data[0])
    logger.info("[Vault] Created %d default folders for project %s", len(created), project_id)
    return created


def save_to_project_folder(
    project_id: str,
    folder_name: str,
    file_content: bytes,
    filename: str,
    content_type: str,
) -> Optional[Dict[str, Any]]:
    """
    Save a file into a named root-level folder of a project's vault.
    Finds the folder by name; creates it if missing. Then uploads the file.
    Returns the vault_files record or None on failure.
    """
    try:
        # Find existing folder
        result = (
            supabase.table("vault_files")
            .select("id")
            .eq("project_id", project_id)
            .eq("is_folder", True)
            .eq("name", folder_name)
            .is_("parent_id", "null")
            .eq("is_deleted", False)
            .limit(1)
            .execute()
        )

        if result.data:
            folder_id = result.data[0]["id"]
        else:
            folder = create_folder(folder_name, None, project_id, None)
            folder_id = folder.get("id")

        if not folder_id:
            logger.warning("[Vault] Could not resolve folder '%s' for project %s", folder_name, project_id)
            return None

        return upload_file(file_content, filename, content_type, folder_id, project_id, None)
    except Exception as e:
        logger.warning("[Vault] save_to_project_folder failed (%s/%s): %s", project_id, folder_name, e)
        return None


def get_folder_tree(project_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Get all folders as a flat list (frontend builds tree).
    Filtered by project_id (None = global).
    """
    query = (
        supabase.table("vault_files")
        .select("id, name, parent_id, project_id, created_at")
        .eq("is_folder", True)
        .eq("is_deleted", False)
    )
    if project_id:
        query = query.eq("project_id", project_id)
    else:
        query = query.is_("project_id", "null")

    result = query.order("name").execute()
    return result.data or []


# ============================================
# File Listing & Details
# ============================================

def list_files(
    parent_id: Optional[str] = None,
    project_id: Optional[str] = None,
    include_children: bool = False,
) -> List[Dict[str, Any]]:
    """
    List files/folders in a given parent folder.
    If parent_id is None, lists root-level items.
    """
    query = (
        supabase.table("vault_files")
        .select("id, name, is_folder, parent_id, project_id, bucket_path, mime_type, size_bytes, file_hash, uploaded_by, created_at, updated_at")
        .eq("is_deleted", False)
    )

    if parent_id:
        query = query.eq("parent_id", parent_id)
    else:
        query = query.is_("parent_id", "null")

    if project_id:
        query = query.eq("project_id", project_id)
    else:
        # When no project_id filter, show global items (project_id IS NULL)
        if not parent_id:
            query = query.is_("project_id", "null")

    result = query.order("is_folder", desc=True).order("name").execute()
    return result.data or []


def get_file(file_id: str) -> Optional[Dict[str, Any]]:
    """Get single file/folder metadata."""
    result = (
        supabase.table("vault_files")
        .select("*")
        .eq("id", file_id)
        .eq("is_deleted", False)
        .execute()
    )
    return result.data[0] if result.data else None


# ============================================
# File Upload (single, < 50MB)
# ============================================

def upload_file(
    file_content: bytes,
    filename: str,
    content_type: str,
    parent_id: Optional[str],
    project_id: Optional[str],
    user_id: str,
) -> Dict[str, Any]:
    """Upload a file and create v1."""
    _ensure_bucket()

    file_id = str(uuid.uuid4())
    ext = _get_extension(filename)
    file_hash = _compute_hash(file_content)
    bucket_path = _storage_path(project_id, file_id, 1, ext,
                                parent_id=parent_id, filename=filename)

    # Upload to Supabase Storage
    supabase.storage.from_(VAULT_BUCKET).upload(
        path=bucket_path,
        file=file_content,
        file_options={"content-type": content_type, "upsert": "true"},
    )

    public_url = supabase.storage.from_(VAULT_BUCKET).get_public_url(bucket_path)

    # Create vault_files record
    file_row = {
        "id": file_id,
        "name": filename,
        "is_folder": False,
        "parent_id": parent_id,
        "project_id": project_id,
        "bucket_path": bucket_path,
        "mime_type": content_type,
        "size_bytes": len(file_content),
        "file_hash": file_hash,
        "uploaded_by": user_id,
    }
    file_result = supabase.table("vault_files").insert(file_row).execute()

    # Create version 1
    version_row = {
        "file_id": file_id,
        "version_number": 1,
        "bucket_path": bucket_path,
        "size_bytes": len(file_content),
        "uploaded_by": user_id,
        "comment": "Initial upload",
    }
    supabase.table("vault_file_versions").insert(version_row).execute()

    data = file_result.data[0] if file_result.data else file_row
    data["public_url"] = public_url
    return data


# ============================================
# Chunked Upload (for > 50MB files)
# ============================================

def store_chunk(upload_id: str, chunk_index: int, chunk_data: bytes) -> Dict[str, Any]:
    """Store a single chunk to temp directory."""
    upload_dir = os.path.join(CHUNK_TEMP_DIR, upload_id)
    os.makedirs(upload_dir, exist_ok=True)

    chunk_path = os.path.join(upload_dir, f"chunk_{chunk_index:06d}")
    with open(chunk_path, "wb") as f:
        f.write(chunk_data)

    return {"upload_id": upload_id, "chunk_index": chunk_index, "stored": True}


def assemble_chunks(
    upload_id: str,
    filename: str,
    total_chunks: int,
    content_type: str,
    parent_id: Optional[str],
    project_id: Optional[str],
    user_id: str,
) -> Dict[str, Any]:
    """Assemble chunks into a complete file, upload to storage, create records."""
    _ensure_bucket()

    upload_dir = os.path.join(CHUNK_TEMP_DIR, upload_id)

    # Assemble all chunks
    assembled = bytearray()
    for i in range(total_chunks):
        chunk_path = os.path.join(upload_dir, f"chunk_{i:06d}")
        if not os.path.exists(chunk_path):
            raise FileNotFoundError(f"Missing chunk {i} for upload {upload_id}")
        with open(chunk_path, "rb") as f:
            assembled.extend(f.read())

    file_content = bytes(assembled)
    file_id = str(uuid.uuid4())
    ext = _get_extension(filename)
    file_hash = _compute_hash(file_content)
    bucket_path = _storage_path(project_id, file_id, 1, ext,
                                parent_id=parent_id, filename=filename)

    # Upload assembled file to Supabase Storage
    supabase.storage.from_(VAULT_BUCKET).upload(
        path=bucket_path,
        file=file_content,
        file_options={"content-type": content_type, "upsert": "true"},
    )

    public_url = supabase.storage.from_(VAULT_BUCKET).get_public_url(bucket_path)

    # Create vault_files record
    file_row = {
        "id": file_id,
        "name": filename,
        "is_folder": False,
        "parent_id": parent_id,
        "project_id": project_id,
        "bucket_path": bucket_path,
        "mime_type": content_type,
        "size_bytes": len(file_content),
        "file_hash": file_hash,
        "uploaded_by": user_id,
    }
    file_result = supabase.table("vault_files").insert(file_row).execute()

    # Create version 1
    version_row = {
        "file_id": file_id,
        "version_number": 1,
        "bucket_path": bucket_path,
        "size_bytes": len(file_content),
        "uploaded_by": user_id,
        "comment": "Initial upload",
    }
    supabase.table("vault_file_versions").insert(version_row).execute()

    # Cleanup temp chunks
    try:
        shutil.rmtree(upload_dir)
    except Exception as e:
        logger.warning("[Vault] Failed to clean temp dir: %s", e)

    data = file_result.data[0] if file_result.data else file_row
    data["public_url"] = public_url
    return data


# ============================================
# Versioning
# ============================================

def list_versions(file_id: str) -> List[Dict[str, Any]]:
    """Get all versions of a file, newest first."""
    result = (
        supabase.table("vault_file_versions")
        .select("*")
        .eq("file_id", file_id)
        .order("version_number", desc=True)
        .execute()
    )
    return result.data or []


def create_version(
    file_id: str,
    file_content: bytes,
    filename: str,
    content_type: str,
    user_id: str,
    comment: Optional[str] = None,
) -> Dict[str, Any]:
    """Upload a new version of an existing file."""
    _ensure_bucket()

    # Get current file record
    file_rec = get_file(file_id)
    if not file_rec:
        raise ValueError(f"File {file_id} not found")

    # Get next version number
    versions = list_versions(file_id)
    next_version = (versions[0]["version_number"] + 1) if versions else 1

    ext = _get_extension(filename)
    file_hash = _compute_hash(file_content)
    bucket_path = _storage_path(file_rec.get("project_id"), file_id, next_version, ext,
                                parent_id=file_rec.get("parent_id"), filename=filename)

    # Upload to storage
    supabase.storage.from_(VAULT_BUCKET).upload(
        path=bucket_path,
        file=file_content,
        file_options={"content-type": content_type, "upsert": "true"},
    )

    # Create version record
    version_row = {
        "file_id": file_id,
        "version_number": next_version,
        "bucket_path": bucket_path,
        "size_bytes": len(file_content),
        "uploaded_by": user_id,
        "comment": comment,
    }
    version_result = supabase.table("vault_file_versions").insert(version_row).execute()

    # Update file record with latest version info
    supabase.table("vault_files").update({
        "bucket_path": bucket_path,
        "size_bytes": len(file_content),
        "file_hash": file_hash,
        "mime_type": content_type,
    }).eq("id", file_id).execute()

    return version_result.data[0] if version_result.data else version_row


def restore_version(file_id: str, version_id: str, user_id: str) -> Dict[str, Any]:
    """Restore an old version as a new version (copies the old version's file)."""
    # Get the version to restore
    version_result = (
        supabase.table("vault_file_versions")
        .select("*")
        .eq("id", version_id)
        .eq("file_id", file_id)
        .execute()
    )
    if not version_result.data:
        raise ValueError(f"Version {version_id} not found for file {file_id}")

    old_version = version_result.data[0]

    # Download old version file
    old_data = supabase.storage.from_(VAULT_BUCKET).download(old_version["bucket_path"])

    file_rec = get_file(file_id)
    if not file_rec:
        raise ValueError(f"File {file_id} not found")

    # Create a new version with the old content
    versions = list_versions(file_id)
    next_version = (versions[0]["version_number"] + 1) if versions else 1

    ext = _get_extension(file_rec["name"])
    bucket_path = _storage_path(
        file_rec.get("project_id"), file_id, next_version, ext,
        parent_id=file_rec.get("parent_id"), filename=file_rec.get("name", ""),
    )

    supabase.storage.from_(VAULT_BUCKET).upload(
        path=bucket_path,
        file=old_data,
        file_options={"content-type": file_rec.get("mime_type", "application/octet-stream"), "upsert": "true"},
    )

    version_row = {
        "file_id": file_id,
        "version_number": next_version,
        "bucket_path": bucket_path,
        "size_bytes": old_version.get("size_bytes", 0),
        "uploaded_by": user_id,
        "comment": f"Restored from version {old_version['version_number']}",
    }
    result = supabase.table("vault_file_versions").insert(version_row).execute()

    # Update file record
    supabase.table("vault_files").update({
        "bucket_path": bucket_path,
        "size_bytes": old_version.get("size_bytes", 0),
    }).eq("id", file_id).execute()

    return result.data[0] if result.data else version_row


# ============================================
# File Operations (move, rename, delete, duplicate)
# ============================================

def rename_file(file_id: str, new_name: str) -> Dict[str, Any]:
    """Rename a file or folder."""
    result = (
        supabase.table("vault_files")
        .update({"name": new_name})
        .eq("id", file_id)
        .eq("is_deleted", False)
        .execute()
    )
    return result.data[0] if result.data else {}


def move_file(file_id: str, new_parent_id: Optional[str]) -> Dict[str, Any]:
    """Move a file or folder to a different parent."""
    update = {"parent_id": new_parent_id}
    result = (
        supabase.table("vault_files")
        .update(update)
        .eq("id", file_id)
        .eq("is_deleted", False)
        .execute()
    )
    return result.data[0] if result.data else {}


def soft_delete(file_id: str) -> Dict[str, Any]:
    """
    Soft-delete a file or folder.
    For folders, recursively soft-delete all children.
    """
    file_rec = get_file(file_id)
    if not file_rec:
        raise ValueError(f"File {file_id} not found")

    if file_rec["is_folder"]:
        # Recursively delete children
        children = (
            supabase.table("vault_files")
            .select("id")
            .eq("parent_id", file_id)
            .eq("is_deleted", False)
            .execute()
        )
        for child in (children.data or []):
            soft_delete(child["id"])

    result = (
        supabase.table("vault_files")
        .update({"is_deleted": True})
        .eq("id", file_id)
        .execute()
    )
    return result.data[0] if result.data else {}


def duplicate_file(file_id: str, user_id: str) -> Dict[str, Any]:
    """Duplicate a file (creates a new file record pointing to the same storage)."""
    file_rec = get_file(file_id)
    if not file_rec:
        raise ValueError(f"File {file_id} not found")
    if file_rec["is_folder"]:
        raise ValueError("Cannot duplicate folders")

    # Download original
    file_content = supabase.storage.from_(VAULT_BUCKET).download(file_rec["bucket_path"])

    # Upload as new file with " (copy)" suffix
    base_name, ext = os.path.splitext(file_rec["name"])
    copy_name = f"{base_name} (copy){ext}"

    return upload_file(
        file_content=file_content,
        filename=copy_name,
        content_type=file_rec.get("mime_type", "application/octet-stream"),
        parent_id=file_rec.get("parent_id"),
        project_id=file_rec.get("project_id"),
        user_id=user_id,
    )


# ============================================
# Download
# ============================================

def get_download_url(file_id: str, version_id: Optional[str] = None) -> str:
    """Get public URL for downloading a file (optionally a specific version)."""
    if version_id:
        version_result = (
            supabase.table("vault_file_versions")
            .select("bucket_path")
            .eq("id", version_id)
            .execute()
        )
        if not version_result.data:
            raise ValueError(f"Version {version_id} not found")
        bucket_path = version_result.data[0]["bucket_path"]
    else:
        file_rec = get_file(file_id)
        if not file_rec:
            raise ValueError(f"File {file_id} not found")
        bucket_path = file_rec["bucket_path"]

    return supabase.storage.from_(VAULT_BUCKET).get_public_url(bucket_path)


# ============================================
# Search
# ============================================

def search_files(
    query: Optional[str] = None,
    project_id: Optional[str] = None,
    mime_type: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    is_folder: Optional[bool] = None,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    """Search files by name, type, date range, project."""
    q = (
        supabase.table("vault_files")
        .select("id, name, is_folder, parent_id, project_id, bucket_path, mime_type, size_bytes, uploaded_by, created_at, updated_at")
        .eq("is_deleted", False)
    )

    if query:
        q = q.ilike("name", f"%{query}%")
    if project_id:
        q = q.eq("project_id", project_id)
    if mime_type:
        q = q.ilike("mime_type", f"%{mime_type}%")
    if date_from:
        q = q.gte("created_at", date_from)
    if date_to:
        q = q.lte("created_at", date_to)
    if is_folder is not None:
        q = q.eq("is_folder", is_folder)

    result = q.order("updated_at", desc=True).limit(limit).execute()
    return result.data or []


def check_receipt_status(file_hashes: List[str]) -> Dict[str, str]:
    """
    Cross-reference vault file hashes with pending_receipts and expenses to determine
    which receipts have been fully authorized.
    Returns dict of {file_hash: status} where:
    - 'linked': All expenses for this receipt are authorized
    - 'pending': Receipt exists but expenses are not all authorized yet
    - (not in dict): Receipt not found
    """
    if not file_hashes:
        return {}
    try:
        # Step 1: Get pending_receipts that match the hashes
        receipts_result = (
            supabase.table("pending_receipts")
            .select("file_hash, file_url, status")
            .in_("file_hash", file_hashes)
            .execute()
        )

        if not receipts_result.data:
            return {}

        # Step 1.5: Detect processing/pending receipts early
        status_map = {}
        for row in receipts_result.data:
            h = row.get("file_hash")
            s = row.get("status")
            if h and s == "processing":
                status_map[h] = "processing"
            elif h and s in ("pending", "error", "check_review") and h not in status_map:
                status_map[h] = "pending"

        # Step 2: Build map of file_hash -> receipt_url (for ready/linked receipts)
        hash_to_url = {}
        for row in receipts_result.data:
            h = row.get("file_hash")
            url = row.get("file_url")
            s = row.get("status")
            if h and url and s in ("ready", "linked"):
                hash_to_url[h] = url

        if not hash_to_url:
            return status_map

        # Step 3: Get bills for these receipt URLs
        receipt_urls = list(set(hash_to_url.values()))
        bills_result = (
            supabase.table("bills")
            .select("bill_id, receipt_url")
            .in_("receipt_url", receipt_urls)
            .execute()
        )

        # Build map of receipt_url -> [bill_ids]
        url_to_bills = {}
        for bill in (bills_result.data or []):
            url = bill.get("receipt_url")
            bid = bill.get("bill_id")
            if url and bid:
                if url not in url_to_bills:
                    url_to_bills[url] = []
                url_to_bills[url].append(bid)

        # Step 4: Check authorization status for expenses in these bills
        status_map = {}
        for file_hash, receipt_url in hash_to_url.items():
            bill_ids = url_to_bills.get(receipt_url, [])
            if not bill_ids:
                # No bills found for this receipt yet
                status_map[file_hash] = "pending"
                continue

            # Get all expenses for these bill_ids
            expenses_result = (
                supabase.table("expenses_manual_COGS")
                .select("auth_status, status")
                .in_("bill_id", bill_ids)
                .execute()
            )

            expenses = expenses_result.data or []
            if not expenses:
                # No expenses created yet
                status_map[file_hash] = "pending"
                continue

            # Check if ALL expenses are authorized
            all_authorized = all(
                exp.get("auth_status") is True or exp.get("status") == "auth"
                for exp in expenses
            )

            status_map[file_hash] = "linked" if all_authorized else "pending"

        return status_map

    except Exception as e:
        logger.warning("[Vault] check_receipt_status error: %s", e)
        return {}


def detect_duplicates(file_hash: str, project_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """Find files with the same hash (potential duplicates)."""
    q = (
        supabase.table("vault_files")
        .select("id, name, project_id, size_bytes, uploaded_by, created_at")
        .eq("file_hash", file_hash)
        .eq("is_deleted", False)
    )
    if project_id:
        q = q.eq("project_id", project_id)

    result = q.execute()
    return result.data or []
