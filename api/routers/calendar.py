"""
In-house Calendar router (Phase 1).

Backs the /calendar React page with a dedicated event store:
  * calendar_events            — the events themselves
  * calendar_event_attendees   — invitee list + RSVP status

Read-only overlays (pipeline task due dates + project milestones) come from
existing routers and are layered in the frontend, not here.

Visibility rules (enforced here, not via RLS):
  * 'team'    → every authenticated staff user sees the event
  * 'private' → only created_by + attendees see the event
  * 'project' → created_by + attendees + members of project_id see the event
                (members = users with a row in project_user_access for the
                project, OR staff whose role grants access via roles_management;
                Phase 1 keeps it permissive at the staff layer — refine when
                external-user calendar access is needed)

Path: C:\\Users\\germa\\Desktop\\NGM_API\\api\\routers\\calendar.py
Migration: C:\\Users\\germa\\Desktop\\NGM_API\\sql\\create_calendar_events.sql
"""
from __future__ import annotations

import logging
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from fastapi.responses import RedirectResponse

from api.auth import get_current_user
from api.services.notifications_feed import create_notifications
from api.services.rrule_lite import expand_occurrences, next_occurrence, parse_rrule
from api.services import google_calendar as gcal
from api.supabase_client import supabase

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/calendar", tags=["calendar"])


_VALID_VISIBILITY = {"team", "private", "project"}
_VALID_RSVP = {"invited", "accepted", "declined", "tentative"}


# ============================================================================
# Pydantic models
# ============================================================================

class AttendeeOut(BaseModel):
    user_id: str
    status: str
    responded_at: Optional[str] = None


class EventOut(BaseModel):
    event_id: str
    title: str
    description: Optional[str] = None
    location: Optional[str] = None
    start_at: str
    end_at: str
    all_day: bool = False
    color: Optional[str] = None
    project_id: Optional[str] = None
    company_id: Optional[str] = None
    created_by: str
    visibility: str
    rrule: Optional[str] = None
    rrule_until: Optional[str] = None
    reminder_minutes: Optional[int] = None
    meeting_url: Optional[str] = None
    meeting_provider: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    attendees: List[AttendeeOut] = Field(default_factory=list)


class EventCreate(BaseModel):
    title: str
    description: Optional[str] = None
    location: Optional[str] = None
    start_at: str                    # ISO 8601 timestamp with tz
    end_at: str
    all_day: bool = False
    color: Optional[str] = None
    project_id: Optional[str] = None
    company_id: Optional[str] = None
    visibility: str = "team"
    rrule: Optional[str] = None
    rrule_until: Optional[str] = None
    reminder_minutes: Optional[int] = None
    attendee_user_ids: Optional[List[str]] = None
    add_meet: bool = False           # request a Google Meet link (needs Google connected)


class EventUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    location: Optional[str] = None
    start_at: Optional[str] = None
    end_at: Optional[str] = None
    all_day: Optional[bool] = None
    color: Optional[str] = None
    project_id: Optional[str] = None
    company_id: Optional[str] = None
    visibility: Optional[str] = None
    rrule: Optional[str] = None
    rrule_until: Optional[str] = None
    reminder_minutes: Optional[int] = None
    attendee_user_ids: Optional[List[str]] = None   # if provided, replaces the set
    add_meet: Optional[bool] = None                  # set True to add a Google Meet link


class AttendeeStatus(BaseModel):
    status: str                       # invited|accepted|declined|tentative


# ============================================================================
# Helpers
# ============================================================================

def _normalize_visibility(value: Optional[str]) -> str:
    if value is None:
        return "team"
    v = str(value).strip().lower()
    if v not in _VALID_VISIBILITY:
        raise HTTPException(status_code=400, detail=f"Invalid visibility: {value!r}")
    return v


def _normalize_status(value: str) -> str:
    v = str(value).strip().lower()
    if v not in _VALID_RSVP:
        raise HTTPException(status_code=400, detail=f"Invalid status: {value!r}")
    return v


def _fetch_attendees(event_ids: List[str]) -> dict:
    """Returns {event_id: [AttendeeOut-as-dict, ...]} for the given events."""
    if not event_ids:
        return {}
    res = (
        supabase.table("calendar_event_attendees")
        .select("event_id, user_id, status, responded_at")
        .in_("event_id", event_ids)
        .execute()
    )
    out: dict = {eid: [] for eid in event_ids}
    for row in (res.data or []):
        eid = row.get("event_id")
        if eid in out:
            out[eid].append({
                "user_id": str(row.get("user_id")),
                "status": row.get("status") or "invited",
                "responded_at": row.get("responded_at"),
            })
    return out


def _replace_attendees(event_id: str, user_ids: List[str]) -> None:
    """Replace the attendee set for an event (idempotent). Preserves existing
    RSVP status for user_ids that were already attendees."""
    clean: List[str] = []
    seen = set()
    for u in (user_ids or []):
        s = str(u or "").strip()
        if s and s not in seen:
            seen.add(s)
            clean.append(s)

    existing_res = (
        supabase.table("calendar_event_attendees")
        .select("user_id, status, responded_at")
        .eq("event_id", event_id)
        .execute()
    )
    existing = {str(r["user_id"]): r for r in (existing_res.data or [])}

    # Delete everyone first so users removed from the list disappear, then
    # re-insert (upsert keeps RSVP state for users that stay).
    supabase.table("calendar_event_attendees").delete().eq("event_id", event_id).execute()

    if not clean:
        return

    rows = []
    for uid in clean:
        prior = existing.get(uid)
        rows.append({
            "event_id": event_id,
            "user_id": uid,
            "status": (prior or {}).get("status") or "invited",
            "responded_at": (prior or {}).get("responded_at"),
        })
    supabase.table("calendar_event_attendees").insert(rows).execute()


