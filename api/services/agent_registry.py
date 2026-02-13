# api/services/agent_registry.py
# ================================
# Agent Function Registry
# ================================
# Defines the capability menu for each agent. The brain uses this
# to decide which function to call (or whether to free-chat).

from typing import Dict, List, Any

# ---------------------------------------------------------------------------
# Function definitions
# ---------------------------------------------------------------------------
# Each function has:
#   name         - unique key (snake_case)
#   description  - natural-language description for the LLM router
#   parameters   - list of {name, type, required, description}
#   handler      - dotted import path to the callable
#   long_running - if True, brain posts an ack before executing
# ---------------------------------------------------------------------------

ANDREW_FUNCTIONS: List[Dict[str, Any]] = [
    {
        "name": "process_receipt",
        "description": (
            "Process a receipt or invoice file that the user attached to their message. "
            "Runs OCR extraction, categorization, and creates a pending receipt record. "
            "USE THIS when the user attaches a file (PDF, image) and asks you to process it."
        ),
        "parameters": [
            {"name": "project_id", "type": "string", "required": False,
             "description": "Project ID. Defaults to current channel project."},
        ],
        "handler": "_builtin:process_receipt",
        "long_running": True,
        "requires_attachments": True,
    },
    {
        "name": "reconcile_bill",
        "description": (
            "Run mismatch reconciliation on a bill. Compares the receipt/invoice "
            "amounts against the expenses logged in the system and reports discrepancies."
        ),
        "parameters": [
            {"name": "bill_id", "type": "string", "required": True,
             "description": "Bill ID or partial reference (e.g. 'bill 1456')"},
        ],
        "handler": "api.services.andrew_mismatch_protocol.run_mismatch_reconciliation",
        "long_running": True,
    },
    {
        "name": "check_receipt_status",
        "description": (
            "Look up the current status of a pending receipt. "
            "Shows flow state, missing fields, and how long it has been pending."
        ),
        "parameters": [
            {"name": "receipt_id", "type": "string", "required": False,
             "description": "Receipt or pending_receipt ID. If omitted, shows all pending."},
        ],
        "handler": "_builtin:check_receipt_status",
        "long_running": False,
    },
    {
        "name": "check_pending_followups",
        "description": (
            "Find receipts that are overdue for follow-up. "
            "Shows which receipts need attention, escalation, or have gone stale."
        ),
        "parameters": [],
        "handler": "api.services.andrew_smart_layer.check_pending_followups",
        "long_running": False,
    },
    {
        "name": "explain_categorization",
        "description": (
            "Explain why an expense was assigned to a specific account/category. "
            "Describes the logic and confidence level behind the categorization."
        ),
        "parameters": [
            {"name": "expense_id", "type": "string", "required": True,
             "description": "The expense ID to explain"},
        ],
        "handler": "_builtin:explain_categorization",
        "long_running": False,
    },
    {
        "name": "edit_bill_categories",
        "description": (
            "Review and modify expense categories for items in an existing bill. "
            "Shows an interactive card with dropdowns to change account assignments. "
            "USE THIS when the user wants to review, change, or fix categories for materials/items in a bill."
        ),
        "parameters": [
            {"name": "bill_identifier", "type": "string", "required": True,
             "description": "Bill number, ID, or reference (e.g., 'Bill 1234', 'bill #45', or receipt hash)"},
            {"name": "material_name", "type": "string", "required": False,
             "description": "Optional - specific material/item name to focus on"},
        ],
        "handler": "api.routers.pending_receipts:edit_bill_categories",
        "long_running": False,
    },
]

