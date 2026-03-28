# api/services/categorization_rules.py
# ============================================================================
# Rule-based Categorization Engine - Keyword + Taxonomy matching
# ============================================================================
# Deterministic categorization using regex special rules and a construction
# taxonomy mapped to dynamic company accounts. No LLM calls.
#
# Usage:
#   from api.services.categorization_rules import get_rule_engine
#
#   engine = get_rule_engine()
#   engine.load_accounts(accounts_list)
#   result = engine.categorize("Wood Stud 2x4x8", stage="Framing")
# ============================================================================

import hashlib
import logging
import re
import time
from typing import Optional

logger = logging.getLogger(__name__)


# ── Special Rules (extracted from GPT prompt) ───────────────────
# These are deterministic pattern matches that don't need LLM reasoning.
# Order matters: first match wins.

SPECIAL_RULES = [
    {
        "name": "power_tools",
        "patterns": [
            r"\b(?:cordless|power)\s*(?:drill|saw|grinder|sander|nailer|compressor)\b",
            r"\b(?:drill|saw|grinder|sander|nailer)\s*(?:kit|set|combo)\b",
            r"\bangle\s*grinder\b",
            r"\bcircular\s*saw\b",
            r"\bjigsaw\b",
            r"\bmiter\s*saw\b",
            r"\btable\s*saw\b",
            r"\bband\s*saw\b",
            r"\breciprocating\s*saw\b",
            r"\brotary\s*hammer\b",
            r"\bimpact\s*driver\b",
            r"\bheat\s*gun\b",
        ],
        "action": "reject",
        "confidence": 0,
        "warning": "WARNING: Power tool - not a COGS expense",
        # Consumables FOR tools are valid COGS -- exclude them
        "exclude_patterns": [
            r"\b(?:bit|blade|nail|sandpaper|abrasive|disc|pad|brush|filter|bag)\s",
            r"\b(?:bit|blade|nail|sandpaper|abrasive|disc|pad|brush|filter|bag)$",
        ],
    },
    {
        "name": "delivery_freight",
        "patterns": [
            r"\b(?:outside\s+)?deliver(?:y|ies)\b",
            r"\bfreight\b",
            r"\bshipping(?:\s+(?:&|and)\s+handling)?\b",
            r"\bhauling\b",
            r"\btransportation\b",
        ],
        "action": "match_keyword",
        "account_hints": ["freight", "delivery", "shipping", "transportation"],
        "confidence": 95,
    },
    {
        "name": "fees_surcharges",
        "patterns": [
            r"\b(?:lumber|env(?:ironmental)?|fuel|processing|handling|service)\s*fee\b",
            r"\bsurcharge\b",
            r"\b(?:ca|california)\s+lumber\s+fee\b",
            r"\brestocking\s*fee\b",
        ],
        "action": "match_keyword",
        "account_hints": ["base materials", "materials"],
        "confidence": 90,
        # Delivery/freight fees should go to freight account, not here
        "exclude_patterns": [r"\b(?:delivery|freight|shipping)\b"],
    },
    {
        "name": "beverages",
        "patterns": [
            r"\b(?:water|gatorade|powerade|energy\s+drink|coffee|soda|drink)\s",
            r"\b(?:water|gatorade|powerade|energy\s+drink|coffee|soda|drink)$",
            r"\bbottle[ds]?\s+water\b",
        ],
        "action": "match_keyword",
        "account_hints": ["base materials", "materials", "supplies"],
        "confidence": 85,
    },
]


# ── Construction Taxonomy ────────────────────────────────────────
# Maps material categories to their keyword synonyms and account hints.
# account_hints are matched against company account names at runtime.

