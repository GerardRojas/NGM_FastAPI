"""
NGM Board Items API Router
===========================
CRUD endpoints for boards, tables, and folders in NGM Board.
Replaces the generic key-value pattern with structured data + audit trail.

Tables used:
  - ngm_board_items:      Unified registry (boards, tables, folders)
  - ngm_board_table_data: Cell data for spreadsheet tables
  - ngm_board_history:    Audit trail (auto-populated via triggers)
"""

from fastapi import APIRouter, HTTPException, Query, Depends
from api.auth import get_current_user
from pydantic import BaseModel
from typing import Optional, List, Any

from api.supabase_client import supabase

router = APIRouter(prefix="/board-items", tags=["NGM Board Items"])


# ========================================
# Pydantic Models
# ========================================

class ItemCreate(BaseModel):
    id: str                                     # board_xxx, table_xxx, folder_xxx
    item_type: str                              # 'board', 'table', 'folder'
    name: str
    folder_id: Optional[str] = None
    board_type: Optional[str] = None            # 'process' | 'freeform'
    cols: Optional[int] = 5
    rows: Optional[int] = 10
    visibility: Optional[str] = "public"        # 'public' | 'private'
    collaborators: Optional[List[str]] = []     # UUIDs of users who can see when private


class ItemUpdate(BaseModel):
    name: Optional[str] = None
    folder_id: Optional[str] = None             # Use "__null__" to clear
    board_type: Optional[str] = None
    cols: Optional[int] = None
    rows: Optional[int] = None
    visibility: Optional[str] = None            # 'public' | 'private'
    collaborators: Optional[List[str]] = None   # UUIDs


class TableDataUpdate(BaseModel):
    cell_data: Optional[dict] = None
    column_headers: Optional[dict] = None


class BulkMoveRequest(BaseModel):
    item_ids: List[str]
    folder_id: Optional[str] = None             # None = move to root


# ========================================
# ITEMS: CRUD
# ========================================

@router.get("/")
async def list_items(
    folder_id: Optional[str] = Query(None, description="Filter by folder (null=root)"),
    item_type: Optional[str] = Query(None, description="Filter by type: board, table, folder"),
    current_user: dict = Depends(get_current_user)
):
    """List items, optionally filtered by folder and/or type.
    Filters out private items the user can't see."""
    user_id = current_user.get("user_id")

    try:
        query = supabase.table("ngm_board_items").select("*")

        if folder_id == "__root__":
            query = query.is_("folder_id", "null")
        elif folder_id:
            query = query.eq("folder_id", folder_id)

        if item_type:
            query = query.eq("item_type", item_type)

        query = query.order("item_type").order("name")
        response = query.execute()

        # Visibility filter
        items = response.data or []
        visible = []
        for item in items:
            vis = item.get("visibility", "public")
            if vis == "public":
                visible.append(item)
            else:
                owner = item.get("created_by")
                collabs = item.get("collaborators") or []
                if user_id and (user_id == owner or user_id in collabs):
                    visible.append(item)

        return {"items": visible}

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error listing items: {str(e)}")


@router.get("/all")
async def list_all_items(current_user: dict = Depends(get_current_user)):
    """Get ALL items regardless of folder. Used for initial load.
    Filters out private items that don't belong to the current user."""
    user_id = current_user.get("user_id")

    try:
        response = supabase.table("ngm_board_items").select("*").order("item_type").order("name").execute()
        items = response.data or []

        # Server-side visibility filter: hide private items the user can't see
        visible = []
        for item in items:
            vis = item.get("visibility", "public")
            if vis == "public":
                visible.append(item)
            else:
                # Private: only creator or collaborator can see
                owner = item.get("created_by")
                collabs = item.get("collaborators") or []
                if user_id and (user_id == owner or user_id in collabs):
                    visible.append(item)

        return {"items": visible}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error listing all items: {str(e)}")