DANEEL_FUNCTIONS: List[Dict[str, Any]] = [
    {
        "name": "run_auto_auth",
        "description": (
            "Run the expense authorization engine for this project. "
            "Scans pending expenses, auto-authorizes safe ones, flags duplicates, "
            "and reports missing information."
        ),
        "parameters": [
            {"name": "project_id", "type": "string", "required": False,
             "description": "Project ID. Defaults to current channel project."},
        ],
        "handler": "api.services.daneel_auto_auth.run_auto_auth",
        "long_running": True,
    },
    {
        "name": "check_budget",
        "description": (
            "Generate a budget vs actuals report for the project. "
            "Shows spending by account, remaining budget, and over-budget alerts."
        ),
        "parameters": [
            {"name": "project_id", "type": "string", "required": False,
             "description": "Project ID. Defaults to current channel project."},
        ],
        "handler": "_builtin:check_budget",
        "long_running": True,
    },
    {
        "name": "check_duplicates",
        "description": (
            "Scan for duplicate expenses in the project. "
            "Uses amount, date, vendor, and receipt hash matching."
        ),
        "parameters": [
            {"name": "project_id", "type": "string", "required": False,
             "description": "Project ID. Defaults to current channel project."},
        ],
        "handler": "_builtin:check_duplicates",
        "long_running": True,
    },
    {
        "name": "expense_health_report",
        "description": (
            "Run a health check on the project expenses. "
            "Finds missing vendors, dates, receipts, categories, and other data gaps."
        ),
        "parameters": [
            {"name": "project_id", "type": "string", "required": False,
             "description": "Project ID. Defaults to current channel project."},
        ],
        "handler": "_builtin:expense_health_report",
        "long_running": True,
    },
    {
        "name": "reprocess_pending",
        "description": (
            "Re-check previously flagged expenses after data has been updated. "
            "Attempts to resolve missing fields and re-authorize if possible."
        ),
        "parameters": [
            {"name": "project_id", "type": "string", "required": False,
             "description": "Project ID. Defaults to current channel project."},
        ],
        "handler": "_builtin:reprocess_pending",
        "long_running": True,
    },
]

# ---------------------------------------------------------------------------
# Registry lookup
# ---------------------------------------------------------------------------

AGENT_REGISTRY: Dict[str, List[Dict[str, Any]]] = {
    "andrew": ANDREW_FUNCTIONS,
    "daneel": DANEEL_FUNCTIONS,
}


def get_functions(agent_name: str) -> List[Dict[str, Any]]:
    """Return the function menu for a given agent."""
    return AGENT_REGISTRY.get(agent_name.lower(), [])


def get_function(agent_name: str, function_name: str) -> Dict[str, Any] | None:
    """Return a specific function definition by name."""
    for fn in get_functions(agent_name):
        if fn["name"] == function_name:
            return fn
    return None


def format_functions_for_llm(agent_name: str) -> str:
    """
    Format the function menu as a readable block for the LLM routing prompt.
    """
    functions = get_functions(agent_name)
    if not functions:
        return "No functions available."

    lines = []
    for fn in functions:
        params_str = ""
        if fn["parameters"]:
            parts = []
            for p in fn["parameters"]:
                req = "required" if p.get("required") else "optional"
                parts.append(f"  - {p['name']} ({p['type']}, {req}): {p['description']}")
            params_str = "\n" + "\n".join(parts)
        else:
            params_str = "\n  (no parameters)"

        lines.append(f"- {fn['name']}: {fn['description']}{params_str}")

    return "\n".join(lines)


def format_capabilities_for_user(agent_name: str) -> str:
    """
    Format a user-facing capability summary for when the agent
    doesn't understand the request. Short, readable, no parameters.
    """
    functions = get_functions(agent_name)
    if not functions:
        return ""

    other = "Daneel" if agent_name.lower() == "andrew" else "Andrew"
    lines = [f"Here's what I can help with:"]
    for fn in functions:
        # First sentence of description only
        short = fn["description"].split(". ")[0].rstrip(".")
        lines.append(f"  - **{fn['name']}**: {short}")
    lines.append(f"\nFor anything outside this scope, try @{other}.")
    return "\n".join(lines)
