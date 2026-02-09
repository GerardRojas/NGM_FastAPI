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
import asyncio
from fastapi import APIRouter, HTTPException, Depends, Query, BackgroundTasks
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from uuid import UUID
from api.supabase_client import supabase
from api.auth import get_current_user
from api.services.firebase_notifications import notify_mentioned_users

router = APIRouter(prefix="/messages", tags=["messages"])


# ═══════════════════════════════════════════════════════════════════════════════
# PYDANTIC MODELS
# ═══════════════════════════════════════════════════════════════════════════════

class MessageCreate(BaseModel):
    content: str = Field(..., min_length=1)
    channel_type: str = Field(..., pattern="^(project_general|project_accounting|project_receipts|custom|direct)$")
    channel_id: Optional[str] = None  # For custom/direct channels
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
    created_at: str
    reactions: Optional[Dict[str, List[str]]]
    attachments: Optional[List[Dict[str, Any]]]


class ChannelCreate(BaseModel):
    type: str = Field(..., pattern="^(custom|direct)$")
    name: Optional[str] = None
    description: Optional[str] = None
    member_ids: List[str] = []


class ReactionToggle(BaseModel):
    emoji: str = Field(..., min_length=1, max_length=10)
    action: str = Field(..., pattern="^(add|remove)$")


class ThreadReplyCreate(BaseModel):
    content: str = Field(..., min_length=1)


# ═══════════════════════════════════════════════════════════════════════════════
# HELPER FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════════════

def normalize_message(row: Dict[str, Any]) -> Dict[str, Any]:
    """Convert raw Supabase row to standardized message format"""
    # Handle joined user data
    user_data = row.get("users") or {}
    if isinstance(user_data, list) and user_data:
        user_data = user_data[0]

    return {
        "id": str(row.get("id", "")),
        "content": row.get("content", ""),
        "channel_type": row.get("channel_type", ""),
        "channel_id": str(row["channel_id"]) if row.get("channel_id") else None,
        "project_id": str(row["project_id"]) if row.get("project_id") else None,
        "user_id": str(row.get("user_id", "")),
        "user_name": user_data.get("user_name") if isinstance(user_data, dict) else None,
        "avatar_color": user_data.get("avatar_color") if isinstance(user_data, dict) else None,
        "reply_to_id": str(row["reply_to_id"]) if row.get("reply_to_id") else None,
        "thread_count": row.get("thread_count", 0),
        "is_edited": row.get("is_edited", False),
        "created_at": row.get("created_at", ""),
        "reactions": row.get("reactions"),
        "attachments": row.get("attachments"),
        "metadata": row.get("metadata"),
    }


