"""
===============================================================================
 Vault Handler for Arturito
===============================================================================
 Handles file storage operations via conversational commands:
 - Search/find files
 - List files in vault
 - Create folders
 - Delete files (with confirmation)
 - Organize vault by type
===============================================================================
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)


# -----------------------------------------------------------------------------
# VAULT SEARCH HANDLER
# -----------------------------------------------------------------------------

def handle_vault_search(
    request: dict,
    context: dict = None
) -> dict:
    """
    Search for files in the vault by name, type, or project.
    """
    from api.supabase_client import supabase

    entities = request.get("entities", {})
    query = entities.get("query", entities.get("file_name", ""))
    project_name = entities.get("project")
    file_type = entities.get("file_type")

    try:
        q = (
            supabase.table("vault_files")
            .select("id, name, is_folder, project_id, mime_type, size_bytes, created_at")
            .eq("is_deleted", False)
        )

        if query:
            q = q.ilike("name", f"%{query}%")
        if file_type:
            ext_map = {
                "pdf": "application/pdf",
                "revit": ".rvt",
                "image": "image/",
                "spreadsheet": "sheet",
            }
            mime_hint = ext_map.get(file_type.lower(), file_type)
            q = q.ilike("mime_type", f"%{mime_hint}%")

        results = q.order("updated_at", desc=True).limit(20).execute()
        files = results.data or []

        if not files:
            search_desc = query or file_type or "your criteria"
            return {
                "text": f"No files found matching '{search_desc}' in the vault.",
                "action": "vault_search_empty",
            }

        # Format results
        lines = []
        for f in files:
            icon = "folder" if f["is_folder"] else _get_type_label(f.get("mime_type", ""))
            size = _format_size(f.get("size_bytes", 0)) if not f["is_folder"] else ""
            name = f["name"]
            lines.append(f"  [{icon}] {name}  {size}")

        text = f"Found {len(files)} result(s):\n" + "\n".join(lines)
        if len(files) == 20:
            text += "\n\n(Showing first 20 results. Use the Vault page for full search.)"

        return {
            "text": text,
            "action": "vault_search_results",
            "data": {"count": len(files), "files": files},
        }

    except Exception as e:
        logger.error(f"[Vault Handler] Search error: {e}")
        return {
            "text": "I had trouble searching the vault. Please try again or use the Vault page directly.",
            "action": "error",
            "error": str(e),
        }


# -----------------------------------------------------------------------------
# VAULT LIST HANDLER
# -----------------------------------------------------------------------------

def handle_vault_list(
    request: dict,
    context: dict = None
) -> dict:
    """
    List files in the vault root or a specific folder.
    """
    from api.supabase_client import supabase

    entities = request.get("entities", {})
    folder_name = entities.get("folder")

    try:
        parent_id = None
        if folder_name:
            # Try to find folder by name
            folder_result = (
                supabase.table("vault_files")
                .select("id, name")
                .eq("is_folder", True)
                .eq("is_deleted", False)
                .ilike("name", f"%{folder_name}%")
                .limit(1)
                .execute()
            )
            if folder_result.data:
                parent_id = folder_result.data[0]["id"]

        q = (
            supabase.table("vault_files")
            .select("id, name, is_folder, mime_type, size_bytes, created_at")
            .eq("is_deleted", False)
        )

        if parent_id:
            q = q.eq("parent_id", parent_id)
        else:
            q = q.is_("parent_id", "null")

        results = q.order("is_folder", desc=True).order("name").limit(30).execute()
        files = results.data or []

        if not files:
            location = f"folder '{folder_name}'" if folder_name else "vault root"
            return {
                "text": f"The {location} is empty. Upload some files to get started!",
                "action": "vault_list_empty",
            }

        folders = [f for f in files if f["is_folder"]]
        docs = [f for f in files if not f["is_folder"]]

        lines = []
        if folders:
            lines.append(f"Folders ({len(folders)}):")
            for f in folders:
                lines.append(f"  > {f['name']}")
        if docs:
            lines.append(f"Files ({len(docs)}):")
            for f in docs:
                size = _format_size(f.get("size_bytes", 0))
                lines.append(f"  - {f['name']}  ({size})")

        location = f"'{folder_name}'" if folder_name else "vault root"
        text = f"Contents of {location}:\n" + "\n".join(lines)

        return {
            "text": text,
            "action": "vault_list_results",
            "data": {"count": len(files)},
        }

    except Exception as e:
        logger.error(f"[Vault Handler] List error: {e}")
        return {
            "text": "I had trouble listing vault contents. Please try the Vault page directly.",
            "action": "error",
            "error": str(e),
        }


# -----------------------------------------------------------------------------
# VAULT CREATE FOLDER HANDLER
# -----------------------------------------------------------------------------

def handle_vault_create_folder(
    request: dict,
    context: dict = None
) -> dict:
    """
    Create a new folder in the vault.
    """
    from api.supabase_client import supabase

    entities = request.get("entities", {})
    folder_name = entities.get("folder_name", "").strip()

    if not folder_name:
        return {
            "text": "What should I name the folder? Try: 'create folder called Plans'",
            "action": "vault_need_name",
        }

    try:
        row = {
            "name": folder_name,
            "is_folder": True,
            "parent_id": None,
            "project_id": None,
        }

        # Check context for current project/folder
        if context:
            user_id = context.get("user_id")
            if user_id:
                row["uploaded_by"] = user_id

        result = supabase.table("vault_files").insert(row).execute()

        if result.data:
            return {
                "text": f"Folder '{folder_name}' created successfully in the vault root.",
                "action": "vault_folder_created",
                "data": {"folder": result.data[0]},
            }
        else:
            return {
                "text": f"I couldn't create the folder '{folder_name}'. Please try again.",
                "action": "error",
            }

    except Exception as e:
        logger.error(f"[Vault Handler] Create folder error: {e}")
        return {
            "text": f"Error creating folder: {str(e)}",
            "action": "error",
            "error": str(e),
        }


# -----------------------------------------------------------------------------
# VAULT DELETE HANDLER
# -----------------------------------------------------------------------------

def handle_vault_delete(
    request: dict,
    context: dict = None
) -> dict:
    """
    Delete a file from the vault (soft delete).
    Searches by name and confirms.
    """
    from api.supabase_client import supabase

    entities = request.get("entities", {})
    file_name = entities.get("file_name", "").strip()

    if not file_name:
        return {
            "text": "Which file should I delete? Tell me the file name.",
            "action": "vault_need_name",
        }

    try:
        # Find file by name
        result = (
            supabase.table("vault_files")
            .select("id, name, is_folder, size_bytes")
            .eq("is_deleted", False)
            .ilike("name", f"%{file_name}%")
            .limit(5)
            .execute()
        )

        files = result.data or []

        if not files:
            return {
                "text": f"No file named '{file_name}' found in the vault.",
                "action": "vault_not_found",
            }

        if len(files) == 1:
            f = files[0]
            # Soft delete
            supabase.table("vault_files").update({"is_deleted": True}).eq("id", f["id"]).execute()
            return {
                "text": f"Deleted '{f['name']}' from the vault.",
                "action": "vault_deleted",
                "data": {"file_id": f["id"]},
            }
        else:
            names = "\n".join([f"  - {f['name']}" for f in files])
            return {
                "text": f"Found multiple matches for '{file_name}':\n{names}\n\nPlease be more specific about which file to delete.",
                "action": "vault_multiple_matches",
            }

    except Exception as e:
        logger.error(f"[Vault Handler] Delete error: {e}")
        return {
            "text": f"Error deleting file: {str(e)}",
            "action": "error",
            "error": str(e),
        }


# -----------------------------------------------------------------------------
# VAULT ORGANIZE HANDLER
# -----------------------------------------------------------------------------

def handle_vault_organize(
    request: dict,
    context: dict = None
) -> dict:
    """
    Auto-organize vault files by type into subfolders.
    Creates folders by extension category and moves files.
    """
    from api.supabase_client import supabase

    entities = request.get("entities", {})
    project_id = entities.get("project_id")

    try:
        # Get all root-level files (not folders, not deleted)
        q = (
            supabase.table("vault_files")
            .select("id, name, mime_type, is_folder")
            .eq("is_deleted", False)
            .eq("is_folder", False)
            .is_("parent_id", "null")
        )
        if project_id:
            q = q.eq("project_id", project_id)

        result = q.execute()
        files = result.data or []

        if not files:
            return {
                "text": "No loose files found at the root level to organize.",
                "action": "vault_organize_empty",
            }

        # Group files by category
        categories = {}
        for f in files:
            cat = _categorize_file(f.get("mime_type", ""), f.get("name", ""))
            if cat not in categories:
                categories[cat] = []
            categories[cat].append(f)

        # Create folders and move files
        moved = 0
        for cat_name, cat_files in categories.items():
            # Check if folder exists
            folder_check = (
                supabase.table("vault_files")
                .select("id")
                .eq("name", cat_name)
                .eq("is_folder", True)
                .eq("is_deleted", False)
                .is_("parent_id", "null")
                .limit(1)
                .execute()
            )

            if folder_check.data:
                folder_id = folder_check.data[0]["id"]
            else:
                folder_row = {
                    "name": cat_name,
                    "is_folder": True,
                    "parent_id": None,
                    "project_id": project_id,
                }
                folder_result = supabase.table("vault_files").insert(folder_row).execute()
                folder_id = folder_result.data[0]["id"]

            # Move files into folder
            for f in cat_files:
                supabase.table("vault_files").update({"parent_id": folder_id}).eq("id", f["id"]).execute()
                moved += 1

        summary_lines = [f"  {cat}: {len(fls)} file(s)" for cat, fls in categories.items()]
        text = (
            f"Organized {moved} file(s) into {len(categories)} folder(s):\n"
            + "\n".join(summary_lines)
        )

        return {
            "text": text,
            "action": "vault_organized",
            "data": {"moved": moved, "categories": len(categories)},
        }

    except Exception as e:
        logger.error(f"[Vault Handler] Organize error: {e}")
        return {
            "text": f"Error organizing vault: {str(e)}",
            "action": "error",
            "error": str(e),
        }


# -----------------------------------------------------------------------------
# VAULT UPLOAD HANDLER (trigger UI)
# -----------------------------------------------------------------------------

def handle_vault_upload(
    request: dict,
    context: dict = None
) -> dict:
    """
    Respond to upload requests by directing user to the Vault page.
    File uploads require the UI (drag-drop or file picker).
    """
    return {
        "text": "To upload files, head to the Data Vault page and use the Upload button or drag & drop files directly into the vault.",
        "action": "vault_upload_redirect",
        "data": {"navigate": "vault.html"},
    }


# -----------------------------------------------------------------------------
# HELPERS
# -----------------------------------------------------------------------------

def _get_type_label(mime_type: str) -> str:
    """Get human-readable type label from MIME type."""
    if not mime_type:
        return "file"
    if "pdf" in mime_type:
        return "PDF"
    if "image" in mime_type:
        return "IMG"
    if "sheet" in mime_type or "excel" in mime_type or "csv" in mime_type:
        return "XLS"
    if "presentation" in mime_type:
        return "PPT"
    if "word" in mime_type or "document" in mime_type:
        return "DOC"
    return "file"


def _format_size(bytes_val: int) -> str:
    """Format bytes into human-readable size."""
    if not bytes_val or bytes_val == 0:
        return "0 B"
    units = ["B", "KB", "MB", "GB", "TB"]
    i = 0
    size = float(bytes_val)
    while size >= 1024 and i < len(units) - 1:
        size /= 1024
        i += 1
    return f"{size:.1f} {units[i]}" if i > 0 else f"{int(size)} {units[i]}"


def _categorize_file(mime_type: str, name: str) -> str:
    """Categorize a file into a folder name based on type."""
    mime = (mime_type or "").lower()
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""

    if ext in ("rvt", "rfa", "rte"):
        return "Revit Files"
    if "pdf" in mime:
        return "PDFs"
    if "image" in mime:
        return "Images"
    if "sheet" in mime or "excel" in mime or ext in ("xls", "xlsx", "csv"):
        return "Spreadsheets"
    if "word" in mime or "document" in mime or ext in ("doc", "docx"):
        return "Documents"
    if ext == "ngm":
        return "Estimates"
    return "Other Files"
