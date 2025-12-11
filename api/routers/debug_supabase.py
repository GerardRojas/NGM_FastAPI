from fastapi import APIRouter, HTTPException
from api.supabase_client import supabase

router = APIRouter(prefix="/debug", tags=["debug"])

@router.get("/supabase-ping")
def supabase_ping():
    try:
        # 🔧 Cambia "projects" por una tabla real que tengas en Supabase
        resp = supabase.table("projects").select("*").limit(1).execute()
        return {
            "message": "Supabase OK",
            "rows_found": len(resp.data),
            "sample": resp.data
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
