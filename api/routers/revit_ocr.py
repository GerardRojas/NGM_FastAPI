# api/routers/revit_ocr.py
# ================================
# Revit OCR - GPT-4o Vision floor plan analysis
# ================================
# Single unified endpoint that returns tagged points for selected layers.
# All coordinates returned in pixel space relative to image dimensions.
# Client-side code connects points into geometry for rendering.

from fastapi import APIRouter, HTTPException, File, UploadFile, Form, Depends
from pydantic import BaseModel
from typing import Optional, List
from api.auth import get_current_user
import base64
import os
import io
import json
from openai import OpenAI

router = APIRouter(prefix="/revit/ocr", tags=["Revit OCR"])


# ====== MODELS ======

class TaggedPoint(BaseModel):
    x: float
    y: float
    tag: str
    order: Optional[int] = None
    pair: Optional[int] = None
    facing: Optional[str] = None
    wall_side: Optional[str] = None

class LayerResult(BaseModel):
    points: List[TaggedPoint] = []
    confidence: float = 0.0

class ScaleResult(BaseModel):
    detected: bool
    pixels_per_foot: Optional[float] = None
    reference: Optional[str] = None
    confidence: float = 0.0


# ====== VALID LAYERS ======

VALID_LAYERS = {
    "exterior_walls", "interior_walls", "doors",
    "windows", "plumbing", "kitchen"
}


# ====== LAYER INSTRUCTION BLOCKS ======

LAYER_INSTRUCTIONS = {
    "exterior_walls": """
EXTERIOR WALLS - Identify every corner point of the building perimeter, tracing the outline CLOCKWISE starting from the top-left-most corner.
For each corner return:
  - tag: "corner"
  - order: sequential integer (1, 2, 3, ...) following the perimeter clockwise
The client will connect point N to N+1, and the last point back to point 1, to form the closed exterior wall polygon.
Include ALL corners where the exterior wall changes direction. A simple rectangle = 4 corners. An L-shaped plan = 6 corners. Count every direction change.""",

    "interior_walls": """
INTERIOR WALLS - Identify every interior wall segment (walls inside the building that divide rooms).
For each wall return TWO points:
  - First point: tag "start", pair: integer (1, 2, 3, ...)
  - Second point: tag "end", pair: same integer as its start
Each pair number groups one wall segment. The client connects start to end for each pair.
Do NOT include exterior perimeter walls here (those go in exterior_walls layer). Only internal partitions.
If a wall has a corner inside a room, break it into 2 pairs (one per straight segment).""",

    "doors": """
DOORS - Identify every door opening in the floor plan.
For each door return ONE point at the center of the door opening on the wall line:
  - tag: one of "single", "double", "sliding", "pocket"
  - facing: cardinal direction the door opens toward: "north" (up), "south" (down), "east" (right), "west" (left)
Quarter-circle arc = single hinged. Double arc = double door. Parallel lines across opening = sliding. Break in wall without arc = assume single.""",

    "windows": """
WINDOWS - Identify every window opening in the floor plan.
For each window return ONE point at the center of the window on the wall line:
  - tag: one of "fixed", "sliding", "casement"
  - wall_side: cardinal direction the window faces outward: "north", "south", "east", "west"
Windows appear as thin parallel lines or small rectangles in exterior walls. Sliding windows show two overlapping panes. Fixed windows are single lines.""",

    "plumbing": """
PLUMBING FIXTURES - Identify all plumbing fixtures visible in the floor plan.
For each fixture return ONE point at its center:
  - tag: one of "toilet", "sink", "shower", "bathtub", "washer_hookup"
Toilets = small oval/circle attached to wall. Sinks = small rectangle/oval with faucet indicator. Showers = square/rectangular enclosure. Bathtubs = elongated oval. Washer hookup may be labeled or shown as a circle near utility area.""",

    "kitchen": """
KITCHEN FIXTURES - Identify all kitchen appliances and fixtures visible in the floor plan.
For each return ONE point at its center:
  - tag: one of "sink", "stove", "refrigerator", "dishwasher", "island", "counter"
Stoves show burner circles. Refrigerators are rectangles often in a corner. Sinks show a basin. Dishwashers are adjacent to sinks. Islands are freestanding rectangles. Counters are L-shaped or linear runs along walls."""
}