CONSTRUCTION_TAXONOMY = {
    "lumber": {
        "keywords": [
            "wood", "stud", "plywood", "osb", "timber", "beam", "joist",
            "rafter", "board", "sheathing", "truss", "furring", "sill",
            "header", "cripple", "blocking", "lvl", "glulam", "mdf",
            "particle board", "cedar", "pine", "oak", "maple", "poplar",
            "treated lumber", "pressure treated", "pt lumber",
        ],
        "dimension_patterns": [r"\b\d+x\d+(?:x\d+)?\b"],
        "account_hints": ["lumber", "wood", "framing", "materials"],
    },
    "electrical": {
        "keywords": [
            "wire", "outlet", "switch", "breaker", "conduit", "romex",
            "junction", "receptacle", "gfci", "afci", "panel", "meter",
            "gauge", "nm-b", "thhn", "emt", "electrical box", "wire nut",
            "circuit", "ampere", "amp", "volt", "watt", "led", "bulb",
            "light fixture", "recessed light", "can light",
        ],
        "account_hints": ["electrical", "wiring", "electric"],
    },
    "plumbing": {
        "keywords": [
            "pipe", "pvc", "cpvc", "copper", "fitting", "valve", "drain",
            "faucet", "coupling", "elbow", "tee", "adapter", "trap",
            "cleanout", "vent", "pex", "shark bite", "abs", "toilet",
            "sink", "shower", "bathtub", "water heater", "supply line",
            "shutoff", "ball valve", "gate valve", "hose bib",
        ],
        "account_hints": ["plumbing", "piping", "plumb"],
    },
    "concrete": {
        "keywords": [
            "cement", "rebar", "concrete", "aggregate", "mortar",
            "masonry", "cinder", "block", "grout", "form", "fiber mesh",
            "ready mix", "quickrete", "quikrete", "sakrete",
            "concrete mix", "anchor bolt", "j-bolt", "footing",
        ],
        "account_hints": ["concrete", "masonry", "cement", "foundation"],
    },
    "roofing": {
        "keywords": [
            "shingle", "felt", "flashing", "gutter", "ridge", "soffit",
            "fascia", "drip edge", "underlayment", "ice guard", "tar",
            "roof cement", "ridge vent", "roof vent", "starter strip",
            "hip cap", "valley", "eave",
        ],
        "account_hints": ["roofing", "roof"],
    },
    "drywall": {
        "keywords": [
            "drywall", "sheetrock", "gypsum", "joint compound", "mud",
            "corner bead", "wallboard", "greenboard", "purpleboard",
            "drywall screw", "drywall tape", "texture", "skim coat",
            "spackle", "setting compound",
        ],
        "account_hints": ["drywall", "gypsum", "wall"],
    },
    "paint": {
        "keywords": [
            "paint", "primer", "stain", "sealer", "lacquer", "varnish",
            "roller", "tray", "masking", "drop cloth", "painter",
            "brush", "spray paint", "wood stain", "polyurethane",
            "semi-gloss", "satin", "flat", "eggshell", "enamel",
        ],
        "account_hints": ["paint", "finish", "coating"],
    },
    "insulation": {
        "keywords": [
            "insulation", "fiberglass", "foam", "batt", "r-13", "r-19",
            "r-30", "r-38", "blown", "cellulose", "rigid", "eps", "xps",
            "spray foam", "foam board", "house wrap", "tyvek", "vapor barrier",
        ],
        "account_hints": ["insulation"],
    },
    "flooring": {
        "keywords": [
            "tile", "hardwood", "vinyl", "laminate", "carpet", "grout",
            "thinset", "baseboard", "transition", "lvp", "lvt",
            "ceramic", "porcelain", "marble", "travertine", "slate",
            "floor", "subfloor", "quarter round", "shoe molding",
        ],
        "account_hints": ["flooring", "floor", "tile"],
    },
    "hardware": {
        "keywords": [
            "screw", "nail", "bolt", "nut", "washer", "anchor",
            "bracket", "hinge", "fastener", "latch", "strike", "knob",
            "handle", "hook", "clip", "staple", "tie", "strap",
            "simpson", "joist hanger", "hurricane tie", "l-bracket",
            "lag bolt", "carriage bolt", "machine screw", "deck screw",
            "wood screw", "drywall anchor", "toggle bolt",
        ],
        "account_hints": ["hardware", "fastener", "supplies"],
    },
    "doors_windows": {
        "keywords": [
            "door", "window", "frame", "jamb", "threshold", "weatherstrip",
            "lockset", "deadbolt", "screen", "glass", "pane", "sash",
            "sliding door", "french door", "pocket door", "barn door",
            "prehung", "bifold", "louver", "shutter",
        ],
        "account_hints": ["door", "window", "opening"],
    },
    "hvac": {
        "keywords": [
            "duct", "hvac", "furnace", "thermostat", "register", "vent",
            "damper", "filter", "refrigerant", "condenser", "handler",
            "ac unit", "air conditioning", "heating", "mini split",
            "ductwork", "flex duct", "return air", "supply air",
        ],
        "account_hints": ["hvac", "heating", "cooling", "mechanical"],
    },
    "tools_consumable": {
        "keywords": [
            "bit", "blade", "sandpaper", "abrasive", "disc", "pad",
            "tape measure", "chalk", "marker", "pencil", "level",
            "utility knife", "razor", "chisel", "putty knife",
            "wire stripper", "pliers", "wrench", "socket",
            "caulk gun", "staple gun", "rivet",
        ],
        "account_hints": ["tools", "supplies", "consumable", "base materials"],
    },
    "safety": {
        "keywords": [
            "glove", "goggle", "helmet", "vest", "harness", "respirator",
            "ear plug", "face shield", "safety glass", "hard hat",
            "first aid", "fire extinguisher", "caution tape",
            "safety cone", "hi-vis", "high visibility",
        ],
        "account_hints": ["safety", "ppe", "protection", "supplies"],
    },
    "adhesives_sealants": {
        "keywords": [
            "glue", "adhesive", "epoxy", "silicone", "caulk", "sealant",
            "construction adhesive", "liquid nail", "pl premium",
            "gorilla", "super glue", "wood glue", "contact cement",
            "foam sealant", "great stuff",
        ],
        "account_hints": ["adhesive", "sealant", "supplies", "base materials"],
    },
    "cabinets_countertops": {
        "keywords": [
            "cabinet", "countertop", "vanity", "drawer", "shelf",
            "pantry", "lazy susan", "pull out", "knob", "pull",
            "granite", "quartz", "formica", "laminate countertop",
            "butcher block", "backsplash",
        ],
        "account_hints": ["cabinet", "countertop", "kitchen", "millwork"],
    },
    "landscaping": {
        "keywords": [
            "mulch", "soil", "topsoil", "sod", "grass", "seed",
            "fertilizer", "gravel", "paver", "retaining wall",
            "landscape fabric", "edging", "border", "stone",
            "river rock", "decomposed granite",
        ],
        "account_hints": ["landscaping", "landscape", "exterior", "site"],
    },
}

