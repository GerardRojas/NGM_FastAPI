"""
═══════════════════════════════════════════════════════════════════════════════
 NGM HUB — Messages Router
═══════════════════════════════════════════════════════════════════════════════
 Endpoints for chat/messaging system:
 - Project channels (General, Accounting, Receipts)
 - Custom channels
 - Direct messages
 - Threads, reactions, mentions, attachments
═══════════════════════════════════════════════════════════════════════════════
"""

import re
import time
import logging
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException, Depends, Query, BackgroundTasks, File, UploadFile
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from uuid import UUID, uuid4
from api.supabase_client import supabase, SUPABASE_URL
from api.auth import get_current_user
from api.services.firebase_notifications import notify_mentioned_users, notify_message_recipients
from api.services.agent_personas import is_bot_user, AGENT_PERSONAS, BOT_USER_IDS

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/messages", tags=["messages"])

# In-memory cache for unread-counts (per user_id): {user_id: {"data": {...}, "ts": float}}
# Cleanup: stale entries purged by _purge_stale_caches() in main.py every 5 min
_unread_cache: Dict[str, dict] = {}
_UNREAD_CACHE_TTL = 30  # seconds
_UNREAD_CACHE_MAX = 100  # max entries to prevent unbounded growth

# Chat attachments live in the existing public 'vault' bucket under a dedicated
# prefix (no new bucket / RLS needed; writes use the service-role client).
MESSAGE_ATTACHMENTS_BUCKET = "vault"
MESSAGE_ATTACHMENTS_PREFIX = "message-attachments"
MESSAGE_ATTACHMENT_MAX_BYTES = 25 * 1024 * 1024  # 25MB


# ═══════════════════════════════════════════════════════════════════════════════
# PYDANTIC MODELS
# ═══════════════════════════════════════════════════════════════════════════════

class MessageCreate(BaseModel):
    # Allow empty content when the message carries attachments (image-only posts);
    # create_message guards that at least one of content/attachments is present.
    content: str = Field(default="")
    channel_type: str = Field(..., pattern="^(project_general|project_accounting|project_receipts|project_photos|custom|direct|group|broadcast)$")
    channel_id: Optional[str] = None  # For custom/direct/group/broadcast channels
    project_id: Optional[str] = None  # For project channels
    reply_to_id: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None
    attachments: Optional[List[Dict[str, Any]]] = None


class MessageResponse(BaseModel):
    id: str
    content: str
    channel_type: str
    channel_id: Optional[str]
    project_id: Optional[str]
    user_id: str
    user_name: Optional[str]
    avatar_color: Optional[str]
    reply_to_id: Optional[str]
    thread_count: int
    is_edited: bool
    is_deleted: bool = False
    created_at: str
    reactions: Optional[Dict[str, List[str]]]
    attachments: Optional[List[Dict[str, Any]]]


class ChannelClearRequest(BaseModel):
    channel_type: str = Field(..., pattern="^(project_general|project_accounting|project_receipts|project_photos|custom|direct|group|broadcast)$")
    channel_id: Optional[str] = None
    project_id: Optional[str] = None


class ChannelCreate(BaseModel):
    type: str = Field(..., pattern="^(custom|direct|group|broadcast)$")
    name: Optional[str] = None
    description: Optional[str] = None
    member_ids: List[str] = []
    write_roles: List[str] = []  # Role names that can write (CEO/COO always can)
    read_roles: List[str] = []   # Role names that can see channel (empty = everyone, CEO/COO always can)
    color: Optional[int] = Field(default=None, ge=0, le=360)  # Avatar hue (0-359); NULL = hashed fallback


class ReactionToggle(BaseModel):
    emoji: str = Field(..., min_length=1, max_length=10)
    action: str = Field(..., pattern="^(add|remove)$")


class ThreadReplyCreate(BaseModel):
    content: str = Field(..., min_length=1)


class MarkReadRequest(BaseModel):
    channel_type: str = Field(..., pattern="^(project_general|project_accounting|project_receipts|project_photos|custom|direct|group|broadcast)$")
    channel_id: Optional[str] = None
    project_id: Optional[str] = None


# ═══════════════════════════════════════════════════════════════════════════════
# HELPER FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════════════

def normalize_message(row: Dict[str, Any]) -> Dict[str, Any]:
    """Convert raw Supabase row to standardized message format"""
    # Handle joined user data
    user_data = row.get("users") or {}
    if isinstance(user_data, list) and user_data:
        user_data = user_data[0]

    is_deleted = row.get("is_deleted", False)

    return {
        "id": str(row.get("id", "")),
        "content": "" if is_deleted else row.get("content", ""),
        "channel_type": row.get("channel_type", ""),
        "channel_id": str(row["channel_id"]) if row.get("channel_id") else None,
        "project_id": str(row["project_id"]) if row.get("project_id") else None,
        "user_id": str(row.get("user_id", "")),
        "user_name": user_data.get("user_name") if isinstance(user_data, dict) else None,
        "avatar_color": user_data.get("avatar_color") if isinstance(user_data, dict) else None,
        "reply_to_id": str(row["reply_to_id"]) if row.get("reply_to_id") else None,
        "thread_count": row.get("thread_count", 0),
        "is_edited": row.get("is_edited", False),
        "is_deleted": is_deleted,
        "created_at": row.get("created_at", ""),
        "reactions": None if is_deleted else row.get("reactions"),
        "attachments": None if is_deleted else row.get("attachments"),
        "metadata": row.get("metadata"),
    }


def normalize_channel(row: Dict[str, Any]) -> Dict[str, Any]:
    """Convert raw Supabase row to standardized channel format"""
    result = {
        "id": str(row.get("id", "")),
        "name": row.get("name"),
        "description": row.get("description"),
        "type": row.get("type", ""),
        "created_by": str(row["created_by"]) if row.get("created_by") else None,
        "created_at": row.get("created_at", ""),
        "color": row.get("color"),
        "unread_count": row.get("unread_count", 0),
        "members": row.get("members", []),
    }
    # Broadcast-specific fields
    if row.get("type") == "broadcast":
        result["write_roles"] = row.get("write_roles") or []
        result["read_roles"] = row.get("read_roles") or []
    return result


def build_channel_key(channel_type: str, channel_id: Optional[str], project_id: Optional[str]) -> str:
    """Build the channel_key string matching the messages table generated column."""
    if channel_type in ("custom", "direct", "group", "broadcast"):
        return f"{channel_type}:{channel_id}"
    else:
        return f"{channel_type}:{project_id}"