def _can_see_event(event: dict, current_user_id: str, attendee_user_ids: set) -> bool:
    """Apply visibility rules in the API layer (RLS is permissive)."""
    visibility = event.get("visibility") or "team"
    if visibility == "team":
        return True
    creator = str(event.get("created_by") or "")
    if creator == current_user_id:
        return True
    if current_user_id in attendee_user_ids:
        return True
    if visibility == "project":
        # Phase 1: staff-only API, so every authenticated staff member of the
        # project gets access. Refine when external-user calendar access lands.
        project_id = event.get("project_id")
        if not project_id:
            return False
        pua = (
            supabase.table("project_user_access")
            .select("user_id")
            .eq("project_id", project_id)
            .eq("user_id", current_user_id)
            .limit(1)
            .execute()
        )
        if pua.data:
            return True
        return False
    return False


def _serialize_event(row: dict, attendees: List[dict], sync: Optional[dict] = None) -> dict:
    return {
        "event_id": str(row.get("event_id")),
        "title": row.get("title") or "",
        "description": row.get("description"),
        "location": row.get("location"),
        "start_at": row.get("start_at"),
        "end_at": row.get("end_at"),
        "all_day": bool(row.get("all_day")),
        "color": row.get("color"),
        "project_id": row.get("project_id"),
        "company_id": row.get("company_id"),
        "created_by": str(row.get("created_by")),
        "visibility": row.get("visibility") or "team",
        "rrule": row.get("rrule"),
        "rrule_until": row.get("rrule_until"),
        "reminder_minutes": row.get("reminder_minutes"),
        "meeting_url": row.get("meeting_url"),
        "meeting_provider": row.get("meeting_provider"),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
        "attendees": attendees,
        "sync": sync,
    }


def _fetch_sync_mappings(event_ids: List[str]) -> dict:
    """Returns {event_id: {status, source, last_synced_at}} for the given events.

    Sync mappings are optional metadata (Google sync). A schema/sync issue here
    must never take down event listing, so any failure degrades to "no sync info".
    """
    if not event_ids:
        return {}
    try:
        res = (
            supabase.table("calendar_sync_mappings")
            .select("event_id, sync_status, sync_source, last_synced_at")
            .in_("event_id", event_ids)
            .execute()
        )
    except Exception:
        logger.exception("_fetch_sync_mappings failed; returning no sync info")
        return {}
    out: dict = {}
    for r in (res.data or []):
        eid = str(r.get("event_id"))
        out[eid] = {
            "status": r.get("sync_status") or "synced",
            "source": r.get("sync_source") or "local",
            "last_synced_at": r.get("last_synced_at"),
        }
    return out


def _require_cron(request: Request) -> None:
    """Authorize a cron endpoint. Fails CLOSED: if NGM_CRON_SECRET isn't set the
    endpoint is unavailable rather than open to the public. Callers must send the
    secret via the X-Cron-Secret header (or ?secret= query param)."""
    secret = os.getenv("NGM_CRON_SECRET")
    if not secret:
        raise HTTPException(status_code=503, detail="Cron not configured")
    provided = request.headers.get("X-Cron-Secret") or request.query_params.get("secret")
    if provided != secret:
        raise HTTPException(status_code=403, detail="Bad cron secret")


def _project_company_id(project_id: Optional[str]) -> Optional[str]:
    """The authoritative company for a project (projects.source_company)."""
    if not project_id:
        return None
    try:
        res = (
            supabase.table("projects")
            .select("source_company")
            .eq("project_id", project_id)
            .limit(1)
            .execute()
        )
        if res.data:
            return res.data[0].get("source_company") or None
    except Exception:
        logger.exception("_project_company_id lookup failed")
    return None


def _resolve_company_id(payload_company_id: Optional[str], project_id: Optional[str]) -> Optional[str]:
    """Server-side workspace scope. A project-linked event is tagged to that
    project's company (authoritative, not client-trusted); otherwise it falls
    back to the company the client supplied (the active workspace)."""
    if project_id:
        derived = _project_company_id(project_id)
        if derived:
            return derived
    return payload_company_id or None


def _validate_rrule(rrule: Optional[str]) -> None:
    """Reject a non-empty recurrence rule that doesn't parse to a valid RRULE."""
    if rrule and parse_rrule(rrule) is None:
        raise HTTPException(status_code=400, detail="Invalid recurrence rule")


def _notify_invitees(event_row: dict, attendee_ids: List[str], actor_id: str) -> None:
    """Best-effort: drop an "invited to event" notification per attendee. The
    actor is filtered out by create_notifications. Failures never propagate."""
    if not attendee_ids:
        return
    try:
        eid = str(event_row.get("event_id"))
        title = event_row.get("title") or "Event"
        start = event_row.get("start_at") or ""
        # Show YYYY-MM-DD HH:mm in the preview so the bell badge has signal.
        preview_when = start.replace("T", " ")[:16] if isinstance(start, str) else ""
        create_notifications(
            attendee_ids,
            type="event_invite",
            module="calendar",
            actor_id=actor_id,
            reference_type="calendar_event",
            reference_id=eid,
            deep_link="/calendar",
            preview=f"Invited to: {title}" + (f" — {preview_when}" if preview_when else ""),
            context={
                "event_id": eid,
                "start_at": start,
                "end_at": event_row.get("end_at"),
            },
        )
    except Exception:
        logger.exception("Failed to emit calendar invite notifications")


# ============================================================================
# Endpoints
# ============================================================================

