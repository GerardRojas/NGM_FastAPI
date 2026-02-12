# api/services/agent_brain.py
# ================================
# Agent Brain - GPT-Powered Routing Layer
# ================================
# Intercepts @mentions in chat, routes to the right function or
# responds conversationally with personality.
#
# Architecture:
#   1. Build context (project, user, recent messages)
#   2. Build function menu from registry
#   3. GPT-5.1 routing call -> decision
#   4. Dispatch: function_call | free_chat | cross_agent | clarify

import os
import re
import json
import time
import asyncio
import traceback
from typing import Dict, Any, Optional, Tuple
from openai import AsyncOpenAI

from api.services.agent_registry import get_functions, get_function, format_functions_for_llm
from api.services.agent_personas import (
    get_persona, get_other_agent, get_cross_agent_suggestion, is_bot_user,
)

# ---------------------------------------------------------------------------
# Rate limiting (in-memory, per-process)
# ---------------------------------------------------------------------------
_cooldowns: Dict[str, float] = {}
COOLDOWN_SECONDS = 5


def _check_cooldown(user_id: str, agent_name: str) -> bool:
    """Return True if the user can invoke this agent (cooldown expired)."""
    key = f"{user_id}:{agent_name}"
    now = time.time()
    last = _cooldowns.get(key, 0)
    if now - last < COOLDOWN_SECONDS:
        return False
    _cooldowns[key] = now
    return True


# ---------------------------------------------------------------------------
# Routing prompt
# ---------------------------------------------------------------------------

BRAIN_ROUTING_PROMPT = """\
{brain_summary}

You are receiving a message from a user in a project channel.
Your job: decide what to do with it.

## Your capabilities
{function_menu}

## Instructions
Analyze the user's message and respond with a JSON object (no markdown fences):

1. If the user wants you to execute one of your functions:
   {{"action": "function_call", "function": "<function_name>", "parameters": {{...}}, "ack_message": "<short acknowledgment>"}}

2. If the user is just chatting, asking a question about you, or saying hello:
   {{"action": "free_chat", "response": "<your conversational reply>"}}

3. If the request is better handled by {other_agent}:
   {{"action": "cross_agent", "suggested_agent": "{other_agent}", "reason": "<brief reason>"}}

4. If you need more information to proceed:
   {{"action": "clarify", "question": "<what you need to know>"}}

## Context
- Project: {project_name} (ID: {project_id})
- User: {user_name}
- Channel: {channel_type}
{attachments_context}
## Recent conversation
{recent_messages}

Respond with ONLY the JSON object. No explanation, no markdown.
"""


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

async def invoke_brain(
    agent_name: str,
    user_text: str,
    user_id: str,
    user_name: str,
    project_id: Optional[str],
    channel_type: str,
    channel_id: Optional[str] = None,
    message_id: Optional[str] = None,
    attachments: Optional[list] = None,
) -> None:
    """
    Main brain entry point. Called from BackgroundTask after @mention detection.

    Builds context, calls GPT-5.1 for routing, dispatches the decision.
    Never raises -- all errors are caught and posted as agent messages.
    """
    tag = f"[AgentBrain:{agent_name}]"

    # Loop prevention layer 1: bot user check
    if is_bot_user(user_id):
        print(f"{tag} Ignoring message from bot user {user_id}")
        return

    # Rate limit
    if not _check_cooldown(user_id, agent_name):
        print(f"{tag} Cooldown active for user {user_id}")
        return

    persona = get_persona(agent_name)
    if not persona:
        print(f"{tag} Unknown agent: {agent_name}")
        return

    try:
        # 1. Build context
        context = await _build_brain_context(
            agent_name, project_id, channel_type, channel_id
        )

        # 2. Route via GPT
        decision = await _route(
            agent_name=agent_name,
            persona=persona,
            user_text=user_text,
            user_name=user_name,
            context=context,
            attachments=attachments,
        )

        if not decision:
            await _post_response(
                agent_name, project_id, channel_type, channel_id,
                "I couldn't process that. Could you rephrase?",
                metadata={"agent_brain": True},
            )
            return

        action = decision.get("action", "free_chat")

        # 3. Dispatch
        if action == "function_call":
            await _execute_function_call(
                agent_name, persona, decision,
                project_id, channel_type, channel_id,
                user_name,
                attachments=attachments,
                user_text=user_text,
            )
        elif action == "cross_agent":
            await _handle_cross_agent(
                agent_name, decision,
                project_id, channel_type, channel_id,
            )
        elif action == "clarify":
            await _handle_clarify(
                agent_name, decision,
                project_id, channel_type, channel_id,
            )
        else:  # free_chat
            await _handle_free_chat(
                agent_name, persona, decision, user_text,
                project_id, channel_type, channel_id,
            )

    except Exception as e:
        print(f"{tag} Brain error: {e}\n{traceback.format_exc()}")
        await _post_response(
            agent_name, project_id, channel_type, channel_id,
            "Something went wrong on my end. Try again in a moment.",
            metadata={"agent_brain": True, "error": str(e)},
        )


# ---------------------------------------------------------------------------
# Context builder
# ---------------------------------------------------------------------------

async def _build_brain_context(
    agent_name: str,
    project_id: Optional[str],
    channel_type: str,
    channel_id: Optional[str],
) -> Dict[str, Any]:
    """Fetch project name and recent messages for the routing prompt."""
    from api.supabase_client import supabase as sb

    context: Dict[str, Any] = {
        "project_name": "Unknown project",
        "project_id": project_id or "none",
        "recent_messages": [],
    }

    # Project name
    if project_id:
        try:
            result = sb.table("projects") \
                .select("project_name") \
                .eq("project_id", project_id) \
                .single() \
                .execute()
            if result.data:
                context["project_name"] = result.data["project_name"]
        except Exception:
            pass

    # Recent messages in this channel (last 5)
    try:
        query = sb.table("messages") \
            .select("content, user_id, created_at") \
            .eq("channel_type", channel_type) \
            .order("created_at", desc=True) \
            .limit(5)

        if channel_id:
            query = query.eq("channel_id", channel_id)
        elif project_id:
            query = query.eq("project_id", project_id)

        result = query.execute()
        if result.data:
            # Reverse to chronological order
            messages = list(reversed(result.data))
            formatted = []
            for m in messages:
                # Resolve user name
                uid = m.get("user_id", "")
                name = _resolve_user_name(sb, uid)
                content = (m.get("content") or "")[:200]
                formatted.append(f"[{name}]: {content}")
            context["recent_messages"] = formatted
    except Exception:
        pass

    return context