def get_reactions_for_message(message_id: str) -> Dict[str, List[str]]:
    """Get reactions grouped by emoji for a message"""
    try:
        result = supabase.table("message_reactions") \
            .select("emoji, user_id") \
            .eq("message_id", message_id) \
            .execute()

        reactions: Dict[str, List[str]] = {}
        for row in (result.data or []):
            emoji = row.get("emoji")
            user_id = str(row.get("user_id"))
            if emoji not in reactions:
                reactions[emoji] = []
            reactions[emoji].append(user_id)

        return reactions
    except Exception:
        return {}


def get_attachments_for_message(message_id: str) -> List[Dict[str, Any]]:
    """Get attachments for a message"""
    try:
        result = supabase.table("message_attachments") \
            .select("id, name, type, size, url, thumbnail_url") \
            .eq("message_id", message_id) \
            .execute()

        return result.data or []
    except Exception:
        return []


def extract_mentioned_user_ids(content: str, sender_user_id: str) -> List[str]:
    """
    Extract user IDs from @mentions in message content.
    Returns list of user IDs that were mentioned (excluding sender).
    Frontend sends @GermanOsorio (spaces stripped), so we match both
    the raw captured name AND against user_name with spaces removed.
    """
    # Find all @mentions (assuming format @Username)
    mention_pattern = r"@(\w+)"
    mentioned_names = re.findall(mention_pattern, content)

    if not mentioned_names:
        return []

    try:
        # Look up user IDs by name (exact match first)
        result = supabase.table("users") \
            .select("user_id, user_name") \
            .in_("user_name", mentioned_names) \
            .execute()

        user_ids = []
        matched_names = set()
        for user in (result.data or []):
            user_id = str(user.get("user_id", ""))
            if user_id and user_id != sender_user_id:
                user_ids.append(user_id)
                matched_names.add(user.get("user_name", ""))

        # For unmatched names, try matching against user_name with spaces removed
        # This handles "German Osorio" stored in DB matching @GermanOsorio in content
        unmatched = [n for n in mentioned_names if n not in matched_names]
        if unmatched:
            all_users = supabase.table("users") \
                .select("user_id, user_name") \
                .execute()
            for user in (all_users.data or []):
                name_nospaces = re.sub(r"\s+", "", user.get("user_name", ""))
                if name_nospaces in unmatched:
                    user_id = str(user.get("user_id", ""))
                    if user_id and user_id != sender_user_id and user_id not in user_ids:
                        user_ids.append(user_id)

        return user_ids
    except Exception as e:
        logger.error("[Messages] Error extracting mentions: %s", e)
        return []


def get_channel_name(channel_type: str, project_id: str = None, channel_id: str = None) -> str:
    """Get a human-readable channel name for notifications."""
    try:
        if channel_type.startswith("project_") and project_id:
            proj_result = supabase.table("projects") \
                .select("project_name") \
                .eq("project_id", project_id) \
                .single() \
                .execute()

            if proj_result.data:
                channel_label = {
                    "project_general": "General",
                    "project_accounting": "Accounting",
                    "project_receipts": "Receipts",
                    "project_photos": "Photos"
                }.get(channel_type, "")
                return f"{proj_result.data.get('project_name', '')} · {channel_label}"

        elif channel_id:
            chan_result = supabase.table("channels") \
                .select("name") \
                .eq("id", channel_id) \
                .single() \
                .execute()

            if chan_result.data:
                return chan_result.data.get("name", "Messages")

    except Exception:
        pass

    return "Messages"


def get_channel_member_ids(channel_id: str, exclude_user_id: str = None) -> List[str]:
    """Get all member user_ids from a channel, optionally excluding one user."""
    try:
        result = supabase.table("channel_members") \
            .select("user_id") \
            .eq("channel_id", channel_id) \
            .execute()

        member_ids = []
        for row in (result.data or []):
            uid = str(row.get("user_id", ""))
            if uid and uid != exclude_user_id:
                member_ids.append(uid)

        return member_ids
    except Exception as e:
        logger.error("[Messages] Error getting channel members: %s", e)
        return []


# ---------------------------------------------------------------------------
# Agent Brain: @mention detection and dispatch
# ---------------------------------------------------------------------------

# Regex: matches @Andrew or @Daneel (case-insensitive)
_AGENT_MENTION_RE = re.compile(
    r"@(" + "|".join(p["name"] for p in AGENT_PERSONAS.values()) + r")\b",
    re.IGNORECASE,
)


def _detect_agent_mentions(
    background_tasks: BackgroundTasks,
    content: str,
    user_id: str,
    user_name: str,
    project_id: str | None,
    channel_type: str,
    channel_id: str | None,
    attachments: list | None = None,
) -> bool:
    """
    Scan message content for @Andrew/@Daneel mentions.
    Launches a brain BackgroundTask for each mentioned agent.
    Also starts an attention session for natural follow-up routing.

    Returns True if at least one agent mention was detected.
    """
    matches = _AGENT_MENTION_RE.findall(content)
    if not matches:
        return False

    # Deduplicate (case-insensitive)
    seen = set()
    for name in matches:
        agent_key = name.lower()
        if agent_key in seen:
            continue
        seen.add(agent_key)

        logger.info("[Messages] Agent mention detected: @%s by user %s", name, user_name)

        # Start attention session (so follow-ups route automatically)
        try:
            from api.services.agent_attention import start_session
            start_session(
                user_id=user_id,
                agent_name=agent_key,
                channel_type=channel_type,
                project_id=project_id,
                channel_id=channel_id,
            )
        except Exception as attn_err:
            logger.debug("[Messages] Attention session start error (non-blocking): %s", attn_err)

        background_tasks.add_task(
            _run_agent_brain,
            agent_key,
            content,
            user_id,
            user_name,
            project_id,
            channel_type,
            channel_id,
            attachments,
        )

    return True


_RECEIPT_TYPES = {"application/pdf", "image/jpeg", "image/png", "image/webp", "image/gif"}
_RECEIPT_EXTS = (".pdf", ".jpg", ".jpeg", ".png", ".webp")


