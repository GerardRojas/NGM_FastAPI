"""
Process Manager State API Router
=================================
Endpoints for managing the shared visual state of the Process Manager.
This includes: node positions, custom modules, flow positions, draft states.
All authorized users share the same state.
"""

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from typing import Optional, Union, List, Any

from api.supabase_client import supabase

router = APIRouter(prefix="/process-manager", tags=["Process Manager State"])


# ========================================
# Pydantic Models
# ========================================

class ProcessManagerStateUpdate(BaseModel):
    state_data: Union[dict, List[Any]]  # Accept both objects and arrays
    updated_by: Optional[str] = None


# Valid state keys for the process manager
VALID_STATE_KEYS = [
    'node_positions',      # Position of module nodes in overview
    'custom_modules',      # User-created custom modules
    'flow_positions',      # Position of nodes in flowcharts
    'draft_states',        # Draft/Live toggle states
    'module_connections'   # Module connection states
]


# ========================================
# Endpoints
# ========================================

@router.get("/state/{state_key}")
async def get_process_manager_state(state_key: str):
    """
    Get a specific state from the process manager shared state.
    Valid keys: node_positions, custom_modules, flow_positions, draft_states, module_connections
    """
    if state_key not in VALID_STATE_KEYS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid state_key. Must be one of: {', '.join(VALID_STATE_KEYS)}"
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
                "state_data": [] if state_key == 'custom_modules' else {},
                "updated_at": None,
                "updated_by": None
            }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching state: {str(e)}")


@router.put("/state/{state_key}")
async def update_process_manager_state(state_key: str, update: ProcessManagerStateUpdate):
    """
    Update a specific state in the process manager shared state.
    Creates the entry if it doesn't exist (upsert behavior).
    Changes are logged to the history table automatically via database trigger.
    """
    if state_key not in VALID_STATE_KEYS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid state_key. Must be one of: {', '.join(VALID_STATE_KEYS)}"
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
async def get_all_process_manager_states():
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
    limit: int = Query(10, description="Number of history entries to return", ge=1, le=100)
):
    """
    Get history of changes for a specific state key.
    Useful for audit trail and recovery.
    """
    if state_key not in VALID_STATE_KEYS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid state_key. Must be one of: {', '.join(VALID_STATE_KEYS)}"
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
async def restore_from_history(state_key: str, history_id: str):
    """
    Restore a state from a specific history entry.
    Useful for recovering from accidental changes.
    """
    if state_key not in VALID_STATE_KEYS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid state_key. Must be one of: {', '.join(VALID_STATE_KEYS)}"
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
