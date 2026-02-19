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
    """Sync GPT call via gpt-5-mini. Returns None on failure."""
    from api.services.gpt_client import gpt
    return gpt.mini(system_prompt, user_content, json_mode=json_mode, max_tokens=max_tokens)


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
    # Group by vendor
    from collections import OrderedDict
    groups = OrderedDict()
    for e in authorized:
        vid = e.get("vendor_id") or "unknown"
        vname = lookups["vendors"].get(vid, "Unknown")
        if vname not in groups:
            groups[vname] = {"count": 0, "total": 0.0}
        groups[vname]["count"] += 1
        groups[vname]["total"] += float(e.get("Amount") or 0)

    lines = [
        f"**Expense Authorization Report**",
        f"Authorized **{len(authorized)}** expenses totaling **${total:,.2f}**",
        "",
    ]
    for vname, g in list(groups.items())[:20]:
        lines.append(f"**{vname}** - {g['count']} item{'s' if g['count'] != 1 else ''} (${g['total']:,.2f})")
    if len(groups) > 20:
        lines.append(f"...and {len(groups) - 20} more vendors")
    lines.append("")
    lines.append("All expenses passed health check and duplicate verification.")
    raw = "\n".join(lines)

    personalized = _call_gpt(
        _DANEEL_CONVERSATION_PROMPT,
        f"Add subtle personality to this authorization report. "
        f"Keep ALL bold vendor lines and the total summary EXACTLY as-is. "
        f"You can only adjust the opening header and closing footer text. "
        f"Do NOT add tables:\n\n{raw}",
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

    lines = [
        f"{mention} {len(missing_list)} expense{'s' if len(missing_list) != 1 else ''} need additional info:".strip(),
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

    # Group by vendor, merge missing fields
    from collections import OrderedDict
    groups = OrderedDict()
    for item in missing_list:
        e = item["expense"]
        vid = e.get("vendor_id") or "unknown"
        vname = lookups["vendors"].get(vid, "Unknown")
        if vname not in groups:
            groups[vname] = {"count": 0, "total": 0.0, "missing": set()}
        groups[vname]["count"] += 1
        groups[vname]["total"] += float(e.get("Amount") or 0)
        groups[vname]["missing"].update(item.get("missing", []))

    for vname, g in list(groups.items())[:20]:
        fields = ", ".join(sorted(g["missing"]))
        lines.append(f"**{vname}** - {g['count']} item{'s' if g['count'] != 1 else ''} (${g['total']:,.2f})")
        lines.append(f"Missing: {fields}")
        lines.append("")
    if len(groups) > 20:
        lines.append(f"...and {len(groups) - 20} more vendors")
        lines.append("")

    lines.append("Please update these expenses with the missing information.")
    raw = "\n".join(lines)

    personalized = _call_gpt(
        _DANEEL_CONVERSATION_PROMPT,
        f"Rewrite this missing info request with personality. "
        f"Keep ALL bold vendor lines, missing field lists, @mentions, "
        f"and auto-resolution context EXACTLY as-is. "
        f"Only adjust the conversational framing text. Do NOT add tables:\n\n{raw}",
        max_tokens=700,
        temperature=0.4,
    )
    return personalized if personalized else raw


def _normalize_reason_key(reason: str) -> str:
    """Extract a grouping key from a reason string, stripping specific amounts."""
    import re
    r = reason.lower().strip()
    # Strip dollar amounts for grouping (same mismatch type, different amounts)
    r = re.sub(r'\$[\d,]+\.?\d*', '$X', r)
    # Strip bill IDs
    r = re.sub(r'#[\w\-]+', '#ID', r)
    return r


def craft_escalation_message(escalated: list, lookups: dict,
                             mention_str: str) -> str:
    """Craft escalation message with personality, grouped by vendor+reason."""
    mention = mention_str if mention_str else ""
    lines = [
        f"{mention} {len(escalated)} expense{'s' if len(escalated) != 1 else ''} require manual review:".strip(),
        "",
    ]

    # Group by (vendor, normalized_reason) — keep first raw reason for display
    from collections import OrderedDict
    groups = OrderedDict()
    for item in escalated:
        e = item["expense"]
        vid = e.get("vendor_id") or "unknown"
        vname = lookups["vendors"].get(vid, "Unknown")
        reason = item["reason"]
        key = (vname, _normalize_reason_key(reason))
        if key not in groups:
            groups[key] = {"count": 0, "total": 0.0, "reason": reason}
        groups[key]["count"] += 1
        groups[key]["total"] += float(e.get("Amount") or 0)

    for (vname, _), g in list(groups.items())[:20]:
        lines.append(f"**{vname}** - {g['count']} item{'s' if g['count'] != 1 else ''} (${g['total']:,.2f})")
        lines.append(g["reason"])
        lines.append("")
    if len(groups) > 20:
        lines.append(f"...and {len(groups) - 20} more groups")
        lines.append("")

    raw = "\n".join(lines)

    personalized = _call_gpt(
        _DANEEL_CONVERSATION_PROMPT,
        f"Rewrite this escalation alert with personality. Keep ALL bold "
        f"vendor lines (with item counts and amounts), reason lines below them, "
        f"and @mentions EXACTLY as-is. Only adjust the opening and closing "
        f"framing text. Be firm but not alarming. Do NOT add tables:\n\n{raw}",
        max_tokens=600,
        temperature=0.4,
    )
    return personalized if personalized else raw


def craft_digest_message(
    reports: list,
    lookups: dict,
    bookkeeping_mentions: str,
    escalation_mentions: str,
    project_name: str = "",
) -> str:
    """
    Craft a consolidated digest from multiple auth reports.

    Each report has:
      summary: {authorized, missing_info, duplicates, escalated, total_amount, ...}
      decisions: [{expense_id, vendor, amount, decision, rule, reason, missing_fields}, ...]
    """
    import json as _json
    from collections import OrderedDict

    # Aggregate across all reports
    total_auth = 0
    total_missing = 0
    total_escalated = 0
    total_duplicate = 0
    auth_amount = 0.0

    all_decisions = []
    for r in reports:
        s = r.get("summary") or {}
        if isinstance(s, str):
            try:
                s = _json.loads(s)
            except Exception:
                s = {}
        total_auth += int(s.get("authorized", 0))
        total_missing += int(s.get("missing_info", 0))
        total_escalated += int(s.get("escalated", 0))
        total_duplicate += int(s.get("duplicates", 0))
        auth_amount += float(s.get("total_amount", 0))

        decisions = r.get("decisions") or []
        if isinstance(decisions, str):
            try:
                decisions = _json.loads(decisions)
            except Exception:
                decisions = []
        all_decisions.extend(decisions)

    # Build structured digest
    lines = []
    header = f"**Expense Digest{' -- ' + project_name if project_name else ''}**"
    lines.append(header)
    lines.append("")

    # Summary line
    parts = []
    if total_auth:
        parts.append(f"**{total_auth}** authorized (${auth_amount:,.2f})")
    if total_missing:
        parts.append(f"**{total_missing}** need info")
    if total_escalated:
        parts.append(f"**{total_escalated}** escalated")
    if total_duplicate:
        parts.append(f"**{total_duplicate}** duplicates flagged")
    lines.append(" | ".join(parts) if parts else "No new activity.")
    lines.append("")

    # Missing info detail (group by vendor + missing fields)
    if total_missing > 0:
        missing_decisions = [d for d in all_decisions if d.get("decision") == "missing_info"]
        if missing_decisions:
            lines.append("**Needs attention:**")
            groups: OrderedDict = OrderedDict()
            for d in missing_decisions:
                vname = d.get("vendor", "Unknown")
                fields = tuple(sorted(d.get("missing_fields") or []))
                key = (vname, fields)
                if key not in groups:
                    groups[key] = {"count": 0, "total": 0.0}
                groups[key]["count"] += 1
                groups[key]["total"] += float(d.get("amount", 0))
            for (vname, fields), g in list(groups.items())[:10]:
                fstr = ", ".join(fields) if fields else "unknown"
                lines.append(f"- **{vname}** ({g['count']}x, ${g['total']:,.2f}): missing {fstr}")
            lines.append("")

    # Escalation detail
    if total_escalated > 0:
        esc_decisions = [d for d in all_decisions if d.get("decision") == "escalated"]
        if esc_decisions:
            lines.append("**Manual review needed:**")
            groups_esc: OrderedDict = OrderedDict()
            for d in esc_decisions:
                vname = d.get("vendor", "Unknown")
                if vname not in groups_esc:
                    groups_esc[vname] = {"count": 0, "total": 0.0, "reason": d.get("reason", "")}
                groups_esc[vname]["count"] += 1
                groups_esc[vname]["total"] += float(d.get("amount", 0))
            for vname, g in list(groups_esc.items())[:10]:
                lines.append(f"- **{vname}** ({g['count']}x, ${g['total']:,.2f})")
            lines.append("")

    # Mentions
    mention_parts = []
    if total_missing > 0 and bookkeeping_mentions:
        mention_parts.append(f"{bookkeeping_mentions} please review missing info")
    if total_escalated > 0 and escalation_mentions:
        mention_parts.append(f"{escalation_mentions} please review escalated items")
    if mention_parts:
        lines.append(" | ".join(mention_parts))

    raw = "\n".join(lines)

    # GPT personality pass
    personalized = _call_gpt(
        _DANEEL_CONVERSATION_PROMPT,
        f"Rewrite this digest with subtle personality. Keep ALL bold text, "
        f"numbers, @mentions, and vendor lines EXACTLY. Only adjust framing "
        f"text. Be concise:\n\n{raw}",
        max_tokens=700,
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