@router.get("/events")
async def list_events(
    from_: Optional[str] = Query(None, alias="from", description="ISO 8601 lower bound (inclusive)"),
    to: Optional[str] = Query(None, description="ISO 8601 upper bound (inclusive)"),
    project_id: Optional[str] = Query(None),
    user_id: Optional[str] = Query(None, description="Filter to events the given user attends or created"),
    company_id: Optional[str] = Query(None, description="Scope to the active workspace (its events plus shared NULL ones). Omit for all."),
    current_user: dict = Depends(get_current_user),
):
    """List events overlapping [from, to] (inclusive). Visibility-filtered for
    the current user. `project_id` narrows to a single project. `user_id`
    narrows to a single attendee/creator. `company_id` scopes to the active
    workspace (events tagged to it plus shared/untagged ones)."""
    try:
        me = str(current_user.get("user_id") or "")

        # Two queries unioned client-side so recurring events whose master row
        # starts before `from_` are still included (the frontend will expand
        # the rrule into the visible window).
        one_off = supabase.table("calendar_events").select("*").is_("rrule", "null")
        recurring = supabase.table("calendar_events").select("*").not_.is_("rrule", "null")
        if from_:
            one_off = one_off.gte("end_at", from_)
            recurring = recurring.or_(f"rrule_until.is.null,rrule_until.gte.{from_}")
        if to:
            one_off = one_off.lte("start_at", to)
            recurring = recurring.lte("start_at", to)
        if project_id:
            one_off = one_off.eq("project_id", project_id)
            recurring = recurring.eq("project_id", project_id)

        one_off_rows = (one_off.order("start_at", desc=False).limit(2000).execute().data or [])
        recurring_rows = (recurring.order("start_at", desc=False).limit(2000).execute().data or [])

        seen_ids: set = set()
        rows: list = []
        for r in one_off_rows + recurring_rows:
            eid = str(r.get("event_id"))
            if eid in seen_ids:
                continue
            seen_ids.add(eid)
            rows.append(r)

        # Workspace scope (additive): keep events tagged to the active company
        # plus shared/untagged ones. Applied in Python so it composes with the
        # one-off/recurring union without conflicting PostgREST or-groups.
        if company_id:
            rows = [r for r in rows if r.get("company_id") in (company_id, None)]

        event_ids = [str(r["event_id"]) for r in rows]
        attendees_by_event = _fetch_attendees(event_ids)

        if user_id:
            uid = str(user_id)
            rows = [
                r for r in rows
                if str(r.get("created_by")) == uid
                or any(a["user_id"] == uid for a in attendees_by_event.get(str(r["event_id"]), []))
            ]

        sync_by_event = _fetch_sync_mappings(event_ids)

        # Visibility filter
        visible = []
        for r in rows:
            eid = str(r["event_id"])
            atts = attendees_by_event.get(eid, [])
            attendee_set = {a["user_id"] for a in atts}
            if _can_see_event(r, me, attendee_set):
                visible.append(_serialize_event(r, atts, sync_by_event.get(eid)))

        return {"events": visible}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("list_events failed")
        raise HTTPException(status_code=500, detail=f"Error listing events: {e}")


@router.get("/events/{event_id}")
async def get_event(event_id: str, current_user: dict = Depends(get_current_user)):
    try:
        me = str(current_user.get("user_id") or "")
        res = (
            supabase.table("calendar_events").select("*").eq("event_id", event_id).limit(1).execute()
        )
        if not res.data:
            raise HTTPException(status_code=404, detail="Event not found")
        row = res.data[0]
        atts = _fetch_attendees([event_id]).get(event_id, [])
        if not _can_see_event(row, me, {a["user_id"] for a in atts}):
            raise HTTPException(status_code=403, detail="Not allowed to view this event")
        sync = _fetch_sync_mappings([event_id]).get(event_id)
        return _serialize_event(row, atts, sync)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("get_event failed")
        raise HTTPException(status_code=500, detail=f"Error fetching event: {e}")


@router.post("/events")
async def create_event(payload: EventCreate, current_user: dict = Depends(get_current_user)):
    try:
        me = str(current_user.get("user_id") or "")
        if not me:
            raise HTTPException(status_code=401, detail="No user context")

        title = (payload.title or "").strip()
        if not title:
            raise HTTPException(status_code=400, detail="Title is required")
        if not payload.start_at or not payload.end_at:
            raise HTTPException(status_code=400, detail="start_at and end_at are required")
        _validate_rrule(payload.rrule)

        row = {
            "title": title,
            "description": (payload.description or None),
            "location": (payload.location or None),
            "start_at": payload.start_at,
            "end_at": payload.end_at,
            "all_day": bool(payload.all_day),
            "color": (payload.color or None),
            "project_id": payload.project_id or None,
            "company_id": _resolve_company_id(payload.company_id, payload.project_id),
            "created_by": me,
            "visibility": _normalize_visibility(payload.visibility),
            "rrule": (payload.rrule or None),
            "rrule_until": (payload.rrule_until or None),
            "reminder_minutes": payload.reminder_minutes,
        }
        res = supabase.table("calendar_events").insert(row).execute()
        if not res.data:
            raise HTTPException(status_code=500, detail="Insert returned no row")
        created = res.data[0]
        event_id = str(created["event_id"])

        clean_attendees: List[str] = []
        if payload.attendee_user_ids:
            _replace_attendees(event_id, payload.attendee_user_ids)
            clean_attendees = [str(u).strip() for u in payload.attendee_user_ids if u]

        # Phase 2: invite notification fires immediately. Time-based "X minutes
        # before" dispatch is in Phase 3 (cron worker).
        _notify_invitees(created, clean_attendees, me)

        # Phase 4: best-effort push to Google Calendar (if the creator has
        # connected their account). Failures don't break the local write — the
        # periodic pull-sync will reconcile.
        # When add_meet is set, push_event requests a Google Meet conference and
        # writes meeting_url back to the row, so re-read it for the response.
        try:
            gcal.push_event(me, created, create_meet=bool(payload.add_meet))
        except gcal.GoogleNotConfigured:
            pass
        except Exception:
            logger.exception("push_event after create failed")

        final_row = created
        if payload.add_meet:
            refreshed = (
                supabase.table("calendar_events").select("*").eq("event_id", event_id).limit(1).execute()
            )
            if refreshed.data:
                final_row = refreshed.data[0]

        atts = _fetch_attendees([event_id]).get(event_id, [])
        sync = _fetch_sync_mappings([event_id]).get(event_id)
        return _serialize_event(final_row, atts, sync)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("create_event failed")
        raise HTTPException(status_code=500, detail=f"Error creating event: {e}")