# ====== DYNAMIC PROMPT BUILDER ======

def _build_analyze_prompt(width: int, height: int, layers: list, context: str = ""):
    header = (
        "You are a construction floor plan digitizer. Analyze this floor plan image "
        "and extract tagged points for the requested layers.\n\n"
        f"Image dimensions: {width}px wide x {height}px tall. "
        "Return all coordinates as pixel integers relative to top-left corner (0,0). "
        "X increases rightward, Y increases downward.\n\n"
        "SCALE DETECTION (always included):\n"
        "Look for scale bars, dimension lines, room labels with sizes, or "
        "standard element references (standard single door ~3ft/36in wide, "
        "standard hallway ~3.5ft wide, standard interior door ~2.67ft/32in). "
        "Report what reference you used and your confidence.\n"
    )

    if context:
        header += f"\nPROJECT CONTEXT: {context}\n"

    # Append selected layer instructions
    layer_sections = []
    for layer in layers:
        if layer in LAYER_INSTRUCTIONS:
            layer_sections.append(LAYER_INSTRUCTIONS[layer])

    body = "\n".join(layer_sections)

    # Build dynamic JSON schema showing only requested layers
    schema_parts = []
    for layer in layers:
        if layer == "exterior_walls":
            schema_parts.append(
                '    "exterior_walls": { "points": [{"x": int, "y": int, "tag": "corner", "order": int}], "confidence": float }'
            )
        elif layer == "interior_walls":
            schema_parts.append(
                '    "interior_walls": { "points": [{"x": int, "y": int, "tag": "start|end", "pair": int}], "confidence": float }'
            )
        elif layer == "doors":
            schema_parts.append(
                '    "doors": { "points": [{"x": int, "y": int, "tag": "single|double|sliding|pocket", "facing": "north|south|east|west"}], "confidence": float }'
            )
        elif layer == "windows":
            schema_parts.append(
                '    "windows": { "points": [{"x": int, "y": int, "tag": "fixed|sliding|casement", "wall_side": "north|south|east|west"}], "confidence": float }'
            )
        elif layer == "plumbing":
            schema_parts.append(
                '    "plumbing": { "points": [{"x": int, "y": int, "tag": "toilet|sink|shower|bathtub|washer_hookup"}], "confidence": float }'
            )
        elif layer == "kitchen":
            schema_parts.append(
                '    "kitchen": { "points": [{"x": int, "y": int, "tag": "sink|stove|refrigerator|dishwasher|island|counter"}], "confidence": float }'
            )

    layers_schema = ",\n".join(schema_parts)

    footer = f"""

Return ONLY valid JSON with this exact structure:
{{
  "scale": {{
    "detected": true or false,
    "pixels_per_foot": number or null,
    "reference": "what reference you used to determine scale",
    "confidence": 0.0 to 1.0
  }},
  "layers": {{
{layers_schema}
  }}
}}

RULES:
- Be precise with pixel coordinates. Measure carefully against the image.
- Every point x must be 0 to {width - 1}, y must be 0 to {height - 1}.
- If you cannot detect any elements for a requested layer, return empty points array with confidence 0.
- For scale: explicit dimensions on the plan = confidence > 0.7. Inferring from standard element sizes = 0.3-0.6. No reference found = detected false.
- Always return valid JSON. No trailing commas. No comments."""

    return header + body + footer


# ====== HELPERS ======

def _get_openai_client():
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise HTTPException(status_code=500, detail="OpenAI API key not configured")
    return OpenAI(api_key=api_key)


def _validate_image(file_content: bytes, content_type: str):
    allowed = ["image/jpeg", "image/jpg", "image/png", "image/webp"]
    if content_type not in allowed:
        raise HTTPException(
            status_code=400,
            detail=f"File type '{content_type}' not allowed. Use JPG, PNG, or WebP."
        )
    max_size = 10 * 1024 * 1024
    if len(file_content) > max_size:
        raise HTTPException(
            status_code=400,
            detail=f"File too large. Maximum 10MB, got {len(file_content) / (1024*1024):.1f}MB."
        )


