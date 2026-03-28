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
            "Directly edit expense account assignments for items in a bill using "
            "interactive dropdowns. Use ONLY when the user explicitly asks to change "
            "or reassign categories/accounts for specific items."
        ),
        "parameters": [
            {"name": "bill_identifier", "type": "string", "required": True,
             "description": "Bill number, ID, or reference (e.g., 'Bill 1234', 'bill #45', or receipt hash)"},
            {"name": "material_name", "type": "string", "required": False,
             "description": "Optional - specific material/item name to focus on"},
        ],
        "handler": "api.routers.pending_receipts.edit_bill_categories",
        "long_running": False,
    },
    {
        "name": "review_bill",
        "description": (
            "Look up and review a bill, invoice, or check by its number or reference. "
            "Shows all line items with amounts, accounts, authorization status, and confidence. "
            "USE THIS when the user mentions a bill number, check number, or invoice reference "
            "and wants to see, review, or discuss it. Examples: 'review bill H-349', "
            "'let me see check 342', 'pull up invoice 1234', 'revisar factura 349'."
        ),
        "parameters": [
            {"name": "bill_identifier", "type": "string", "required": True,
             "description": "Bill number, check number, invoice reference, or ID (e.g. 'H-349', '342', 'INV-1234')"},
        ],
        "handler": "_builtin:review_bill",
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

HARI_FUNCTIONS: List[Dict[str, Any]] = [
    {
        "name": "create_task",
        "description": (
            "Create a new task from a natural language instruction. "
            "Parses the instruction to extract the assignee, description, deadline, and location. "
            "USE THIS when the user asks you to assign work, schedule something, or put someone on a task. "
            "Examples: 'put Juan on the Oak Ave site tomorrow at 8am', "
            "'have Maria review the permits by Friday', 'assign the inspection to Carlos'."
        ),
        "parameters": [
            {"name": "assignee_name", "type": "string", "required": True,
             "description": "Name of the person to assign the task to"},
            {"name": "task_description", "type": "string", "required": True,
             "description": "What needs to be done (extracted from instruction)"},
            {"name": "deadline", "type": "string", "required": False,
             "description": "When it's due (date/time string, e.g. 'tomorrow 8am', 'March 29 3pm', 'Friday EOD')"},
            {"name": "location", "type": "string", "required": False,
             "description": "Where the task takes place (address, site name)"},
            {"name": "project_id", "type": "string", "required": False,
             "description": "Project ID. Defaults to current channel project."},
        ],
        "handler": "_builtin:hari_create_task",
        "long_running": False,
    },
    {
        "name": "schedule_event",
        "description": (
            "Schedule a time-bound event like a meeting, review, or deadline. "
            "USE THIS when the user wants to set up a meeting or event with specific attendees and time. "
            "Examples: 'schedule a review meeting Friday 3pm with the team', "
            "'set up a call with the architect tomorrow morning'."
        ),
        "parameters": [
            {"name": "event_description", "type": "string", "required": True,
             "description": "Description of the event"},
            {"name": "event_time", "type": "string", "required": True,
             "description": "Date and time for the event"},
            {"name": "attendees", "type": "string", "required": False,
             "description": "Names of people who should attend (comma-separated)"},
            {"name": "location", "type": "string", "required": False,
             "description": "Where the event takes place"},
            {"name": "project_id", "type": "string", "required": False,
             "description": "Project ID. Defaults to current channel project."},
        ],
        "handler": "_builtin:hari_schedule_event",
        "long_running": False,
    },
    {
        "name": "assign_person",
        "description": (
            "Reassign a person to a project, task, or location. "
            "USE THIS when the user wants to move someone to a different assignment. "
            "Examples: 'move Carlos to the Riverside project', 'reassign the plumbing crew to site B'."
        ),
        "parameters": [
            {"name": "person_name", "type": "string", "required": True,
             "description": "Name of the person to reassign"},
            {"name": "assignment", "type": "string", "required": True,
             "description": "What or where they are being assigned to"},
            {"name": "start_date", "type": "string", "required": False,
             "description": "When the reassignment starts"},
        ],
        "handler": "_builtin:hari_assign_person",
        "long_running": False,
    },
    {
        "name": "check_tasks",
        "description": (
            "List active tasks with their status. Can filter by assignee, project, or status. "
            "USE THIS when the user asks about task status, wants to see what's pending, "
            "or check who's doing what. Examples: 'what tasks are overdue?', "
            "'show me Juan's active tasks', 'what's pending for this project?'."
        ),
        "parameters": [
            {"name": "assignee_name", "type": "string", "required": False,
             "description": "Filter by assignee name"},
            {"name": "status", "type": "string", "required": False,
             "description": "Filter by status: active, overdue, completed, blocked, all"},
            {"name": "project_id", "type": "string", "required": False,
             "description": "Project ID. Defaults to current channel project."},
        ],
        "handler": "_builtin:hari_check_tasks",
        "long_running": False,
    },
    {
        "name": "update_task",
        "description": (
            "Update an existing task: mark complete, change deadline, reassign, or add notes. "
            "USE THIS when the user wants to close a task, extend a deadline, or reassign it. "
            "Examples: 'mark the inspection task as done', 'extend Juan's deadline to Monday', "
            "'reassign the permit review to Maria'."
        ),
        "parameters": [
            {"name": "task_identifier", "type": "string", "required": True,
             "description": "Task description, ID, or enough context to identify it"},
            {"name": "action", "type": "string", "required": True,
             "description": "What to do: complete, extend, reassign, cancel, add_note"},
            {"name": "new_value", "type": "string", "required": False,
             "description": "New deadline (for extend), new assignee (for reassign), or note text"},
        ],
        "handler": "_builtin:hari_update_task",
        "long_running": False,
    },
    {
        "name": "weekly_summary",
        "description": (
            "Generate a weekly task digest: tasks created, completed, overdue, and blocked. "
            "USE THIS when the user asks for a summary, report, or overview of team activity. "
            "Examples: 'give me the weekly summary', 'how did the team do this week?', "
            "'task report for this project'."
        ),
        "parameters": [
            {"name": "project_id", "type": "string", "required": False,
             "description": "Project ID. Defaults to current channel project."},
            {"name": "days", "type": "integer", "required": False,
             "description": "Number of days to look back (default: 7)"},
        ],
        "handler": "_builtin:hari_weekly_summary",
        "long_running": False,
    },
]

# ---------------------------------------------------------------------------
# Registry lookup
# ---------------------------------------------------------------------------

AGENT_REGISTRY: Dict[str, List[Dict[str, Any]]] = {
    "andrew": ANDREW_FUNCTIONS,
    "daneel": DANEEL_FUNCTIONS,
    "hari": HARI_FUNCTIONS,
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

    others = [n.capitalize() for n in AGENT_REGISTRY if n != agent_name.lower()]
    others_str = ", ".join(f"@{o}" for o in others) if others else "another agent"
    lines = [f"Here's what I can help with:"]
    for fn in functions:
        # First sentence of description only
        short = fn["description"].split(". ")[0].rstrip(".")
        lines.append(f"  - **{fn['name']}**: {short}")
    lines.append(f"\nFor anything outside this scope, try {others_str}.")
    return "\n".join(lines)