@router.patch("/events/{event_id}")
async def update_event(event_id: str, payload: EventUpdate, current_user: dict = Depends(get_current_user)):
    try:
        me = str(current_user.get("user_id") or "")
        existing = (
            supabase.table("calendar_events").select("*").eq("event_id", event_id).limit(1).execute()
        )
        if not existing.data:
            raise HTTPException(status_code=404, detail="Event not found")
        row = existing.data[0]
        if str(row.get("created_by")) != me:
            raise HTTPException(status_code=403, detail="Only the creator can edit this event")

        update: dict = {}
        if payload.title is not None:
            t = payload.title.strip()
            if not t:
                raise HTTPException(status_code=400, detail="Title cannot be empty")
            update["title"] = t
        if payload.description is not None:
            update["description"] = payload.description or None
        if payload.location is not None:
            update["location"] = payload.location or None
        if payload.start_at is not None:
            update["start_at"] = payload.start_at
        if payload.end_at is not None:
            update["end_at"] = payload.end_at
        if payload.all_day is not None:
            update["all_day"] = bool(payload.all_day)
        if payload.color is not None:
            update["color"] = payload.color or None
        if payload.project_id is not None:
            update["project_id"] = payload.project_id or None
        # Re-resolve the workspace whenever the project or company changes: a
        # project-linked event always follows the project's company.
        if payload.project_id is not None or payload.company_id is not None:
            eff_project = (payload.project_id if payload.project_id is not None else row.get("project_id")) or None
            eff_company = (payload.company_id if payload.company_id is not None else row.get("company_id")) or None
            update["company_id"] = _resolve_company_id(eff_company, eff_project)
        if payload.visibility is not None:
            update["visibility"] = _normalize_visibility(payload.visibility)
        if payload.rrule is not None:
            _validate_rrule(payload.rrule)
            update["rrule"] = payload.rrule or None
        if payload.rrule_until is not None:
            update["rrule_until"] = payload.rrule_until or None
        if payload.reminder_minutes is not None:
            update["reminder_minutes"] = payload.reminder_minutes

        if update:
            supabase.table("calendar_events").update(update).eq("event_id", event_id).execute()

        prior_attendee_ids: set = set()
        if payload.attendee_user_ids is not None:
            prior_attendees = _fetch_attendees([event_id]).get(event_id, [])
            prior_attendee_ids = {a["user_id"] for a in prior_attendees}
            _replace_attendees(event_id, payload.attendee_user_ids)

        refreshed = (
            supabase.table("calendar_events").select("*").eq("event_id", event_id).limit(1).execute()
        )
        atts = _fetch_attendees([event_id]).get(event_id, [])

        # Notify any NEWLY-added attendees (not the full set on every PATCH).
        if payload.attendee_user_ids is not None:
            new_ids = [a["user_id"] for a in atts if a["user_id"] not in prior_attendee_ids]
            _notify_invitees(refreshed.data[0], new_ids, me)

        # Phase 4: push update to Google. Only the event creator has tokens;
        # other editors don't have OAuth to the creator's calendar, so this
        # is intentionally creator-scoped.
        # add_meet semantics: True = add a Meet (if none yet), False = remove the
        # existing one, None = leave the conference untouched.
        existing_meet = refreshed.data[0].get("meeting_url")
        want_meet = payload.add_meet is True and not existing_meet
        want_remove = payload.add_meet is False and bool(existing_meet)
        final_row = refreshed.data[0]
        try:
            creator_id = str(refreshed.data[0].get("created_by") or "")
            if creator_id:
                gcal.push_event(
                    creator_id, refreshed.data[0],
                    create_meet=want_meet, remove_meet=want_remove,
                )
                if want_meet or want_remove:
                    re_read = (
                        supabase.table("calendar_events").select("*").eq("event_id", event_id).limit(1).execute()
                    )
                    if re_read.data:
                        final_row = re_read.data[0]
        except gcal.GoogleNotConfigured:
            pass
        except Exception:
            logger.exception("push_event after update failed")

        sync = _fetch_sync_mappings([event_id]).get(event_id)
        return _serialize_event(final_row, atts, sync)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("update_event failed")
        raise HTTPException(status_code=500, detail=f"Error updating event: {e}")


@router.delete("/events/{event_id}")
async def delete_event(event_id: str, current_user: dict = Depends(get_current_user)):
    try:
        me = str(current_user.get("user_id") or "")
        existing = (
            supabase.table("calendar_events").select("event_id, created_by").eq("event_id", event_id).limit(1).execute()
        )
        if not existing.data:
            raise HTTPException(status_code=404, detail="Event not found")
        if str(existing.data[0].get("created_by")) != me:
            raise HTTPException(status_code=403, detail="Only the creator can delete this event")
        # Push the delete to Google BEFORE removing the mapping (the mapping is
        # ON DELETE CASCADE on the local row).
        try:
            gcal.push_delete(me, event_id)
        except gcal.GoogleNotConfigured:
            pass
        except Exception:
            logger.exception("push_delete failed")
        supabase.table("calendar_events").delete().eq("event_id", event_id).execute()
        return {"ok": True}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("delete_event failed")
        raise HTTPException(status_code=500, detail=f"Error deleting event: {e}")


