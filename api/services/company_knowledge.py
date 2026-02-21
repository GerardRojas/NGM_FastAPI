# api/services/company_knowledge.py
# ============================================================================
# Modular Company Knowledge System
# ============================================================================
# Provides contextual knowledge snippets for agent conversations.
# Each module is a lightweight function that returns a short string (<200 tokens)
# injected into the agent brain's routing prompt.
#
# Architecture:
#   - Each knowledge module is independent and self-contained
#   - build_knowledge_context() selects which modules to load per request
#   - Keeps prompt lean: only load what's relevant to the current interaction
#
# Modules:
#   1. user_profile       - Who is talking (name, role, seniority)
#   2. company_profile    - Company identity and standards
#   3. project_context    - Current project details and status
#   4. team_roster        - Key people on the current project
#   5. managed_companies  - Companies we manage + their active projects
# ============================================================================

import logging
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)

# ── Module cache (in-memory, TTL-based) ──────────────────────────────────────
_cache: Dict[str, Dict[str, Any]] = {}
_CACHE_TTL = 600  # 10 minutes
_CACHE_MAX = 100

import time


def _get_cached(key: str) -> Optional[str]:
    entry = _cache.get(key)
    if entry and (time.time() - entry["ts"]) < _CACHE_TTL:
        return entry["value"]
    return None


