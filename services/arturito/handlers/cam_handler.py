"""
===============================================================================
 NGM Cam Photo Search Handler for Arturito
===============================================================================
 Searches construction photos stored in vault_files by:
 - Project name (required, or ask_project flow)
 - Milestone (optional, fuzzy-matched against existing milestones)
 - Date range (optional, parsed from NGMCAM_ filename)

 Edge cases handled:
 - No photos in project -> clear message
 - Milestone not found -> fuzzy match, suggest available milestones
 - Milestone fuzzy score too low -> list available milestones as buttons
===============================================================================
"""

import re
import logging
from difflib import SequenceMatcher
from typing import Dict, Any, Optional, List

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Filename parser
# ---------------------------------------------------------------------------

_NGMCAM_RE = re.compile(r'^NGMCAM_(.+?)_(\d{8})_(\d{6})\.')


def _parse_ngmcam_filename(name: str) -> Dict[str, Optional[str]]:
    """Parse NGMCAM_Milestone_YYYYMMDD_HHMMSS.ext filename."""
    match = _NGMCAM_RE.match(name)
    if not match:
        return {"milestone": None, "date": None, "time": None}
    return {
        "milestone": match.group(1).replace("-", " "),
        "date": match.group(2),
        "time": match.group(3),
    }


# ---------------------------------------------------------------------------
# Fuzzy matching
# ---------------------------------------------------------------------------

FUZZY_THRESHOLD = 0.55  # Minimum similarity to consider a match


def _similarity(a: str, b: str) -> float:
    """Character-level similarity (0-1) using SequenceMatcher."""
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def _best_milestone_match(
    query: str, available: List[str]
) -> Optional[Dict[str, Any]]:
    """
    Find the best matching milestone from available list.

    Returns dict with 'name' and 'score', or None if no good match.
    Tries: exact substring first, then fuzzy scoring.
    """
    if not query or not available:
        return None

    q = query.lower().strip()

    # 1) Exact match
    for m in available:
        if m.lower() == q:
            return {"name": m, "score": 1.0}

    # 2) Substring match (query inside milestone or milestone inside query)
    for m in available:
        ml = m.lower()
        if q in ml or ml in q:
            return {"name": m, "score": 0.85}

    # 3) Fuzzy scoring
    scored = []
    for m in available:
        # Score against full name
        s = _similarity(q, m)
        # Also score each word of milestone vs query
        for word in m.lower().split():
            ws = _similarity(q, word)
            if ws > s:
                s = ws
        scored.append({"name": m, "score": s})

    scored.sort(key=lambda x: x["score"], reverse=True)
    best = scored[0]

    if best["score"] >= FUZZY_THRESHOLD:
        return best

    return None


def _extract_milestones(all_files: List[dict]) -> List[str]:
    """Extract unique milestone names from parsed filenames, sorted."""
    seen = {}
    for f in all_files:
        parsed = _parse_ngmcam_filename(f.get("name", ""))
        if parsed["milestone"]:
            key = parsed["milestone"].lower()
            if key not in seen:
                seen[key] = parsed["milestone"]  # preserve original case
    return sorted(seen.values())


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _find_photos_folder(supabase, project_id: str) -> Optional[str]:
    """Find the root-level 'Photos' folder for a project in vault_files."""
    try:
        result = (
            supabase.table("vault_files")
            .select("id")
            .eq("name", "Photos")
            .eq("is_folder", True)
            .eq("is_deleted", False)
            .eq("project_id", str(project_id))
            .is_("parent_id", "null")
            .limit(1)
            .execute()
        )
        return result.data[0]["id"] if result.data else None
    except Exception as e:
        logger.error("[Cam Handler] Error finding Photos folder: %s", e)
        return None


def _format_date(d: str) -> str:
    """YYYYMMDD -> YYYY-MM-DD"""
    if d and len(d) == 8:
        return f"{d[:4]}-{d[4:6]}-{d[6:8]}"
    return d or ""


# ---------------------------------------------------------------------------
# Main handler
# ---------------------------------------------------------------------------

MAX_PREVIEW = 9