@router.patch("/events/{event_id}/attendees/{user_id}")
async def update_attendee_status(
    event_id: str,
    user_id: str,
    payload: AttendeeStatus,
    current_user: dict = Depends(get_current_user),
):
    """RSVP. Only the attendee themselves (or the creator on their behalf)
    can update an attendee's status."""
    try:
        me = str(current_user.get("user_id") or "")
        ev = (
            supabase.table("calendar_events").select("event_id, created_by").eq("event_id", event_id).limit(1).execute()
        )
        if not ev.data:
            raise HTTPException(status_code=404, detail="Event not found")
        creator = str(ev.data[0].get("created_by") or "")
        if me != user_id and me != creator:
            raise HTTPException(status_code=403, detail="Cannot change another user's RSVP")

        status = _normalize_status(payload.status)
        # Upsert: ensures the row exists even if the user wasn't already invited.
        responded_at = datetime.now(timezone.utc).isoformat() if status != "invited" else None
        supabase.table("calendar_event_attendees").upsert({
            "event_id": event_id,
            "user_id": user_id,
            "status": status,
            "responded_at": responded_at,
        }, on_conflict="event_id,user_id").execute()

        atts = _fetch_attendees([event_id]).get(event_id, [])
        return {"attendees": atts}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("update_attendee_status failed")
        raise HTTPException(status_code=500, detail=f"Error updating RSVP: {e}")


# ============================================================================
# ICS export (Phase 3) — RFC 5545 .ics generation + tokenized feed URL
# ============================================================================
# Per-event:  GET /calendar/events/{id}.ics   (Authorization required)
# Per-user :  GET /calendar/feed.ics?token=…  (token is the auth; subscribable)
# Tokens   :  GET/POST/DELETE /calendar/feed-tokens                (auth)


def _ics_escape(value: Optional[str]) -> str:
    if not value:
        return ""
    return (
        str(value)
        .replace("\\", "\\\\")
        .replace(",", "\\,")
        .replace(";", "\\;")
        .replace("\n", "\\n")
    )


def _ics_dt(iso: Optional[str]) -> str:
    """Convert an ISO 8601 timestamp into ICS DTSTART/DTEND format (UTC Z)."""
    if not iso:
        return ""
    try:
        v = str(iso).replace("Z", "+00:00")
        dt = datetime.fromisoformat(v)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        dt = dt.astimezone(timezone.utc)
        return dt.strftime("%Y%m%dT%H%M%SZ")
    except Exception:
        return ""


def _ics_lines_for_event(row: dict) -> List[str]:
    """One VEVENT block for the event. RRULE field passes through verbatim."""
    eid = str(row.get("event_id") or "")
    out = ["BEGIN:VEVENT"]
    out.append(f"UID:{eid}@ngm-hub")
    out.append(f"DTSTAMP:{_ics_dt(row.get('updated_at') or row.get('created_at')) or _ics_dt(datetime.now(timezone.utc).isoformat())}")
    out.append(f"DTSTART:{_ics_dt(row.get('start_at'))}")
    out.append(f"DTEND:{_ics_dt(row.get('end_at'))}")
    out.append(f"SUMMARY:{_ics_escape(row.get('title'))}")
    if row.get("description"):
        out.append(f"DESCRIPTION:{_ics_escape(row.get('description'))}")
    if row.get("location"):
        out.append(f"LOCATION:{_ics_escape(row.get('location'))}")
    if row.get("meeting_url"):
        # Join link for video meetings (Google Meet). URL is the canonical field;
        # also surfaced in the description so clients that ignore URL still show it.
        out.append(f"URL:{_ics_escape(row.get('meeting_url'))}")
    if row.get("rrule"):
        # Our stored RRULE-lite is already RFC 5545 compatible.
        out.append(f"RRULE:{row.get('rrule')}")
    out.append("END:VEVENT")
    return out


def _ics_envelope(events_lines: List[str]) -> str:
    return "\r\n".join([
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//NGM Hub//Calendar//EN",
        "CALSCALE:GREGORIAN",
        *events_lines,
        "END:VCALENDAR",
        "",
    ])


@router.get("/events/{event_id}.ics")
async def export_event_ics(event_id: str, current_user: dict = Depends(get_current_user)):
    """Single-event ICS download. Visibility-checked exactly like GET /events/{id}."""
    me = str(current_user.get("user_id") or "")
    res = supabase.table("calendar_events").select("*").eq("event_id", event_id).limit(1).execute()
    if not res.data:
        raise HTTPException(status_code=404, detail="Event not found")
    row = res.data[0]
    atts = _fetch_attendees([event_id]).get(event_id, [])
    if not _can_see_event(row, me, {a["user_id"] for a in atts}):
        raise HTTPException(status_code=403, detail="Not allowed to export this event")
    body = _ics_envelope(_ics_lines_for_event(row))
    filename = f"event-{event_id}.ics"
    return Response(
        content=body,
        media_type="text/calendar; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/feed.ics")