_user_name_cache: Dict[str, str] = {}


def _resolve_user_name(sb, user_id: str) -> str:
    """Resolve user_id to user_name with caching."""
    if user_id in _user_name_cache:
        return _user_name_cache[user_id]

    # Check bot IDs
    from api.services.agent_personas import BOT_USER_IDS, AGENT_PERSONAS
    if user_id in BOT_USER_IDS:
        name = AGENT_PERSONAS[BOT_USER_IDS[user_id]]["name"]
        _user_name_cache[user_id] = name
        return name

    try:
        result = sb.table("users") \
            .select("user_name") \
            .eq("user_id", user_id) \
            .single() \
            .execute()
        name = result.data.get("user_name", "User") if result.data else "User"
    except Exception:
        name = "User"

    _user_name_cache[user_id] = name
    return name


# ---------------------------------------------------------------------------
# GPT routing call
# ---------------------------------------------------------------------------

async def _route(
    agent_name: str,
    persona: Dict[str, Any],
    user_text: str,
    user_name: str,
    context: Dict[str, Any],
    attachments: Optional[list] = None,
) -> Optional[Dict[str, Any]]:
    """Call GPT-5.1 to decide what action to take."""
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return {"action": "free_chat", "response": "I'm having trouble connecting right now."}

    other_agent = get_other_agent(agent_name)
    recent = "\n".join(context["recent_messages"]) if context["recent_messages"] else "(no recent messages)"

    # Build attachments context for the routing prompt
    attachments_ctx = ""
    if attachments:
        att_lines = []
        for att in attachments:
            name = att.get("name", "unknown")
            ftype = att.get("type", "unknown")
            att_lines.append(f"  - {name} ({ftype})")
        attachments_ctx = (
            "\n- Attachments:\n" + "\n".join(att_lines) + "\n"
            "  IMPORTANT: The user attached file(s). If they are receipts/invoices "
            "(PDF, image), use the process_receipt function."
        )

    system_prompt = BRAIN_ROUTING_PROMPT.format(
        brain_summary=persona["brain_summary"],
        function_menu=format_functions_for_llm(agent_name),
        other_agent=other_agent or "N/A",
        project_name=context["project_name"],
        project_id=context["project_id"],
        user_name=user_name,
        channel_type="receipts" if "receipt" in (context.get("channel_type") or "") else "general",
        attachments_context=attachments_ctx,
        recent_messages=recent,
    )

    try:
        client = AsyncOpenAI(api_key=api_key)
        response = await client.chat.completions.create(
            model="gpt-5.1",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_text},
            ],
            temperature=0.1,
            max_completion_tokens=300,
        )

        raw = response.choices[0].message.content.strip()

        # Strip markdown fences if present
        if raw.startswith("```"):
            raw = re.sub(r"^```(?:json)?\s*", "", raw)
            raw = re.sub(r"\s*```$", "", raw)

        return json.loads(raw)

    except json.JSONDecodeError:
        print(f"[AgentBrain:{agent_name}] Failed to parse routing JSON: {raw[:200]}")
        # Fallback: treat as free chat
        return {"action": "free_chat", "response": raw}

    except Exception as e:
        print(f"[AgentBrain:{agent_name}] Routing GPT error: {e}")
        return None


# ---------------------------------------------------------------------------
# Dispatch handlers
# ---------------------------------------------------------------------------

async def _execute_function_call(
    agent_name: str,
    persona: Dict[str, Any],
    decision: Dict[str, Any],
    project_id: Optional[str],
    channel_type: str,
    channel_id: Optional[str],
    user_name: str,
    attachments: Optional[list] = None,
    user_text: str = "",
) -> None:
    """Execute a registered function. Post ack if long-running."""
    fn_name = decision.get("function", "")
    params = decision.get("parameters", {})
    ack_msg = decision.get("ack_message", "")

    fn_def = get_function(agent_name, fn_name)
    if not fn_def:
        await _post_response(
            agent_name, project_id, channel_type, channel_id,
            await _personalize(agent_name, f"I don't have a function called '{fn_name}'."),
            metadata={"agent_brain": True},
        )
        return

    tag = f"[AgentBrain:{agent_name}]"

    # Post acknowledgment for long-running functions
    if fn_def["long_running"] and ack_msg:
        await _post_response(
            agent_name, project_id, channel_type, channel_id,
            await _personalize(agent_name, ack_msg),
            metadata={"agent_brain": True, "brain_ack": True},
        )

    try:
        # Inject project_id if the function expects it and user didn't provide one
        if "project_id" in [p["name"] for p in fn_def["parameters"]]:
            if "project_id" not in params or not params["project_id"]:
                params["project_id"] = project_id

        # Pass attachments and user text to handlers that need them
        if fn_def.get("requires_attachments") and attachments:
            params["_attachments"] = attachments
            params["_user_name"] = user_name
            params["_user_text"] = user_text

        # Execute the handler
        result = await _call_handler(agent_name, fn_def, params)

        # Format result into readable text
        result_text = _format_result(agent_name, fn_name, result)

        # Skip posting if handler already posted its own messages (e.g. receipt flow)
        if result_text:
            personalized = await _personalize(agent_name, result_text)
            await _post_response(
                agent_name, project_id, channel_type, channel_id,
                personalized,
                metadata={
                    "agent_brain": True,
                    "function": fn_name,
                },
            )

    except Exception as e:
        print(f"{tag} Function {fn_name} failed: {e}\n{traceback.format_exc()}")
        error_msg = f"I ran into a problem executing {fn_name}: {str(e)[:100]}"
        await _post_response(
            agent_name, project_id, channel_type, channel_id,
            await _personalize(agent_name, error_msg),
            metadata={"agent_brain": True, "error": str(e)},
        )


async def _handle_free_chat(
    agent_name: str,
    persona: Dict[str, Any],
    decision: Dict[str, Any],
    user_text: str,
    project_id: Optional[str],
    channel_type: str,
    channel_id: Optional[str],
) -> None:
    """Handle conversational / free-chat responses."""
    response_text = decision.get("response", "")

    # If the router already gave a good response, personalize it
    if response_text:
        # The routing model already responded in character, but re-pass
        # through personality for consistency
        personalized = await _personalize(agent_name, response_text)
    else:
        # Generate a fresh conversational response
        personalized = await _generate_conversation(agent_name, persona, user_text)

    await _post_response(
        agent_name, project_id, channel_type, channel_id,
        personalized,
        metadata={"agent_brain": True, "action": "free_chat"},
    )