def _get_image_dimensions(file_content: bytes):
    try:
        from PIL import Image
        img = Image.open(io.BytesIO(file_content))
        return img.size  # (width, height)
    except Exception:
        return (0, 0)


def _call_vision(client, prompt: str, img_b64: str, media_type: str, max_tokens: int = 2000):
    content = [
        {"type": "text", "text": prompt},
        {
            "type": "image_url",
            "image_url": {
                "url": f"data:{media_type};base64,{img_b64}",
                "detail": "high"
            }
        }
    ]
    response = client.chat.completions.create(
        model="gpt-5.1",
        messages=[{"role": "user", "content": content}],
        max_completion_tokens=max_tokens,
        response_format={"type": "json_object"},
        temperature=0.1,
        timeout=120
    )
    return json.loads(response.choices[0].message.content)


# ====== ENDPOINT ======

@router.post("/analyze")
async def analyze_floorplan(
    file: UploadFile = File(...),
    layers: str = Form(...),
    context: str = Form(""),
    current_user: dict = Depends(get_current_user)
):
    """
    Unified floor plan analysis. Single GPT-4o Vision call that returns
    tagged points for all selected layers plus automatic scale detection.
    Accepts: JPG, PNG, WebP (max 10MB).
    Layers (comma-separated): exterior_walls, interior_walls, doors, windows, plumbing, kitchen
    """
    try:
        file_content = await file.read()
        _validate_image(file_content, file.content_type)

        width_px, height_px = _get_image_dimensions(file_content)
        if width_px == 0:
            raise HTTPException(status_code=400, detail="Could not read image dimensions")

        # Parse and validate layers
        requested_layers = [l.strip() for l in layers.split(",") if l.strip()]
        invalid = [l for l in requested_layers if l not in VALID_LAYERS]
        if invalid:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid layers: {', '.join(invalid)}. Valid: {', '.join(sorted(VALID_LAYERS))}"
            )
        if not requested_layers:
            raise HTTPException(status_code=400, detail="At least one layer required")

        client = _get_openai_client()
        img_b64 = base64.b64encode(file_content).decode("utf-8")

        prompt = _build_analyze_prompt(width_px, height_px, requested_layers, context.strip())

        # Scale max_tokens based on layer count
        max_tokens = 2000 + len(requested_layers) * 1500
        max_tokens = min(max_tokens, 16000)

        parsed = _call_vision(client, prompt, img_b64, file.content_type, max_tokens=max_tokens)

        # Extract response sections
        scale_data = parsed.get("scale", {})
        layers_data = parsed.get("layers", {})

        # Clamp all points to image bounds
        for layer_name, layer_obj in layers_data.items():
            for pt in layer_obj.get("points", []):
                pt["x"] = max(0, min(width_px - 1, int(pt.get("x", 0))))
                pt["y"] = max(0, min(height_px - 1, int(pt.get("y", 0))))

        # Count totals
        total_points = sum(len(ld.get("points", [])) for ld in layers_data.values())

        warning = None
        scale_conf = scale_data.get("confidence", 0)
        if scale_conf < 0.3:
            warning = "Very low scale confidence. Manual calibration recommended."
        elif scale_conf < 0.5:
            warning = "Low scale confidence. Verify with a known dimension."

        return {
            "message": f"Analysis complete. {total_points} points across {len(layers_data)} layers.",
            "data": {
                "image_width_px": width_px,
                "image_height_px": height_px,
                "scale": {
                    "detected": scale_data.get("detected", False),
                    "pixels_per_foot": scale_data.get("pixels_per_foot"),
                    "reference": scale_data.get("reference"),
                    "confidence": scale_conf
                },
                "layers": layers_data
            },
            "warning": warning
        }

    except HTTPException:
        raise
    except json.JSONDecodeError:
        raise HTTPException(status_code=500, detail="Failed to parse AI response as JSON")
    except Exception as e:
        print(f"[REVIT_OCR] Analysis error: {repr(e)}")
        raise HTTPException(status_code=500, detail=f"Analysis failed: {str(e)}")