async def export_feed_ics(token: str = Query(..., min_length=16)):
    """Public ICS subscription feed keyed by token. No JWT — the token IS auth.

    Returns every event the token's user can see (team + their own + project),
    over a 1-year forward window. Recurring events keep their RRULE so the
    subscriber expands them locally."""
    tok = supabase.table("user_calendar_tokens").select("user_id").eq("token", token).limit(1).execute()
    if not tok.data:
        raise HTTPException(status_code=404, detail="Feed token not found")
    user_id = str(tok.data[0]["user_id"])

    # Touch last_used_at (best-effort).
    try:
        supabase.table("user_calendar_tokens").update(
            {"last_used_at": datetime.now(timezone.utc).isoformat()}
        ).eq("token", token).execute()
    except Exception:
        pass

    now = datetime.now(timezone.utc)
    horizon = now + timedelta(days=365)

    one_off = (
        supabase.table("calendar_events").select("*")
        .is_("rrule", "null")
        .gte("end_at", now.isoformat())
        .lte("start_at", horizon.isoformat())
        .order("start_at", desc=False)
        .limit(2000)
        .execute().data or []
    )
    recurring = (
        supabase.table("calendar_events").select("*")
        .not_.is_("rrule", "null")
        .or_(f"rrule_until.is.null,rrule_until.gte.{now.isoformat()}")
        .order("start_at", desc=False)
        .limit(2000)
        .execute().data or []
    )

    event_ids = [str(r["event_id"]) for r in (one_off + recurring)]
    attendees_by_event = _fetch_attendees(event_ids)

    visible_lines: List[str] = []
    seen = set()
    for r in one_off + recurring:
        eid = str(r["event_id"])
        if eid in seen:
            continue
        seen.add(eid)
        atts = attendees_by_event.get(eid, [])
        if not _can_see_event(r, user_id, {a["user_id"] for a in atts}):
            continue
        visible_lines.extend(_ics_lines_for_event(r))

    body = _ics_envelope(visible_lines)
    return Response(content=body, media_type="text/calendar; charset=utf-8")


class FeedTokenCreate(BaseModel):
    label: Optional[str] = None


@router.get("/feed-tokens")
async def list_feed_tokens(request: Request, current_user: dict = Depends(get_current_user)):
    """List the user's ICS feed tokens (without exposing the secret directly).

    Returns one entry per token with a `feed_url` ready to subscribe with.
    """
    me = str(current_user.get("user_id") or "")
    res = (
        supabase.table("user_calendar_tokens")
        .select("token, label, created_at, last_used_at")
        .eq("user_id", me)
        .order("created_at", desc=True)
        .execute()
    )
    base = str(request.base_url).rstrip("/")
    out = []
    for r in (res.data or []):
        out.append({
            "label": r.get("label") or "default",
            "created_at": r.get("created_at"),
            "last_used_at": r.get("last_used_at"),
            "feed_url": f"{base}/calendar/feed.ics?token={r.get('token')}",
        })
    return {"tokens": out}


@router.post("/feed-tokens")
async def create_feed_token(payload: FeedTokenCreate, request: Request, current_user: dict = Depends(get_current_user)):
    """Mint a new ICS feed token. The label disambiguates devices ("Phone", etc.)."""
    me = str(current_user.get("user_id") or "")
    label = (payload.label or "default").strip() or "default"
    token = secrets.token_urlsafe(32)
    try:
        supabase.table("user_calendar_tokens").insert({
            "token": token,
            "user_id": me,
            "label": label,
        }).execute()
    except Exception as e:
        # Most common: unique(label) collision for the user.
        raise HTTPException(status_code=400, detail=f"Could not create feed token: {e}")
    base = str(request.base_url).rstrip("/")
    return {
        "label": label,
        "feed_url": f"{base}/calendar/feed.ics?token={token}",
    }


@router.delete("/feed-tokens/{label}")
async def revoke_feed_token(label: str, current_user: dict = Depends(get_current_user)):
    me = str(current_user.get("user_id") or "")
    try:
        supabase.table("user_calendar_tokens").delete().eq("user_id", me).eq("label", label).execute()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Could not revoke token: {e}")
    return {"ok": True}


# ============================================================================
# Cron — scheduled reminder dispatch (Phase 3)
# ============================================================================
# Wire this to a Render Cron Job (every 1 minute):
#   curl -fsSL -H "X-Cron-Secret: $NGM_CRON_SECRET" \
#       https://ngm-fastapi.onrender.com/calendar/cron/dispatch-reminders
#
# The endpoint walks calendar_events with reminder_minutes set and emits
# notifications for occurrences whose reminder time falls in the lookback
# window. Idempotency via calendar_reminder_log.

@router.get("/cron/dispatch-reminders")
@router.post("/cron/dispatch-reminders")
async def dispatch_reminders(request: Request):
    """Sweep events whose reminder fires in the last ~lookback_seconds window
    and emit notifications. Idempotent via calendar_reminder_log."""
    _require_cron(request)

    lookback_seconds = int(request.query_params.get("lookback_seconds") or 90)
    now = datetime.now(timezone.utc)
    floor = now - timedelta(seconds=lookback_seconds)
    horizon = now + timedelta(days=60)  # how far ahead to look for next recurring occurrence

    # Candidate events: reminder set, not yet ended (one-off) OR rrule_until not yet passed.
    res = (
        supabase.table("calendar_events").select("*")
        .not_.is_("reminder_minutes", "null")
        .execute()
    )
    candidates = res.data or []

    dispatched = 0
    errors = 0

    for row in candidates:
        try:
            event_id = str(row["event_id"])
            reminder = int(row.get("reminder_minutes") or 0)
            if reminder <= 0:
                continue

            occurrence = _next_due_occurrence(row, floor, now, horizon, reminder)
            if occurrence is None:
                continue

            # Idempotent insert; PK conflict means already dispatched.
            try:
                supabase.table("calendar_reminder_log").insert({
                    "event_id": event_id,
                    "occurrence_at": occurrence.isoformat(),
                }).execute()
            except Exception:
                # Already dispatched for this (event, occurrence) — skip.
                continue

            atts = _fetch_attendees([event_id]).get(event_id, [])
            recipients = list({a["user_id"] for a in atts} | {str(row.get("created_by"))})
            preview_when = occurrence.strftime("%Y-%m-%d %H:%M UTC")
            create_notifications(
                recipients,
                type="event_reminder",
                module="calendar",
                actor_id=None,
                reference_type="calendar_event",
                reference_id=event_id,
                deep_link="/calendar",
                preview=f"Reminder: {row.get('title') or 'Event'} — {preview_when}",
                context={
                    "event_id": event_id,
                    "occurrence_at": occurrence.isoformat(),
                    "reminder_minutes": reminder,
                },
            )

            # Also push to devices (best-effort) so reminders actually ping the
            # phone/desktop, not just the in-app bell. Push failure never blocks.
            try:
                from api.services.firebase_notifications import send_push_notification
                for rid in recipients:
                    await send_push_notification(
                        rid,
                        title="Event reminder",
                        body=f"{row.get('title') or 'Event'} — {preview_when}",
                        data={"event_id": event_id, "deep_link": "/calendar"},
                        tag=f"event-reminder-{event_id}",
                    )
            except Exception:
                logger.exception("dispatch_reminders: push failed for event %s", event_id)

            dispatched += 1
        except Exception:
            errors += 1
            logger.exception("dispatch_reminders: failed for event %s", row.get("event_id"))

    return {
        "ok": True,
        "now": now.isoformat(),
        "lookback_seconds": lookback_seconds,
        "scanned": len(candidates),
        "dispatched": dispatched,
        "errors": errors,
    }