async def _handle_cross_agent(
    agent_name: str,
    decision: Dict[str, Any],
    project_id: Optional[str],
    channel_type: str,
    channel_id: Optional[str],
) -> None:
    """Suggest the user talk to the other agent."""
    suggested = decision.get("suggested_agent", "the other agent")
    reason = decision.get("reason", "")

    raw = f"That's more of a @{suggested} thing"
    if reason:
        raw += f" -- {reason}"
    raw += f". Try mentioning @{suggested} directly."

    personalized = await _personalize(agent_name, raw)
    await _post_response(
        agent_name, project_id, channel_type, channel_id,
        personalized,
        metadata={"agent_brain": True, "action": "cross_agent", "suggested": suggested},
    )


async def _handle_clarify(
    agent_name: str,
    decision: Dict[str, Any],
    project_id: Optional[str],
    channel_type: str,
    channel_id: Optional[str],
) -> None:
    """Ask the user for more information."""
    question = decision.get("question", "Could you give me more details?")

    personalized = await _personalize(agent_name, question)
    await _post_response(
        agent_name, project_id, channel_type, channel_id,
        personalized,
        metadata={"agent_brain": True, "action": "clarify"},
    )


# ---------------------------------------------------------------------------
# Function handler caller
# ---------------------------------------------------------------------------

async def _call_handler(
    agent_name: str,
    fn_def: Dict[str, Any],
    params: Dict[str, Any],
) -> Any:
    """
    Call the registered handler function.
    Handles both sync and async handlers.
    Handles _builtin: prefix for built-in brain functions.
    """
    handler_path = fn_def["handler"]

    # Built-in handlers (implemented below)
    if handler_path.startswith("_builtin:"):
        builtin_name = handler_path.split(":", 1)[1]
        builtin_fn = _BUILTIN_HANDLERS.get(builtin_name)
        if not builtin_fn:
            return {"error": f"Unknown built-in handler: {builtin_name}"}
        return await builtin_fn(params)

    # Dynamic import of external handler
    parts = handler_path.rsplit(".", 1)
    if len(parts) != 2:
        return {"error": f"Invalid handler path: {handler_path}"}

    module_path, func_name = parts

    import importlib
    module = importlib.import_module(module_path)
    handler_fn = getattr(module, func_name)

    # Call with the params dict unpacked
    if asyncio.iscoroutinefunction(handler_fn):
        return await handler_fn(**params)
    else:
        # Run sync handlers in a thread to avoid blocking
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, lambda: handler_fn(**params))


# ---------------------------------------------------------------------------
# Built-in lightweight handlers
# ---------------------------------------------------------------------------

async def _builtin_check_receipt_status(params: Dict[str, Any]) -> Dict[str, Any]:
    """Look up pending receipt status."""
    from api.supabase_client import supabase as sb

    receipt_id = params.get("receipt_id")

    try:
        query = sb.table("pending_receipts") \
            .select("id, file_name, status, flow_state, updated_at, parsed_data") \
            .eq("status", "ready")

        if receipt_id:
            query = query.eq("id", receipt_id)

        result = query.order("updated_at", desc=True).limit(10).execute()

        if not result.data:
            return {"message": "No pending receipts found.", "count": 0}

        items = []
        for r in result.data:
            parsed = r.get("parsed_data") or {}
            items.append({
                "id": r["id"],
                "file": r.get("file_name", "?"),
                "state": r.get("flow_state", "unknown"),
                "vendor": parsed.get("vendor_name", "Unknown"),
                "amount": parsed.get("total_amount", "?"),
                "updated": (r.get("updated_at") or "")[:16],
            })

        return {"count": len(items), "receipts": items}

    except Exception as e:
        return {"error": str(e)}


async def _builtin_explain_categorization(params: Dict[str, Any]) -> Dict[str, Any]:
    """Explain why an expense got a certain account/category."""
    from api.supabase_client import supabase as sb

    expense_id = params.get("expense_id")
    if not expense_id:
        return {"error": "expense_id is required"}

    try:
        result = sb.table("expenses_manual_COGS") \
            .select("LineDescription, Amount, account_id, vendor_id, TxnDate, categorized_by") \
            .eq("expense_id", expense_id) \
            .single() \
            .execute()

        if not result.data:
            return {"error": f"Expense {expense_id} not found"}

        exp = result.data
        account_name = "Unknown"
        if exp.get("account_id"):
            try:
                acc = sb.table("accounts") \
                    .select("Name") \
                    .eq("id", exp["account_id"]) \
                    .single() \
                    .execute()
                if acc.data:
                    account_name = acc.data["Name"]
            except Exception:
                pass

        vendor_name = "Unknown"
        if exp.get("vendor_id"):
            try:
                v = sb.table("Vendors") \
                    .select("vendor_name") \
                    .eq("vendor_id", exp["vendor_id"]) \
                    .single() \
                    .execute()
                if v.data:
                    vendor_name = v.data["vendor_name"]
            except Exception:
                pass

        return {
            "expense_id": expense_id,
            "description": exp.get("LineDescription", ""),
            "amount": exp.get("Amount"),
            "account": account_name,
            "vendor": vendor_name,
            "date": (exp.get("TxnDate") or "")[:10],
            "categorized_by": exp.get("categorized_by", "unknown"),
        }

    except Exception as e:
        return {"error": str(e)}


async def _builtin_check_budget(params: Dict[str, Any]) -> Dict[str, Any]:
    """Budget vs actuals report for a project."""
    from api.supabase_client import supabase as sb

    project_id = params.get("project_id")
    if not project_id:
        return {"error": "project_id is required"}

    try:
        # Get project budget
        proj = sb.table("projects") \
            .select("project_name, budget") \
            .eq("project_id", project_id) \
            .single() \
            .execute()

        budget = float(proj.data.get("budget") or 0) if proj.data else 0
        project_name = proj.data.get("project_name", "Unknown") if proj.data else "Unknown"

        # Sum expenses by account
        expenses = sb.table("expenses_manual_COGS") \
            .select("Amount, account_id") \
            .eq("project", project_id) \
            .execute()

        total_spent = 0.0
        by_account: Dict[str, float] = {}
        for exp in (expenses.data or []):
            amt = float(exp.get("Amount") or 0)
            total_spent += amt
            acc_id = exp.get("account_id") or "uncategorized"
            by_account[acc_id] = by_account.get(acc_id, 0) + amt

        # Resolve account names
        account_breakdown = []
        for acc_id, total in sorted(by_account.items(), key=lambda x: -x[1]):
            name = acc_id
            if acc_id != "uncategorized":
                try:
                    acc = sb.table("accounts") \
                        .select("Name") \
                        .eq("id", acc_id) \
                        .single() \
                        .execute()
                    if acc.data:
                        name = acc.data["Name"]
                except Exception:
                    pass
            account_breakdown.append({"account": name, "spent": round(total, 2)})

        remaining = round(budget - total_spent, 2)
        pct_used = round((total_spent / budget * 100), 1) if budget > 0 else 0

        return {
            "project": project_name,
            "budget": budget,
            "total_spent": round(total_spent, 2),
            "remaining": remaining,
            "percent_used": pct_used,
            "by_account": account_breakdown[:10],
        }

    except Exception as e:
        return {"error": str(e)}