def normalize_channel(row: Dict[str, Any]) -> Dict[str, Any]:
    """Convert raw Supabase row to standardized channel format"""
    return {
        "id": str(row.get("id", "")),
        "name": row.get("name"),
        "description": row.get("description"),
        "type": row.get("type", ""),
        "created_by": str(row["created_by"]) if row.get("created_by") else None,
        "created_at": row.get("created_at", ""),
        "unread_count": row.get("unread_count", 0),
        "members": row.get("members", []),
    }


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
    """
    # Find all @mentions (assuming format @Username)
    mention_pattern = r"@(\w+)"
    mentioned_names = re.findall(mention_pattern, content)

    if not mentioned_names:
        return []

    try:
        # Look up user IDs by name
        result = supabase.table("users") \
            .select("user_id, user_name") \
            .in_("user_name", mentioned_names) \
            .execute()

        user_ids = []
        for user in (result.data or []):
            user_id = str(user.get("user_id", ""))
            # Don't notify the sender
            if user_id and user_id != sender_user_id:
                user_ids.append(user_id)

        return user_ids
    except Exception as e:
        print(f"[Messages] Error extracting mentions: {e}")
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
                    "project_receipts": "Receipts"
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


def _run_mention_notifications(content, sender_user_id, sender_name,
                               sender_avatar_color, channel_type,
                               project_id, channel_id):
    """Sync wrapper to safely run async mention notifications from a background task."""
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(send_mention_notifications(
                content=content,
                sender_user_id=sender_user_id,
                sender_name=sender_name,
                sender_avatar_color=sender_avatar_color,
                channel_type=channel_type,
                project_id=project_id,
                channel_id=channel_id
            ))
        finally:
            loop.close()
    except Exception as e:
        print(f"[Messages] Mention notification error: {e}")


async def send_mention_notifications(
    content: str,
    sender_user_id: str,
    sender_name: str,
    sender_avatar_color: str,
    channel_type: str,
    project_id: str = None,
    channel_id: str = None
):
    """Background task to send push notifications for mentions."""
    mentioned_user_ids = extract_mentioned_user_ids(content, sender_user_id)

    if not mentioned_user_ids:
        return

    channel_name = get_channel_name(channel_type, project_id, channel_id)

    # Build URL for notification click
    if channel_type.startswith("project_") and project_id:
        message_url = f"/messages.html?project={project_id}&channel={channel_type}"
    elif channel_id:
        message_url = f"/messages.html?channel={channel_id}"
    else:
        message_url = "/messages.html"

    await notify_mentioned_users(
        mentioned_user_ids=mentioned_user_ids,
        sender_name=sender_name,
        message_preview=content,
        channel_name=channel_name,
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
        if channel_type in ["custom", "direct"]:
            if not channel_id:
                raise HTTPException(status_code=400, detail="channel_id required for custom/direct channels")
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


@router.post("", status_code=201)
def create_message(
    payload: MessageCreate,
    background_tasks: BackgroundTasks,
    current_user: dict = Depends(get_current_user)
):
    """Create a new message"""
    try:
        user_id = current_user["user_id"]

        data = {
            "content": payload.content,
            "channel_type": payload.channel_type,
            "user_id": user_id,
            "reply_to_id": payload.reply_to_id,
        }

        if payload.metadata:
            data["metadata"] = payload.metadata

        # Set channel reference
        if payload.channel_type in ["custom", "direct"]:
            if not payload.channel_id:
                raise HTTPException(status_code=400, detail="channel_id required for custom/direct channels")
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
                    print(f"[Messages] Attachment insert error (non-blocking): {att_err}")

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
            print(f"[Messages] User lookup error (non-blocking): {user_err}")

        # Send push notifications for @mentions (in background)
        try:
            background_tasks.add_task(
                _run_mention_notifications,
                payload.content,
                user_id,
                sender_name,
                sender_avatar_color,
                payload.channel_type,
                payload.project_id,
                payload.channel_id
            )
        except Exception as bg_err:
            print(f"[Messages] Background task setup error (non-blocking): {bg_err}")

        return {"message": response}

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")


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

        # Send push notifications for @mentions (in background)
        try:
            background_tasks.add_task(
                _run_mention_notifications,
                payload.content,
                user_id,
                sender_name,
                sender_avatar_color,
                parent_data["channel_type"],
                parent_data.get("project_id"),
                parent_data.get("channel_id")
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
            # Try to insert (will fail if already exists due to unique constraint)
            try:
                supabase.table("message_reactions").insert({
                    "message_id": message_id,
                    "user_id": user_id,
                    "emoji": payload.emoji,
                }).execute()
            except Exception:
                # Already exists, ignore
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

        return {"channels": channels}

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

        # For direct messages, check if a DM already exists with these exact members
        if payload.type == "direct":
            all_member_ids = set(payload.member_ids + [user_id])

            # Find existing DM channels where the current user is a member
            existing_channels = supabase.table("channels") \
                .select("id, type, name, description, created_by, created_at") \
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

        # Create channel
        channel_data = {
            "type": payload.type,
            "name": payload.name,
            "description": payload.description,
            "created_by": user_id,
        }

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

            if channel_type in ["custom", "direct"] and channel_id:
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
    current_user: dict = Depends(get_current_user)
):
    """
    Get messages where current user was mentioned.
    Returns a flattened format for easy dashboard display.
    """
    try:
        user_id = current_user["user_id"]
        user_name = current_user.get("user_name", "")

        # Search for messages that contain @username pattern
        # This is a simple text search - for production, use a dedicated mentions table
        search_pattern = f"%@{user_name}%"

        query = supabase.table("messages") \
            .select("*, users!user_id(user_name, avatar_color, user_photo)") \
            .ilike("content", search_pattern) \
            .neq("user_id", user_id)  # Don't include self-mentions

        result = query.order("created_at", desc=True).limit(limit).execute()

        mentions = []
        for row in (result.data or []):
            user_data = row.get("users") or {}
            if isinstance(user_data, list) and user_data:
                user_data = user_data[0]

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
                                "project_receipts": "Receipts"
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
                "message_id": str(row.get("id", "")),
                "channel_id": str(row.get("channel_id", "")) if row.get("channel_id") else str(row.get("project_id", "")),
                "channel_type": row.get("channel_type", ""),
                "channel_name": channel_name,
                "content": row.get("content", ""),
                "created_at": row.get("created_at", ""),
                "sender_id": str(row.get("user_id", "")),
                "sender_name": user_data.get("user_name") if isinstance(user_data, dict) else None,
                "sender_photo": user_data.get("user_photo") if isinstance(user_data, dict) else None,
                "sender_avatar_color": user_data.get("avatar_color") if isinstance(user_data, dict) else None,
            }
            mentions.append(mention)

        return {"mentions": mentions}

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")


@router.patch("/mentions/{mention_id}/read")
def mark_mention_read(
    mention_id: str,
    current_user: dict = Depends(get_current_user)
):
    """Mark a mention as read"""
    try:
        user_id = current_user["user_id"]

        result = supabase.table("message_mentions") \
            .update({"read_at": "now()"}) \
            .eq("id", mention_id) \
            .eq("user_id", user_id) \
            .execute()

        if not result.data:
            raise HTTPException(status_code=404, detail="Mention not found")

        return {"ok": True}

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")