def _next_due_occurrence(
    row: dict,
    floor: datetime,
    now: datetime,
    horizon: datetime,
    reminder_minutes: int,
) -> Optional[datetime]:
    """Return the occurrence whose reminder fires in (floor, now]. Handles both
    one-off events (start_at) and recurring events (rrule expansion)."""
    start = _parse_iso(row.get("start_at"))
    if not start:
        return None
    rrule = row.get("rrule")
    delta = timedelta(minutes=reminder_minutes)

    if not rrule:
        fire_at = start - delta
        if floor < fire_at <= now:
            return start
        return None

    rule = parse_rrule(rrule)
    if not rule:
        # Stored rrule was malformed; treat as one-off.
        fire_at = start - delta
        if floor < fire_at <= now:
            return start
        return None

    # Find the first occurrence whose fire_at falls in window.
    occurrences = expand_occurrences(rule, start, floor, horizon)
    for occ in occurrences:
        fire_at = occ - delta
        if floor < fire_at <= now:
            return occ
    return None


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        v = str(value).replace("Z", "+00:00")
        d = datetime.fromisoformat(v)
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d
    except Exception:
        return None


# ============================================================================
# Google Calendar bidirectional sync (Phase 4)
# ============================================================================
# Gated on GOOGLE_CLIENT_ID/SECRET/REDIRECT_URI env vars. If not set, every
# endpoint here returns {configured: false} or 503 so the UI degrades gracefully.

@router.get("/google/status")
async def google_status(current_user: dict = Depends(get_current_user)):
    me = str(current_user.get("user_id") or "")
    return gcal.status_for_user(me)


@router.get("/google/connect")
async def google_connect(current_user: dict = Depends(get_current_user)):
    """Returns the URL the frontend should redirect the user to. Done as a
    JSON endpoint (not a 302) so the SPA controls the navigation."""
    me = str(current_user.get("user_id") or "")
    try:
        url = gcal.build_auth_url(me)
    except gcal.GoogleNotConfigured:
        raise HTTPException(status_code=503, detail="Google OAuth is not configured on this server")
    return {"auth_url": url}


@router.get("/google/callback")
async def google_callback(code: Optional[str] = None, state: Optional[str] = None, error: Optional[str] = None):
    """OAuth redirect URI. Google sends the user here after consent. We exchange
    the code, persist tokens, then redirect the browser back to /calendar."""
    if error:
        return RedirectResponse(url=f"/calendar?google=error&reason={error}")
    if not code or not state:
        return RedirectResponse(url="/calendar?google=error&reason=missing_params")
    user_id = gcal.consume_state(state)
    if not user_id:
        return RedirectResponse(url="/calendar?google=error&reason=bad_state")
    try:
        token_response = gcal.exchange_code_for_tokens(code)
        gcal.upsert_tokens(user_id, token_response)
        # Kick a first sync so the user sees their events immediately.
        try:
            gcal.pull_sync(user_id)
        except Exception:
            logger.exception("Initial pull-sync failed for %s", user_id)
        # Phase 5: register an events.watch channel so subsequent changes
        # arrive in realtime via the webhook (replaces the 5-min pull cron
        # for connected users; the cron still backstops dropped notifications).
        try:
            gcal.register_watch(user_id)
        except Exception:
            logger.exception("register_watch failed for %s", user_id)
    except gcal.GoogleNotConfigured:
        return RedirectResponse(url="/calendar?google=error&reason=not_configured")
    except Exception as e:
        logger.exception("google_callback failed")
        return RedirectResponse(url=f"/calendar?google=error&reason=callback_failed&detail={str(e)[:200]}")
    return RedirectResponse(url="/calendar?google=connected")


@router.post("/google/disconnect")
async def google_disconnect(current_user: dict = Depends(get_current_user)):
    me = str(current_user.get("user_id") or "")
    gcal.disconnect_user(me)
    return {"ok": True}


@router.post("/google/sync")
async def google_sync_now(current_user: dict = Depends(get_current_user)):
    """Manual pull-sync from the UI (the cron does the same on a schedule)."""
    me = str(current_user.get("user_id") or "")
    try:
        counts = gcal.pull_sync(me)
    except gcal.GoogleNotConfigured:
        raise HTTPException(status_code=503, detail="Google OAuth is not configured on this server")
    except gcal.GoogleSyncError as e:
        raise HTTPException(status_code=502, detail=str(e))
    return {"ok": True, "counts": counts}


