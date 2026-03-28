"""
NGM Board State API Router
=================================
Endpoints for managing the shared visual state of NGM Board (formerly Process Manager).
This includes: node positions, custom modules, flow positions, draft states,
and per-board namespaced state (board_{id}_{datatype}).
All authorized users share the same state.
"""

import re
from fastapi import APIRouter, HTTPException, Query, Depends
from api.auth import get_current_user
from pydantic import BaseModel
from typing import Optional, Union, List, Any

from api.supabase_client import supabase

router = APIRouter(prefix="/process-manager", tags=["NGM Board State"])


# ========================================
# Pydantic Models
# ========================================

class ProcessManagerStateUpdate(BaseModel):
    state_data: Union[dict, List[Any]]  # Accept both objects and arrays
    updated_by: Optional[str] = None


# Static state keys (legacy + new)
STATIC_STATE_KEYS = [
    'node_positions',      # Position of module nodes in overview
    'custom_modules',      # User-created custom modules
    'flow_positions',      # Position of nodes in flowcharts
    'draft_states',        # Draft/Live toggle states
    'module_connections',  # Module connection states
    # Team Org Chart keys
    'orgchart_positions',  # Position of user nodes in org chart
    'orgchart_connections',# Connections between user nodes
    'orgchart_groups',     # Group areas in org chart
    'orgchart_hidden_users', # Hidden user IDs in org chart
    # NGM Board system
    'boards_registry',     # Board list with metadata
]

# Dynamic board-prefixed keys: board_{boardId}_{dataType}
BOARD_KEY_PATTERN = re.compile(
    r'^board_[a-zA-Z0-9_-]+_'
    r'(modules|connections|positions|flow_positions|draft_states|canvas_elements|settings)$'
)


def is_valid_state_key(key: str) -> bool:
    """Check if a state key is valid (static or board-prefixed)."""
    return key in STATIC_STATE_KEYS or bool(BOARD_KEY_PATTERN.match(key))


# Keep backward compat alias
VALID_STATE_KEYS = STATIC_STATE_KEYS


# ========================================
# Endpoints
# ========================================

@router.get("/state/{state_key}")
async def get_process_manager_state(state_key: str, current_user: dict = Depends(get_current_user)):
    """
    Get a specific state from the process manager shared state.
    Valid keys: node_positions, custom_modules, flow_positions, draft_states, module_connections
    """
    if not is_valid_state_key(state_key):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid state_key: '{state_key}'. Must be a known key or match board_{{id}}_{{type}} pattern."
        )

    try:
        response = supabase.table("process_manager_state").select("*").eq("state_key", state_key).execute()
        if response.data and len(response.data) > 0:
            return {
                "state_key": state_key,
                "state_data": response.data[0].get("state_data", {}),
                "updated_at": response.data[0].get("updated_at"),
                "updated_by": response.data[0].get("updated_by")
            }
        else:
            # Return empty default if not found
            return {
                "state_key": state_key,
                "state_data": [] if state_key in ('custom_modules', 'orgchart_connections', 'orgchart_groups', 'orgchart_hidden_users', 'boards_registry') or state_key.endswith('_modules') or state_key.endswith('_connections') else {},
                "updated_at": None,
                "updated_by": None
            }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching state: {str(e)}")


@router.put("/state/{state_key}")
async def update_process_manager_state(state_key: str, update: ProcessManagerStateUpdate, current_user: dict = Depends(get_current_user)):
    """
    Update a specific state in the process manager shared state.
    Creates the entry if it doesn't exist (upsert behavior).
    Changes are logged to the history table automatically via database trigger.
    """
    if not is_valid_state_key(state_key):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid state_key: '{state_key}'. Must be a known key or match board_{{id}}_{{type}} pattern."
        )

    try:
        # Try to update existing
        response = supabase.table("process_manager_state").update({
            "state_data": update.state_data,
            "updated_by": update.updated_by
        }).eq("state_key", state_key).execute()

        if response.data and len(response.data) > 0:
            return {
                "message": "State updated",
                "state_key": state_key,
                "updated_at": response.data[0].get("updated_at")
            }
        else:
            # Insert if not exists
            insert_response = supabase.table("process_manager_state").insert({
                "state_key": state_key,
                "state_data": update.state_data,
                "updated_by": update.updated_by
            }).execute()
            return {
                "message": "State created",
                "state_key": state_key,
                "updated_at": insert_response.data[0].get("updated_at") if insert_response.data else None
            }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error updating state: {str(e)}")


@router.get("/state")
async def get_all_process_manager_states(current_user: dict = Depends(get_current_user)):
    """
    Get all process manager states.
    Returns all state keys with their data.
    """
    try:
        response = supabase.table("process_manager_state").select("*").execute()
        states = {}
        if response.data:
            for row in response.data:
                states[row["state_key"]] = {
                    "state_data": row.get("state_data", {}),
                    "updated_at": row.get("updated_at"),
                    "updated_by": row.get("updated_by")
                }
        return {"states": states}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching states: {str(e)}")


@router.get("/history/{state_key}")
async def get_process_manager_history(
    state_key: str,
    limit: int = Query(10, description="Number of history entries to return", ge=1, le=100),
    current_user: dict = Depends(get_current_user)
):
    """
    Get history of changes for a specific state key.
    Useful for audit trail and recovery.
    """
    if not is_valid_state_key(state_key):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid state_key: '{state_key}'. Must be a known key or match board_{{id}}_{{type}} pattern."
        )

    try:
        response = supabase.table("process_manager_history").select("*").eq(
            "state_key", state_key
        ).order("changed_at", desc=True).limit(limit).execute()

        return {
            "state_key": state_key,
            "history": response.data if response.data else [],
            "count": len(response.data) if response.data else 0
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching history: {str(e)}")


@router.post("/history/{state_key}/restore/{history_id}")
async def restore_from_history(state_key: str, history_id: str, current_user: dict = Depends(get_current_user)):
    """
    Restore a state from a specific history entry.
    Useful for recovering from accidental changes.
    """
    if not is_valid_state_key(state_key):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid state_key: '{state_key}'. Must be a known key or match board_{{id}}_{{type}} pattern."
        )

    try:
        # Get the history entry
        history_response = supabase.table("process_manager_history").select("*").eq(
            "id", history_id
        ).eq("state_key", state_key).execute()

        if not history_response.data or len(history_response.data) == 0:
            raise HTTPException(status_code=404, detail="History entry not found")

        history_entry = history_response.data[0]

        # Update the current state with the historical data
        update_response = supabase.table("process_manager_state").update({
            "state_data": history_entry["state_data"],
            "updated_by": None  # System restore
        }).eq("state_key", state_key).execute()

        return {
            "message": "State restored from history",
            "state_key": state_key,
            "restored_from": history_entry["changed_at"]
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error restoring state: {str(e)}")
