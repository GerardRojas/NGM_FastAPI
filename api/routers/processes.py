"""
Process Manager API Router
==========================
Endpoints for managing business processes - both implemented (from code) and drafts.
"""

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from typing import Optional
from datetime import datetime
import os

from api.services.process_parser import (
    get_all_implemented_processes,
    merge_with_database_processes,
    calculate_layout
)
from api.database import supabase

router = APIRouter(prefix="/processes", tags=["Process Manager"])


# ========================================
# Pydantic Models
# ========================================

class ProcessStep(BaseModel):
    number: int
    name: str
    type: str = "action"  # condition, action, notification, wait, assignment, approval
    description: Optional[str] = ""
    connects_to: list[int] = []


class ProcessCreate(BaseModel):
    id: str
    name: str
    category: str = "operations"
    trigger: str = "manual"
    description: Optional[str] = ""
    owner: Optional[str] = ""
    steps: list[ProcessStep] = []
    position: Optional[dict] = None


class ProcessUpdate(BaseModel):
    name: Optional[str] = None
    category: Optional[str] = None
    trigger: Optional[str] = None
    description: Optional[str] = None
    owner: Optional[str] = None
    steps: Optional[list[ProcessStep]] = None
    position: Optional[dict] = None
    status: Optional[str] = None  # draft, proposed, approved


# ========================================
# Endpoints
# ========================================

@router.get("")
async def get_all_processes(
    include_implemented: bool = Query(True, description="Include code-based processes"),
    include_drafts: bool = Query(True, description="Include database draft processes"),
    category: Optional[str] = Query(None, description="Filter by category")
):
    """
    Get all processes (both implemented from code and drafts from database).

    Returns a unified list with visual layout positions calculated.
    """
    processes = []

    # Get implemented processes from code
    if include_implemented:
        try:
            # Get the API root directory
            api_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
            implemented = get_all_implemented_processes(api_root)
            processes.extend(implemented)
        except Exception as e:
            print(f"Error parsing implemented processes: {e}")

    # Get draft processes from database
    if include_drafts:
        try:
            response = supabase.table("process_drafts").select("*").execute()
            if response.data:
                for draft in response.data:
                    processes.append({
                        'id': draft['process_id'],
                        'name': draft['name'],
                        'category': draft.get('category', 'operations'),
                        'trigger': draft.get('trigger_type', 'manual'),
                        'description': draft.get('description', ''),
                        'owner': draft.get('owner', ''),
                        'steps': draft.get('steps', []),
                        'position': draft.get('position'),
                        'is_implemented': False,
                        'status': draft.get('status', 'draft'),
                        'created_at': draft.get('created_at'),
                        'updated_at': draft.get('updated_at'),
                        'created_by': draft.get('created_by')
                    })
        except Exception as e:
            # Table might not exist yet - that's OK
            print(f"Note: Could not fetch draft processes: {e}")

    # Filter by category if specified
    if category:
        processes = [p for p in processes if p.get('category') == category]

    # Calculate layout positions
    processes = calculate_layout(processes)

    # Sort: implemented first, then by category, then by name
    processes.sort(key=lambda p: (
        0 if p.get('is_implemented') else 1,
        p.get('category', 'zzz'),
        p.get('name', '')
    ))

    return {
        "processes": processes,
        "total": len(processes),
        "implemented_count": sum(1 for p in processes if p.get('is_implemented')),
        "draft_count": sum(1 for p in processes if not p.get('is_implemented'))
    }


@router.get("/{process_id}")
async def get_process(process_id: str):
    """
    Get a single process by ID.
    Checks both implemented (code) and draft (database) processes.
    """
    # First check implemented processes
    try:
        api_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        implemented = get_all_implemented_processes(api_root)
        for p in implemented:
            if p['id'] == process_id:
                return p
    except Exception as e:
        print(f"Error checking implemented processes: {e}")

    # Then check database drafts
    try:
        response = supabase.table("process_drafts").select("*").eq("process_id", process_id).execute()
        if response.data and len(response.data) > 0:
            draft = response.data[0]
            return {
                'id': draft['process_id'],
                'name': draft['name'],
                'category': draft.get('category', 'operations'),
                'trigger': draft.get('trigger_type', 'manual'),
                'description': draft.get('description', ''),
                'owner': draft.get('owner', ''),
                'steps': draft.get('steps', []),
                'position': draft.get('position'),
                'is_implemented': False,
                'status': draft.get('status', 'draft'),
                'created_at': draft.get('created_at'),
                'updated_at': draft.get('updated_at')
            }
    except Exception as e:
        print(f"Error checking draft processes: {e}")

    raise HTTPException(status_code=404, detail=f"Process '{process_id}' not found")