async def _builtin_check_duplicates(params: Dict[str, Any]) -> Dict[str, Any]:
    """Scan for duplicate expenses in the project."""
    from api.supabase_client import supabase as sb

    project_id = params.get("project_id")
    if not project_id:
        return {"error": "project_id is required"}

    try:
        expenses = sb.table("expenses_manual_COGS") \
            .select("expense_id, Amount, TxnDate, vendor_id, LineDescription") \
            .eq("project", project_id) \
            .order("TxnDate", desc=True) \
            .limit(500) \
            .execute()

        if not expenses.data:
            return {"message": "No expenses found.", "duplicates": []}

        # Group by (amount, vendor_id, date) to find potential duplicates
        groups: Dict[str, list] = {}
        for exp in expenses.data:
            key = f"{exp.get('Amount')}|{exp.get('vendor_id')}|{(exp.get('TxnDate') or '')[:10]}"
            groups.setdefault(key, []).append(exp)

        duplicates = []
        for key, group in groups.items():
            if len(group) >= 2:
                duplicates.append({
                    "count": len(group),
                    "amount": group[0].get("Amount"),
                    "date": (group[0].get("TxnDate") or "")[:10],
                    "expense_ids": [e.get("expense_id") for e in group],
                    "descriptions": [e.get("LineDescription", "")[:40] for e in group],
                })

        return {
            "total_expenses_scanned": len(expenses.data),
            "duplicate_groups": len(duplicates),
            "duplicates": duplicates[:15],
        }

    except Exception as e:
        return {"error": str(e)}


async def _builtin_expense_health_report(params: Dict[str, Any]) -> Dict[str, Any]:
    """Health check on project expenses: missing data fields."""
    from api.supabase_client import supabase as sb

    project_id = params.get("project_id")
    if not project_id:
        return {"error": "project_id is required"}

    try:
        expenses = sb.table("expenses_manual_COGS") \
            .select("expense_id, Amount, TxnDate, vendor_id, account_id, "
                    "LineDescription, bill_id") \
            .eq("project", project_id) \
            .limit(500) \
            .execute()

        if not expenses.data:
            return {"message": "No expenses found.", "total": 0}

        total = len(expenses.data)
        missing_vendor = []
        missing_date = []
        missing_account = []
        missing_receipt = []
        missing_description = []

        for exp in expenses.data:
            eid = exp.get("expense_id", "?")
            if not exp.get("vendor_id"):
                missing_vendor.append(eid)
            if not exp.get("TxnDate"):
                missing_date.append(eid)
            if not exp.get("account_id"):
                missing_account.append(eid)
            if not exp.get("bill_id"):
                missing_receipt.append(eid)
            if not exp.get("LineDescription"):
                missing_description.append(eid)

        return {
            "total_expenses": total,
            "missing_vendor": len(missing_vendor),
            "missing_date": len(missing_date),
            "missing_account": len(missing_account),
            "missing_receipt": len(missing_receipt),
            "missing_description": len(missing_description),
            "health_score": round(
                (1 - (len(missing_vendor) + len(missing_date) + len(missing_account)
                      + len(missing_receipt)) / max(total * 4, 1)) * 100, 1
            ),
        }

    except Exception as e:
        return {"error": str(e)}


async def _builtin_reprocess_pending(params: Dict[str, Any]) -> Dict[str, Any]:
    """Re-check flagged expenses in daneel_pending_info."""
    from api.supabase_client import supabase as sb

    project_id = params.get("project_id")
    if not project_id:
        return {"error": "project_id is required"}

    try:
        # Get unresolved pending items for this project
        pending = sb.table("daneel_pending_info") \
            .select("id, expense_id, missing_fields") \
            .eq("project_id", project_id) \
            .is_("resolved_at", "null") \
            .limit(50) \
            .execute()

        if not pending.data:
            return {"message": "No pending items to reprocess.", "count": 0}

        resolved_count = 0
        still_pending = 0

        for item in pending.data:
            expense_id = item.get("expense_id")
            if not expense_id:
                continue

            # Check if the expense now has the previously missing fields
            exp = sb.table("expenses_manual_COGS") \
                .select("vendor_id, TxnDate, account_id, bill_id, Amount") \
                .eq("expense_id", expense_id) \
                .single() \
                .execute()

            if not exp.data:
                still_pending += 1
                continue

            missing = item.get("missing_fields") or []
            still_missing = []
            for field in missing:
                field_lower = field.lower()
                if "vendor" in field_lower and not exp.data.get("vendor_id"):
                    still_missing.append(field)
                elif "date" in field_lower and not exp.data.get("TxnDate"):
                    still_missing.append(field)
                elif "account" in field_lower and not exp.data.get("account_id"):
                    still_missing.append(field)
                elif "receipt" in field_lower and not exp.data.get("bill_id"):
                    still_missing.append(field)

            if not still_missing:
                # All fields resolved - mark as resolved
                from datetime import datetime
                sb.table("daneel_pending_info") \
                    .update({"resolved_at": datetime.utcnow().isoformat()}) \
                    .eq("id", item["id"]) \
                    .execute()
                resolved_count += 1
            else:
                still_pending += 1

        return {
            "reprocessed": len(pending.data),
            "resolved": resolved_count,
            "still_pending": still_pending,
        }

    except Exception as e:
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# User context extraction (for smart receipt flow)
# ---------------------------------------------------------------------------