@router.get("/{item_id}")
async def get_item(item_id: str, current_user: dict = Depends(get_current_user)):
    """Get a single item by ID."""
    try:
        response = supabase.table("ngm_board_items").select("*").eq("id", item_id).execute()
        if not response.data:
            raise HTTPException(status_code=404, detail=f"Item not found: {item_id}")
        return response.data[0]
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching item: {str(e)}")


@router.post("/")
async def create_item(item: ItemCreate, current_user: dict = Depends(get_current_user)):
    """Create a new board, table, or folder."""
    user_id = current_user.get("user_id")

    if item.item_type not in ("board", "table", "folder"):
        raise HTTPException(status_code=400, detail="item_type must be 'board', 'table', or 'folder'")

    if item.item_type == "board" and item.board_type not in ("process", "freeform", None):
        raise HTTPException(status_code=400, detail="board_type must be 'process' or 'freeform'")

    try:
        visibility = item.visibility or "public"
        if visibility not in ("public", "private"):
            raise HTTPException(status_code=400, detail="visibility must be 'public' or 'private'")

        row = {
            "id": item.id,
            "item_type": item.item_type,
            "name": item.name,
            "folder_id": item.folder_id,
            "created_by": user_id,
            "updated_by": user_id,
            "visibility": visibility,
            "collaborators": item.collaborators or [],
        }

        if item.item_type == "board":
            row["board_type"] = item.board_type or "freeform"
        elif item.item_type == "table":
            row["cols"] = max(1, min(item.cols or 5, 26))
            row["rows"] = max(1, min(item.rows or 10, 500))

        response = supabase.table("ngm_board_items").insert(row).execute()

        if not response.data:
            raise HTTPException(status_code=500, detail="Failed to create item")

        created = response.data[0]

        # If table, also create empty table_data row
        if item.item_type == "table":
            supabase.table("ngm_board_table_data").insert({
                "table_id": item.id,
                "cell_data": {},
                "column_headers": {},
                "updated_by": user_id,
            }).execute()

        return created

    except HTTPException:
        raise
    except Exception as e:
        # Check for duplicate key
        if "duplicate key" in str(e).lower() or "23505" in str(e):
            raise HTTPException(status_code=409, detail=f"Item with ID '{item.id}' already exists")
        raise HTTPException(status_code=500, detail=f"Error creating item: {str(e)}")


@router.patch("/{item_id}")
async def update_item(item_id: str, update: ItemUpdate, current_user: dict = Depends(get_current_user)):
    """Update an item's metadata (name, folder, etc.).
    Visibility and collaborators can only be changed by the creator."""
    user_id = current_user.get("user_id")

    try:
        # Ownership check for protected fields (visibility, collaborators)
        if update.visibility is not None or update.collaborators is not None:
            item_resp = supabase.table("ngm_board_items").select("created_by").eq("id", item_id).execute()
            if not item_resp.data:
                raise HTTPException(status_code=404, detail=f"Item not found: {item_id}")
            owner = item_resp.data[0].get("created_by")
            if owner and owner != user_id:
                raise HTTPException(status_code=403, detail="Only the creator can change visibility or collaborators")

        data = {"updated_by": user_id}

        if update.name is not None:
            data["name"] = update.name
        if update.folder_id is not None:
            data["folder_id"] = None if update.folder_id == "__null__" else update.folder_id
        if update.board_type is not None:
            data["board_type"] = update.board_type
        if update.cols is not None:
            data["cols"] = max(1, min(update.cols, 26))
        if update.rows is not None:
            data["rows"] = max(1, min(update.rows, 500))
        if update.visibility is not None:
            if update.visibility not in ("public", "private"):
                raise HTTPException(status_code=400, detail="visibility must be 'public' or 'private'")
            data["visibility"] = update.visibility
        if update.collaborators is not None:
            data["collaborators"] = update.collaborators

        response = supabase.table("ngm_board_items").update(data).eq("id", item_id).execute()

        if not response.data:
            raise HTTPException(status_code=404, detail=f"Item not found: {item_id}")

        return response.data[0]

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error updating item: {str(e)}")