async def _run_agent_brain(
    agent_name: str,
    user_text: str,
    user_id: str,
    user_name: str,
    project_id: str | None,
    channel_type: str,
    channel_id: str | None,
    attachments: list | None = None,
    is_followup: bool = False,
) -> None:
    """Run the async agent brain from a BackgroundTask."""
    import traceback as _tb

    def _post_to_channel(content: str, metadata: dict | None = None):
        """Post a message to the channel as the agent (sync helper)."""
        try:
            if agent_name == "andrew":
                from api.helpers.andrew_messenger import post_andrew_message
                post_andrew_message(
                    content=content,
                    project_id=project_id,
                    channel_type=channel_type,
                    channel_id=channel_id,
                    metadata=metadata or {"agent_message": True},
                )
            elif agent_name == "daneel":
                from api.helpers.daneel_messenger import post_daneel_message
                post_daneel_message(
                    content=content,
                    project_id=project_id,
                    channel_type=channel_type,
                    channel_id=channel_id,
                    metadata=metadata or {"agent_message": True},
                )
        except Exception as post_err:
            logger.error("[Messages] Failed to post agent message: %s", post_err)

    try:
        logger.info("[Messages] _run_agent_brain START | agent=%s user=%s project=%s channel=%s",
                     agent_name, user_name, project_id, channel_type)

        # Immediate ack for Andrew + file attachments (before GPT routing)
        has_receipt_attachment = False
        if agent_name == "andrew" and attachments:
            for att in attachments:
                att_type = (att.get("type") or "").lower()
                att_name = (att.get("name") or "").lower()
                if att_type in _RECEIPT_TYPES or att_name.endswith(_RECEIPT_EXTS):
                    has_receipt_attachment = True
                    _post_to_channel(
                        f"Got it! Processing **{att.get('name', 'receipt')}**...",
                        metadata={
                            "agent_message": True,
                            "receipt_status": "processing",
                            "processing_started": True,
                        },
                    )
                    logger.info("[Messages] Immediate ack posted for %s", att.get('name'))
                    break

        logger.info("[Messages] Importing agent_brain...")
        from api.services.agent_brain import invoke_brain
        logger.info("[Messages] agent_brain imported OK, calling invoke_brain...")

        await invoke_brain(
            agent_name=agent_name,
            user_text=user_text,
            user_id=user_id,
            user_name=user_name,
            project_id=project_id,
            channel_type=channel_type,
            channel_id=channel_id,
            attachments=attachments,
            is_followup=is_followup,
        )
        logger.info("[Messages] _run_agent_brain COMPLETE | agent=%s followup=%s", agent_name, is_followup)

    except Exception as e:
        logger.error("[Messages] Agent brain error (%s): %s\n%s", agent_name, e, _tb.format_exc())
        # Post error message to channel so user sees feedback
        _post_to_channel(
            "Something went wrong while processing your request. Please try again.",
            metadata={"agent_message": True, "error": str(e)},
        )


async def send_message_notifications(
    content: str,
    sender_user_id: str,
    sender_name: str,
    sender_avatar_color: str,
    channel_type: str,
    project_id: str = None,
    channel_id: str = None,
    metadata: dict = None,
    message_id: str = None,
):
    """Background task to send push notifications for mentions and DM/group messages."""

    # Build URL for notification click.
    # NGM Cam photo comments (channel_type=project_photos) deep-link into the
    # NGM Cam page (to the tagged photo), NOT the Messages page — photo tags
    # live only inside NGM Cam.
    if channel_type == "project_photos" and project_id:
        photo_file_id = (metadata or {}).get("photo_file_id")
        message_url = f"/ngm-cam?project={project_id}"
        if photo_file_id:
            message_url += f"&photo={photo_file_id}"
    elif channel_type.startswith("project_") and project_id:
        message_url = f"/messages.html?project={project_id}&channel={channel_type}"
    elif channel_id:
        message_url = f"/messages.html?channel={channel_id}"
    else:
        message_url = "/messages.html"

    channel_name = get_channel_name(channel_type, project_id, channel_id)

    # --- Phase 1: @mention notifications ---
    mentioned_user_ids = extract_mentioned_user_ids(content, sender_user_id)

    if mentioned_user_ids:
        await notify_mentioned_users(
            mentioned_user_ids=mentioned_user_ids,
            sender_name=sender_name,
            message_preview=content,
            channel_name=channel_name,
            message_url=message_url,
            avatar_color=sender_avatar_color
        )

        # In-app notifications feed (dashboard Mentions widget). Deep-link uses
        # React routes; photo-channel mentions point at the NGM Cam photo.
        try:
            from api.services.notifications_feed import create_notifications
            is_photo = channel_type == "project_photos"
            if is_photo and project_id:
                deep_link = f"/ngm-cam?project={project_id}"
                pf = (metadata or {}).get("photo_file_id")
                if pf:
                    deep_link += f"&photo={pf}"
            elif channel_type.startswith("project_") and project_id:
                deep_link = f"/messages?project={project_id}&channel={channel_type}"
                if message_id:
                    deep_link += f"&message={message_id}"
            elif channel_id:
                deep_link = f"/messages?channel={channel_id}"
                if message_id:
                    deep_link += f"&message={message_id}"
            else:
                deep_link = "/messages"

            create_notifications(
                mentioned_user_ids,
                type="mention_photo" if is_photo else "mention_message",
                module="ngm_cam" if is_photo else "messages",
                actor_id=sender_user_id,
                actor_name=sender_name,
                reference_type="message",
                reference_id=message_id,
                deep_link=deep_link,
                preview=content,
                context={
                    "channel_name": channel_name,
                    "channel_type": channel_type,
                    "project_id": project_id,
                    "channel_id": channel_id,
                },
            )
        except Exception as notif_err:
            logger.debug("[Messages] notifications feed insert failed: %s", notif_err)

    # --- Phase 2: DM / Group notifications ---
    if channel_type in ("direct", "group") and channel_id:
        recipient_ids = get_channel_member_ids(channel_id, exclude_user_id=sender_user_id)

        # Exclude users already notified via @mention to avoid double notifications
        already_notified = set(mentioned_user_ids)
        recipient_ids = [uid for uid in recipient_ids if uid not in already_notified]

        if recipient_ids:
            await notify_message_recipients(
                recipient_user_ids=recipient_ids,
                sender_name=sender_name,
                message_preview=content,
                channel_name=channel_name,
                channel_type=channel_type,
                message_url=message_url,
                avatar_color=sender_avatar_color
            )


# ═══════════════════════════════════════════════════════════════════════════════
# MESSAGES ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("")
def get_messages(
    channel_type: str = Query(...),
    channel_id: Optional[str] = Query(None),
    project_id: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    current_user: dict = Depends(get_current_user)
):
    """
    Get messages for a channel.
    For project channels, use channel_type + project_id.
    For custom/direct channels, use channel_type + channel_id.
    """
    try:
        query = supabase.table("messages") \
            .select("*, users!user_id(user_name, avatar_color)") \
            .eq("channel_type", channel_type)

        # Filter by channel
        if channel_type in ["custom", "direct", "group", "broadcast"]:
            if not channel_id:
                raise HTTPException(status_code=400, detail="channel_id required for custom/direct/group/broadcast channels")
            query = query.eq("channel_id", channel_id)
        else:
            # Project channels
            if not project_id:
                raise HTTPException(status_code=400, detail="project_id required for project channels")
            query = query.eq("project_id", project_id)

        # Only get top-level messages (not thread replies)
        query = query.is_("reply_to_id", "null")

        # Order and paginate
        result = query.order("created_at", desc=False) \
            .range(offset, offset + limit - 1) \
            .execute()

        messages = []
        for row in (result.data or []):
            msg = normalize_message(row)
            # Fetch reactions and attachments
            msg["reactions"] = get_reactions_for_message(row["id"])
            msg["attachments"] = get_attachments_for_message(row["id"])
            messages.append(msg)

        return {"messages": messages}

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")