USER_CONTEXT_PROMPT = """\
You are analyzing a user's message that accompanies a receipt/invoice upload.
Extract structured intent from their text. The user may write in English or Spanish.

Current project: {project_name}

User message: "{user_text}"

Extract a JSON object with these fields (use null for anything not mentioned):

{{
  "project_decision": "all_this_project" | "split" | null,
  "split_projects": [
    {{"name": "<project name or 'this_project'>", "portion": "<half/third/etc or null>", "amount": <number or null>}}
  ] or null,
  "category_hints": ["<account/category names mentioned>"] or null,
  "vendor_hint": "<vendor name>" or null,
  "amount_hint": <number> or null,
  "date_hint": "<date string>" or null
}}

Rules:
- "all_this_project": user says ALL items are for the current project (e.g. "all for this project", "todo para este proyecto", "everything goes here")
- "split": user mentions splitting between projects (e.g. "half for X, half for Y", "mitad aqui mitad alla", "$500 for X project")
- If the user says "this project" or "este proyecto" or "aqui" in split_projects, use the literal string "this_project"
- category_hints: extract material/account category names (e.g. "Materials", "Pisos", "Lumber", "Delivery")
- amount_hint: extract dollar amounts if mentioned (e.g. "$1,204" -> 1204.00)
- For portions like "half"/"mitad" -> "half", "third"/"tercio" -> "third"
- Return ONLY the JSON object. No explanation.
"""


async def _extract_user_context(
    user_text: str,
    project_name: str,
) -> Dict[str, Any]:
    """
    Use gpt-4.1-nano to extract structured intent from user's message text.
    Returns dict with project_decision, split_projects, category_hints, etc.
    Returns empty dict on failure (graceful fallback).
    """
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return {}

    prompt = USER_CONTEXT_PROMPT.format(
        project_name=project_name,
        user_text=user_text,
    )

    try:
        client = AsyncOpenAI(api_key=api_key)
        response = await client.chat.completions.create(
            model="gpt-4.1-nano",
            messages=[
                {"role": "system", "content": prompt},
            ],
            temperature=0.0,
            max_completion_tokens=250,
        )

        raw = response.choices[0].message.content.strip()
        if raw.startswith("```"):
            raw = re.sub(r"^```(?:json)?\s*", "", raw)
            raw = re.sub(r"\s*```$", "", raw)

        result = json.loads(raw)
        print(f"[UserContext] Extracted: {json.dumps(result, ensure_ascii=False)[:200]}")
        return result

    except Exception as e:
        print(f"[UserContext] Extraction failed (non-blocking): {e}")
        return {}


