# api/services/andrew_smart_layer.py
# ============================================================================
# Andrew Smart Layer - Intelligent Conversation & Proactive Info Resolution
# ============================================================================
# Adds an AI personality and proactive intelligence layer to Andrew's receipt
# processing workflow. Instead of posting generic messages, Andrew now:
#
#   1. Analyzes what's missing/unclear and tries to resolve it automatically
#   2. Crafts conversational messages with personality (dry wit, competent)
#   3. Understands human replies in natural language
#   4. Follows up on unanswered questions
#
# Uses GPT for: message crafting, reply interpretation, info resolution
# Does NOT use GPT for: OCR (handled by receipt_scanner), categorization
# ============================================================================

import os
import json
import logging
import re
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any, List

logger = logging.getLogger(__name__)

# ============================================================================
# SYSTEM PROMPTS
# ============================================================================

_ANDREW_CONVERSATION_PROMPT = """\
You are Andrew, a receipt processing agent for a construction company (NGM).
You communicate through a project chat system with bookkeepers and project managers.

PERSONALITY:
- Dry, slightly witty. Efficient and competent.
- You don't waste words, but allow yourself the occasional wry observation.
- Think: competent accountant who's seen it all.
- You are helpful and proactive -- you anticipate what people need.
- When something is missing, you explain what you tried before asking.

RULES:
- Preserve ALL dollar amounts, dates, vendor names exactly as given.
- Preserve all markdown formatting (**bold**, *italic*), links, and lists.
- Keep messages concise. Max 4-5 sentences for regular updates.
- Always be in English.
- When asking for info, be specific about what you need and why.
- If you tried to find something and failed, briefly mention what you tried.
- Never say "I'm an AI" or "As an AI". You are Andrew, part of the team.
- Return ONLY the message content. No preamble, no quotes.\
"""

_REPLY_INTERPRETER_PROMPT = """\
You are an assistant that interprets human replies in the context of a receipt processing workflow.
A bot named Andrew asked a question about an expense or receipt, and a human replied.

Your job: extract structured data from the reply. Return a JSON object with the fields you can identify.

CONTEXT about what Andrew might have asked:
- Vendor name (who sold the items)
- Check number (for labor checks)
- Category/account (Materials, Labor, Equipment, etc.)
- Whether the bill is for this project only or split
- Date of the transaction
- Any clarification about line items

RULES:
- Return valid JSON only, no other text.
- Only include fields you can confidently extract from the reply.
- For vendor: {"vendor_name": "Home Depot"}
- For check number: {"check_number": "12345"}
- For category corrections: {"category_corrections": {"1": "Materials", "3": "Labor"}}
- For project confirmation: {"all_this_project": true} or {"all_this_project": false}
- For split info: {"split_items": "3, 4 to Sunset Heights"}
- For date: {"receipt_date": "2026-01-15"}
- If the reply is unclear or unrelated, return: {"unclear": true, "raw_text": "..."}
- If the user says "yes", "ok", "correct", "all good" in response to a confirmation: {"confirmed": true}
- If "no" or "that's wrong": {"confirmed": false}\
"""

_MISSING_INFO_RESOLVER_PROMPT = """\
You are analyzing a receipt processing result to determine what information is missing
and what actions to take. Given the extracted data, identify gaps and suggest resolution steps.

Return a JSON object:
{
    "missing_fields": ["vendor", "date", ...],
    "resolution_attempts": [
        {"field": "vendor", "method": "bill_hint", "result": "found", "value": "Home Depot"},
        {"field": "date", "method": "similar_expenses", "result": "not_found"}
    ],
    "can_auto_resolve": true/false,
    "message_to_human": "Brief, specific question about what's still needed",
    "severity": "low|medium|high"
}

RULES:
- Only flag fields that are genuinely missing or clearly wrong.
- "Unknown" vendor = missing. Null date = missing. $0 amount = missing.
- If vendor is missing but bill_id hint has a vendor, that's a resolution.
- severity: low = cosmetic (missing date), medium = needs human (unknown vendor), high = blocking (no amount)
- Return valid JSON only.\
"""


# ============================================================================
# GPT HELPERS
# ============================================================================

