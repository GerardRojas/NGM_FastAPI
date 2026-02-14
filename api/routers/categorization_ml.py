"""
Categorization ML Router
Endpoints for managing the TF-IDF + k-NN expense categorization model.
- Train/retrain the model
- Check model status
- Test predictions
"""

import logging
from fastapi import APIRouter, HTTPException, Query
from typing import Optional

from api.supabase_client import supabase
from api.services.categorization_ml import get_ml_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/categorization-ml", tags=["categorization-ml"])


@router.post("/train")
async def train_model():
    """
    Force retrain the ML categorization model from current expense data.
    Loads all expenses_manual_COGS rows, enriches with cache confidence
    and corrections, then builds TF-IDF + k-NN model.
    """
    try:
        ml = get_ml_service()
        result = ml.train(supabase)
        return {"success": True, **result}
    except Exception as e:
        logger.error("[ML-Router] Train error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/status")
async def model_status():
    """
    Get current model status: trained, stale, training size, features, etc.
    """
    ml = get_ml_service()
    return {"success": True, **ml.get_status()}


@router.post("/predict")
async def predict_category(payload: dict):
    """
    Test a single description against the ML model.

    Body: {"description": "2x4 SPF Lumber 8ft", "min_confidence": 90}
    Returns: prediction or null if below threshold.
    """
    description = payload.get("description", "").strip()
    if not description:
        raise HTTPException(status_code=400, detail="Missing description")

    min_confidence = float(payload.get("min_confidence", 90.0))
    stage = payload.get("stage")

    ml = get_ml_service()
    ml.ensure_trained(supabase)

    if not ml.is_trained:
        return {
            "success": False,
            "message": "Model not trained (insufficient data or error)",
        }

    result = ml.predict(description, construction_stage=stage, min_confidence=min_confidence)
    return {
        "success": True,
        "prediction": result,
        "model_status": ml.get_status(),
    }


@router.post("/predict-batch")
async def predict_batch(payload: dict):
    """
    Test multiple descriptions against the ML model.

    Body: {"items": [{"description": "Lumber 2x4"}, ...], "min_confidence": 90}
    """
    items = payload.get("items", [])
    if not items:
        raise HTTPException(status_code=400, detail="Missing items array")

    min_confidence = float(payload.get("min_confidence", 90.0))
    stage = payload.get("stage")

    ml = get_ml_service()
    ml.ensure_trained(supabase)

    if not ml.is_trained:
        return {
            "success": False,
            "message": "Model not trained (insufficient data or error)",
        }

    results = ml.predict_batch(items, construction_stage=stage, min_confidence=min_confidence)

    classified = sum(1 for r in results if r is not None)
    return {
        "success": True,
        "predictions": results,
        "classified": classified,
        "total": len(items),
        "hit_rate": round(classified / len(items) * 100, 1) if items else 0,
        "model_status": ml.get_status(),
    }