@router.delete("/{item_id}")
async def delete_item(item_id: str, current_user: dict = Depends(get_current_user)):
    """
    Delete an item. Only the creator can delete.
    If it's a folder, children are moved to the parent folder
    (handled by ON DELETE SET NULL on folder_id FK).
    """
    user_id = current_user.get("user_id")

    try:
        # Fetch item first (for audit metadata + ownership check)
        item_resp = supabase.table("ngm_board_items").select("*").eq("id", item_id).execute()
        if not item_resp.data:
            raise HTTPException(status_code=404, detail=f"Item not found: {item_id}")

        item = item_resp.data[0]

        # Ownership check: only creator can delete
        owner = item.get("created_by")
        if owner and owner != user_id:
            raise HTTPException(status_code=403, detail="Only the creator can delete this item")

        # Set updated_by before delete so the trigger captures it
        supabase.table("ngm_board_items").update({"updated_by": user_id}).eq("id", item_id).execute()

        # Delete (cascade will remove table_data if it's a table)
        supabase.table("ngm_board_items").delete().eq("id", item_id).execute()

        return {"message": f"Deleted {item['item_type']} '{item['name']}'", "id": item_id}

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error deleting item: {str(e)}")


@router.post("/bulk-move")
async def bulk_move_items(req: BulkMoveRequest, current_user: dict = Depends(get_current_user)):
    """Move multiple items to a folder (or root)."""
    user_id = current_user.get("user_id")

    try:
        for item_id in req.item_ids:
            supabase.table("ngm_board_items").update({
                "folder_id": req.folder_id,
                "updated_by": user_id,
            }).eq("id", item_id).execute()

        return {"message": f"Moved {len(req.item_ids)} items", "folder_id": req.folder_id}

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error moving items: {str(e)}")


# ========================================
# TABLE DATA: Read / Write cell data
# ========================================

