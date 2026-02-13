# api/routers/adu_calculator.py
# ================================
# ADU Allowance Calculator API Router
# ================================
# Provides GPT Vision-powered floor plan analysis and cost estimation
# for Accessory Dwelling Units (ADUs).

from fastapi import APIRouter, HTTPException, File, UploadFile, Form, Depends
from fastapi.security import HTTPBearer
from pydantic import BaseModel
from typing import Optional, List, Any, Dict
from api.auth import get_current_user
from api.supabase_client import supabase
import base64
import os
import json
from api.services.gpt_client import gpt

router = APIRouter(prefix="/adu-calculator", tags=["ADU Calculator"])


# ====== MODELS ======

class ADUScreenshotAnalysis(BaseModel):
    floor_label: str
    bedrooms: int = 0
    bathrooms: int = 0
    half_baths: int = 0
    kitchen_type: str = "none"
    kitchen_features: List[str] = []
    living_areas: int = 0
    dining_area: bool = False
    laundry_area: bool = False
    garage_or_carport: bool = False
    storage_closets: int = 0
    walk_in_closets: int = 0
    hallways: int = 0
    estimated_doors: int = 0
    estimated_windows: int = 0
    special_features: List[str] = []
    structural_notes: List[str] = []
    plumbing_fixtures_estimate: int = 0
    electrical_complexity: str = "moderate"
    confidence: float = 0.0


class ADUCalculateRequest(BaseModel):
    adu_type: str
    stories: int
    construction_type: str
    sqft: int
    screenshot_analysis: Optional[List[ADUScreenshotAnalysis]] = None


class CostLineItem(BaseModel):
    category: str
    description: str
    estimated_cost: float
    cost_per_sqft: Optional[float] = None
    notes: Optional[str] = None


class ADUCostEstimate(BaseModel):
    total_estimated_cost: float
    cost_per_sqft: float
    line_items: List[CostLineItem]
    assumptions: List[str]
    disclaimer: str


# ====== GPT PROMPT ======

SCREENSHOT_ANALYSIS_PROMPT = """You are an expert construction estimator analyzing an ADU (Accessory Dwelling Unit) floor plan screenshot.

Analyze this floor plan image and extract all construction-relevant features. This data will be used to estimate construction costs.

Return a JSON object with EXACTLY these fields:

{
    "floor_label": "string - describe which floor this is, e.g. 'Floor 1', 'Floor 2', 'Single Floor'",
    "bedrooms": "integer - number of bedrooms visible",
    "bathrooms": "integer - number of full bathrooms (with shower/tub)",
    "half_baths": "integer - number of half baths (toilet + sink only)",
    "kitchen_type": "string - one of: 'full', 'kitchenette', 'galley', 'open_concept', 'none'",
    "kitchen_features": ["list of strings from: 'island', 'pantry', 'double_sink', 'dishwasher_space', 'gas_range', 'hood_vent', 'breakfast_bar', 'large_refrigerator_space'"],
    "living_areas": "integer - number of distinct living/family room spaces",
    "dining_area": "boolean - is there a dedicated or combined dining area",
    "laundry_area": "boolean - is there a laundry room or laundry closet",
    "garage_or_carport": "boolean - is a garage or carport included in this floor",
    "storage_closets": "integer - count of standard closets and storage areas",
    "walk_in_closets": "integer - count of walk-in closets",
    "hallways": "integer - number of distinct hallway segments",
    "estimated_doors": "integer - estimate total interior doors visible or implied",
    "estimated_windows": "integer - estimate number of windows shown or implied by exterior walls",
    "special_features": ["list of strings from: 'fireplace', 'balcony', 'patio_door', 'skylight', 'built_in_shelving', 'wet_bar', 'cathedral_ceiling', 'loft', 'staircase', 'elevator_shaft', 'deck', 'covered_porch'"],
    "structural_notes": ["list of strings from: 'load_bearing_wall', 'vaulted_ceiling', 'open_floor_plan', 'split_level', 'cantilever', 'large_span', 'reinforced_foundation'"],
    "plumbing_fixtures_estimate": "integer - total count of all plumbing fixtures (each sink, toilet, shower, tub, washer hookup counts as 1)",
    "electrical_complexity": "string - one of: 'basic' (few outlets, simple lighting), 'moderate' (standard residential), 'complex' (many circuits, special lighting, smart home wiring)",
    "confidence": "float 0.0-1.0 - how confident you are in this analysis"
}

RULES:
- If you cannot clearly identify something, use conservative estimates
- Count ALL plumbing fixtures: each sink, toilet, shower/tub, washer hookup
- For windows, estimate based on exterior wall segments visible
- For doors, count visible doors plus estimate for closets and bathrooms
- Kitchen type 'full' = standard residential kitchen with range, fridge space, sink, counter space
- Kitchen type 'kitchenette' = compact with limited counter/appliance space
- Kitchen type 'galley' = narrow corridor-style kitchen
- Kitchen type 'open_concept' = kitchen open to living/dining area
- Set confidence below 0.5 if the image is not clearly a floor plan
- Always return valid JSON matching the exact schema above"""