def _set_cached(key: str, value: str):
    if len(_cache) >= _CACHE_MAX:
        # Evict oldest half
        sorted_keys = sorted(_cache, key=lambda k: _cache[k]["ts"])
        for k in sorted_keys[:len(sorted_keys) // 2]:
            del _cache[k]
    _cache[key] = {"value": value, "ts": time.time()}


# ============================================================================
# MODULE 1: User Profile
# ============================================================================
# Always loaded. Tells the agent who they're talking to.
# ~50-80 tokens.

async def get_user_profile(user_id: str) -> str:
    """Fetch basic user info for the agent's context."""
    cache_key = f"user:{user_id}"
    cached = _get_cached(cache_key)
    if cached:
        return cached

    try:
        from api.supabase_client import supabase as sb
        result = sb.table("users") \
            .select("user_name, role, seniority") \
            .eq("user_id", user_id) \
            .single() \
            .execute()

        if result.data:
            name = result.data.get("user_name", "Unknown")
            role = result.data.get("role", "Team Member")
            seniority = result.data.get("seniority", "")
            snippet = (
                f"- User: {name}\n"
                f"- Role: {role}"
            )
            if seniority:
                snippet += f" ({seniority})"
            _set_cached(cache_key, snippet)
            return snippet
    except Exception as e:
        logger.debug("[Knowledge:user_profile] %s", e)

    return "- User: (unknown)"


# ============================================================================
# MODULE 2: Company Profile
# ============================================================================
# Loaded on first interaction or when agent needs company context.
# Static knowledge about the company. ~100-150 tokens.

COMPANY_PROFILE = """\
- Company: NGM Managements LLC
- Industry: General Contracting / Construction Management
- Based in: Southern California
- Specialties: Residential remodeling, ADU construction, commercial tenant improvement
- Business model: NGM manages multiple client companies, each with its own projects
- Accounting: QuickBooks-integrated, accrual basis
- Currency: USD
- Fiscal year: Calendar year (Jan-Dec)"""


async def get_company_profile() -> str:
    """Return static company profile."""
    return COMPANY_PROFILE


# ============================================================================
# MODULE 3: Project Context
# ============================================================================
# Loaded when project_id is present. Gives the agent project-specific info.
# ~80-120 tokens.

async def get_project_context(project_id: str) -> str:
    """Fetch current project details for context."""
    cache_key = f"project:{project_id}"
    cached = _get_cached(cache_key)
    if cached:
        return cached

    try:
        from api.supabase_client import supabase as sb
        result = sb.table("projects") \
            .select("project_name, client_name, city, state, status") \
            .eq("project_id", project_id) \
            .single() \
            .execute()

        if result.data:
            d = result.data
            snippet = (
                f"- Project: {d.get('project_name', '?')}\n"
                f"- Client: {d.get('client_name', '?')}\n"
                f"- Location: {d.get('city', '?')}, {d.get('state', '?')}\n"
                f"- Status: {d.get('status', '?')}"
            )
            _set_cached(cache_key, snippet)
            return snippet
    except Exception as e:
        logger.debug("[Knowledge:project_context] %s", e)

    return f"- Project ID: {project_id}"


# ============================================================================
# MODULE 4: Team Roster
# ============================================================================
# Loaded when agent needs to know who's on the project.
# ~60-100 tokens.

async def get_team_roster(project_id: str) -> str:
    """Fetch key team members for the current project."""
    cache_key = f"team:{project_id}"
    cached = _get_cached(cache_key)
    if cached:
        return cached

    try:
        from api.supabase_client import supabase as sb
        result = sb.table("project_members") \
            .select("user_id, role") \
            .eq("project_id", project_id) \
            .limit(10) \
            .execute()

        if not result.data:
            return "- Team: (no members assigned)"

        # Resolve names
        members = []
        for m in result.data:
            uid = m.get("user_id", "")
            role = m.get("role", "Member")
            try:
                u = sb.table("users") \
                    .select("user_name") \
                    .eq("user_id", uid) \
                    .single() \
                    .execute()
                name = u.data.get("user_name", "?") if u.data else "?"
            except Exception:
                name = "?"
            members.append(f"{name} ({role})")

        snippet = "- Team: " + ", ".join(members[:6])
        if len(members) > 6:
            snippet += f" +{len(members) - 6} more"
        _set_cached(cache_key, snippet)
        return snippet
    except Exception as e:
        logger.debug("[Knowledge:team_roster] %s", e)

    return "- Team: (unavailable)"


# ============================================================================
# MODULE 5: Managed Companies + Projects
# ============================================================================
# Loaded for both agents. Gives the agent a map of which companies
# NGM manages and which projects belong to each.
# ~100-200 tokens (compact list format).

async def get_managed_companies() -> str:
    """Fetch companies and their active projects as a compact map."""
    cache_key = "managed_companies"
    cached = _get_cached(cache_key)
    if cached:
        return cached

    try:
        from api.supabase_client import supabase as sb

        # Fetch active companies
        comp_result = sb.table("companies") \
            .select("id, name") \
            .eq("status", "Active") \
            .order("name") \
            .limit(20) \
            .execute()

        companies = comp_result.data or []
        if not companies:
            return "- Managed companies: (none)"

        # Fetch all projects in one query (avoid N+1)
        comp_ids = [c["id"] for c in companies]
        proj_result = sb.table("projects") \
            .select("project_name, source_company, status") \
            .in_("source_company", comp_ids) \
            .order("project_name") \
            .limit(100) \
            .execute()

        # Group projects by company
        projects_by_company: Dict[str, List[str]] = {}
        for p in (proj_result.data or []):
            cid = p.get("source_company", "")
            name = p.get("project_name", "?")
            status = p.get("status", "")
            label = f"{name} [{status}]" if status else name
            projects_by_company.setdefault(cid, []).append(label)

        # Build compact output
        lines = ["- Managed companies:"]
        for c in companies:
            cid = c["id"]
            cname = c.get("name", "?")
            projs = projects_by_company.get(cid, [])
            if projs:
                proj_list = ", ".join(projs[:8])
                if len(projs) > 8:
                    proj_list += f" +{len(projs) - 8} more"
                lines.append(f"  - {cname}: {proj_list}")
            else:
                lines.append(f"  - {cname}: (no projects)")

        snippet = "\n".join(lines)
        _set_cached(cache_key, snippet)
        return snippet
    except Exception as e:
        logger.debug("[Knowledge:managed_companies] %s", e)

    return "- Managed companies: (unavailable)"


# ============================================================================
# Knowledge Context Builder
# ============================================================================
# Selects which modules to load based on agent and context.
# Returns a formatted string ready to inject into the routing prompt.

# Module selection matrix:
#   user_profile:       ALWAYS (lightweight, essential for personalization)
#   company_profile:    ALWAYS (static, cached permanently)
#   project_context:    when project_id present
#   team_roster:        only for Daneel (authorization context)
#   managed_companies:  ALWAYS (gives agent the company/project map)

AGENT_MODULES = {
    "andrew": ["user_profile", "company_profile", "managed_companies", "project_context"],
    "daneel": ["user_profile", "company_profile", "managed_companies", "project_context", "team_roster"],
}

MODULE_FETCHERS = {
    "user_profile":       lambda ctx: get_user_profile(ctx["user_id"]),
    "company_profile":    lambda ctx: get_company_profile(),
    "managed_companies":  lambda ctx: get_managed_companies(),
    "project_context":    lambda ctx: get_project_context(ctx["project_id"]) if ctx.get("project_id") else None,
    "team_roster":        lambda ctx: get_team_roster(ctx["project_id"]) if ctx.get("project_id") else None,
}


def _detect_language(text: str) -> str:
    """Detect user language from message text. Fast heuristic, no ML needed."""
    if not text:
        return "en"
    t = text.lower()
    # Spanish indicators (common words/patterns)
    es_signals = ["hola", "por favor", "gracias", "como", "qué", "que ",
                  "cuánto", "cuanto", "necesito", "puedes", "dime", "tengo",
                  "está", "esta ", "donde", "quiero", "puede", "cuál",
                  "cuantos", "revisar", "mira", "oye", "buenas"]
    es_count = sum(1 for w in es_signals if w in t)
    if es_count >= 2 or (es_count == 1 and len(t.split()) <= 6):
        return "es"
    return "en"


async def build_knowledge_context(
    agent_name: str,
    user_id: str,
    project_id: Optional[str] = None,
    user_text: str = "",
) -> str:
    """
    Build a knowledge context string for the agent brain prompt.
    Selects modules based on agent type and available context.
    Detects user language and instructs agent to respond accordingly.

    Returns a formatted multi-line string (~200-500 tokens max).
    """
    modules = AGENT_MODULES.get(agent_name, ["user_profile", "company_profile"])
    ctx = {"user_id": user_id, "project_id": project_id}

    sections = []
    for mod_name in modules:
        fetcher = MODULE_FETCHERS.get(mod_name)
        if not fetcher:
            continue
        try:
            result = await fetcher(ctx)
            if result:
                sections.append(result)
        except Exception as e:
            logger.debug("[Knowledge] Module %s failed: %s", mod_name, e)

    if not sections:
        return ""

    # Language instruction
    lang = _detect_language(user_text)
    if lang == "es":
        sections.append("- Language: Respond in Spanish (the user wrote in Spanish)")
    else:
        sections.append("- Language: Respond in English")

    return "## Knowledge context\n" + "\n".join(sections)


def clear_knowledge_cache():
    """Clear the knowledge cache (called by memory management loop)."""
    _cache.clear()