@router.get("/broadcast-feed")
def get_broadcast_feed(
    limit: int = Query(20, ge=1, le=100),
    current_user: dict = Depends(get_current_user)
):
    """
    Aggregated, read-only news feed: the most recent top-level messages across
    every broadcast channel visible to the current user, newest first. Powers
    the dashboard News widget. Each item carries its source channel name and
    reactions so the widget can render and react in-place (reactions are shared
    with the Messages view via the same message_reactions table).
    """
    try:
        user_role = current_user.get("role") or ""

        # Resolve which broadcast channels this user may read. Mirrors the
        # visibility rule in get_channels: CEO/COO see all; empty read_roles
        # means everyone; otherwise the user's role must be listed.
        broadcast_res = supabase.table("channels") \
            .select("id, name, read_roles") \
            .eq("type", "broadcast") \
            .execute()

        channel_names: Dict[str, Optional[str]] = {}
        for bc in (broadcast_res.data or []):
            read_roles = bc.get("read_roles") or []
            if user_role in ("CEO", "COO") or not read_roles or user_role in read_roles:
                channel_names[str(bc["id"])] = bc.get("name")

        if not channel_names:
            return {"items": []}

        rows = supabase.table("messages") \
            .select("*, users!user_id(user_name, avatar_color)") \
            .eq("channel_type", "broadcast") \
            .in_("channel_id", list(channel_names.keys())) \
            .is_("reply_to_id", "null") \
            .order("created_at", desc=True) \
            .limit(limit) \
            .execute()

        items = []
        for row in (rows.data or []):
            if row.get("is_deleted"):
                continue
            msg = normalize_message(row)
            cid = msg.get("channel_id")
            items.append({
                "id": msg["id"],
                "content": msg["content"],
                "channel_id": cid,
                "channel_name": channel_names.get(cid),
                "sender_name": msg["user_name"],
                "avatar_color": msg["avatar_color"],
                "created_at": msg["created_at"],
                "reactions": get_reactions_for_message(row["id"]),
            })

        return {"items": items}

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")


@router.post("", status_code=201)
def create_message(
    payload: MessageCreate,
    background_tasks: BackgroundTasks,
    current_user: dict = Depends(get_current_user)
):
    """Create a new message"""
    try:
        user_id = current_user["user_id"]
        user_role = current_user.get("role") or ""

        # A message must carry text or at least one attachment.
        if not (payload.content or "").strip() and not payload.attachments:
            raise HTTPException(status_code=400, detail="Message must have content or an attachment.")

        # Broadcast channels: check write permission
        if payload.channel_type == "broadcast" and payload.channel_id:
            if user_role not in ("CEO", "COO"):
                # Fetch channel to check write_roles
                ch_res = supabase.table("channels") \
                    .select("write_roles") \
                    .eq("id", payload.channel_id) \
                    .single() \
                    .execute()
                write_roles = (ch_res.data or {}).get("write_roles") or []
                if user_role not in write_roles:
                    raise HTTPException(status_code=403, detail="You don't have write permission in this broadcast channel")

        data = {
            "content": payload.content,
            "channel_type": payload.channel_type,
            "user_id": user_id,
            "reply_to_id": payload.reply_to_id,
        }

        if payload.metadata:
            data["metadata"] = payload.metadata

        # Set channel reference
        if payload.channel_type in ["custom", "direct", "group", "broadcast"]:
            if not payload.channel_id:
                raise HTTPException(status_code=400, detail="channel_id required for custom/direct/group/broadcast channels")
            data["channel_id"] = payload.channel_id
        else:
            if not payload.project_id:
                raise HTTPException(status_code=400, detail="project_id required for project channels")
            data["project_id"] = payload.project_id

        # Don't include reply_to_id if null (avoid FK issues)
        if not payload.reply_to_id:
            data.pop("reply_to_id", None)

        result = supabase.table("messages").insert(data).execute()

        if not result.data:
            raise HTTPException(status_code=500, detail="Failed to create message")

        msg = result.data[0]
        message_id = msg["id"]

        # Insert attachments into message_attachments table (separate from messages)
        saved_attachments = []
        if payload.attachments:
            for att in payload.attachments:
                try:
                    att_data = {
                        "message_id": message_id,
                        "name": att.get("name", ""),
                        "type": att.get("type", ""),
                        "size": att.get("size", 0),
                        "url": att.get("url", ""),
                        "thumbnail_url": att.get("thumbnail_url"),
                    }
                    att_result = supabase.table("message_attachments").insert(att_data).execute()
                    if att_result.data:
                        saved_attachments.append(att_result.data[0])
                except Exception as att_err:
                    logger.warning("[Messages] Attachment insert error (non-blocking): %s", att_err)

        # Get user info for response
        response = normalize_message(msg)
        response["attachments"] = saved_attachments or payload.attachments
        sender_name = "Someone"
        sender_avatar_color = None

        try:
            user_result = supabase.table("users") \
                .select("user_name, avatar_color") \
                .eq("user_id", user_id) \
                .execute()

            if user_result.data and len(user_result.data) > 0:
                user_row = user_result.data[0]
                response["user_name"] = user_row.get("user_name")
                response["avatar_color"] = user_row.get("avatar_color")
                sender_name = user_row.get("user_name", "Someone")
                sender_avatar_color = user_row.get("avatar_color")
        except Exception as user_err:
            logger.warning("[Messages] User lookup error (non-blocking): %s", user_err)

        # Send push notifications for @mentions + DM/group (in background)
        try:
            background_tasks.add_task(
                send_message_notifications,
                content=payload.content,
                sender_user_id=user_id,
                sender_name=sender_name,
                sender_avatar_color=sender_avatar_color,
                channel_type=payload.channel_type,
                project_id=payload.project_id,
                channel_id=payload.channel_id,
                metadata=payload.metadata,
                message_id=message_id,
            )
        except Exception as bg_err:
            logger.warning("[Messages] Background task setup error (non-blocking): %s", bg_err)

        # --- Agent Brain: detect @mentions OR active attention sessions ---
        try:
            if not is_bot_user(user_id):
                # 1. Check for explicit @mentions first
                mentions_found = _detect_agent_mentions(
                    background_tasks,
                    payload.content,
                    user_id,
                    sender_name,
                    payload.project_id,
                    payload.channel_type,
                    payload.channel_id,
                    payload.attachments,
                )

                # 2. If NO explicit @mention, check for active attention session
                #    (follow-up messages in an ongoing agent conversation)
                if not mentions_found:
                    routed_via_session = False
                    try:
                        from api.services.agent_attention import consume_session
                        session = consume_session(
                            user_id=user_id,
                            channel_type=payload.channel_type,
                            content=payload.content,
                            project_id=payload.project_id,
                            channel_id=payload.channel_id,
                        )
                        if session:
                            logger.info(
                                "[Messages] Attention session follow-up: %s -> @%s (remaining=%d)",
                                sender_name, session.agent_name, session.remaining
                            )
                            background_tasks.add_task(
                                _run_agent_brain,
                                session.agent_name,
                                payload.content,
                                user_id,
                                sender_name,
                                payload.project_id,
                                payload.channel_type,
                                payload.channel_id,
                                payload.attachments,
                                True,  # is_followup flag
                            )
                            routed_via_session = True
                    except Exception as attn_err:
                        logger.debug("[Messages] Attention check error (non-blocking): %s", attn_err)

                    # 3. If still not routed, check if this is a DM channel with a bot
                    #    (auto-route to the bot agent without needing @mention)
                    if not routed_via_session and payload.channel_id and payload.channel_type == "direct":
                        try:
                            member_ids = get_channel_member_ids(payload.channel_id, exclude_user_id=user_id)
                            for mid in member_ids:
                                if is_bot_user(mid):
                                    agent_key = BOT_USER_IDS.get(mid)
                                    if agent_key:
                                        logger.info(
                                            "[Messages] DM auto-route: %s -> @%s (channel=%s)",
                                            sender_name, agent_key, payload.channel_id
                                        )
                                        background_tasks.add_task(
                                            _run_agent_brain,
                                            agent_key,
                                            payload.content,
                                            user_id,
                                            sender_name,
                                            payload.project_id,
                                            payload.channel_type,
                                            payload.channel_id,
                                            payload.attachments,
                                            True,  # treat as follow-up (no rate limit)
                                        )
                                    break  # Only route to the first bot in the DM
                        except Exception as dm_err:
                            logger.debug("[Messages] DM bot auto-route error (non-blocking): %s", dm_err)
        except Exception as brain_err:
            logger.warning("[Messages] Agent brain setup error (non-blocking): %s", brain_err)

        return {"message": response}

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")