# ====== COST ALGORITHM CONSTANTS ======

# Base cost per sqft by construction type (USD)
BASE_COST_PER_SQFT = {
    "stick_build": 250,
    "energy_efficient": 320,
    "renovation": 200,
    "manufactured": 180,
}

# Multiplier by ADU type
TYPE_MULTIPLIERS = {
    "attached": 0.95,
    "detached": 1.10,
    "above_garage": 1.15,
    "garage_conversion": 0.75,
    "multifamily": 1.20,
}

# Story multiplier
STORY_MULTIPLIERS = {
    1: 1.0,
    2: 1.15,
    4: 1.35,
    5: 1.45,
}

# Per-feature cost adders (when screenshot analysis is available)
FEATURE_COSTS = {
    "full_bathroom": 18000,
    "half_bathroom": 8000,
    "kitchen_full": 25000,
    "kitchen_kitchenette": 12000,
    "kitchen_galley": 18000,
    "kitchen_open_concept": 30000,
    "laundry": 6000,
    "fireplace": 8000,
    "balcony": 12000,
    "skylight": 3500,
    "deck": 10000,
    "covered_porch": 8000,
    "staircase": 7000,
    "built_in_shelving": 2500,
    "wet_bar": 5000,
    "loft": 15000,
    "patio_door": 2000,
    "cathedral_ceiling": 6000,
    "door_unit": 350,
    "window_unit": 650,
    "walk_in_closet": 1500,
}


# ====== ENDPOINTS ======