def handle_cam_photo_search(
    request: dict,
    context: dict = None
) -> dict:
    """
    Search NGM Cam photos by project + optional milestone + optional date range.

    Entities:
        project   - project name (required, triggers ask_project if missing)
        milestone - construction phase filter (optional, fuzzy-matched)
        date_from - YYYYMMDD lower bound (optional)
        date_to   - YYYYMMDD upper bound (optional)
    """
    from api.supabase_client import supabase, SUPABASE_URL
    from .bva_handler import resolve_project, fetch_recent_projects

    entities = request.get("entities", {})
    ctx = context or {}
    project_input = entities.get("project") or ""
    milestone_filter = entities.get("milestone") or ""
    date_from = entities.get("date_from") or ""
    date_to = entities.get("date_to") or ""

    # ----- No project: ask_project flow -----
    if not project_input or project_input.lower() in (
        "default", "general", "random", "none", "ngm hub web"
    ):
        recent = fetch_recent_projects(limit=8)
        data = {}
        if recent:
            data["projects"] = [
                {"id": p.get("project_id"), "name": p.get("project_name")}
                for p in recent
            ]
        data["command"] = "cam"
        return {
            "text": "Which project do you want to see photos from?",
            "action": "ask_project",
            "data": data,
        }

    # ----- Resolve project -----
    try:
        project = resolve_project(project_input)
    except Exception as e:
        logger.error("[Cam Handler] resolve_project error: %s", e)
        project = None

    if not project:
        return {
            "text": f"I could not find a project matching '{project_input}'.",
        }

    project_id = project["project_id"]
    project_name = project.get("project_name", project_input)

    # ----- Find Photos folder -----
    photos_folder_id = _find_photos_folder(supabase, project_id)
    if not photos_folder_id:
        return {
            "text": f"{project_name} does not have any photos yet. "
                    "Upload photos via NGM Cam to get started.",
        }

    # ----- List files -----
    try:
        result = (
            supabase.table("vault_files")
            .select("id, name, bucket_path, size_bytes, created_at")
            .eq("parent_id", photos_folder_id)
            .eq("is_deleted", False)
            .eq("is_folder", False)
            .order("name", desc=True)
            .limit(500)
            .execute()
        )
        all_files = result.data or []
    except Exception as e:
        logger.error("[Cam Handler] Error listing photos: %s", e)
        return {
            "text": "I had trouble fetching photos. Please try again.",
            "error": str(e),
        }

    # ----- No photos at all -----
    ngmcam_files = [f for f in all_files if f.get("bucket_path") and _parse_ngmcam_filename(f["name"])["milestone"]]

    if not ngmcam_files:
        return {
            "text": f"{project_name} does not have any photos yet. "
                    "Upload photos via NGM Cam to get started.",
        }

    # ----- Extract available milestones -----
    available_milestones = _extract_milestones(ngmcam_files)

    # ----- Resolve milestone filter (fuzzy) -----
    resolved_milestone = None  # the actual milestone name to filter by

    if milestone_filter:
        match_result = _best_milestone_match(milestone_filter, available_milestones)

        if not match_result:
            # No match at all -> show available milestones
            ms_list = ", ".join(available_milestones[:10])
            return {
                "text": f"No milestone matching '{milestone_filter}' found for {project_name}.\n\n"
                        f"Available milestones: {ms_list}",
                "action": "cam_ask_milestone",
                "data": {
                    "milestones": available_milestones[:10],
                    "project_name": project_name,
                    "project_id": str(project_id),
                },
            }

        if match_result["score"] < FUZZY_THRESHOLD:
            # Score too low -> show available milestones
            ms_list = ", ".join(available_milestones[:10])
            return {
                "text": f"No milestone matching '{milestone_filter}' found for {project_name}.\n\n"
                        f"Available milestones: {ms_list}",
                "action": "cam_ask_milestone",
                "data": {
                    "milestones": available_milestones[:10],
                    "project_name": project_name,
                    "project_id": str(project_id),
                },
            }

        resolved_milestone = match_result["name"]
        logger.info(
            "[Cam Handler] Milestone '%s' -> '%s' (score=%.2f)",
            milestone_filter, resolved_milestone, match_result["score"]
        )

    # ----- Filter photos -----
    matched: List[dict] = []
    resolved_lower = resolved_milestone.lower() if resolved_milestone else ""

    for f in ngmcam_files:
        parsed = _parse_ngmcam_filename(f["name"])

        # Milestone filter (exact match against resolved name)
        if resolved_lower and parsed["milestone"].lower() != resolved_lower:
            continue

        # Date range filters
        if date_from and parsed["date"] < date_from:
            continue
        if date_to and parsed["date"] > date_to:
            continue

        thumb_url = (
            f"{SUPABASE_URL}/storage/v1/object/public/vault/"
            f"{f['bucket_path']}?width=200&height=200&resize=cover"
        )
        full_url = (
            f"{SUPABASE_URL}/storage/v1/object/public/vault/{f['bucket_path']}"
        )

        matched.append({
            "id": f["id"],
            "name": f["name"],
            "thumbnail_url": thumb_url,
            "full_url": full_url,
            "milestone": parsed["milestone"],
            "date": parsed["date"],
            "time": parsed["time"],
        })

    # ----- No results after filtering -----
    total = len(matched)
    preview = matched[:MAX_PREVIEW]

    if total == 0:
        parts = [f"No photos found for {project_name}"]
        if resolved_milestone:
            parts.append(f"with milestone '{resolved_milestone}'")
        if date_from or date_to:
            dr = ""
            if date_from:
                dr += f"from {_format_date(date_from)}"
            if date_to:
                dr += f" to {_format_date(date_to)}" if date_from else f"up to {_format_date(date_to)}"
            parts.append(dr.strip())

        # Add available milestones as help
        if available_milestones:
            ms_list = ", ".join(available_milestones[:10])
            parts.append(f"\n\nAvailable milestones: {ms_list}")

        return {
            "text": " ".join(parts),
            "action": "cam_ask_milestone",
            "data": {
                "milestones": available_milestones[:10],
                "project_name": project_name,
                "project_id": str(project_id),
            },
        }

    # ----- Success response -----
    text = f"Found {total} photo(s) for {project_name}"
    filter_parts = []
    if resolved_milestone:
        filter_parts.append(f"milestone '{resolved_milestone}'")
    if date_from:
        filter_parts.append(f"from {_format_date(date_from)}")
    if date_to:
        filter_parts.append(f"to {_format_date(date_to)}")
    if filter_parts:
        text += f" ({', '.join(filter_parts)})"
    text += "."

    return {
        "text": text,
        "action": "cam_photo_results",
        "data": {
            "photos": preview,
            "total_count": total,
            "showing": len(preview),
            "project_name": project_name,
            "project_id": str(project_id),
            "available_milestones": available_milestones,
            "filters": {
                "milestone": resolved_milestone or None,
                "date_from": date_from or None,
                "date_to": date_to or None,
            },
        },
    }