# ═══════════════════════════════════════════════════════════════════════════════
# ATTACHMENTS ENDPOINT
# ═══════════════════════════════════════════════════════════════════════════════

@router.post("/upload", status_code=201)
async def upload_message_attachment(
    file: UploadFile = File(...),
    current_user: dict = Depends(get_current_user),
):
    """Upload a single chat attachment to the 'vault' bucket and return its public
    URL + metadata. The client then includes the returned object in the message's
    `attachments` array on POST /messages (which persists it to message_attachments)."""
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Empty file.")
    if len(content) > MESSAGE_ATTACHMENT_MAX_BYTES:
        raise HTTPException(status_code=413, detail="File too large. Maximum size is 25MB.")

    ext = ""
    if file.filename and "." in file.filename:
        ext = "." + file.filename.rsplit(".", 1)[-1].lower()
    content_type = file.content_type or "application/octet-stream"
    object_path = f"{MESSAGE_ATTACHMENTS_PREFIX}/{uuid4().hex}{ext}"

    try:
        supabase.storage.from_(MESSAGE_ATTACHMENTS_BUCKET).upload(
            path=object_path,
            file=content,
            file_options={"content-type": content_type, "upsert": "true"},
        )
    except Exception as e:
        logger.error("[Messages] Attachment upload error: %s", e)
        raise HTTPException(status_code=500, detail="Attachment upload failed.")

    url = f"{SUPABASE_URL.rstrip('/')}/storage/v1/object/public/{MESSAGE_ATTACHMENTS_BUCKET}/{object_path}"
    is_image = content_type.startswith("image/")
    return {
        "name": file.filename or "file",
        "type": content_type,
        "size": len(content),
        "url": url,
        "thumbnail_url": url if is_image else None,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# THREADS ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/{message_id}/thread")
def get_thread_replies(
    message_id: str,
    current_user: dict = Depends(get_current_user)
):
    """Get all replies to a message (thread)"""
    try:
        result = supabase.table("messages") \
            .select("*, users!user_id(user_name, avatar_color)") \
            .eq("reply_to_id", message_id) \
            .order("created_at", desc=False) \
            .execute()

        replies = []
        for row in (result.data or []):
            msg = normalize_message(row)
            msg["reactions"] = get_reactions_for_message(row["id"])
            replies.append(msg)

        return {"replies": replies}

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")


@router.post("/{message_id}/thread", status_code=201)
def create_thread_reply(
    message_id: str,
    payload: ThreadReplyCreate,
    background_tasks: BackgroundTasks,
    current_user: dict = Depends(get_current_user)
):
    """Reply to a message in a thread"""
    try:
        user_id = current_user["user_id"]

        # Get parent message to copy channel info
        parent = supabase.table("messages") \
            .select("channel_type, channel_id, project_id") \
            .eq("id", message_id) \
            .single() \
            .execute()

        if not parent.data:
            raise HTTPException(status_code=404, detail="Parent message not found")

        parent_data = parent.data

        data = {
            "content": payload.content,
            "channel_type": parent_data["channel_type"],
            "channel_id": parent_data.get("channel_id"),
            "project_id": parent_data.get("project_id"),
            "user_id": user_id,
            "reply_to_id": message_id,
        }

        result = supabase.table("messages").insert(data).execute()

        if not result.data:
            raise HTTPException(status_code=500, detail="Failed to create reply")

        msg = normalize_message(result.data[0])
        sender_name = "Someone"
        sender_avatar_color = None

        # Get user info
        user_result = supabase.table("users") \
            .select("user_name, avatar_color") \
            .eq("user_id", user_id) \
            .single() \
            .execute()

        if user_result.data:
            msg["user_name"] = user_result.data.get("user_name")
            msg["avatar_color"] = user_result.data.get("avatar_color")
            sender_name = user_result.data.get("user_name", "Someone")
            sender_avatar_color = user_result.data.get("avatar_color")

        # Send push notifications for @mentions + DM/group (in background)
        try:
            background_tasks.add_task(
                send_message_notifications,
                content=payload.content,
                sender_user_id=user_id,
                sender_name=sender_name,
                sender_avatar_color=sender_avatar_color,
                channel_type=parent_data["channel_type"],
                project_id=parent_data.get("project_id"),
                channel_id=parent_data.get("channel_id")
            )
        except Exception:
            pass

        return {"reply": msg}

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")


# ═══════════════════════════════════════════════════════════════════════════════
# REACTIONS ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════════

@router.post("/{message_id}/reactions")
def toggle_reaction(
    message_id: str,
    payload: ReactionToggle,
    current_user: dict = Depends(get_current_user)
):
    """Add or remove a reaction to a message"""
    try:
        user_id = current_user["user_id"]

        if payload.action == "add":
            # Single-reaction policy: a user may have at most one reaction per
            # message. Clear any previous reaction by this user before adding the
            # new one so switching reactions replaces rather than accumulates.
            supabase.table("message_reactions") \
                .delete() \
                .eq("message_id", message_id) \
                .eq("user_id", user_id) \
                .execute()
            try:
                supabase.table("message_reactions").insert({
                    "message_id": message_id,
                    "user_id": user_id,
                    "emoji": payload.emoji,
                }).execute()
            except Exception:
                # Already exists (race), ignore
                pass
        else:
            # Remove reaction
            supabase.table("message_reactions") \
                .delete() \
                .eq("message_id", message_id) \
                .eq("user_id", user_id) \
                .eq("emoji", payload.emoji) \
                .execute()

        # Return updated reactions
        reactions = get_reactions_for_message(message_id)

        return {"reactions": reactions}

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")


@router.get("/{message_id}/reactions")
def get_reactions(
    message_id: str,
    current_user: dict = Depends(get_current_user)
):
    """Current reactions for a single message. Used by clients to refetch after
    a realtime message_reactions change without reloading the whole thread."""
    try:
        return {"reactions": get_reactions_for_message(message_id)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")


# ═══════════════════════════════════════════════════════════════════════════════
# CHANNELS ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/channels")
def get_channels(
    current_user: dict = Depends(get_current_user)
):
    """Get all custom and direct message channels for current user"""
    try:
        user_id = current_user["user_id"]

        # Get channels where user is a member
        result = supabase.table("channel_members") \
            .select("channel_id, channels(*)") \
            .eq("user_id", user_id) \
            .execute()

        channels = []
        for row in (result.data or []):
            channel_data = row.get("channels")
            if channel_data:
                channel = normalize_channel(channel_data)

                # Get members for this channel
                members_result = supabase.table("channel_members") \
                    .select("user_id, users(user_id, user_name, avatar_color)") \
                    .eq("channel_id", channel["id"]) \
                    .execute()

                members = []
                for m in (members_result.data or []):
                    user_data = m.get("users")
                    if user_data:
                        members.append({
                            "user_id": str(user_data.get("user_id", "")),
                            "user_name": user_data.get("user_name"),
                            "avatar_color": user_data.get("avatar_color"),
                        })

                channel["members"] = members
                channels.append(channel)

        # Also fetch broadcast channels visible to this user's role
        user_role = current_user.get("role") or ""
        broadcast_res = supabase.table("channels") \
            .select("*") \
            .eq("type", "broadcast") \
            .execute()

        existing_ids = {c["id"] for c in channels}
        for bc in (broadcast_res.data or []):
            if bc["id"] in existing_ids:
                continue
            read_roles = bc.get("read_roles") or []
            # CEO/COO see all; empty read_roles = everyone can see
            if user_role in ("CEO", "COO") or not read_roles or user_role in read_roles:
                channels.append(normalize_channel(bc))

        return {"channels": channels}

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")


@router.get("/unread-counts")
def get_unread_counts(
    current_user: dict = Depends(get_current_user)
):
    """Get unread message counts for all channels the user has access to."""
    try:
        user_id = current_user["user_id"]

        # Check in-memory cache first
        cached = _unread_cache.get(user_id)
        if cached and (time.time() - cached["ts"]) < _UNREAD_CACHE_TTL:
            return {"unread_counts": cached["data"]}

        result = supabase.rpc("get_unread_counts", {"p_user_id": user_id}).execute()

        counts = {}
        for row in (result.data or []):
            counts[row["channel_key"]] = row["unread_count"]

        # Purge stale entries on every write (cheap: TTL is 30s, most will be expired)
        now = time.time()
        stale = [k for k, v in _unread_cache.items() if now - v["ts"] > _UNREAD_CACHE_TTL]
        for k in stale:
            del _unread_cache[k]
        # Hard cap: if still over limit, drop oldest half
        if len(_unread_cache) >= _UNREAD_CACHE_MAX:
            sorted_keys = sorted(_unread_cache, key=lambda k: _unread_cache[k]["ts"])
            for k in sorted_keys[: len(sorted_keys) // 2]:
                del _unread_cache[k]
        _unread_cache[user_id] = {"data": counts, "ts": now}

        return {"unread_counts": counts}

    except Exception as e:
        logger.error(f"[unread-counts] RPC failed for user {current_user.get('user_id','?')}: {e}")
        return {"unread_counts": {}}


@router.post("/mark-read")
def mark_channel_read(
    payload: MarkReadRequest,
    current_user: dict = Depends(get_current_user)
):
    """Mark a channel as read for the current user (upsert last_read_at = now())."""
    try:
        user_id = current_user["user_id"]
        channel_key = build_channel_key(payload.channel_type, payload.channel_id, payload.project_id)
        now = datetime.now(timezone.utc).isoformat()

        # SELECT + UPDATE/INSERT pattern (Supabase upsert can silently no-op)
        existing = supabase.table("channel_read_status") \
            .select("id") \
            .eq("user_id", user_id) \
            .eq("channel_key", channel_key) \
            .execute()

        if existing.data:
            supabase.table("channel_read_status") \
                .update({"last_read_at": now, "updated_at": now}) \
                .eq("user_id", user_id) \
                .eq("channel_key", channel_key) \
                .execute()
        else:
            supabase.table("channel_read_status") \
                .insert({"user_id": user_id, "channel_key": channel_key, "last_read_at": now, "updated_at": now}) \
                .execute()

        # Invalidate unread cache so next poll reflects the change
        _unread_cache.pop(user_id, None)

        return {"ok": True, "channel_key": channel_key}

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")


@router.post("/channels", status_code=201)
def create_channel(
    payload: ChannelCreate,
    current_user: dict = Depends(get_current_user)
):
    """Create a new custom or direct message channel"""
    try:
        user_id = current_user["user_id"]

        # Validate
        if payload.type == "custom" and not payload.name:
            raise HTTPException(status_code=400, detail="Name required for custom channels")

        if payload.type == "direct" and len(payload.member_ids) == 0:
            raise HTTPException(status_code=400, detail="At least one member required for direct messages")

        # For group channels, deduplicate by name (return existing if same name)
        if payload.type == "group":
            if not payload.name:
                raise HTTPException(status_code=400, detail="Name required for group channels")

            existing = supabase.table("channels") \
                .select("id, type, name, description, created_by, created_at, color") \
                .eq("type", "group") \
                .eq("name", payload.name) \
                .execute()

            if existing.data:
                channel = normalize_channel(existing.data[0])
                channel_id = channel["id"]

                # Ensure current user is a member
                member_check = supabase.table("channel_members") \
                    .select("id") \
                    .eq("channel_id", channel_id) \
                    .eq("user_id", user_id) \
                    .execute()

                if not member_check.data:
                    supabase.table("channel_members").insert({
                        "channel_id": channel_id,
                        "user_id": user_id,
                        "role": "member",
                    }).execute()

                # Return members
                members_result = supabase.table("channel_members") \
                    .select("user_id, users(user_id, user_name, avatar_color)") \
                    .eq("channel_id", channel_id) \
                    .execute()

                channel["members"] = []
                for m in (members_result.data or []):
                    user_data = m.get("users")
                    if user_data:
                        channel["members"].append({
                            "user_id": str(user_data.get("user_id", "")),
                            "user_name": user_data.get("user_name"),
                            "avatar_color": user_data.get("avatar_color"),
                        })

                return {"channel": channel, "existing": True}

        # For direct messages, check if a DM already exists with these exact members
        if payload.type == "direct":
            all_member_ids = set(payload.member_ids + [user_id])

            # Find existing DM channels where the current user is a member
            existing_channels = supabase.table("channels") \
                .select("id, type, name, description, created_by, created_at, color") \
                .eq("type", "direct") \
                .execute()

            for channel in (existing_channels.data or []):
                # Get members of this channel
                members_result = supabase.table("channel_members") \
                    .select("user_id") \
                    .eq("channel_id", channel["id"]) \
                    .execute()

                channel_member_ids = set(m["user_id"] for m in (members_result.data or []))

                # If the members match exactly, return existing channel
                if channel_member_ids == all_member_ids:
                    existing = normalize_channel(channel)
                    # Add members info
                    existing["members"] = []
                    for mid in channel_member_ids:
                        user_info = supabase.table("users") \
                            .select("user_id, user_name, avatar_color") \
                            .eq("user_id", mid) \
                            .single() \
                            .execute()
                        if user_info.data:
                            existing["members"].append(user_info.data)

                    return {"channel": existing, "existing": True}

        # For broadcast channels, deduplicate by name and validate
        if payload.type == "broadcast":
            if not payload.name:
                raise HTTPException(status_code=400, detail="Name required for broadcast channels")
            if not payload.write_roles:
                raise HTTPException(status_code=400, detail="At least one write role required for broadcast channels")

            existing = supabase.table("channels") \
                .select("id, type, name, description, created_by, created_at, write_roles, read_roles, color") \
                .eq("type", "broadcast") \
                .eq("name", payload.name) \
                .execute()

            if existing.data:
                channel = normalize_channel(existing.data[0])
                return {"channel": channel, "existing": True}

        # Create channel
        channel_data = {
            "type": payload.type,
            "name": payload.name,
            "description": payload.description,
            "created_by": user_id,
        }

        # Optional channel color (avatar hue). DMs derive color from the other
        # member's avatar, so a stored color only applies to non-direct channels.
        if payload.color is not None and payload.type != "direct":
            channel_data["color"] = payload.color

        # Broadcast-specific fields
        if payload.type == "broadcast":
            channel_data["write_roles"] = payload.write_roles
            channel_data["read_roles"] = payload.read_roles

        result = supabase.table("channels").insert(channel_data).execute()

        if not result.data:
            raise HTTPException(status_code=500, detail="Failed to create channel")

        channel_id = result.data[0]["id"]

        # Add creator as admin member
        supabase.table("channel_members").insert({
            "channel_id": channel_id,
            "user_id": user_id,
            "role": "admin",
        }).execute()

        # Add other members
        for member_id in payload.member_ids:
            if member_id != user_id:  # Don't add creator twice
                try:
                    supabase.table("channel_members").insert({
                        "channel_id": channel_id,
                        "user_id": member_id,
                        "role": "member",
                    }).execute()
                except Exception:
                    # Ignore if user doesn't exist
                    pass

        channel = normalize_channel(result.data[0])

        return {"channel": channel}

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")


# ═══════════════════════════════════════════════════════════════════════════════
# SEARCH ENDPOINT
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/search")
def search_messages(
    q: str = Query(..., min_length=2),
    channel_type: Optional[str] = Query(None),
    channel_id: Optional[str] = Query(None),
    project_id: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=100),
    current_user: dict = Depends(get_current_user)
):
    """Search messages by content"""
    try:
        query = supabase.table("messages") \
            .select("*, users!user_id(user_name, avatar_color)") \
            .ilike("content", f"%{q}%")

        # Apply channel filters if provided
        if channel_type:
            query = query.eq("channel_type", channel_type)

            if channel_type in ["custom", "direct", "group", "broadcast"] and channel_id:
                query = query.eq("channel_id", channel_id)
            elif project_id:
                query = query.eq("project_id", project_id)

        result = query.order("created_at", desc=True) \
            .limit(limit) \
            .execute()

        messages = [normalize_message(row) for row in (result.data or [])]

        return {"messages": messages}

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")


# ═══════════════════════════════════════════════════════════════════════════════
# MENTIONS ENDPOINT
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/mentions")
def get_my_mentions(
    unread_only: bool = Query(False),
    limit: int = Query(20, ge=1, le=100),
    channel_type: Optional[str] = Query(
        None,
        description="Filter to a single channel_type (e.g. project_photos for the NGM Cam inbox). "
                    "When omitted, project_photos mentions are excluded so photo tags stay inside NGM Cam.",
    ),
    current_user: dict = Depends(get_current_user)
):
    """
    Get messages where current user was mentioned.
    Returns a flattened format for easy dashboard display.

    Photo tags (channel_type=project_photos) live exclusively in NGM Cam:
    they are excluded from the default Messages mentions inbox and are only
    returned when the caller explicitly requests channel_type=project_photos.
    """
    try:
        user_id = current_user["user_id"]
        user_name = current_user.get("user_name", "")

        # Frontend strips spaces from names: "German Osorio" -> @GermanOsorio
        # Search with ilike (broad), then filter in Python with word-boundary
        # regex to eliminate false positives (e.g. @German vs @GermanOsorio).
        name_nospaces = re.sub(r"\s+", "", user_name)
        search_pattern = f"%@{name_nospaces}%"

        # Build word-boundary regex for Python-side filtering
        # Matches @GermanOsorio followed by non-word char or end of string
        _mention_re = re.compile(
            rf"@{re.escape(name_nospaces)}\b", re.IGNORECASE
        )

        query = supabase.table("messages") \
            .select("*, users!user_id(user_name, avatar_color, user_photo)") \
            .ilike("content", search_pattern) \
            .neq("user_id", user_id) \
            .or_("is_deleted.is.null,is_deleted.eq.false")

        # Scope by channel_type: explicit filter wins; otherwise keep photo tags
        # out of the default (Messages) mentions inbox.
        if channel_type:
            query = query.eq("channel_type", channel_type)
        else:
            query = query.neq("channel_type", "project_photos")

        # Fetch extra rows since Python-side regex may filter some out
        result = query.order("created_at", desc=True).limit(limit * 2).execute()

        # Python-side word-boundary filter (eliminates false positives)
        filtered_rows = [
            row for row in (result.data or [])
            if _mention_re.search(row.get("content", ""))
        ][:limit]

        # Get read status from message_mentions table
        message_ids = [str(row.get("id", "")) for row in filtered_rows if row.get("id")]
        read_message_ids = set()
        if message_ids:
            try:
                read_result = supabase.table("message_mentions") \
                    .select("message_id") \
                    .eq("user_id", user_id) \
                    .not_.is_("read_at", "null") \
                    .in_("message_id", message_ids) \
                    .execute()
                read_message_ids = {str(r["message_id"]) for r in (read_result.data or [])}
            except:
                pass

        mentions = []
        for row in filtered_rows:
            user_data = row.get("users") or {}
            if isinstance(user_data, list) and user_data:
                user_data = user_data[0]

            msg_id = str(row.get("id", ""))
            is_read = msg_id in read_message_ids

            if unread_only and is_read:
                continue

            # Get channel name
            channel_name = ""
            if row.get("channel_type", "").startswith("project_"):
                # Get project name
                if row.get("project_id"):
                    try:
                        proj_result = supabase.table("projects") \
                            .select("project_name") \
                            .eq("project_id", row["project_id"]) \
                            .single() \
                            .execute()
                        if proj_result.data:
                            channel_type_label = {
                                "project_general": "General",
                                "project_accounting": "Accounting",
                                "project_receipts": "Receipts",
                                "project_photos": "Photos"
                            }.get(row.get("channel_type"), "")
                            channel_name = f"{proj_result.data.get('project_name', '')} · {channel_type_label}"
                    except:
                        pass
            elif row.get("channel_id"):
                # Get custom channel name
                try:
                    chan_result = supabase.table("channels") \
                        .select("name") \
                        .eq("id", row["channel_id"]) \
                        .single() \
                        .execute()
                    if chan_result.data:
                        channel_name = chan_result.data.get("name", "")
                except:
                    pass

            mention = {
                "message_id": msg_id,
                "channel_id": str(row.get("channel_id", "")) if row.get("channel_id") else None,
                "project_id": str(row.get("project_id", "")) if row.get("project_id") else None,
                "channel_type": row.get("channel_type", ""),
                "channel_name": channel_name,
                "content": row.get("content", ""),
                "created_at": row.get("created_at", ""),
                "sender_id": str(row.get("user_id", "")),
                "sender_name": user_data.get("user_name") if isinstance(user_data, dict) else None,
                "sender_photo": user_data.get("user_photo") if isinstance(user_data, dict) else None,
                "sender_avatar_color": user_data.get("avatar_color") if isinstance(user_data, dict) else None,
                "is_read": is_read,
                "metadata": row.get("metadata"),
            }
            mentions.append(mention)

        return {"mentions": mentions}

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")


@router.patch("/mentions/{message_id}/read")
def mark_mention_read(
    message_id: str,
    current_user: dict = Depends(get_current_user)
):
    """Mark a mention as read by message_id"""
    try:
        user_id = current_user["user_id"]

        # Verify the message still exists before creating mention record
        msg_check = supabase.table("messages") \
            .select("id") \
            .eq("id", message_id) \
            .execute()
        if not msg_check.data:
            # Message was deleted - nothing to mark as read
            return {"ok": True}

        # Check if record exists
        existing = supabase.table("message_mentions") \
            .select("id") \
            .eq("message_id", message_id) \
            .eq("user_id", user_id) \
            .execute()

        if existing.data:
            # Update existing record
            supabase.table("message_mentions") \
                .update({"read_at": "now()"}) \
                .eq("message_id", message_id) \
                .eq("user_id", user_id) \
                .execute()
        else:
            # Insert new record
            supabase.table("message_mentions") \
                .insert({
                    "message_id": message_id,
                    "user_id": user_id,
                    "read_at": "now()"
                }) \
                .execute()

        return {"ok": True}

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")


# ═══════════════════════════════════════════════════════════════════════════════
# MESSAGE DELETE / CLEAR ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════════

@router.patch("/{message_id}/delete")
def soft_delete_message(
    message_id: str,
    current_user: dict = Depends(get_current_user)
):
    """Soft-delete a message. Users can only delete their own messages."""
    try:
        user_id = current_user["user_id"]

        # Verify message exists and belongs to user
        msg_result = supabase.table("messages") \
            .select("id, user_id") \
            .eq("id", message_id) \
            .single() \
            .execute()

        if not msg_result.data:
            raise HTTPException(status_code=404, detail="Message not found")

        if str(msg_result.data["user_id"]) != user_id:
            raise HTTPException(status_code=403, detail="You can only delete your own messages")

        # Soft delete
        supabase.table("messages") \
            .update({
                "is_deleted": True,
                "deleted_at": "now()",
                "deleted_by": user_id,
            }) \
            .eq("id", message_id) \
            .execute()

        return {"ok": True}

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")


@router.post("/channel/clear")
def clear_channel_messages(
    payload: ChannelClearRequest,
    current_user: dict = Depends(get_current_user)
):
    """Clear all messages in a channel. CEO and COO only."""
    try:
        user_id = current_user["user_id"]
        role = current_user.get("role", "")

        if role not in ["CEO", "COO"]:
            raise HTTPException(status_code=403, detail="Only CEO and COO can clear conversations")

        # Hard-delete: remove messages entirely (no "this message was deleted" placeholders)
        query = supabase.table("messages") \
            .delete() \
            .eq("channel_type", payload.channel_type)

        if payload.channel_type in ["custom", "direct", "group", "broadcast"]:
            if not payload.channel_id:
                raise HTTPException(status_code=400, detail="channel_id required for custom/direct/group/broadcast channels")
            query = query.eq("channel_id", payload.channel_id)
        else:
            if not payload.project_id:
                raise HTTPException(status_code=400, detail="project_id required for project channels")
            query = query.eq("project_id", payload.project_id)

        result = query.execute()
        count = len(result.data) if result.data else 0

        return {"ok": True, "count": count}

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")