@router.post("/drafts")
async def create_draft_process(process: ProcessCreate):
    """
    Create a new draft process.
    Draft processes are proposals that haven't been implemented in code yet.
    """
    # Check if process ID already exists (implemented or draft)
    try:
        api_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        implemented = get_all_implemented_processes(api_root)
        if any(p['id'] == process.id for p in implemented):
            raise HTTPException(
                status_code=400,
                detail=f"Process ID '{process.id}' already exists as an implemented process"
            )
    except HTTPException:
        raise
    except Exception:
        pass

    # Check database for existing draft
    try:
        existing = supabase.table("process_drafts").select("id").eq("process_id", process.id).execute()
        if existing.data and len(existing.data) > 0:
            raise HTTPException(
                status_code=400,
                detail=f"Draft process '{process.id}' already exists"
            )
    except HTTPException:
        raise
    except Exception:
        pass

    # Create the draft
    insert_data = {
        "process_id": process.id,
        "name": process.name,
        "category": process.category,
        "trigger_type": process.trigger,
        "description": process.description,
        "owner": process.owner,
        "steps": [step.dict() for step in process.steps],
        "position": process.position,
        "status": "draft"
    }

    try:
        response = supabase.table("process_drafts").insert(insert_data).execute()
        return {
            "message": "Draft process created",
            "data": response.data[0] if response.data else None
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error creating draft: {str(e)}")


@router.patch("/drafts/{process_id}")
async def update_draft_process(process_id: str, update: ProcessUpdate):
    """
    Update a draft process.
    Only draft processes can be updated - implemented processes require code changes.
    """
    # Check if this is an implemented process (can't update those)
    try:
        api_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        implemented = get_all_implemented_processes(api_root)
        if any(p['id'] == process_id for p in implemented):
            raise HTTPException(
                status_code=400,
                detail="Cannot update implemented processes. Modify the source code instead."
            )
    except HTTPException:
        raise
    except Exception:
        pass

    # Build update data
    update_data = {"updated_at": datetime.utcnow().isoformat()}

    if update.name is not None:
        update_data["name"] = update.name
    if update.category is not None:
        update_data["category"] = update.category
    if update.trigger is not None:
        update_data["trigger_type"] = update.trigger
    if update.description is not None:
        update_data["description"] = update.description
    if update.owner is not None:
        update_data["owner"] = update.owner
    if update.steps is not None:
        update_data["steps"] = [step.dict() for step in update.steps]
    if update.position is not None:
        update_data["position"] = update.position
    if update.status is not None:
        update_data["status"] = update.status

    try:
        response = supabase.table("process_drafts").update(update_data).eq("process_id", process_id).execute()
        if not response.data:
            raise HTTPException(status_code=404, detail=f"Draft process '{process_id}' not found")
        return {
            "message": "Draft process updated",
            "data": response.data[0]
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error updating draft: {str(e)}")


@router.delete("/drafts/{process_id}")
async def delete_draft_process(process_id: str):
    """
    Delete a draft process.
    Only draft processes can be deleted.
    """
    # Check if this is an implemented process
    try:
        api_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        implemented = get_all_implemented_processes(api_root)
        if any(p['id'] == process_id for p in implemented):
            raise HTTPException(
                status_code=400,
                detail="Cannot delete implemented processes."
            )
    except HTTPException:
        raise
    except Exception:
        pass

    try:
        response = supabase.table("process_drafts").delete().eq("process_id", process_id).execute()
        return {"message": "Draft process deleted", "deleted_id": process_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error deleting draft: {str(e)}")


@router.get("/categories/list")
async def get_process_categories():
    """
    Get list of available process categories.
    """
    return {
        "categories": [
            {"id": "coordination", "name": "Coordination", "description": "Project and team coordination processes"},
            {"id": "bookkeeping", "name": "Bookkeeping", "description": "Financial and accounting processes"},
            {"id": "operations", "name": "Operations", "description": "Day-to-day operational processes"},
            {"id": "finance", "name": "Finance", "description": "Financial management and budgeting"},
            {"id": "hr", "name": "Human Resources", "description": "HR and team management processes"},
            {"id": "sales", "name": "Sales", "description": "Sales and client acquisition processes"},
        ]
    }


@router.get("/triggers/list")
async def get_trigger_types():
    """
    Get list of available trigger types.
    """
    return {
        "triggers": [
            {"id": "manual", "name": "Manual", "description": "Triggered manually by a user"},
            {"id": "scheduled", "name": "Scheduled", "description": "Runs on a schedule (daily, weekly, etc.)"},
            {"id": "event", "name": "Event", "description": "Triggered by a system event"},
            {"id": "webhook", "name": "Webhook", "description": "Triggered by an external webhook call"},
        ]
    }


@router.get("/step-types/list")
async def get_step_types():
    """
    Get list of available step types.
    """
    return {
        "step_types": [
            {"id": "condition", "name": "Condition", "description": "Evaluate a condition to determine flow", "icon": "split"},
            {"id": "action", "name": "Action", "description": "Perform an action or operation", "icon": "play"},
            {"id": "notification", "name": "Notification", "description": "Send a notification", "icon": "bell"},
            {"id": "wait", "name": "Wait", "description": "Wait for time or event", "icon": "clock"},
            {"id": "assignment", "name": "Assignment", "description": "Assign task to user/role", "icon": "user"},
            {"id": "approval", "name": "Approval", "description": "Require approval to continue", "icon": "check"},
        ]
    }