async def _builtin_process_receipt(params: Dict[str, Any]) -> Dict[str, Any]:
    """
    Process a receipt/invoice file attached to a chat message.
    Downloads the file, runs OCR + categorization, creates pending_receipt.
    """
    import hashlib
    import httpx
    from uuid import uuid4
    from datetime import datetime
    from api.supabase_client import supabase as sb

    attachments = params.get("_attachments", [])
    user_name = params.get("_user_name", "Unknown")
    project_id = params.get("project_id")

    if not attachments:
        return {"error": "No file attached. Please attach a receipt or invoice and try again."}

    if not project_id:
        return {"error": "No project context. Please use this in a project channel."}

    # Find the first receipt-like attachment (PDF or image)
    receipt_att = None
    ALLOWED_TYPES = {"application/pdf", "image/jpeg", "image/png", "image/webp", "image/gif"}
    for att in attachments:
        att_type = (att.get("type") or "").lower()
        att_name = (att.get("name") or "").lower()
        if att_type in ALLOWED_TYPES or att_name.endswith((".pdf", ".jpg", ".jpeg", ".png", ".webp")):
            receipt_att = att
            break

    if not receipt_att:
        return {"error": "No receipt file found. Supported formats: PDF, JPG, PNG, WebP."}

    file_url = receipt_att.get("url", "")
    file_name = receipt_att.get("name", "receipt")
    file_type = receipt_att.get("type", "application/pdf")

    if not file_url:
        return {"error": "Attachment has no URL. The file may not have uploaded correctly."}

    tag = "[AgentBrain:process_receipt]"

    try:
        # 1. Download file bytes from the attachment URL
        print(f"{tag} Downloading {file_name} from {file_url[:80]}...")
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(file_url)
            resp.raise_for_status()
            file_content = resp.content

        file_size = len(file_content)
        if file_size > 20 * 1024 * 1024:
            return {"error": "File too large. Maximum size is 20MB."}

        # 2. Compute file hash for duplicate detection
        file_hash = hashlib.sha256(file_content).hexdigest()

        # Check for duplicate receipt
        try:
            dup_check = sb.table("pending_receipts") \
                .select("id, file_name") \
                .eq("file_hash", file_hash) \
                .eq("project_id", project_id) \
                .limit(1) \
                .execute()
            if dup_check.data:
                existing = dup_check.data[0]
                return {
                    "status": "duplicate",
                    "message": (
                        f"This file was already uploaded as '{existing['file_name']}' "
                        f"(ID: {existing['id'][:8]}...). No duplicate created."
                    ),
                }
        except Exception:
            pass

        # 3. Save to project vault (Receipts folder)
        receipt_id = str(uuid4())
        from api.services.vault_service import save_to_project_folder

        print(f"{tag} Saving to vault Receipts folder for project {project_id[:8]}...")
        vault_result = save_to_project_folder(
            project_id=project_id,
            folder_name="Receipts",
            file_content=file_content,
            filename=file_name,
            content_type=file_type,
        )
        if not vault_result:
            return {"error": "Failed to save receipt to project vault. Storage may be unavailable."}

        public_url = vault_result.get("public_url", "")

        # 4. Create pending_receipt record
        receipt_data = {
            "id": receipt_id,
            "project_id": project_id,
            "file_name": file_name,
            "file_url": public_url,
            "file_type": file_type,
            "file_size": file_size,
            "file_hash": file_hash,
            "status": "pending",
            "uploaded_by": user_name,
            "created_at": datetime.utcnow().isoformat(),
            "updated_at": datetime.utcnow().isoformat(),
        }

        # Add thumbnail for images
        if file_type.startswith("image/"):
            receipt_data["thumbnail_url"] = f"{public_url}?width=200&height=200&resize=contain"

        sb.table("pending_receipts").insert(receipt_data).execute()
        print(f"{tag} Created pending_receipt {receipt_id}")

        # 5. Run OCR scan
        from services.receipt_scanner import scan_receipt
        print(f"{tag} Running OCR scan...")
        scan_result = scan_receipt(
            file_content=file_content,
            file_type=file_type,
            model="heavy",
        )

        expenses = scan_result.get("expenses", [])
        validation = scan_result.get("validation", {})

        # 6. Auto-categorize if expenses found
        categorize_data = []
        construction_stage = "General"
        project_name = "this project"
        if expenses:
            try:
                proj = sb.table("projects") \
                    .select("construction_stage, project_name") \
                    .eq("project_id", project_id) \
                    .single() \
                    .execute()
                construction_stage = (proj.data or {}).get("construction_stage", "General")
                project_name = (proj.data or {}).get("project_name", "this project")

                from services.receipt_scanner import auto_categorize
                cat_input = [
                    {"rowIndex": i, "description": e.get("description", "")}
                    for i, e in enumerate(expenses)
                ]
                categorize_data = auto_categorize(construction_stage, cat_input)
                print(f"{tag} Categorized {len(categorize_data)} items")
            except Exception as cat_err:
                print(f"{tag} Categorization failed (non-blocking): {cat_err}")

        # 6.5 Build line_items with categorization (like agent-process)
        vendor = expenses[0].get("vendor", "Unknown") if expenses else "Unknown"
        total = validation.get("invoice_total") or sum(
            float(e.get("amount", 0)) for e in expenses
        )
        vendor_id = None
        receipt_date = expenses[0].get("date") if expenses else None
        description = expenses[0].get("description", "") if expenses else ""
        bill_id = expenses[0].get("bill_id") if expenses else None

        line_items = []
        cat_map = {c["rowIndex"]: c for c in categorize_data}
        for i, exp in enumerate(expenses):
            cat = cat_map.get(i, {})
            line_items.append({
                "description": exp.get("description", ""),
                "amount": exp.get("amount", 0),
                "date": exp.get("date"),
                "vendor": exp.get("vendor"),
                "bill_id": exp.get("bill_id"),
                "account_id": cat.get("account_id"),
                "account_name": cat.get("account_name"),
                "confidence": cat.get("confidence", 0),
                "reasoning": cat.get("reasoning"),
                "warning": cat.get("warning"),
            })

        parsed_data = {
            "vendor_name": vendor,
            "vendor_id": vendor_id,
            "amount": round(total, 2),
            "receipt_date": receipt_date,
            "bill_id": bill_id,
            "description": description,
            "line_items": line_items,
            "validation": validation,
            "extraction_method": scan_result.get("extraction_method", "unknown"),
        }

        # 6.6 Detect low-confidence categories
        agent_cfg = {}
        try:
            cfg_result = sb.table("agent_config").select("key, value").execute()
            for row in (cfg_result.data or []):
                agent_cfg[row["key"]] = row["value"]
        except Exception:
            pass
        min_confidence = int(agent_cfg.get("min_confidence", 70))

        low_confidence_items = []
        for i, item in enumerate(line_items):
            item_conf = item.get("confidence", 0)
            if item_conf < min_confidence and item.get("account_id"):
                low_confidence_items.append({
                    "index": i,
                    "description": item.get("description", ""),
                    "amount": item.get("amount"),
                    "suggested": item.get("account_name"),
                    "suggested_account_id": item.get("account_id"),
                    "confidence": item_conf,
                })
            elif not item.get("account_id"):
                low_confidence_items.append({
                    "index": i,
                    "description": item.get("description", ""),
                    "amount": item.get("amount"),
                    "suggested": None,
                    "suggested_account_id": None,
                    "confidence": 0,
                })

        if low_confidence_items:
            print(f"{tag} {len(low_confidence_items)} item(s) with low confidence")

        # 7. Extract user context from message text
        user_text = params.get("_user_text", "")
        user_context = {}
        if user_text:
            clean_text = re.sub(r'@\w+\s*', '', user_text).strip()
            if len(clean_text) > 5:
                user_context = await _extract_user_context(clean_text, project_name)

        # 7.5 Apply user context to auto-resolve steps
        from api.routers.pending_receipts import (
            _apply_user_context, _build_confirm_summary,
        )

        ctx_result = _apply_user_context(
            user_context=user_context,
            line_items=line_items,
            low_confidence_items=low_confidence_items,
            project_id=project_id,
            project_name=project_name,
            parsed_data=parsed_data,
        )

        # Re-evaluate low-confidence after context application
        if ctx_result["resolved_items"]:
            low_confidence_items = [
                lci for lci in low_confidence_items
                if lci["index"] not in ctx_result["resolved_items"]
            ]

        # 8. Initialize receipt_flow based on user context
        start_state = ctx_result["start_state"]
        pre_resolved = ctx_result["pre_resolved"]

        if start_state == "awaiting_user_confirm":
            receipt_flow = {
                "state": "awaiting_user_confirm",
                "started_at": datetime.utcnow().isoformat(),
                "pre_resolved": pre_resolved,
                "split_items": [],
                "total_for_project": 0.0,
            }
        elif start_state == "awaiting_category_confirmation":
            receipt_flow = {
                "state": "awaiting_category_confirmation",
                "started_at": datetime.utcnow().isoformat(),
                "low_confidence_items": low_confidence_items,
                "pre_resolved": pre_resolved,
                "split_items": [],
                "total_for_project": 0.0,
            }
        elif low_confidence_items:
            receipt_flow = {
                "state": "awaiting_category_confirmation",
                "started_at": datetime.utcnow().isoformat(),
                "low_confidence_items": low_confidence_items,
                "split_items": [],
                "total_for_project": 0.0,
            }
        else:
            receipt_flow = {
                "state": "awaiting_item_selection",
                "started_at": datetime.utcnow().isoformat(),
                "split_items": [],
                "total_for_project": 0.0,
            }

        parsed_data["receipt_flow"] = receipt_flow
        if user_context:
            parsed_data["user_context"] = user_context

        # 9. Update pending_receipt with full parsed data
        update_data = {
            "status": "ready",
            "parsed_data": parsed_data,
            "vendor_name": vendor,
            "amount": round(total, 2),
            "receipt_date": receipt_date,
            "updated_at": datetime.utcnow().isoformat(),
        }

        sb.table("pending_receipts") \
            .update(update_data) \
            .eq("id", receipt_id) \
            .execute()

        # 10. Post Andrew message with receipt flow metadata
        from api.helpers.andrew_messenger import post_andrew_message

        flow_state = receipt_flow["state"]

        if flow_state == "awaiting_user_confirm":
            msg_content = _build_confirm_summary(pre_resolved, line_items, parsed_data)
            post_andrew_message(
                content=msg_content,
                project_id=project_id,
                metadata={
                    "agent_message": True,
                    "pending_receipt_id": receipt_id,
                    "receipt_status": "ready",
                    "receipt_flow_state": "awaiting_user_confirm",
                    "receipt_flow_active": True,
                    "pre_resolved": pre_resolved,
                }
            )
            print(f"{tag} Posted confirm summary (user context resolved all steps)")

        elif flow_state == "awaiting_category_confirmation":
            # Build category question message
            cat_lines = []
            for lci in low_confidence_items:
                idx = lci["index"] + 1
                desc = lci["description"][:60]
                if lci["suggested"]:
                    cat_lines.append(f"{idx}. '{desc}' -- suggested: {lci['suggested']} ({lci['confidence']}%)")
                else:
                    cat_lines.append(f"{idx}. '{desc}' -- no match found")
            cat_list = "\n".join(cat_lines)

            context_note = ""
            if pre_resolved.get("project_decision"):
                decision = pre_resolved["project_decision"]
                if decision == "all_this_project":
                    context_note = f"I see this is all for **{project_name}**. "
                elif decision == "split":
                    context_note = "I have the split info saved. "

            msg_content = (
                f"Receipt scanned: **{vendor}** -- ${total:,.2f}\n\n"
                f"{context_note}"
                f"I need help with the category for these items:\n{cat_list}\n\n"
                "Type the correct account for each, e.g.: `1 Materials, 2 Delivery`\n"
                "Or reply **all correct** to accept the suggestions."
            )
            post_andrew_message(
                content=msg_content,
                project_id=project_id,
                metadata={
                    "agent_message": True,
                    "pending_receipt_id": receipt_id,
                    "receipt_status": "ready",
                    "receipt_flow_state": "awaiting_category_confirmation",
                    "receipt_flow_active": True,
                    "awaiting_text_input": True,
                    "low_confidence_items": low_confidence_items,
                }
            )
            print(f"{tag} Posted category question ({len(low_confidence_items)} items)")

        else:
            # Normal flow: awaiting_item_selection
            item_summary = ""
            if len(line_items) > 1:
                item_lines = []
                for i, item in enumerate(line_items):
                    desc = (item.get("description") or "Item")[:60]
                    amt = item.get("amount", 0)
                    cat = item.get("account_name") or "Uncategorized"
                    conf = item.get("confidence", 0)
                    item_lines.append(f"{i+1}. {desc} -- ${amt:,.2f} -> {cat} ({conf}%)")
                item_summary = "\n".join(item_lines)
            else:
                primary_cat = line_items[0].get("account_name", "Uncategorized") if line_items else "Uncategorized"
                primary_conf = line_items[0].get("confidence", 0) if line_items else 0
                item_summary = f"Category: **{primary_cat}** ({primary_conf}% confidence)"

            msg_content = (
                f"Receipt scanned: **{vendor}** -- ${total:,.2f}\n\n"
                f"{item_summary}\n\n"
                f"Is this entire bill for **{project_name}**?"
            )
            post_andrew_message(
                content=msg_content,
                project_id=project_id,
                metadata={
                    "agent_message": True,
                    "pending_receipt_id": receipt_id,
                    "receipt_status": "ready",
                    "receipt_flow_state": "awaiting_item_selection",
                    "receipt_flow_active": True,
                }
            )
            print(f"{tag} Posted project question (normal flow)")

        # Return status for brain formatting (messages already posted above)
        return {
            "status": "flow_started",
            "receipt_id": receipt_id,
            "file_name": file_name,
            "vendor": vendor,
            "total": round(total, 2),
            "line_items": len(expenses),
            "flow_state": flow_state,
            "user_context_applied": bool(user_context),
            "steps_skipped": (
                ctx_result["skip_categories"] or ctx_result["skip_project_question"]
            ),
        }

    except Exception as e:
        print(f"{tag} Error: {e}\n{traceback.format_exc()}")
        return {"error": f"Failed to process receipt: {str(e)[:150]}"}