def _call_gpt(system_prompt: str, user_content: str, max_tokens: int = 400,
              temperature: float = 0.4, json_mode: bool = False) -> Optional[str]:
    """Sync GPT call. Returns None on failure."""
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return None
    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key)
        kwargs = {
            "model": "gpt-5-mini",
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        response = client.chat.completions.create(**kwargs)
        return response.choices[0].message.content.strip()
    except Exception as e:
        logger.warning(f"[AndrewSmart] GPT call failed: {e}")
        return None


# ============================================================================
# 1. SMART MISSING INFO ANALYSIS
# ============================================================================

def analyze_missing_info(parsed_data: dict, receipt_data: dict,
                         project_id: str) -> dict:
    """
    Analyze what's missing from extracted receipt data and attempt to resolve it
    using available context (bill hints, similar expenses, vendor DB).

    Returns:
        {
            "missing_fields": [...],
            "resolutions": {"vendor": "Home Depot", ...},
            "unresolved": ["date"],
            "message_context": "...",  # context string for message crafting
        }
    """
    from api.supabase_client import supabase

    vendor_name = parsed_data.get("vendor_name", "Unknown")
    amount = parsed_data.get("amount", 0)
    receipt_date = parsed_data.get("receipt_date")
    vendor_id = parsed_data.get("vendor_id")
    bill_id = parsed_data.get("bill_id")

    missing = []
    resolutions = {}
    attempts = []

    # -- Check vendor --
    if not vendor_name or vendor_name == "Unknown":
        missing.append("vendor")

        # Attempt 1: Bill hint from filename
        bill_hint = parsed_data.get("bill_hint", {})
        if bill_hint.get("vendor_hint"):
            resolutions["vendor"] = bill_hint["vendor_hint"]
            attempts.append(f"Found vendor '{bill_hint['vendor_hint']}' from filename")
        else:
            # Attempt 2: Search similar expenses (same project, similar amount, last 30d)
            if amount and amount > 0:
                try:
                    thirty_days_ago = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
                    tolerance = amount * 0.05
                    similar = supabase.table("expenses_manual_COGS") \
                        .select("vendor_id, LineDescription") \
                        .eq("project", project_id) \
                        .gte("Amount", amount - tolerance) \
                        .lte("Amount", amount + tolerance) \
                        .gt("created_at", thirty_days_ago) \
                        .limit(3) \
                        .execute()
                    if similar.data:
                        # Get vendor name from first match
                        vid = similar.data[0].get("vendor_id")
                        if vid:
                            v_resp = supabase.table("Vendors") \
                                .select("vendor_name") \
                                .eq("id", vid) \
                                .single() \
                                .execute()
                            if v_resp.data and v_resp.data.get("vendor_name"):
                                resolutions["vendor"] = v_resp.data["vendor_name"]
                                resolutions["vendor_id"] = vid
                                attempts.append(
                                    f"Found similar expense (~${amount:,.2f}) from "
                                    f"'{v_resp.data['vendor_name']}' in this project"
                                )
                except Exception as e:
                    logger.warning(f"[AndrewSmart] Similar expense lookup failed: {e}")
                    attempts.append("Searched similar expenses -- no match")

    # -- Check vendor_id (vendor known by name but not in DB) --
    if vendor_name and vendor_name != "Unknown" and not vendor_id and "vendor" not in resolutions:
        missing.append("vendor_not_in_db")
        attempts.append(f"Vendor '{vendor_name}' not found in vendor database")

    # -- Check amount --
    if not amount or amount <= 0:
        missing.append("amount")
        # Try bill hint
        bill_hint = parsed_data.get("bill_hint", {})
        if bill_hint.get("amount_hint") and bill_hint["amount_hint"] > 0:
            resolutions["amount"] = bill_hint["amount_hint"]
            attempts.append(f"Found amount ${bill_hint['amount_hint']:,.2f} from filename")
        else:
            attempts.append("Could not determine amount from receipt or filename")

    # -- Check date --
    if not receipt_date:
        missing.append("date")
        bill_hint = parsed_data.get("bill_hint", {})
        if bill_hint.get("date_hint"):
            resolutions["date"] = bill_hint["date_hint"]
            attempts.append(f"Found date {bill_hint['date_hint']} from filename")
        else:
            attempts.append("No date found on receipt or filename")

    # -- Check bill linkage --
    if not bill_id and vendor_name and vendor_name != "Unknown" and amount and amount > 0:
        # Try to find a matching bill
        try:
            tolerance = amount * 0.02
            bills_resp = supabase.table("bills") \
                .select("bill_id, vendor_name, total_amount, bill_date") \
                .ilike("vendor_name", f"%{vendor_name}%") \
                .gte("total_amount", amount - tolerance) \
                .lte("total_amount", amount + tolerance) \
                .eq("status", "open") \
                .limit(3) \
                .execute()
            if bills_resp.data and len(bills_resp.data) == 1:
                # Unique match -- auto-link
                matched_bill = bills_resp.data[0]
                resolutions["bill_id"] = matched_bill["bill_id"]
                attempts.append(
                    f"Found matching open bill #{matched_bill['bill_id'][:8]}... "
                    f"(${matched_bill.get('total_amount', 0):,.2f} from "
                    f"{matched_bill.get('vendor_name', 'vendor')})"
                )
            elif bills_resp.data and len(bills_resp.data) > 1:
                attempts.append(
                    f"Found {len(bills_resp.data)} possible bills -- "
                    "needs manual selection"
                )
        except Exception as e:
            logger.warning(f"[AndrewSmart] Bill lookup failed: {e}")

    unresolved = [f for f in missing if f not in resolutions and f != "vendor_not_in_db"]

    # Build context string for message crafting
    context_parts = []
    for a in attempts:
        context_parts.append(f"- {a}")
    message_context = "\n".join(context_parts) if context_parts else ""

    return {
        "missing_fields": missing,
        "resolutions": resolutions,
        "unresolved": unresolved,
        "attempts": attempts,
        "message_context": message_context,
    }


def apply_resolutions(parsed_data: dict, resolutions: dict) -> dict:
    """Apply auto-resolved values back into parsed_data."""
    updated = {**parsed_data}

    if "vendor" in resolutions:
        updated["vendor_name"] = resolutions["vendor"]
        if "vendor_id" in resolutions:
            updated["vendor_id"] = resolutions["vendor_id"]
        # Update first line item too
        if updated.get("line_items") and len(updated["line_items"]) > 0:
            updated["line_items"][0]["vendor"] = resolutions["vendor"]

    if "amount" in resolutions:
        updated["amount"] = resolutions["amount"]

    if "date" in resolutions:
        updated["receipt_date"] = resolutions["date"]
        if updated.get("line_items") and len(updated["line_items"]) > 0:
            updated["line_items"][0]["date"] = resolutions["date"]

    if "bill_id" in resolutions:
        updated["bill_id"] = resolutions["bill_id"]

    return updated


# ============================================================================
# 2. INTELLIGENT MESSAGE CRAFTING
# ============================================================================

def craft_receipt_message(parsed_data: dict, categorize_data: dict,
                          warnings: list, analysis: dict,
                          project_name: str = "this project",
                          flow_state: str = "awaiting_item_selection") -> str:
    """
    Craft Andrew's receipt processing message with personality and context.
    Uses GPT to add personality to the structured data.

    Falls back to a well-formatted template if GPT fails.
    """
    vendor = parsed_data.get("vendor_name") or "Unknown"
    amount = parsed_data.get("amount") or 0
    date = parsed_data.get("receipt_date") or "unknown date"
    category = (categorize_data.get("account_name")
                or parsed_data.get("suggested_category") or "Uncategorized")
    confidence = int(categorize_data.get("confidence", 0))
    line_items = parsed_data.get("line_items", [])
    resolutions = analysis.get("resolutions", {})
    unresolved = analysis.get("unresolved", [])
    attempts = analysis.get("attempts", [])

    # Build the raw data block for GPT to personalize
    raw_parts = []

    # Header
    raw_parts.append(f"Receipt scanned: **{vendor}** -- ${amount:,.2f} on {date}.")

    # Show what was auto-resolved
    if resolutions:
        resolved_notes = []
        for field, value in resolutions.items():
            if field == "vendor":
                resolved_notes.append(f"vendor identified as '{value}' from context")
            elif field == "bill_id":
                resolved_notes.append(f"matched to bill #{str(value)[:8]}...")
            elif field == "date":
                resolved_notes.append(f"date found from filename: {value}")
            elif field == "amount":
                resolved_notes.append(f"amount from filename: ${value:,.2f}")
        if resolved_notes:
            raw_parts.append("Auto-resolved: " + "; ".join(resolved_notes) + ".")

    # Line items (if multiple)
    if len(line_items) > 1:
        item_lines = []
        for i, item in enumerate(line_items):
            desc = (item.get("description") or "Item")[:60]
            item_amt = item.get("amount", 0)
            cat = item.get("account_name") or category
            conf = item.get("confidence", confidence)
            item_lines.append(f"{i+1}. {desc} -- ${item_amt:,.2f} -> {cat} ({conf}%)")
        raw_parts.append("\n".join(item_lines))
    else:
        raw_parts.append(f"Category: **{category}** ({confidence}% confidence)")

    # Warnings
    if warnings:
        raw_parts.append("Heads up:\n" + "\n".join(f"- {w}" for w in warnings))

    # What's still missing (proactive)
    if unresolved:
        missing_asks = []
        for field in unresolved:
            if field == "vendor":
                missing_asks.append("Who is the vendor? I checked the filename and "
                                    "similar expenses but couldn't determine it.")
            elif field == "amount":
                missing_asks.append("What's the total amount? The receipt was unclear "
                                    "and the filename didn't have it either.")
            elif field == "date":
                missing_asks.append("What's the transaction date? I couldn't find it "
                                    "on the receipt or filename.")
        if missing_asks:
            raw_parts.append("I need help with:\n" + "\n".join(f"- {q}" for q in missing_asks))

    # Project question (only if no blocking missing info)
    if flow_state == "awaiting_item_selection" and "amount" not in unresolved:
        raw_parts.append(f"Is this entire bill for **{project_name}**?")

    raw_content = "\n\n".join(raw_parts)

    # Try GPT personality pass
    personalized = _call_gpt(
        _ANDREW_CONVERSATION_PROMPT,
        f"Rewrite this receipt processing message with my personality. "
        f"Keep ALL data exactly as-is. Just adjust the tone:\n\n{raw_content}",
        max_tokens=500,
        temperature=0.5,
    )

    return personalized if personalized else raw_content


def craft_followup_message(receipt_data: dict, pending_hours: int,
                           original_question: str) -> str:
    """
    Craft a follow-up message when a question hasn't been answered.
    """
    vendor = (receipt_data.get("parsed_data") or {}).get("vendor_name", "a receipt")
    amount = (receipt_data.get("parsed_data") or {}).get("amount", 0)

    raw = (
        f"Following up on the {vendor} receipt (${amount:,.2f}). "
        f"It's been {pending_hours} hours since I asked. "
        f"Original question: {original_question}\n"
        f"This receipt is pending and can't be processed until I get this info."
    )

    personalized = _call_gpt(
        _ANDREW_CONVERSATION_PROMPT,
        f"Write a brief, polite follow-up message. Don't be annoying. "
        f"Keep the data exact:\n\n{raw}",
        max_tokens=200,
        temperature=0.5,
    )

    return personalized if personalized else raw


def craft_escalation_message(receipt_data: dict, mentions: str,
                             issue: str, attempts: list) -> str:
    """
    Craft an escalation message when Andrew needs bookkeeping help.
    """
    vendor = (receipt_data.get("parsed_data") or {}).get("vendor_name", "Unknown")
    amount = (receipt_data.get("parsed_data") or {}).get("amount", 0)

    attempts_str = "\n".join(f"- {a}" for a in attempts) if attempts else "None"
    raw = (
        f"{mentions} -- Need help with a receipt from **{vendor}** (${amount:,.2f}).\n\n"
        f"Issue: {issue}\n\n"
        f"What I tried:\n{attempts_str}\n\n"
        "Manual review needed."
    )

    personalized = _call_gpt(
        _ANDREW_CONVERSATION_PROMPT,
        f"Rewrite this escalation message. Keep it professional but with personality. "
        f"Keep ALL data and @mentions exactly:\n\n{raw}",
        max_tokens=300,
        temperature=0.4,
    )

    return personalized if personalized else raw


# ============================================================================
# 3. REPLY INTERPRETATION
# ============================================================================

def interpret_reply(reply_text: str, context: dict) -> dict:
    """
    Interpret a human's reply to Andrew's question using GPT.

    Args:
        reply_text: The human's message text
        context: {
            "flow_state": "awaiting_item_selection" | "awaiting_category_confirmation" | ...,
            "original_question": "What Andrew asked",
            "vendor_name": "...",
            "amount": ...,
            "missing_fields": [...],
        }

    Returns:
        Parsed dict with extracted fields, e.g.:
        {"confirmed": True} or {"vendor_name": "Home Depot"} or {"unclear": True}
    """
    context_str = json.dumps(context, default=str)
    user_content = (
        f"CONTEXT:\n{context_str}\n\n"
        f"HUMAN REPLY:\n{reply_text}\n\n"
        "Extract structured data from this reply."
    )

    result = _call_gpt(
        _REPLY_INTERPRETER_PROMPT,
        user_content,
        max_tokens=300,
        temperature=0.1,
        json_mode=True,
    )

    if result:
        try:
            return json.loads(result)
        except json.JSONDecodeError:
            logger.warning(f"[AndrewSmart] Reply parse failed: {result}")

    # Fallback: simple keyword matching
    lower = reply_text.strip().lower()
    if lower in ("yes", "ok", "correct", "all correct", "si", "all good",
                 "yep", "yeah", "confirmed", "that's right"):
        return {"confirmed": True}
    if lower in ("no", "wrong", "incorrect", "nope"):
        return {"confirmed": False}

    return {"unclear": True, "raw_text": reply_text}


def craft_reply_response(interpretation: dict, context: dict) -> Optional[str]:
    """
    Craft Andrew's response after interpreting a human reply.
    Returns None if no response is needed (e.g., confirmation leads to action).
    """
    if interpretation.get("confirmed") is True:
        return None  # Caller handles the action (create expense, etc.)

    if interpretation.get("confirmed") is False:
        return _call_gpt(
            _ANDREW_CONVERSATION_PROMPT,
            "The user said my analysis is wrong. Ask what specifically needs "
            "to be corrected. Be brief and specific.",
            max_tokens=100,
        ) or "Got it -- what needs to be corrected?"

    if interpretation.get("unclear"):
        raw = interpretation.get("raw_text", "")
        state = context.get("flow_state", "")
        hint = ""
        if state == "awaiting_item_selection":
            hint = (
                "I didn't quite catch that. I need to know: is this entire bill "
                "for this project, or should some items go to a different project?\n\n"
                "You can say **yes** (all here), or tell me which items go elsewhere "
                "(e.g., '3, 4 to Project Name')."
            )
        elif state == "awaiting_category_confirmation":
            hint = (
                "I didn't catch that. I need the correct category for the flagged items.\n"
                "Format: `1 Materials, 3 Labor` -- or say **all correct** to accept."
            )
        elif "vendor" in context.get("missing_fields", []):
            hint = "I still need the vendor name for this receipt. Who sold these items?"
        else:
            hint = "I didn't understand that. Could you be more specific?"

        return _call_gpt(
            _ANDREW_CONVERSATION_PROMPT,
            f"The user replied '{raw}' but I couldn't understand it in context. "
            f"Rewrite this clarification with personality:\n\n{hint}",
            max_tokens=200,
        ) or hint

    # Extracted specific fields -- acknowledge and apply
    extracted = {k: v for k, v in interpretation.items()
                 if k not in ("unclear", "raw_text", "confirmed")}
    if extracted:
        fields_str = ", ".join(f"{k}: {v}" for k, v in extracted.items())
        return _call_gpt(
            _ANDREW_CONVERSATION_PROMPT,
            f"The user provided this info: {fields_str}. "
            f"Acknowledge briefly and confirm you've updated the record.",
            max_tokens=150,
        ) or f"Got it -- updated: {fields_str}."

    return None


# ============================================================================
# 4. FOLLOW-UP CHECKER
# ============================================================================

def check_pending_followups() -> List[dict]:
    """
    Check for receipts awaiting human response for too long.
    Returns list of receipts that need follow-up.

    Thresholds:
    - 24h: first follow-up
    - 48h: second follow-up + escalation
    - 72h: mark as stale in report
    """
    from api.supabase_client import supabase

    results = []
    now = datetime.now(timezone.utc)

    try:
        # Get all receipts in active flow states
        pending = supabase.table("pending_receipts") \
            .select("id, project_id, parsed_data, updated_at, file_name") \
            .eq("status", "ready") \
            .execute()

        for receipt in (pending.data or []):
            parsed = receipt.get("parsed_data") or {}
            flow = parsed.get("receipt_flow") or {}
            state = flow.get("state", "")

            # Only check active awaiting states
            if state not in ("awaiting_item_selection",
                             "awaiting_category_confirmation",
                             "awaiting_check_number"):
                continue

            # Calculate hours since last update
            updated_str = receipt.get("updated_at") or flow.get("started_at")
            if not updated_str:
                continue

            try:
                updated_at = datetime.fromisoformat(
                    updated_str.replace("Z", "+00:00"))
                if updated_at.tzinfo is None:
                    updated_at = updated_at.replace(tzinfo=timezone.utc)
            except (ValueError, TypeError):
                continue

            hours_pending = (now - updated_at).total_seconds() / 3600
            followup_count = flow.get("followup_count", 0)

            action = None
            if hours_pending >= 72 and followup_count >= 2:
                action = "stale"
            elif hours_pending >= 48 and followup_count >= 1:
                action = "escalate"
            elif hours_pending >= 24 and followup_count == 0:
                action = "followup"

            if action:
                results.append({
                    "receipt_id": receipt["id"],
                    "project_id": receipt["project_id"],
                    "parsed_data": parsed,
                    "hours_pending": round(hours_pending, 1),
                    "followup_count": followup_count,
                    "action": action,
                    "flow_state": state,
                    "file_name": receipt.get("file_name"),
                })

    except Exception as e:
        logger.error(f"[AndrewSmart] Follow-up check failed: {e}")

    return results


def execute_followups(followups: list) -> dict:
    """
    Execute follow-up actions for pending receipts.
    Posts messages and updates follow-up counters.
    """
    from api.supabase_client import supabase
    from api.helpers.andrew_messenger import post_andrew_message

    stats = {"followups_sent": 0, "escalations_sent": 0, "marked_stale": 0}

    for item in followups:
        receipt_id = item["receipt_id"]
        project_id = item["project_id"]
        parsed = item["parsed_data"]
        action = item["action"]
        state = item["flow_state"]
        hours = item["hours_pending"]

        # Determine what was originally asked
        original_q = ""
        if state == "awaiting_item_selection":
            original_q = "Is this entire bill for this project?"
        elif state == "awaiting_category_confirmation":
            original_q = "Category confirmation for flagged items"
        elif state == "awaiting_check_number":
            original_q = "Check number for labor check"

        try:
            if action == "followup":
                msg = craft_followup_message(
                    {"parsed_data": parsed},
                    int(hours),
                    original_q,
                )
                post_andrew_message(
                    content=msg,
                    project_id=project_id,
                    metadata={
                        "agent_message": True,
                        "pending_receipt_id": receipt_id,
                        "followup": True,
                        "followup_number": 1,
                    }
                )
                # Update counter
                flow = parsed.get("receipt_flow", {})
                flow["followup_count"] = 1
                flow["last_followup_at"] = datetime.now(timezone.utc).isoformat()
                parsed["receipt_flow"] = flow
                supabase.table("pending_receipts").update({
                    "parsed_data": parsed,
                    "updated_at": datetime.utcnow().isoformat(),
                }).eq("id", receipt_id).execute()
                stats["followups_sent"] += 1

            elif action == "escalate":
                mentions = _get_bookkeeping_mentions_safe()
                msg = craft_escalation_message(
                    {"parsed_data": parsed},
                    mentions,
                    f"No response for {int(hours)} hours on: {original_q}",
                    [f"Sent first follow-up {int(hours - 24)}h ago", "No reply received"],
                )
                post_andrew_message(
                    content=msg,
                    project_id=project_id,
                    metadata={
                        "agent_message": True,
                        "pending_receipt_id": receipt_id,
                        "escalation": "no_response",
                        "hours_pending": hours,
                    }
                )
                flow = parsed.get("receipt_flow", {})
                flow["followup_count"] = 2
                flow["escalated_at"] = datetime.now(timezone.utc).isoformat()
                parsed["receipt_flow"] = flow
                supabase.table("pending_receipts").update({
                    "parsed_data": parsed,
                    "updated_at": datetime.utcnow().isoformat(),
                }).eq("id", receipt_id).execute()
                stats["escalations_sent"] += 1

            elif action == "stale":
                flow = parsed.get("receipt_flow", {})
                flow["state"] = "stale"
                flow["marked_stale_at"] = datetime.now(timezone.utc).isoformat()
                parsed["receipt_flow"] = flow
                supabase.table("pending_receipts").update({
                    "parsed_data": parsed,
                    "updated_at": datetime.utcnow().isoformat(),
                }).eq("id", receipt_id).execute()
                stats["marked_stale"] += 1

        except Exception as e:
            logger.error(f"[AndrewSmart] Follow-up failed for {receipt_id}: {e}")

    return stats


def _get_bookkeeping_mentions_safe() -> str:
    """Get bookkeeping @mentions. Never fails."""
    try:
        from api.supabase_client import supabase
        result = supabase.table("users") \
            .select("user_name, rols!users_user_rol_fkey(rol_name)") \
            .execute()
        mentions = []
        for u in (result.data or []):
            role = u.get("rols") or {}
            if role.get("rol_name") in ("Bookkeeper", "Accounting Manager"):
                name = (u.get("user_name") or "").replace(" ", "")
                if name:
                    mentions.append(f"@{name}")
        return " ".join(mentions) if mentions else ""
    except Exception:
        return ""