# ----- Cron pull-sync (Render Cron Job) -------------------------------------

@router.get("/cron/google-sync")
@router.post("/cron/google-sync")
async def cron_google_sync(request: Request):
    """Sweep every connected user and pull-sync. Wire to Render Cron Job at a
    schedule of your taste — every 5 min is a good baseline.

    curl -fsSL -H "X-Cron-Secret: $NGM_CRON_SECRET" \\
        https://ngm-fastapi.onrender.com/calendar/cron/google-sync
    """
    _require_cron(request)

    if not gcal.is_google_configured():
        return {"ok": False, "configured": False, "reason": "Google OAuth not configured"}

    users_res = supabase.table("google_calendar_tokens").select("user_id").execute()
    users = [str(r["user_id"]) for r in (users_res.data or [])]

    totals = {"users": len(users), "created": 0, "updated": 0, "deleted": 0, "skipped": 0, "errors": 0}
    for uid in users:
        try:
            counts = gcal.pull_sync(uid)
            for k in ("created", "updated", "deleted", "skipped"):
                totals[k] += int(counts.get(k) or 0)
        except Exception:
            totals["errors"] += 1
            logger.exception("cron_google_sync: pull_sync failed for %s", uid)
    return {"ok": True, **totals}


# ============================================================================
# Phase 5 — webhook receiver, conflict resolution, watch-renewal cron
# ============================================================================

@router.post("/google/webhook")
async def google_webhook(request: Request):
    """Receives Google's events.watch push notifications. Google sends:
      X-Goog-Channel-ID, X-Goog-Resource-ID, X-Goog-Resource-State, X-Goog-Channel-Token
    We look up the user by channel_id (+ verify the shared token) and kick a
    pull_sync. The initial 'sync' state confirms channel creation; subsequent
    'exists' states mean something changed.

    We respond 200 fast — Google retries on non-2xx, so any heavy work that
    fails should re-trigger naturally."""
    channel_id = request.headers.get("X-Goog-Channel-ID") or request.headers.get("x-goog-channel-id")
    channel_token = request.headers.get("X-Goog-Channel-Token") or request.headers.get("x-goog-channel-token")
    state = (request.headers.get("X-Goog-Resource-State") or request.headers.get("x-goog-resource-state") or "").lower()

    if not channel_id:
        # Some health-check or misdirected POST. 200 so callers don't retry.
        return {"ok": True, "ignored": True}

    user_id = gcal.lookup_user_by_channel(channel_id, channel_token)
    if not user_id:
        # Unknown channel — most likely a stale notification after we revoked.
        # 200 to suppress retries.
        return {"ok": True, "unknown_channel": True}

    if state == "sync":
        # Initial confirmation Google sends right after we open the channel.
        return {"ok": True, "state": "sync"}

    try:
        counts = gcal.pull_sync(user_id)
        return {"ok": True, "state": state, **counts}
    except Exception:
        logger.exception("google_webhook: pull_sync failed for %s", user_id)
        # Still 200 so Google doesn't retry forever — the periodic cron will
        # eventually reconcile.
        return {"ok": False, "state": state, "error": "pull_failed"}


class ConflictResolveBody(BaseModel):
    use: str   # "google" → pull single event; "local" → force-push, overwriting Google


@router.post("/events/{event_id}/sync/resolve")
async def resolve_conflict(
    event_id: str,
    body: ConflictResolveBody,
    current_user: dict = Depends(get_current_user),
):
    """Resolve a sync conflict for one event.

    use='google' → pull Google's version into our DB.
    use='local'  → re-push our version, ignoring Google's etag (overwrites).
    Only the event creator can resolve."""
    me = str(current_user.get("user_id") or "")
    ev_res = (
        supabase.table("calendar_events").select("*").eq("event_id", event_id).limit(1).execute()
    )
    if not ev_res.data:
        raise HTTPException(status_code=404, detail="Event not found")
    ev = ev_res.data[0]
    if str(ev.get("created_by")) != me:
        raise HTTPException(status_code=403, detail="Only the creator can resolve")

    use = (body.use or "").strip().lower()
    if use not in ("google", "local"):
        raise HTTPException(status_code=400, detail="use must be 'google' or 'local'")

    try:
        if use == "google":
            result = gcal.pull_single_event(me, event_id)
            if result is None:
                raise HTTPException(status_code=502, detail="Could not pull event from Google")
        else:
            mapping = gcal.push_event(me, ev, force=True)
            if mapping is None:
                raise HTTPException(status_code=502, detail="Could not push event to Google")
    except gcal.GoogleNotConfigured:
        raise HTTPException(status_code=503, detail="Google OAuth is not configured")
    except gcal.GoogleSyncError as e:
        raise HTTPException(status_code=502, detail=str(e))

    refreshed = (
        supabase.table("calendar_events").select("*").eq("event_id", event_id).limit(1).execute()
    )
    atts = _fetch_attendees([event_id]).get(event_id, [])
    sync = _fetch_sync_mappings([event_id]).get(event_id)
    return _serialize_event(refreshed.data[0], atts, sync)


@router.get("/cron/google-watch-renew")
@router.post("/cron/google-watch-renew")
async def cron_google_watch_renew(request: Request):
    """Renew events.watch channels nearing expiry. Schedule daily.

    curl -fsSL -H "X-Cron-Secret: $NGM_CRON_SECRET" \\
        https://ngm-fastapi.onrender.com/calendar/cron/google-watch-renew
    """
    _require_cron(request)

    if not gcal.is_google_configured():
        return {"ok": False, "configured": False}

    counts = gcal.renew_expiring_watches()
    return {"ok": True, **counts}