@router.post("/analyze-screenshot")
async def analyze_screenshot(
    file: UploadFile = File(...),
    floor_label: str = Form("Floor 1"),
    current_user: dict = Depends(get_current_user)
):
    """
    Analyze a floor plan screenshot using GPT-4o Vision.

    Accepts: JPG, PNG, WebP images (max 10MB).
    Returns structured JSON with construction-relevant features.
    """
    try:
        # Validate file type
        allowed_types = ["image/jpeg", "image/jpg", "image/png", "image/webp"]
        if file.content_type not in allowed_types:
            raise HTTPException(
                status_code=400,
                detail=f"File type '{file.content_type}' not allowed. Use JPG, PNG, or WebP."
            )

        # Read file content
        file_content = await file.read()

        # Validate file size (10MB)
        max_size = 10 * 1024 * 1024
        if len(file_content) > max_size:
            raise HTTPException(
                status_code=400,
                detail=f"File too large. Maximum size is 10MB, got {len(file_content) / (1024*1024):.1f}MB."
            )

        # Encode image to base64
        img_b64 = base64.b64encode(file_content).decode("utf-8")
        media_type = file.content_type

        # Build prompt
        prompt_with_label = f"Floor being analyzed: {floor_label}\n\n{SCREENSHOT_ANALYSIS_PROMPT}"

        # Call GPT-5.2 Vision
        raw = gpt.heavy(
            system=prompt_with_label,
            user=[{"type": "image_url", "image_url": {
                "url": f"data:{media_type};base64,{img_b64}",
                "detail": "high"
            }}],
            max_tokens=1500,
            json_mode=True,
            timeout=60,
        )
        if not raw:
            raise HTTPException(status_code=500, detail="GPT Vision returned empty response")

        # Parse response
        parsed_data = json.loads(raw)

        # Override floor_label with what the user specified
        parsed_data["floor_label"] = floor_label

        # Validate confidence
        confidence = parsed_data.get("confidence", 0)
        warning = None
        if confidence < 0.5:
            warning = "Low confidence analysis. The image may not be a clear floor plan."

        return {
            "message": "Screenshot analyzed successfully",
            "data": parsed_data,
            "warning": warning
        }

    except HTTPException:
        raise
    except json.JSONDecodeError:
        raise HTTPException(status_code=500, detail="Failed to parse AI analysis response")
    except Exception as e:
        print(f"[ADU_CALC] Error analyzing screenshot: {repr(e)}")
        raise HTTPException(status_code=500, detail=f"Analysis failed: {str(e)}")