@router.get("/{table_id}/data")
async def get_table_data(table_id: str, current_user: dict = Depends(get_current_user)):
    """Get cell data and column headers for a table."""
    try:
        response = supabase.table("ngm_board_table_data").select("*").eq("table_id", table_id).execute()

        if not response.data:
            # Table might exist but no data row yet -> return empty
            return {
                "table_id": table_id,
                "cell_data": {},
                "column_headers": {},
                "updated_at": None,
                "updated_by": None,
            }

        row = response.data[0]
        return {
            "table_id": table_id,
            "cell_data": row.get("cell_data", {}),
            "column_headers": row.get("column_headers", {}),
            "updated_at": row.get("updated_at"),
            "updated_by": row.get("updated_by"),
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching table data: {str(e)}")


@router.put("/{table_id}/data")
async def update_table_data(table_id: str, update: TableDataUpdate, current_user: dict = Depends(get_current_user)):
    """Update cell data and/or column headers for a table."""
    user_id = current_user.get("user_id")

    try:
        data = {"updated_by": user_id}

        if update.cell_data is not None:
            data["cell_data"] = update.cell_data
        if update.column_headers is not None:
            data["column_headers"] = update.column_headers

        # Try update first
        response = supabase.table("ngm_board_table_data").update(data).eq("table_id", table_id).execute()

        if response.data:
            # Also update the item's updated_at/updated_by
            supabase.table("ngm_board_items").update({
                "updated_by": user_id,
            }).eq("id", table_id).execute()

            return {
                "message": "Table data updated",
                "table_id": table_id,
                "updated_at": response.data[0].get("updated_at"),
            }
        else:
            # Insert if not exists (upsert)
            insert_data = {
                "table_id": table_id,
                "cell_data": update.cell_data or {},
                "column_headers": update.column_headers or {},
                "updated_by": user_id,
            }
            insert_resp = supabase.table("ngm_board_table_data").insert(insert_data).execute()

            # Log to history
            supabase.table("ngm_board_history").insert({
                "item_id": table_id,
                "action": "cell_edit",
                "changed_by": user_id,
                "metadata": {"cells_count": len(update.cell_data) if update.cell_data else 0},
            }).execute()

            return {
                "message": "Table data created",
                "table_id": table_id,
                "updated_at": insert_resp.data[0].get("updated_at") if insert_resp.data else None,
            }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error updating table data: {str(e)}")


# ========================================
# HISTORY: Audit trail
# ========================================

@router.get("/{item_id}/history")
async def get_item_history(
    item_id: str,
    limit: int = Query(20, ge=1, le=100),
    current_user: dict = Depends(get_current_user)
):
    """Get audit history for an item."""
    try:
        response = (
            supabase.table("ngm_board_history")
            .select("*")
            .eq("item_id", item_id)
            .order("changed_at", desc=True)
            .limit(limit)
            .execute()
        )
        return {"history": response.data or []}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching history: {str(e)}")


# ========================================
# MIGRATION: Import from legacy key-value store
# ========================================

@router.post("/migrate-from-legacy")
async def migrate_from_legacy(current_user: dict = Depends(get_current_user)):
    """
    One-time migration: reads boards_registry and tables_registry from
    process_manager_state and imports them into ngm_board_items.
    Skips items that already exist. Safe to run multiple times.
    """
    user_id = current_user.get("user_id")
    migrated = {"boards": 0, "tables": 0, "skipped": 0}

    try:
        # Load legacy boards_registry
        boards_resp = supabase.table("process_manager_state").select("state_data").eq("state_key", "boards_registry").execute()
        if boards_resp.data and boards_resp.data[0].get("state_data"):
            boards = boards_resp.data[0]["state_data"]
            if isinstance(boards, list):
                for board in boards:
                    if not board.get("id"):
                        continue
                    # Skip folders (itemType === 'folder')
                    if board.get("itemType") == "folder":
                        row = {
                            "id": board["id"],
                            "item_type": "folder",
                            "name": board.get("name", "Unnamed"),
                            "folder_id": board.get("folderId"),
                            "created_by": user_id,
                            "updated_by": user_id,
                        }
                    else:
                        row = {
                            "id": board["id"],
                            "item_type": "board",
                            "name": board.get("name", "Unnamed"),
                            "board_type": board.get("type", "freeform"),
                            "folder_id": board.get("folderId"),
                            "created_by": user_id,
                            "updated_by": user_id,
                        }
                    try:
                        supabase.table("ngm_board_items").insert(row).execute()
                        migrated["boards"] += 1
                    except Exception:
                        migrated["skipped"] += 1

        # Load legacy tables_registry
        tables_resp = supabase.table("process_manager_state").select("state_data").eq("state_key", "tables_registry").execute()
        if tables_resp.data and tables_resp.data[0].get("state_data"):
            tables = tables_resp.data[0]["state_data"]
            if isinstance(tables, list):
                for table in tables:
                    if not table.get("id"):
                        continue
                    row = {
                        "id": table["id"],
                        "item_type": "table",
                        "name": table.get("name", "Unnamed"),
                        "cols": table.get("cols", 5),
                        "rows": table.get("rows", 10),
                        "folder_id": table.get("folderId"),
                        "created_by": user_id,
                        "updated_by": user_id,
                    }
                    try:
                        supabase.table("ngm_board_items").insert(row).execute()
                        migrated["tables"] += 1

                        # Migrate table cell data
                        table_id = table["id"]
                        data_resp = supabase.table("process_manager_state").select("state_data").eq("state_key", f"{table_id}_data").execute()
                        cols_resp = supabase.table("process_manager_state").select("state_data").eq("state_key", f"{table_id}_columns").execute()

                        cell_data = data_resp.data[0]["state_data"] if data_resp.data else {}
                        col_headers = cols_resp.data[0]["state_data"] if cols_resp.data else {}

                        if isinstance(cell_data, dict) or isinstance(col_headers, dict):
                            supabase.table("ngm_board_table_data").upsert({
                                "table_id": table_id,
                                "cell_data": cell_data if isinstance(cell_data, dict) else {},
                                "column_headers": col_headers if isinstance(col_headers, dict) else {},
                                "updated_by": user_id,
                            }).execute()

                    except Exception:
                        migrated["skipped"] += 1

        return {
            "message": "Migration complete",
            "migrated": migrated,
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Migration error: {str(e)}")