# Handler registry
_BUILTIN_HANDLERS = {
    "process_receipt": _builtin_process_receipt,
    "check_receipt_status": _builtin_check_receipt_status,
    "explain_categorization": _builtin_explain_categorization,
    "check_budget": _builtin_check_budget,
    "check_duplicates": _builtin_check_duplicates,
    "expense_health_report": _builtin_expense_health_report,
    "reprocess_pending": _builtin_reprocess_pending,
}


# ---------------------------------------------------------------------------
# Result formatting
# ---------------------------------------------------------------------------

def _format_result(agent_name: str, fn_name: str, result: Any) -> str:
    """Convert handler result dict into readable markdown."""
    if not isinstance(result, dict):
        return str(result)

    if "error" in result:
        return f"Error: {result['error']}"

    if "message" in result and len(result) <= 2:
        return result["message"]

    # Function-specific formatting
    if fn_name == "check_receipt_status":
        receipts = result.get("receipts", [])
        if not receipts:
            return "No pending receipts found."
        lines = [f"**{result['count']} pending receipt(s):**\n"]
        for r in receipts:
            lines.append(
                f"- **{r['file']}** | {r['vendor']} | ${r['amount']} | "
                f"State: {r['state']} | Updated: {r['updated']}"
            )
        return "\n".join(lines)

    if fn_name == "explain_categorization":
        return (
            f"**Expense {result.get('expense_id', '?')}**\n"
            f"- Description: {result.get('description', '?')}\n"
            f"- Amount: ${result.get('amount', '?')}\n"
            f"- Account: {result.get('account', '?')}\n"
            f"- Vendor: {result.get('vendor', '?')}\n"
            f"- Categorized by: {result.get('categorized_by', 'unknown')}"
        )

    if fn_name == "check_budget":
        breakdown = result.get("by_account", [])
        lines = [
            f"**{result.get('project', '?')} - Budget Report**\n",
            f"- Budget: ${result.get('budget', 0):,.2f}",
            f"- Spent: ${result.get('total_spent', 0):,.2f} ({result.get('percent_used', 0)}%)",
            f"- Remaining: ${result.get('remaining', 0):,.2f}",
        ]
        if breakdown:
            lines.append("\n**Breakdown by account:**")
            for item in breakdown:
                lines.append(f"- {item['account']}: ${item['spent']:,.2f}")
        return "\n".join(lines)

    if fn_name == "check_duplicates":
        dupes = result.get("duplicates", [])
        if not dupes:
            return (
                f"Scanned {result.get('total_expenses_scanned', 0)} expenses. "
                f"No duplicates found."
            )
        lines = [
            f"**{result.get('duplicate_groups', 0)} potential duplicate group(s)** "
            f"(from {result.get('total_expenses_scanned', 0)} expenses):\n"
        ]
        for d in dupes:
            descs = ", ".join(d.get("descriptions", []))
            lines.append(
                f"- **${d.get('amount', '?')}** on {d.get('date', '?')} "
                f"({d.get('count', 0)}x): {descs}"
            )
        return "\n".join(lines)

    if fn_name == "expense_health_report":
        return (
            f"**Expense Health Report** (score: {result.get('health_score', 0)}%)\n\n"
            f"- Total expenses: {result.get('total_expenses', 0)}\n"
            f"- Missing vendor: {result.get('missing_vendor', 0)}\n"
            f"- Missing date: {result.get('missing_date', 0)}\n"
            f"- Missing account: {result.get('missing_account', 0)}\n"
            f"- Missing receipt: {result.get('missing_receipt', 0)}\n"
            f"- Missing description: {result.get('missing_description', 0)}"
        )

    if fn_name == "reprocess_pending":
        return (
            f"Reprocessed {result.get('reprocessed', 0)} pending item(s):\n"
            f"- Resolved: {result.get('resolved', 0)}\n"
            f"- Still pending: {result.get('still_pending', 0)}"
        )

    if fn_name == "process_receipt":
        status = result.get("status", "")
        if status == "duplicate":
            return result.get("message", "Duplicate receipt detected.")
        if status == "flow_started":
            # Messages already posted directly by the handler -- return empty
            # so the brain doesn't double-post
            return ""
        if status == "processed":
            validation = "passed" if result.get("validation_passed") else "needs review"
            cat_count = result.get("categorize_count", 0)
            return (
                f"**Receipt processed: {result.get('file_name', '?')}**\n"
                f"- Vendor: {result.get('vendor', 'Unknown')}\n"
                f"- Total: ${result.get('total', 0):,.2f}\n"
                f"- Line items: {result.get('line_items', 0)}\n"
                f"- Extraction: {result.get('extraction_method', '?')}\n"
                f"- Validation: {validation}\n"
                f"- Categories assigned: {cat_count}\n"
                f"- Receipt ID: {result.get('receipt_id', '?')[:8]}..."
            )
        return result.get("error", "Unknown processing result.")

    if fn_name == "reconcile_bill":
        # Mismatch protocol returns a rich dict
        status = result.get("status", "")
        if status == "disabled":
            return result.get("message", "Mismatch protocol is disabled.")
        if status == "error":
            return f"Error: {result.get('message', 'Unknown error')}"
        # The mismatch protocol posts its own messages, so just confirm
        return "Mismatch reconciliation complete. Check the receipts channel for the full report."

    if fn_name == "run_auto_auth":
        # Auto-auth posts its own messages too
        authorized = result.get("authorized", 0)
        flagged = result.get("flagged", 0)
        missing = result.get("missing_info", 0)
        return (
            f"Authorization complete. "
            f"{authorized} authorized, {flagged} flagged, {missing} need info."
        )

    # Generic fallback: render dict as key-value list
    lines = []
    for k, v in result.items():
        if isinstance(v, list):
            lines.append(f"- **{k}**: {len(v)} item(s)")
        else:
            lines.append(f"- **{k}**: {v}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Personality wrapper
# ---------------------------------------------------------------------------

async def _personalize(agent_name: str, raw_text: str) -> str:
    """Pass text through gpt-5-mini for personality flavoring."""
    persona = get_persona(agent_name)
    if not persona:
        return raw_text

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return raw_text

    try:
        client = AsyncOpenAI(api_key=api_key)
        response = await client.chat.completions.create(
            model="gpt-5-mini",
            messages=[
                {"role": "system", "content": persona["conversation_prompt"]},
                {"role": "user", "content": raw_text},
            ],
            temperature=0.4,
            max_completion_tokens=500,
        )
        result = response.choices[0].message.content.strip()
        return result if result else raw_text
    except Exception as e:
        print(f"[AgentBrain:{agent_name}] Personality pass failed: {e}")
        return raw_text


async def _generate_conversation(
    agent_name: str,
    persona: Dict[str, Any],
    user_text: str,
) -> str:
    """Generate a full conversational response (for free chat when router didn't provide one)."""
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return "I'm having trouble connecting right now."

    try:
        client = AsyncOpenAI(api_key=api_key)
        response = await client.chat.completions.create(
            model="gpt-5-mini",
            messages=[
                {"role": "system", "content": persona["conversation_prompt"]},
                {"role": "user", "content": user_text},
            ],
            temperature=0.7,
            max_completion_tokens=300,
        )
        result = response.choices[0].message.content.strip()
        return result if result else "I'm not sure how to respond to that."
    except Exception as e:
        print(f"[AgentBrain:{agent_name}] Conversation generation failed: {e}")
        return "I'm having a moment. Try again."


# ---------------------------------------------------------------------------
# Message posting
# ---------------------------------------------------------------------------

async def _post_response(
    agent_name: str,
    project_id: Optional[str],
    channel_type: str,
    channel_id: Optional[str],
    content: str,
    metadata: Optional[Dict[str, Any]] = None,
) -> None:
    """Post a response message using the appropriate messenger."""
    meta = metadata or {}
    meta["agent_brain"] = True

    if agent_name == "andrew":
        from api.helpers.andrew_messenger import post_andrew_message
        post_andrew_message(
            content=content,
            project_id=project_id,
            channel_type=channel_type,
            channel_id=channel_id,
            metadata=meta,
        )
    elif agent_name == "daneel":
        from api.helpers.daneel_messenger import post_daneel_message
        # Daneel messenger requires project_id and doesn't support channel_id
        if project_id:
            post_daneel_message(
                content=content,
                project_id=project_id,
                channel_type=channel_type,
                metadata=meta,
            )
        else:
            print(f"[AgentBrain:daneel] Cannot post without project_id")


# ---------------------------------------------------------------------------
# Bill reference resolution
# ---------------------------------------------------------------------------

async def resolve_bill_reference(text: str, project_id: Optional[str]) -> Optional[str]:
    """
    Convert human-friendly bill references to UUIDs.
    Matches patterns like "bill 1456", "bill #1456", "invoice 1456".
    """
    from api.supabase_client import supabase as sb

    match = re.search(r"(?:bill|invoice)\s*#?\s*(\d+)", text, re.IGNORECASE)
    if not match:
        return None

    partial_id = match.group(1)

    try:
        query = sb.table("bills") \
            .select("bill_id") \
            .ilike("bill_id", f"%{partial_id}%")

        if project_id:
            # Bills don't have project_id directly, but we can filter via expenses
            pass

        result = query.limit(5).execute()

        if result.data and len(result.data) == 1:
            return result.data[0]["bill_id"]
        elif result.data and len(result.data) > 1:
            # Multiple matches -- return the first one but log ambiguity
            print(f"[AgentBrain] Ambiguous bill reference '{partial_id}': {len(result.data)} matches")
            return result.data[0]["bill_id"]

    except Exception as e:
        print(f"[AgentBrain] Bill resolution error: {e}")

    return None