# Pre-compile all regex patterns for performance
_COMPILED_SPECIAL_RULES = []
for rule in SPECIAL_RULES:
    compiled = {
        "name": rule["name"],
        "action": rule["action"],
        "confidence": rule["confidence"],
        "warning": rule.get("warning"),
        "account_hints": rule.get("account_hints", []),
        "patterns": [re.compile(p, re.IGNORECASE) for p in rule["patterns"]],
        "exclude_patterns": [re.compile(p, re.IGNORECASE) for p in rule.get("exclude_patterns", [])],
    }
    _COMPILED_SPECIAL_RULES.append(compiled)

_COMPILED_TAXONOMY = {}
for cat_name, cat_data in CONSTRUCTION_TAXONOMY.items():
    _COMPILED_TAXONOMY[cat_name] = {
        "keywords": set(kw.lower() for kw in cat_data["keywords"]),
        "dimension_patterns": [re.compile(p, re.IGNORECASE) for p in cat_data.get("dimension_patterns", [])],
        "account_hints": cat_data["account_hints"],
    }


# ── Text Preprocessing ──────────────────────────────────────────

def _preprocess(text: str) -> str:
    """Normalize description for matching. Same logic as ML preprocessor."""
    if not text or not isinstance(text, str):
        return ""
    t = text.lower().strip()
    t = re.sub(r"[^a-z0-9\s\-/]", " ", t)
    t = re.sub(r"(\d)([a-z])", r"\1 \2", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


# ── Account Resolver ─────────────────────────────────────────────

class AccountResolver:
    """Maps taxonomy categories to company accounts by parsing account names.

    The resolver never stores account UUIDs in the taxonomy. Instead, it
    matches taxonomy account_hints against the company's actual account
    names at runtime. If a company renames "Lumber & Materials" to
    "Wood Products", the hint "wood" still matches.
    """

    def __init__(self):
        self._category_map: dict = {}      # taxonomy_category -> account dict
        self._keyword_map: dict = {}       # hint keyword -> account dict
        self._accounts_hash: Optional[str] = None
        self._accounts_list: list = []

    def build(self, accounts: list):
        """Parse account names and build category + keyword maps.

        Args:
            accounts: List of {"account_id": str, "name": str, "number": int|None}
        """
        new_hash = hashlib.md5(
            str(sorted(a.get("account_id", "") for a in accounts)).encode()
        ).hexdigest()

        if new_hash == self._accounts_hash:
            return  # accounts unchanged

        self._accounts_list = accounts
        self._accounts_hash = new_hash
        self._category_map.clear()
        self._keyword_map.clear()

        # For each taxonomy category, find the best matching account
        for cat_name, cat_data in _COMPILED_TAXONOMY.items():
            best_account = None
            best_score = 0

            for acc in accounts:
                acc_name_lower = acc.get("name", "").lower()
                acc_tokens = set(re.split(r"[\s&\-/,]+", acc_name_lower))
                score = 0

                for hint in cat_data["account_hints"]:
                    hint_lower = hint.lower()
                    # Exact token match
                    if hint_lower in acc_tokens:
                        score += 10
                    # Substring match (e.g., "lumber" in "lumber & materials")
                    elif hint_lower in acc_name_lower:
                        score += 7
                    # Partial token match (e.g., "electric" matches "electrical")
                    else:
                        for token in acc_tokens:
                            if token.startswith(hint_lower) or hint_lower.startswith(token):
                                score += 4
                                break

                if score > best_score:
                    best_score = score
                    best_account = acc

            if best_account and best_score >= 4:
                self._category_map[cat_name] = best_account

        # Build keyword map for special rules (account_hints → account)
        for acc in accounts:
            acc_name_lower = acc.get("name", "").lower()
            acc_tokens = set(re.split(r"[\s&\-/,]+", acc_name_lower))
            for token in acc_tokens:
                if token and len(token) > 2:
                    self._keyword_map[token] = acc

        logger.info(
            f"[RuleEngine] Account resolver built: "
            f"{len(self._category_map)}/{len(_COMPILED_TAXONOMY)} categories mapped, "
            f"{len(self._keyword_map)} keyword entries"
        )

    def resolve_category(self, taxonomy_category: str) -> Optional[dict]:
        """Get the account for a taxonomy category."""
        return self._category_map.get(taxonomy_category)

    def find_by_hints(self, hints: list) -> Optional[dict]:
        """Find best account matching any of the hint keywords.

        Tries exact token match first, then substring match.
        Used by special rules (delivery → freight account).
        """
        # Pass 1: exact token match in keyword map
        for hint in hints:
            hint_lower = hint.lower()
            if hint_lower in self._keyword_map:
                return self._keyword_map[hint_lower]

        # Pass 2: substring match against all account names
        for hint in hints:
            hint_lower = hint.lower()
            for acc in self._accounts_list:
                if hint_lower in acc.get("name", "").lower():
                    return acc

        return None


# ── Rule Engine Service ──────────────────────────────────────────

class RuleEngineService:
    """Deterministic categorization via regex rules + construction taxonomy.

    Three-phase matching:
      Phase 1: Special rules (power tools, delivery, fees, beverages)
      Phase 2: Taxonomy keyword matching against description
      Phase 3: Stage-aware disambiguation when multiple categories match
    """

    def __init__(self):
        self._resolver = AccountResolver()
        self._loaded = False

    def load_accounts(self, accounts: list):
        """Load/refresh company accounts for resolution.

        Args:
            accounts: List of {"account_id": str, "name": str, "number": int|None}
                      (Labor accounts should already be filtered out by caller)
        """
        self._resolver.build(accounts)
        self._loaded = True

    @property
    def is_loaded(self) -> bool:
        return self._loaded

    def categorize(self, description: str, stage: str = "") -> Optional[dict]:
        """Categorize a single expense description.

        Returns:
            dict with account_id, account_name, confidence, reasoning, warning
            or None if no confident match (caller should fall through to GPT).
        """
        if not self._loaded:
            logger.warning("[RuleEngine] Accounts not loaded, skipping")
            return None

        text = _preprocess(description)
        if not text:
            return None

        # ── Phase 1: Special Rules ──
        result = self._check_special_rules(text, description)
        if result is not None:
            return result

        # ── Phase 2: Taxonomy Matching ──
        matches = self._match_taxonomy(text)
        if not matches:
            return None

        # ── Phase 3: Disambiguation + Scoring ──
        return self._resolve_best_match(matches, text, stage)

    def categorize_batch(self, expenses: list, stage: str = "") -> list:
        """Categorize a batch of expenses.

        Args:
            expenses: List of {"rowIndex": int, "description": str}
            stage: Construction stage

        Returns:
            List of (expense, result_or_None) tuples
        """
        results = []
        for exp in expenses:
            result = self.categorize(exp.get("description", ""), stage)
            results.append((exp, result))
        return results

    # ── Internal Methods ─────────────────────────────────────────

    def _check_special_rules(self, text: str, original: str) -> Optional[dict]:
        """Phase 1: Check against special rules (power tools, delivery, etc.)."""
        for rule in _COMPILED_SPECIAL_RULES:
            # Check if any pattern matches
            matched = False
            for pattern in rule["patterns"]:
                if pattern.search(text):
                    matched = True
                    break

            if not matched:
                continue

            # Check exclude patterns (e.g., "drill bits" not a power tool)
            excluded = False
            for exc_pattern in rule["exclude_patterns"]:
                if exc_pattern.search(text):
                    excluded = True
                    break

            if excluded:
                continue

            # Rule matched
            if rule["action"] == "reject":
                return {
                    "account_id": None,
                    "account_name": None,
                    "confidence": rule["confidence"],
                    "reasoning": f"Rule: {rule['name']} - {rule.get('warning', '')}",
                    "warning": rule.get("warning"),
                }

            if rule["action"] == "match_keyword":
                account = self._resolver.find_by_hints(rule["account_hints"])
                if account:
                    return {
                        "account_id": account["account_id"],
                        "account_name": account["name"],
                        "confidence": rule["confidence"],
                        "reasoning": f"Rule: {rule['name']} -> {account['name']}",
                        "warning": None,
                    }
                # Hints didn't resolve to any account, fall through

        return None

    def _match_taxonomy(self, text: str) -> list:
        """Phase 2: Match description tokens against taxonomy keywords.

        Returns list of (category_name, hit_count, has_dimension) sorted by hits desc.
        """
        tokens = set(text.split())
        # Also build bigrams for multi-word keywords like "joint compound"
        words = text.split()
        bigrams = set()
        for i in range(len(words) - 1):
            bigrams.add(f"{words[i]} {words[i+1]}")

        all_tokens = tokens | bigrams
        matches = []

        for cat_name, cat_data in _COMPILED_TAXONOMY.items():
            hit_count = 0
            has_dimension = False

            # Check keyword hits
            for kw in cat_data["keywords"]:
                if " " in kw:
                    # Multi-word keyword: check bigrams
                    if kw in bigrams:
                        hit_count += 1
                else:
                    if kw in tokens:
                        hit_count += 1

            # Check dimension patterns (e.g., 2x4 → lumber)
            for dim_pattern in cat_data["dimension_patterns"]:
                if dim_pattern.search(text):
                    has_dimension = True
                    hit_count += 1
                    break

            if hit_count > 0:
                matches.append((cat_name, hit_count, has_dimension))

        # Sort by hit count descending
        matches.sort(key=lambda m: m[1], reverse=True)
        return matches

    def _resolve_best_match(self, matches: list, text: str, stage: str) -> Optional[dict]:
        """Phase 3: Pick best category, apply stage awareness, compute confidence.

        Args:
            matches: List of (category_name, hit_count, has_dimension) from Phase 2
            text: Preprocessed description
            stage: Construction stage (e.g., "Framing", "Roofing")
        """
        stage_lower = stage.lower().strip() if stage else ""

        if len(matches) == 1:
            # Single match: resolve directly
            cat_name, hits, has_dim = matches[0]
            account = self._resolver.resolve_category(cat_name)
            if not account:
                return None

            confidence = self._compute_confidence(hits, has_dim, stage_lower, account)
            if confidence < 80:
                return None

            return {
                "account_id": account["account_id"],
                "account_name": account["name"],
                "confidence": confidence,
                "reasoning": f"Keyword match: {cat_name} ({hits} hits) -> {account['name']}",
                "warning": None,
            }

        # Multiple matches: try stage disambiguation
        top_hit_count = matches[0][1]
        # Only consider matches within 1 hit of the top
        candidates = [m for m in matches if m[1] >= top_hit_count - 1]

        # Stage disambiguation: prefer category whose account name contains stage
        if stage_lower:
            for cat_name, hits, has_dim in candidates:
                account = self._resolver.resolve_category(cat_name)
                if not account:
                    continue
                acc_name_lower = account["name"].lower()
                # Check if stage keyword appears in account name
                stage_tokens = set(re.split(r"[\s&\-/,]+", stage_lower))
                if any(st in acc_name_lower for st in stage_tokens if len(st) > 3):
                    confidence = self._compute_confidence(hits, has_dim, stage_lower, account)
                    if confidence < 80:
                        continue
                    return {
                        "account_id": account["account_id"],
                        "account_name": account["name"],
                        "confidence": confidence,
                        "reasoning": f"Keyword match: {cat_name} ({hits} hits), stage-preferred -> {account['name']}",
                        "warning": None,
                    }

        # No stage disambiguation: use highest hit count if clear winner
        if matches[0][1] > matches[1][1]:
            cat_name, hits, has_dim = matches[0]
            account = self._resolver.resolve_category(cat_name)
            if account:
                confidence = self._compute_confidence(hits, has_dim, stage_lower, account)
                if confidence >= 80:
                    return {
                        "account_id": account["account_id"],
                        "account_name": account["name"],
                        "confidence": confidence,
                        "reasoning": f"Keyword match: {cat_name} ({hits} hits, best of {len(matches)}) -> {account['name']}",
                        "warning": None,
                    }

        # Tied or ambiguous: fall through to GPT
        return None

    def _compute_confidence(self, hits: int, has_dimension: bool,
                            stage_lower: str, account: dict) -> int:
        """Compute confidence score for a keyword match.

        Base:      75
        +5/hit:    max +15  (1 hit=80, 2=85, 3+=90)
        +3 dim:    dimension pattern matched (strong construction indicator)
        +5 stage:  account name contains stage keyword
        Cap:       93 (reserve 94-100 for cache/corrections/human)
        """
        score = 75
        score += min(hits * 5, 15)

        if has_dimension:
            score += 3

        if stage_lower and account:
            acc_lower = account.get("name", "").lower()
            stage_tokens = set(re.split(r"[\s&\-/,]+", stage_lower))
            if any(st in acc_lower for st in stage_tokens if len(st) > 3):
                score += 5

        return min(score, 93)


# ── Singleton ────────────────────────────────────────────────────

_instance: Optional[RuleEngineService] = None


def get_rule_engine() -> RuleEngineService:
    """Get or create the singleton rule engine instance."""
    global _instance
    if _instance is None:
        _instance = RuleEngineService()
    return _instance
