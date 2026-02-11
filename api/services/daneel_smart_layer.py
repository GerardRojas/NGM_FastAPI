# api/services/daneel_smart_layer.py
# ============================================================================
# Daneel Smart Layer - Intelligent Conversation & Proactive Resolution
# ============================================================================
# Adds AI personality and proactive intelligence to Daneel's expense
# authorization workflow. Instead of posting template tables, Daneel now:
#
#   1. Tries to resolve missing info before asking humans
#   2. Crafts messages with personality (watchful guardian, serene, firm)
#   3. Follows up on unresolved pending_info items
#   4. Interprets human replies to missing info requests
#
# Uses GPT for: message crafting, reply interpretation
# Does NOT use GPT for: rule engine, duplicate detection (those stay deterministic)
# ============================================================================

import os
import json
import logging
import re
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any, List, Tuple

logger = logging.getLogger(__name__)

# ============================================================================
# SYSTEM PROMPTS
# ============================================================================

_DANEEL_CONVERSATION_PROMPT = """\
You are Daneel, a budget monitoring agent for a construction company (NGM).
You communicate through a project chat system with bookkeepers, accountants, and project managers.

PERSONALITY:
- Watchful guardian. Serene, observant, calm but firm.
- You notice everything. You communicate with quiet authority.
- Think: vigilant sentinel who protects the company's finances.
- When something is wrong, you state it clearly without alarm.
- When you tried to fix something yourself, mention it briefly.

RULES:
- Preserve ALL dollar amounts, dates, vendor names, percentages, and @mentions exactly as given.
- Preserve all markdown formatting (**bold**, tables, lists).
- Keep messages concise. You are efficient, not chatty.
- Always be in English.
- Never say "I'm an AI". You are Daneel, part of the finance team.
- Return ONLY the message content. No preamble, no quotes.\
"""

_DANEEL_REPLY_PROMPT = """\
You are an assistant that interprets human replies to a finance bot named Daneel.
Daneel asked about missing expense information or flagged an issue.

CONTEXT about what Daneel might have asked:
- Missing vendor name for an expense
- Missing transaction date
- Missing receipt/bill attachment
- Missing account/category
- Duplicate expense confirmation
- Bill amount mismatch clarification

RULES:
- Return valid JSON only, no other text.
- Only include fields you can confidently extract.
- For vendor: {"vendor_name": "Home Depot"}
- For date: {"date": "2026-01-15"}
- For confirmation (yes/no): {"confirmed": true/false}
- For "it's not a duplicate": {"not_duplicate": true, "reason": "..."}
- For "already handled": {"already_resolved": true}
- For category: {"account_name": "Materials"}
- If unclear: {"unclear": true, "raw_text": "..."}\
"""


# ============================================================================
# GPT HELPERS
# ============================================================================