@router.post("/calculate")
async def calculate_cost(
    payload: ADUCalculateRequest,
    current_user: dict = Depends(get_current_user)
):
    """
    Calculate estimated ADU construction cost.

    Uses base cost multipliers and optional screenshot analysis
    to produce a line-item cost breakdown.
    """
    try:
        # Validate inputs
        if payload.adu_type not in TYPE_MULTIPLIERS:
            raise HTTPException(status_code=400, detail=f"Invalid adu_type: {payload.adu_type}")
        if payload.stories not in STORY_MULTIPLIERS:
            raise HTTPException(status_code=400, detail=f"Invalid stories: {payload.stories}")
        if payload.construction_type not in BASE_COST_PER_SQFT:
            raise HTTPException(status_code=400, detail=f"Invalid construction_type: {payload.construction_type}")
        if payload.sqft <= 0:
            raise HTTPException(status_code=400, detail="sqft must be greater than 0")

        # Calculate base cost
        base_per_sqft = BASE_COST_PER_SQFT[payload.construction_type]
        type_mult = TYPE_MULTIPLIERS[payload.adu_type]
        story_mult = STORY_MULTIPLIERS[payload.stories]

        adjusted_per_sqft = base_per_sqft * type_mult * story_mult
        base_cost = adjusted_per_sqft * payload.sqft

        # Build line items
        line_items = []

        # Base structure
        line_items.append(CostLineItem(
            category="Structure",
            description=f"Base construction ({payload.construction_type.replace('_', ' ').title()})",
            estimated_cost=round(base_cost * 0.35, 2),
            cost_per_sqft=round(adjusted_per_sqft * 0.35, 2),
            notes=f"{payload.adu_type.replace('_', ' ').title()} ADU, {payload.stories}-story"
        ))

        # Foundation
        foundation_cost = base_cost * 0.12
        line_items.append(CostLineItem(
            category="Foundation",
            description="Foundation and site work",
            estimated_cost=round(foundation_cost, 2),
            cost_per_sqft=round(foundation_cost / payload.sqft, 2)
        ))

        # Electrical
        electrical_base = base_cost * 0.10
        line_items.append(CostLineItem(
            category="Electrical",
            description="Electrical systems and wiring",
            estimated_cost=round(electrical_base, 2),
            cost_per_sqft=round(electrical_base / payload.sqft, 2)
        ))

        # Plumbing
        plumbing_base = base_cost * 0.10
        line_items.append(CostLineItem(
            category="Plumbing",
            description="Plumbing systems and fixtures",
            estimated_cost=round(plumbing_base, 2),
            cost_per_sqft=round(plumbing_base / payload.sqft, 2)
        ))

        # HVAC
        hvac_cost = base_cost * 0.08
        line_items.append(CostLineItem(
            category="HVAC",
            description="Heating, ventilation, and air conditioning",
            estimated_cost=round(hvac_cost, 2),
            cost_per_sqft=round(hvac_cost / payload.sqft, 2)
        ))

        # Interior finishes
        finishes_cost = base_cost * 0.15
        line_items.append(CostLineItem(
            category="Interior Finishes",
            description="Drywall, paint, flooring, trim",
            estimated_cost=round(finishes_cost, 2),
            cost_per_sqft=round(finishes_cost / payload.sqft, 2)
        ))

        # Exterior
        exterior_cost = base_cost * 0.10
        line_items.append(CostLineItem(
            category="Exterior",
            description="Roofing, siding, windows, doors",
            estimated_cost=round(exterior_cost, 2),
            cost_per_sqft=round(exterior_cost / payload.sqft, 2)
        ))

        assumptions = [
            f"Base rate: ${base_per_sqft}/sqft for {payload.construction_type.replace('_', ' ')} construction",
            f"ADU type multiplier: {type_mult}x ({payload.adu_type.replace('_', ' ')})",
            f"Story multiplier: {story_mult}x ({payload.stories}-story)",
            "Costs based on average Southern California market rates",
            "Does not include permits, design fees, or utility connections",
        ]

        # Adjust if screenshot analysis is provided
        feature_adders = 0
        if payload.screenshot_analysis:
            for analysis in payload.screenshot_analysis:
                # Bathrooms
                if analysis.bathrooms > 0:
                    cost = analysis.bathrooms * FEATURE_COSTS["full_bathroom"]
                    feature_adders += cost
                    line_items.append(CostLineItem(
                        category="Bathrooms",
                        description=f"{analysis.bathrooms} full bathroom(s) - {analysis.floor_label}",
                        estimated_cost=round(cost, 2),
                        notes="Based on floor plan analysis"
                    ))

                if analysis.half_baths > 0:
                    cost = analysis.half_baths * FEATURE_COSTS["half_bathroom"]
                    feature_adders += cost
                    line_items.append(CostLineItem(
                        category="Bathrooms",
                        description=f"{analysis.half_baths} half bath(s) - {analysis.floor_label}",
                        estimated_cost=round(cost, 2),
                        notes="Based on floor plan analysis"
                    ))

                # Kitchen
                kitchen_key = f"kitchen_{analysis.kitchen_type}"
                if kitchen_key in FEATURE_COSTS:
                    cost = FEATURE_COSTS[kitchen_key]
                    feature_adders += cost
                    line_items.append(CostLineItem(
                        category="Kitchen",
                        description=f"{analysis.kitchen_type.replace('_', ' ').title()} kitchen - {analysis.floor_label}",
                        estimated_cost=round(cost, 2),
                        notes="Based on floor plan analysis"
                    ))

                # Laundry
                if analysis.laundry_area:
                    cost = FEATURE_COSTS["laundry"]
                    feature_adders += cost
                    line_items.append(CostLineItem(
                        category="Laundry",
                        description=f"Laundry area - {analysis.floor_label}",
                        estimated_cost=round(cost, 2)
                    ))

                # Special features
                for feature in analysis.special_features:
                    feature_key = feature.lower().replace(" ", "_")
                    if feature_key in FEATURE_COSTS:
                        cost = FEATURE_COSTS[feature_key]
                        feature_adders += cost
                        line_items.append(CostLineItem(
                            category="Special Features",
                            description=f"{feature.replace('_', ' ').title()} - {analysis.floor_label}",
                            estimated_cost=round(cost, 2)
                        ))

                # Doors and windows
                if analysis.estimated_doors > 0:
                    cost = analysis.estimated_doors * FEATURE_COSTS["door_unit"]
                    feature_adders += cost
                    line_items.append(CostLineItem(
                        category="Doors & Windows",
                        description=f"{analysis.estimated_doors} interior doors - {analysis.floor_label}",
                        estimated_cost=round(cost, 2)
                    ))

                if analysis.estimated_windows > 0:
                    cost = analysis.estimated_windows * FEATURE_COSTS["window_unit"]
                    feature_adders += cost
                    line_items.append(CostLineItem(
                        category="Doors & Windows",
                        description=f"{analysis.estimated_windows} windows - {analysis.floor_label}",
                        estimated_cost=round(cost, 2)
                    ))

                # Walk-in closets
                if analysis.walk_in_closets > 0:
                    cost = analysis.walk_in_closets * FEATURE_COSTS["walk_in_closet"]
                    feature_adders += cost
                    line_items.append(CostLineItem(
                        category="Closets",
                        description=f"{analysis.walk_in_closets} walk-in closet(s) - {analysis.floor_label}",
                        estimated_cost=round(cost, 2)
                    ))

                # Electrical complexity adjustment
                if analysis.electrical_complexity == "complex":
                    adj = electrical_base * 0.30
                    feature_adders += adj
                    line_items.append(CostLineItem(
                        category="Electrical",
                        description=f"Complexity surcharge - {analysis.floor_label}",
                        estimated_cost=round(adj, 2),
                        notes="Complex electrical layout detected"
                    ))

            assumptions.append("Feature-level adjustments applied from floor plan analysis")

        total_cost = base_cost + feature_adders
        effective_per_sqft = total_cost / payload.sqft

        estimate = ADUCostEstimate(
            total_estimated_cost=round(total_cost, 2),
            cost_per_sqft=round(effective_per_sqft, 2),
            line_items=line_items,
            assumptions=assumptions,
            disclaimer="This is a preliminary estimate for planning purposes only. Actual costs may vary based on local labor rates, material prices, site conditions, permits, and other factors. Not a binding quote."
        )

        return {
            "message": "Cost estimate calculated",
            "data": estimate.model_dump()
        }

    except HTTPException:
        raise
    except Exception as e:
        print(f"[ADU_CALC] Error calculating cost: {repr(e)}")
        raise HTTPException(status_code=500, detail=f"Calculation failed: {str(e)}")


