# api/routers/estimator.py

from typing import Dict, Any
from pathlib import Path
import json

from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/estimator", tags=["estimator"])

# ============================================
# Rutas correctas según tu estructura:
# NGM_API/
#   templates/estimate.ngm   👈 aquí
#   api/
#     main.py
#     routers/estimator.py
# ============================================

BASE_DIR = Path(__file__).resolve().parents[2]   # .../NGM_API
TEMPLATES_DIR = BASE_DIR / "templates"
NGM_FILE_PATH = TEMPLATES_DIR / "estimate.ngm"

print(f"[ESTIMATOR] BASE_DIR      = {BASE_DIR}")
print(f"[ESTIMATOR] TEMPLATES_DIR = {TEMPLATES_DIR}")
print(f"[ESTIMATOR] NGM_FILE_PATH = {NGM_FILE_PATH}")
if not NGM_FILE_PATH.exists():
    print(f"[ESTIMATOR] WARNING: estimate.ngm not found at {NGM_FILE_PATH}")


@router.get("/base-structure")
async def get_base_structure() -> Dict[str, Any]:
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
async def save_estimate(payload: Dict[str, Any]) -> Dict[str, Any]:
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