def _call_gpt(system_prompt: str, user_content: str, max_tokens: int = 500,
              temperature: float = 0.4, json_mode: bool = False) -> Optional[str]:
    """Sync GPT call. Returns None on failure."""
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return None
    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key)
        kwargs = {
            "model": "gpt-4o",
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
        logger.warning(f"[DaneelSmart] GPT call failed: {e}")
        return None


# ============================================================================
# 1. PROACTIVE MISSING INFO RESOLUTION
# ============================================================================

def try_resolve_missing(expense: dict, missing_fields: List[str],
                        project_id: str, bills_map: dict,
                        lookups: dict) -> Tuple[dict, List[str], List[str]]:
    """
    Try to auto-resolve missing fields before asking humans.

    Args:
        expense: The expense record
        missing_fields: List of field names from health check
        project_id: Project UUID
        bills_map: {bill_id: bill_record}
        lookups: {vendors: {id: name}, accounts: {id: name}, ...}

    Returns:
        (updates_dict, still_missing, attempts)
        - updates_dict: {field: value} to apply to expense
        - still_missing: fields that couldn't be resolved
        - attempts: human-readable list of what was tried
    """
    from api.supabase_client import supabase as sb

    updates = {}
    still_missing = []
    attempts = []

    for field in missing_fields:
        resolved = False

        if field == "vendor":
            resolved = _try_resolve_vendor(
                expense, project_id, sb, lookups, updates, attempts)

        elif field == "date":
            resolved = _try_resolve_date(
                expense, project_id, sb, updates, attempts)

        elif field == "bill_id":
            resolved = _try_resolve_bill(
                expense, project_id, sb, lookups, updates, attempts)

        elif field == "receipt":
            resolved = _try_resolve_receipt(
                expense, bills_map, sb, updates, attempts)

        elif field == "account":
            resolved = _try_resolve_account(
                expense, project_id, sb, lookups, updates, attempts)

        elif field == "amount":
            resolved = _try_resolve_amount(
                expense, sb, updates, attempts)

        if not resolved:
            still_missing.append(field)

    return updates, still_missing, attempts


def _try_resolve_vendor(expense, project_id, sb, lookups, updates, attempts) -> bool:
    """Try to find vendor from bill hint, description, or similar expenses."""
    # 1. Check bill_id -> bill record -> vendor
    bill_id = (expense.get("bill_id") or "").strip()
    if bill_id:
        try:
            bill_resp = sb.table("bills") \
                .select("vendor_name") \
                .eq("bill_id", bill_id) \
                .single() \
                .execute()
            if bill_resp.data and bill_resp.data.get("vendor_name"):
                vname = bill_resp.data["vendor_name"]
                # Resolve vendor_id
                vid = _find_vendor_id(sb, vname)
                if vid:
                    updates["vendor_id"] = vid
                    attempts.append(f"Found vendor '{vname}' from bill #{bill_id[:8]}...")
                    return True
        except Exception:
            pass

    # 2. Check description for vendor keywords
    desc = (expense.get("LineDescription") or "").strip()
    if desc and desc != "Material purchase":
        vid = _fuzzy_match_vendor(sb, desc)
        if vid:
            vname = lookups.get("vendors", {}).get(vid, "")
            updates["vendor_id"] = vid
            attempts.append(f"Matched vendor '{vname}' from description '{desc[:40]}'")
            return True

    # 3. Search similar expenses (same project, similar amount, last 30d)
    amount = float(expense.get("Amount") or 0)
    if amount > 0:
        try:
            thirty_ago = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
            tolerance = amount * 0.05
            similar = sb.table("expenses_manual_COGS") \
                .select("vendor_id") \
                .eq("project", project_id) \
                .neq("vendor_id", None) \
                .gte("Amount", amount - tolerance) \
                .lte("Amount", amount + tolerance) \
                .gt("created_at", thirty_ago) \
                .limit(3) \
                .execute()
            if similar.data:
                vid = similar.data[0].get("vendor_id")
                if vid:
                    vname = lookups.get("vendors", {}).get(vid, "Unknown")
                    updates["vendor_id"] = vid
                    attempts.append(f"Found vendor '{vname}' from similar expense (~${amount:,.2f})")
                    return True
        except Exception:
            pass

    attempts.append("Searched bill, description, and similar expenses -- vendor not found")
    return False


def _try_resolve_date(expense, project_id, sb, updates, attempts) -> bool:
    """Try to find date from bill or similar expenses."""
    bill_id = (expense.get("bill_id") or "").strip()
    if bill_id:
        try:
            bill_resp = sb.table("bills") \
                .select("bill_date") \
                .eq("bill_id", bill_id) \
                .single() \
                .execute()
            if bill_resp.data and bill_resp.data.get("bill_date"):
                updates["TxnDate"] = bill_resp.data["bill_date"]
                attempts.append(f"Found date {bill_resp.data['bill_date']} from bill")
                return True
        except Exception:
            pass

    attempts.append("No date found from bill or context")
    return False


def _try_resolve_bill(expense, project_id, sb, lookups, updates, attempts) -> bool:
    """Try to find matching bill by vendor + amount."""
    vendor_id = expense.get("vendor_id")
    amount = float(expense.get("Amount") or 0)
    if not vendor_id or amount <= 0:
        attempts.append("Cannot search bills without vendor and amount")
        return False

    vname = lookups.get("vendors", {}).get(vendor_id, "")
    try:
        tolerance = amount * 0.03
        bills_resp = sb.table("bills") \
            .select("bill_id, vendor_name, total_amount") \
            .ilike("vendor_name", f"%{vname}%") \
            .gte("total_amount", amount - tolerance) \
            .lte("total_amount", amount + tolerance) \
            .eq("status", "open") \
            .limit(3) \
            .execute()
        if bills_resp.data and len(bills_resp.data) == 1:
            matched = bills_resp.data[0]
            updates["bill_id"] = matched["bill_id"]
            attempts.append(
                f"Matched open bill #{matched['bill_id'][:8]}... "
                f"(${matched.get('total_amount', 0):,.2f})"
            )
            return True
        elif bills_resp.data and len(bills_resp.data) > 1:
            attempts.append(f"Found {len(bills_resp.data)} possible bills -- ambiguous")
    except Exception:
        pass

    attempts.append("No matching open bill found")
    return False


def _try_resolve_receipt(expense, bills_map, sb, updates, attempts) -> bool:
    """Check if bill already has a receipt we can link."""
    bill_id = (expense.get("bill_id") or "").strip()
    if bill_id and bill_id in bills_map:
        receipt_url = bills_map[bill_id].get("receipt_url")
        if receipt_url:
            attempts.append(f"Receipt already attached to bill #{bill_id[:8]}...")
            return True  # Don't need to update anything -- health check uses bills_map
    attempts.append("No receipt found on linked bill or expense")
    return False


def _try_resolve_account(expense, project_id, sb, lookups, updates, attempts) -> bool:
    """Try to infer account from description or similar expenses."""
    desc = (expense.get("LineDescription") or "").lower().strip()
    if not desc:
        attempts.append("No description to infer account from")
        return False

    # Common keyword -> account mapping for construction
    keyword_map = {
        "lumber": "Materials", "wood": "Materials", "plywood": "Materials",
        "concrete": "Materials", "drywall": "Materials", "insulation": "Materials",
        "pipe": "Materials", "wire": "Materials", "nail": "Materials",
        "screw": "Materials", "paint": "Materials", "tile": "Materials",
        "delivery": "Delivery", "shipping": "Delivery", "freight": "Delivery",
        "rental": "Equipment Rental", "rent": "Equipment Rental",
        "labor": "Labor", "worker": "Labor", "crew": "Labor",
        "permit": "Permits & Fees", "inspection": "Permits & Fees",
        "tool": "Tools", "drill": "Tools", "saw": "Tools",
    }

    for keyword, acct_name in keyword_map.items():
        if keyword in desc:
            # Find account_id
            accounts = lookups.get("accounts", {})
            for aid, aname in accounts.items():
                if aname.lower() == acct_name.lower():
                    updates["account_id"] = aid
                    attempts.append(f"Inferred account '{aname}' from description keyword '{keyword}'")
                    return True

    # Try similar expenses
    vendor_id = expense.get("vendor_id")
    if vendor_id:
        try:
            similar = sb.table("expenses_manual_COGS") \
                .select("account_id") \
                .eq("vendor_id", vendor_id) \
                .neq("account_id", None) \
                .limit(5) \
                .execute()
            if similar.data:
                # Most common account for this vendor
                acct_counts = {}
                for s in similar.data:
                    aid = s.get("account_id")
                    if aid:
                        acct_counts[aid] = acct_counts.get(aid, 0) + 1
                if acct_counts:
                    best_aid = max(acct_counts, key=acct_counts.get)
                    aname = lookups.get("accounts", {}).get(best_aid, "")
                    updates["account_id"] = best_aid
                    attempts.append(f"Inferred account '{aname}' from vendor's most common category")
                    return True
        except Exception:
            pass

    attempts.append("Could not infer account from description or vendor history")
    return False


def _try_resolve_amount(expense, sb, updates, attempts) -> bool:
    """Amount is rarely resolvable without the receipt. Just note it."""
    attempts.append("Amount cannot be auto-resolved -- requires receipt or manual entry")
    return False


def _find_vendor_id(sb, vendor_name: str) -> Optional[str]:
    """Find vendor_id by exact name match."""
    try:
        resp = sb.table("Vendors").select("id, vendor_name").execute()
        for v in (resp.data or []):
            if (v.get("vendor_name") or "").lower() == vendor_name.lower():
                return v["id"]
    except Exception:
        pass
    return None


def _fuzzy_match_vendor(sb, description: str) -> Optional[str]:
    """Try to match vendor name from expense description."""
    try:
        resp = sb.table("Vendors").select("id, vendor_name").execute()
        desc_lower = description.lower()
        for v in (resp.data or []):
            vname = (v.get("vendor_name") or "").lower()
            if vname and len(vname) > 3 and vname in desc_lower:
                return v["id"]
    except Exception:
        pass
    return None


def apply_auto_updates(sb, expense_id: str, updates: dict):
    """Apply auto-resolved field updates to an expense."""
    if not updates:
        return
    try:
        sb.table("expenses_manual_COGS") \
            .update(updates) \
            .eq("expense_id", expense_id) \
            .execute()
        logger.info(f"[DaneelSmart] Auto-updated expense {expense_id}: {list(updates.keys())}")
    except Exception as e:
        logger.error(f"[DaneelSmart] Auto-update failed for {expense_id}: {e}")


# ============================================================================
# 2. INTELLIGENT MESSAGE CRAFTING
# ============================================================================

def craft_batch_auth_message(authorized: list, lookups: dict) -> str:
    """Craft batch authorization message with Daneel personality."""
    total = sum(float(e.get("Amount") or 0) for e in authorized)
    # Build raw table
    lines = [
        f"**Expense Authorization Report**",
        f"Authorized **{len(authorized)}** expenses totaling **${total:,.2f}**",
        "",
        "| Vendor | Amount | Date | Bill # |",
        "|--------|--------|------|--------|",
    ]
    for e in authorized[:20]:
        vname = lookups["vendors"].get(e.get("vendor_id"), "Unknown")
        amt = f"${float(e.get('Amount') or 0):,.2f}"
        date = (e.get("TxnDate") or "")[:10]
        bill = e.get("bill_id") or "-"
        lines.append(f"| {vname} | {amt} | {date} | {bill} |")
    if len(authorized) > 20:
        lines.append(f"| ... and {len(authorized) - 20} more | | | |")
    lines.append("")
    lines.append("All expenses passed health check and duplicate verification.")
    raw = "\n".join(lines)

    personalized = _call_gpt(
        _DANEEL_CONVERSATION_PROMPT,
        f"Add subtle personality to this authorization report. "
        f"Keep the table and all data EXACTLY as-is. "
        f"You can adjust the header line and footer line only:\n\n{raw}",
        max_tokens=600,
        temperature=0.4,
    )
    return personalized if personalized else raw


def craft_missing_info_message(missing_list: list, lookups: dict,
                               mention_str: str, resolution_summary: dict) -> str:
    """
    Craft missing info message with context about what Daneel tried.

    resolution_summary: {
        "auto_resolved_count": N,
        "auto_resolved_fields": ["vendor", "date", ...],
        "still_missing_count": M,
        "attempts": ["Searched bills for vendor -- not found", ...]
    }
    """
    mention = mention_str if mention_str else ""

    # Build raw table
    lines = [
        f"{mention} The following expenses need additional information:".strip(),
        "",
    ]

    # Add resolution context if any
    auto_count = resolution_summary.get("auto_resolved_count", 0)
    if auto_count > 0:
        fields_str = ", ".join(resolution_summary.get("auto_resolved_fields", []))
        lines.append(
            f"*I was able to auto-resolve {auto_count} field(s) ({fields_str}) "
            f"from bills, vendor history, and expense patterns.*"
        )
        lines.append("")

    attempts = resolution_summary.get("attempts", [])
    if attempts:
        lines.append("What I tried before asking:")
        for a in attempts[:5]:
            lines.append(f"- {a}")
        lines.append("")

    lines.append("| Vendor | Amount | Date | Missing |")
    lines.append("|--------|--------|------|---------|")
    for item in missing_list[:20]:
        e = item["expense"]
        vname = lookups["vendors"].get(e.get("vendor_id"), "Unknown")
        amt = f"${float(e.get('Amount') or 0):,.2f}"
        date = (e.get("TxnDate") or "")[:10]
        fields = ", ".join(item["missing"])
        lines.append(f"| {vname} | {amt} | {date} | {fields} |")
    if len(missing_list) > 20:
        lines.append(f"| ... and {len(missing_list) - 20} more | | | |")
    lines.append("")
    lines.append("Please update these expenses with the missing information.")

    raw = "\n".join(lines)

    personalized = _call_gpt(
        _DANEEL_CONVERSATION_PROMPT,
        f"Rewrite this missing info request with personality. "
        f"Keep the table, @mentions, and all data EXACTLY as-is. "
        f"Adjust the conversational framing:\n\n{raw}",
        max_tokens=700,
        temperature=0.4,
    )
    return personalized if personalized else raw


def craft_escalation_message(escalated: list, lookups: dict,
                             mention_str: str) -> str:
    """Craft escalation message with personality."""
    mention = mention_str if mention_str else ""
    lines = [
        f"{mention} The following expenses require manual review:".strip(),
        "",
        "| Vendor | Amount | Date | Reason |",
        "|--------|--------|------|--------|",
    ]
    for item in escalated[:20]:
        e = item["expense"]
        vname = lookups["vendors"].get(e.get("vendor_id"), "Unknown")
        amt = f"${float(e.get('Amount') or 0):,.2f}"
        date = (e.get("TxnDate") or "")[:10]
        reason = item["reason"]
        lines.append(f"| {vname} | {amt} | {date} | {reason} |")
    if len(escalated) > 20:
        lines.append(f"| ... and {len(escalated) - 20} more | | | |")
    raw = "\n".join(lines)

    personalized = _call_gpt(
        _DANEEL_CONVERSATION_PROMPT,
        f"Rewrite this escalation alert with personality. Keep the table "
        f"and @mentions EXACTLY as-is. Be firm but not alarming:\n\n{raw}",
        max_tokens=600,
        temperature=0.4,
    )
    return personalized if personalized else raw


def craft_followup_message(pending_items: list, lookups: dict,
                           hours_pending: int) -> str:
    """Craft a follow-up message for unresolved pending info."""
    count = len(pending_items)
    lines = []
    for item in pending_items[:5]:
        eid = item.get("expense_id", "?")
        fields = ", ".join(item.get("missing_fields", []))
        lines.append(f"- Expense {eid[:8]}...: missing {fields}")
    items_str = "\n".join(lines)

    raw = (
        f"Following up on {count} expense(s) still waiting for info "
        f"({hours_pending} hours pending).\n\n{items_str}\n\n"
        f"These expenses cannot be authorized until the missing data is provided."
    )

    personalized = _call_gpt(
        _DANEEL_CONVERSATION_PROMPT,
        f"Write a firm but respectful follow-up message. "
        f"Keep all data exact:\n\n{raw}",
        max_tokens=300,
        temperature=0.4,
    )
    return personalized if personalized else raw


# ============================================================================
# 3. REPLY INTERPRETATION
# ============================================================================

def interpret_reply(reply_text: str, context: dict) -> dict:
    """
    Interpret a human reply to Daneel's message.

    context: {
        "message_type": "missing_info" | "escalation" | "mismatch",
        "expense_ids": [...],
        "missing_fields": [...],
    }
    """
    context_str = json.dumps(context, default=str)
    result = _call_gpt(
        _DANEEL_REPLY_PROMPT,
        f"CONTEXT:\n{context_str}\n\nHUMAN REPLY:\n{reply_text}\n\nExtract structured data.",
        max_tokens=300,
        temperature=0.1,
        json_mode=True,
    )

    if result:
        try:
            return json.loads(result)
        except json.JSONDecodeError:
            pass

    # Fallback: simple keyword matching
    lower = reply_text.strip().lower()
    if lower in ("done", "fixed", "updated", "resolved", "handled"):
        return {"already_resolved": True}
    if lower in ("yes", "ok", "confirmed", "approve"):
        return {"confirmed": True}
    if lower in ("no", "wrong", "not a duplicate", "not duplicate"):
        return {"not_duplicate": True}

    return {"unclear": True, "raw_text": reply_text}


def craft_reply_acknowledgment(interpretation: dict, context: dict) -> Optional[str]:
    """Craft Daneel's response to a human reply. Returns None if no response needed."""
    if interpretation.get("already_resolved"):
        return _call_gpt(
            _DANEEL_CONVERSATION_PROMPT,
            "The human said they already fixed the issue. "
            "Acknowledge briefly. Say you'll re-check on the next run.",
            max_tokens=100,
        ) or "Noted. I'll verify on the next authorization run."

    if interpretation.get("confirmed"):
        return None  # Caller handles the action

    if interpretation.get("not_duplicate"):
        reason = interpretation.get("reason", "")
        return _call_gpt(
            _DANEEL_CONVERSATION_PROMPT,
            f"The human says an expense is not a duplicate. "
            f"Reason: '{reason}'. Acknowledge and say you'll re-evaluate.",
            max_tokens=100,
        ) or "Understood. I'll re-evaluate this expense."

    if interpretation.get("unclear"):
        return _call_gpt(
            _DANEEL_CONVERSATION_PROMPT,
            "The human's reply is unclear. Ask them to be more specific "
            "about which expense and what information they're providing.",
            max_tokens=150,
        ) or "I didn't catch that. Could you specify which expense you're referring to?"

    # Extracted fields -- acknowledge
    extracted = {k: v for k, v in interpretation.items()
                 if k not in ("unclear", "raw_text", "confirmed",
                              "already_resolved", "not_duplicate")}
    if extracted:
        fields_str = ", ".join(f"{k}: {v}" for k, v in extracted.items())
        return _call_gpt(
            _DANEEL_CONVERSATION_PROMPT,
            f"The human provided: {fields_str}. "
            f"Acknowledge briefly and confirm you'll apply the update.",
            max_tokens=100,
        ) or f"Noted -- {fields_str}. Applying update."

    return None


# ============================================================================
# 4. FOLLOW-UP CHECKER
# ============================================================================

def check_pending_followups(followup_hours: int = 24,
                            escalation_hours: int = 48) -> List[dict]:
    """
    Check daneel_pending_info for items needing follow-up.

    Returns list of items with action needed:
    - "followup": first reminder
    - "escalate": second reminder + escalation
    - "stale": mark as stale (72h+)
    """
    from api.supabase_client import supabase as sb

    results = []
    now = datetime.now(timezone.utc)

    try:
        pending = sb.table("daneel_pending_info") \
            .select("expense_id, project_id, missing_fields, requested_at, message_id") \
            .is_("resolved_at", "null") \
            .execute()

        for item in (pending.data or []):
            requested_str = item.get("requested_at")
            if not requested_str:
                continue

            try:
                requested_at = datetime.fromisoformat(
                    requested_str.replace("Z", "+00:00"))
                if requested_at.tzinfo is None:
                    requested_at = requested_at.replace(tzinfo=timezone.utc)
            except (ValueError, TypeError):
                continue

            hours = (now - requested_at).total_seconds() / 3600

            action = None
            if hours >= 72:
                action = "stale"
            elif hours >= escalation_hours:
                action = "escalate"
            elif hours >= followup_hours:
                action = "followup"

            if action:
                results.append({
                    "expense_id": item["expense_id"],
                    "project_id": item.get("project_id"),
                    "missing_fields": item.get("missing_fields", []),
                    "hours_pending": round(hours, 1),
                    "action": action,
                })

    except Exception as e:
        logger.error(f"[DaneelSmart] Follow-up check failed: {e}")

    return results


def execute_followups(followups: list, lookups: dict = None) -> dict:
    """Execute follow-up actions for pending info items."""
    from api.supabase_client import supabase as sb
    from api.helpers.daneel_messenger import post_daneel_message

    stats = {"followups_sent": 0, "escalations_sent": 0, "marked_stale": 0}

    # Group by project for batch messages
    by_project = {}
    for item in followups:
        pid = item.get("project_id")
        if pid:
            by_project.setdefault(pid, {"followup": [], "escalate": [], "stale": []})
            by_project[pid][item["action"]].append(item)

    for pid, actions in by_project.items():
        try:
            # Follow-up reminders
            if actions["followup"]:
                msg = craft_followup_message(
                    actions["followup"],
                    lookups or {},
                    int(actions["followup"][0]["hours_pending"]),
                )
                post_daneel_message(
                    content=msg,
                    project_id=pid,
                    metadata={
                        "type": "auto_auth_followup",
                        "count": len(actions["followup"]),
                    }
                )
                stats["followups_sent"] += len(actions["followup"])

            # Escalations
            if actions["escalate"]:
                mentions = _get_escalation_mentions(sb)
                msg = craft_followup_message(
                    actions["escalate"],
                    lookups or {},
                    int(actions["escalate"][0]["hours_pending"]),
                )
                escalation_msg = f"{mentions} {msg}" if mentions else msg
                post_daneel_message(
                    content=escalation_msg,
                    project_id=pid,
                    metadata={
                        "type": "auto_auth_escalation_followup",
                        "count": len(actions["escalate"]),
                    }
                )
                stats["escalations_sent"] += len(actions["escalate"])

            # Stale markers (just log, don't nag further)
            stats["marked_stale"] += len(actions["stale"])

        except Exception as e:
            logger.error(f"[DaneelSmart] Follow-up execution failed for project {pid}: {e}")

    return stats


def _get_escalation_mentions(sb) -> str:
    """Get @mentions for escalation users/roles."""
    try:
        from api.services.daneel_auto_auth import load_auto_auth_config
        cfg = load_auto_auth_config()

        # Try user-based mentions first
        users_json = cfg.get("daneel_accounting_mgr_users", "[]")
        if users_json and users_json != "[]":
            try:
                ids = json.loads(users_json) if isinstance(users_json, str) else users_json
                if ids:
                    result = sb.table("users").select("user_name").in_("user_id", ids).execute()
                    names = [u["user_name"] for u in (result.data or []) if u.get("user_name")]
                    if names:
                        return " ".join(f"@{n}" for n in names)
            except Exception:
                pass

        # Fallback to role
        role_id = cfg.get("daneel_accounting_mgr_role")
        if role_id:
            result = sb.table("rols").select("rol_name").eq("rol_id", role_id).single().execute()
            if result.data:
                return f"@{result.data['rol_name']}"
    except Exception:
        pass
    return ""