# ====== PRICING CONFIG ENDPOINTS ======

class PricingConfigUpdate(BaseModel):
    pricing_data: Dict[str, Any]


@router.get("/pricing-config")
async def get_pricing_config(current_user: dict = Depends(get_current_user)):
    """Load the pricing configuration from the database."""
    try:
        result = supabase.table("adu_pricing_config") \
            .select("pricing_data") \
            .eq("config_key", "main") \
            .single() \
            .execute()

        if result.data and result.data.get("pricing_data"):
            return {"data": result.data["pricing_data"]}

        return {"data": None}

    except Exception as e:
        print(f"[ADU_CALC] Error loading pricing config: {repr(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to load pricing config: {str(e)}")


@router.put("/pricing-config")
async def update_pricing_config(
    payload: PricingConfigUpdate,
    current_user: dict = Depends(get_current_user)
):
    """Save the pricing configuration to the database."""
    try:
        result = supabase.table("adu_pricing_config") \
            .update({
                "pricing_data": payload.pricing_data,
                "updated_at": "now()"
            }) \
            .eq("config_key", "main") \
            .execute()

        if not result.data:
            # Row doesn't exist yet — insert it
            supabase.table("adu_pricing_config") \
                .insert({
                    "config_key": "main",
                    "pricing_data": payload.pricing_data
                }) \
                .execute()

        return {"message": "Pricing configuration saved"}

    except Exception as e:
        print(f"[ADU_CALC] Error saving pricing config: {repr(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to save pricing config: {str(e)}")
