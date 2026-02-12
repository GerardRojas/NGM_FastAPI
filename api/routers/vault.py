# api/routers/vault.py
# ================================
# Vault API Router
# ================================
# File storage management with folders, versioning, chunked uploads,
# search and duplicate detection.

from fastapi import APIRouter, HTTPException, File, UploadFile, Form, Query, Depends
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
import logging

from api.auth import get_current_user
from api.services.vault_service import (
    create_folder,
    get_folder_tree,
    list_files,
    get_file,
    upload_file,
    store_chunk,
    assemble_chunks,
    list_versions,
    create_version,
    restore_version,
    rename_file,
    move_file,
    soft_delete,
    duplicate_file,
    get_download_url,
    search_files,
    detect_duplicates,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/vault", tags=["Vault"])


# ====== MODELS ======

class FolderCreate(BaseModel):
    name: str
    parent_id: Optional[str] = None
    project_id: Optional[str] = None


class FileUpdate(BaseModel):
    name: Optional[str] = None
    parent_id: Optional[str] = None


class SearchRequest(BaseModel):
    query: Optional[str] = None
    project_id: Optional[str] = None
    mime_type: Optional[str] = None
    date_from: Optional[str] = None
    date_to: Optional[str] = None
    is_folder: Optional[bool] = None
    limit: int = 50


# ====== FOLDER ENDPOINTS ======

@router.post("/folders", status_code=201)
async def api_create_folder(
    body: FolderCreate,
    current_user: dict = Depends(get_current_user),
):
    """Create a new virtual folder."""
    try:
        result = create_folder(
            name=body.name,
            parent_id=body.parent_id,
            project_id=body.project_id,
            user_id=current_user["user_id"],
        )
        return result
    except Exception as e:
        logger.error("[Vault] Create folder error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/tree")
async def api_get_folder_tree(
    project_id: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    """Get folder tree for a project (or global vault)."""
    try:
        return get_folder_tree(project_id)
    except Exception as e:
        logger.error("[Vault] Get tree error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


# ====== FILE LISTING ======

@router.get("/files")
async def api_list_files(
    parent_id: Optional[str] = Query(None),
    project_id: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    """List files/folders in a parent folder."""
    try:
        return list_files(parent_id=parent_id, project_id=project_id)
    except Exception as e:
        logger.error("[Vault] List files error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/files/{file_id}")
async def api_get_file(
    file_id: str,
    current_user: dict = Depends(get_current_user),
):
    """Get single file/folder metadata."""
    try:
        result = get_file(file_id)
        if not result:
            raise HTTPException(status_code=404, detail="File not found")
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error("[Vault] Get file error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


# ====== FILE UPLOAD (single) ======

@router.post("/upload", status_code=201)
async def api_upload_file(
    file: UploadFile = File(...),
    parent_id: Optional[str] = Form(None),
    project_id: Optional[str] = Form(None),
    current_user: dict = Depends(get_current_user),
):
    """
    Upload a single file (up to ~50MB via this endpoint).
    For larger files, use the chunked upload endpoints.
    """
    try:
        file_content = await file.read()

        # Soft limit at 50MB for single upload
        if len(file_content) > 50 * 1024 * 1024:
            raise HTTPException(
                status_code=400,
                detail="File too large for single upload. Use chunked upload for files over 50MB.",
            )

        result = upload_file(
            file_content=file_content,
            filename=file.filename or "untitled",
            content_type=file.content_type or "application/octet-stream",
            parent_id=parent_id,
            project_id=project_id,
            user_id=current_user["user_id"],
        )
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error("[Vault] Upload error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


# ====== CHUNKED UPLOAD ======

@router.post("/upload-chunk")
async def api_upload_chunk(
    file: UploadFile = File(...),
    upload_id: str = Form(...),
    chunk_index: int = Form(...),
    total_chunks: int = Form(...),
    current_user: dict = Depends(get_current_user),
):
    """Store a single chunk for a chunked upload."""
    try:
        chunk_data = await file.read()
        result = store_chunk(upload_id, chunk_index, chunk_data)
        result["total_chunks"] = total_chunks
        return result
    except Exception as e:
        logger.error("[Vault] Chunk upload error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/upload-complete", status_code=201)
async def api_upload_complete(
    upload_id: str = Form(...),
    filename: str = Form(...),
    total_chunks: int = Form(...),
    content_type: str = Form("application/octet-stream"),
    parent_id: Optional[str] = Form(None),
    project_id: Optional[str] = Form(None),
    current_user: dict = Depends(get_current_user),
):
    """Assemble uploaded chunks into a complete file."""
    try:
        result = assemble_chunks(
            upload_id=upload_id,
            filename=filename,
            total_chunks=total_chunks,
            content_type=content_type,
            parent_id=parent_id,
            project_id=project_id,
            user_id=current_user["user_id"],
        )
        return result
    except FileNotFoundError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error("[Vault] Assemble chunks error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


# ====== VERSIONING ======

@router.get("/files/{file_id}/versions")
async def api_list_versions(
    file_id: str,
    current_user: dict = Depends(get_current_user),
):
    """Get version history for a file."""
    try:
        return list_versions(file_id)
    except Exception as e:
        logger.error("[Vault] List versions error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/files/{file_id}/versions", status_code=201)
async def api_create_version(
    file_id: str,
    file: UploadFile = File(...),
    comment: Optional[str] = Form(None),
    current_user: dict = Depends(get_current_user),
):
    """Upload a new version of an existing file."""
    try:
        file_content = await file.read()
        result = create_version(
            file_id=file_id,
            file_content=file_content,
            filename=file.filename or "untitled",
            content_type=file.content_type or "application/octet-stream",
            user_id=current_user["user_id"],
            comment=comment,
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error("[Vault] Create version error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/files/{file_id}/restore/{version_id}")
async def api_restore_version(
    file_id: str,
    version_id: str,
    current_user: dict = Depends(get_current_user),
):
    """Restore an old version as the current version."""
    try:
        result = restore_version(file_id, version_id, current_user["user_id"])
        return result
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error("[Vault] Restore version error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


# ====== FILE OPERATIONS ======

@router.patch("/files/{file_id}")
async def api_update_file(
    file_id: str,
    body: FileUpdate,
    current_user: dict = Depends(get_current_user),
):
    """Rename or move a file/folder."""
    try:
        result = {}
        if body.name is not None:
            result = rename_file(file_id, body.name)
        if body.parent_id is not None:
            result = move_file(file_id, body.parent_id)
        if not result:
            raise HTTPException(status_code=400, detail="No updates provided")
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error("[Vault] Update file error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/files/{file_id}")
async def api_delete_file(
    file_id: str,
    current_user: dict = Depends(get_current_user),
):
    """Soft-delete a file or folder (recursive for folders)."""
    try:
        result = soft_delete(file_id)
        return {"deleted": True, "file": result}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error("[Vault] Delete error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/duplicate/{file_id}", status_code=201)
async def api_duplicate_file(
    file_id: str,
    current_user: dict = Depends(get_current_user),
):
    """Duplicate a file."""
    try:
        result = duplicate_file(file_id, current_user["user_id"])
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error("[Vault] Duplicate error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


# ====== DOWNLOAD ======

@router.get("/files/{file_id}/download")
async def api_download_file(
    file_id: str,
    version_id: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    """Get download URL for a file (optionally a specific version)."""
    try:
        url = get_download_url(file_id, version_id)
        return {"url": url}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error("[Vault] Download URL error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


# ====== SEARCH ======

@router.post("/search")
async def api_search_files(
    body: SearchRequest,
    current_user: dict = Depends(get_current_user),
):
    """Search files by name, type, date range, project."""
    try:
        results = search_files(
            query=body.query,
            project_id=body.project_id,
            mime_type=body.mime_type,
            date_from=body.date_from,
            date_to=body.date_to,
            is_folder=body.is_folder,
            limit=body.limit,
        )
        return results
    except Exception as e:
        logger.error("[Vault] Search error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/duplicates")
async def api_detect_duplicates(
    file_hash: str = Query(...),
    project_id: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user),
):
    """Find files with the same hash."""
    try:
        return detect_duplicates(file_hash, project_id)
    except Exception as e:
        logger.error("[Vault] Duplicates error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))
