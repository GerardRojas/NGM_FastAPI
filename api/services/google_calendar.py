"""
Google Calendar bidirectional sync (Phase 4).

Architecture:
  * One Google calendar per user (default `primary`). The user OAuths in once;
    we store access + refresh tokens in `google_calendar_tokens`.
  * Push (local -> Google) is best-effort and called inline from the existing
    create/update/delete endpoints in api.routers.calendar. Failures are logged
    and don't break the local write — the periodic pull-sync reconciles.
  * Pull (Google -> local) is incremental via Google's syncToken. On first
    sync we list all events from `time_min=NOW()-30d` to seed the cursor; on
    subsequent syncs we pass `syncToken` and receive only the delta.
  * Conflict resolution: last-write-wins. Push uses If-Match etag. On 412
    (etag mismatch) we trust Google and re-pull that single event.

Gated on env vars:
    GOOGLE_CLIENT_ID
    GOOGLE_CLIENT_SECRET
    GOOGLE_REDIRECT_URI   (e.g. https://ngm-fastapi.onrender.com/calendar/google/callback)

If any are missing, is_google_configured() returns False and every public
function in this module raises GoogleNotConfigured. The router catches that
and surfaces "Not configured" to the UI.
"""
from __future__ import annotations

import logging
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlencode

import httpx

from api.supabase_client import supabase

logger = logging.getLogger(__name__)

_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
_TOKEN_URL = "https://oauth2.googleapis.com/token"
_CAL_BASE = "https://www.googleapis.com/calendar/v3"

OAUTH_SCOPES = " ".join([
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/userinfo.email",
    "openid",
])

# OAuth `state` -> user_id is persisted to the `oauth_states` table so the
# integration is multi-instance safe and survives dyno restarts. TTL ~10 min.
_STATE_TTL = timedelta(minutes=10)


class GoogleNotConfigured(RuntimeError):
    """Raised when GOOGLE_CLIENT_ID/SECRET/REDIRECT_URI aren't set in env."""


class GoogleSyncError(RuntimeError):
    pass


def is_google_configured() -> bool:
    return all(os.getenv(k) for k in ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_REDIRECT_URI"))


def _require_config() -> Tuple[str, str, str]:
    cid = os.getenv("GOOGLE_CLIENT_ID")
    csec = os.getenv("GOOGLE_CLIENT_SECRET")
    redir = os.getenv("GOOGLE_REDIRECT_URI")
    if not (cid and csec and redir):
        raise GoogleNotConfigured("Google OAuth env vars are not set")
    return cid, csec, redir


# ============================================================================
# OAuth helpers
# ============================================================================

def build_auth_url(user_id: str) -> str:
    """Generate the consent-screen URL the user is redirected to. Persists a
    `state` token tied to user_id so the callback can attribute the result.
    Multi-instance safe (state lives in DB, not in process memory)."""
    cid, _, redir = _require_config()
    _gc_states()
    state = secrets.token_urlsafe(24)
    expires_at = datetime.now(timezone.utc) + _STATE_TTL
    supabase.table("oauth_states").insert({
        "state": state,
        "user_id": str(user_id),
        "provider": "google",
        "expires_at": expires_at.isoformat(),
    }).execute()
    params = {
        "client_id": cid,
        "redirect_uri": redir,
        "response_type": "code",
        "scope": OAUTH_SCOPES,
        "access_type": "offline",       # required to receive a refresh_token
        "prompt": "consent",            # force refresh_token return on re-connect
        "include_granted_scopes": "true",
        "state": state,
    }
    return f"{_AUTH_URL}?{urlencode(params)}"


def consume_state(state: str) -> Optional[str]:
    """Pop a state token, returning the user_id it was bound to (or None).
    Single-use — the row is deleted regardless of whether it's expired."""
    res = (
        supabase.table("oauth_states").select("user_id, expires_at")
        .eq("state", state).limit(1).execute()
    )
    if not res.data:
        return None
    row = res.data[0]
    supabase.table("oauth_states").delete().eq("state", state).execute()
    expires_at = _parse_iso(row.get("expires_at"))
    if expires_at and expires_at < datetime.now(timezone.utc):
        return None
    return str(row.get("user_id") or "")


def _gc_states() -> None:
    """Sweep expired rows. Best-effort; race-safe (deletes are idempotent)."""
    try:
        now_iso = datetime.now(timezone.utc).isoformat()
        supabase.table("oauth_states").delete().lt("expires_at", now_iso).execute()
    except Exception:
        logger.exception("oauth_states GC failed")


def exchange_code_for_tokens(code: str) -> Dict[str, Any]:
    """POST /token to swap an authorization code for tokens + email."""
    cid, csec, redir = _require_config()
    data = {
        "code": code,
        "client_id": cid,
        "client_secret": csec,
        "redirect_uri": redir,
        "grant_type": "authorization_code",
    }
    with httpx.Client(timeout=15) as client:
        res = client.post(_TOKEN_URL, data=data)
        if res.status_code >= 400:
            raise GoogleSyncError(f"Token exchange failed: {res.status_code} {res.text}")
        tokens = res.json()
        # Fetch the user's Google email so we can show it in the UI.
        email = None
        try:
            info_res = client.get(
                "https://openidconnect.googleapis.com/v1/userinfo",
                headers={"Authorization": f"Bearer {tokens['access_token']}"},
            )
            if info_res.status_code < 400:
                email = info_res.json().get("email")
        except Exception:
            pass
        tokens["email"] = email
        return tokens


def _refresh_access_token(refresh_token: str) -> Dict[str, Any]:
    cid, csec, _ = _require_config()
    data = {
        "client_id": cid,
        "client_secret": csec,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    }
    with httpx.Client(timeout=15) as client:
        res = client.post(_TOKEN_URL, data=data)
        if res.status_code >= 400:
            raise GoogleSyncError(f"Token refresh failed: {res.status_code} {res.text}")
        return res.json()


def _get_valid_access_token(user_id: str) -> Tuple[str, str]:
    """Return (access_token, calendar_id). Refreshes if access_token is stale."""
    row_res = (
        supabase.table("google_calendar_tokens")
        .select("*").eq("user_id", user_id).limit(1).execute()
    )
    if not row_res.data:
        raise GoogleSyncError("User has not connected Google Calendar")
    row = row_res.data[0]
    expires_at_str = row.get("token_expires_at")
    expires_at = _parse_iso(expires_at_str) or datetime.now(timezone.utc)
    if expires_at - datetime.now(timezone.utc) < timedelta(seconds=60):
        new_tokens = _refresh_access_token(row["refresh_token"])
        new_expiry = datetime.now(timezone.utc) + timedelta(seconds=int(new_tokens.get("expires_in") or 3600))
        supabase.table("google_calendar_tokens").update({
            "access_token": new_tokens["access_token"],
            "token_expires_at": new_expiry.isoformat(),
        }).eq("user_id", user_id).execute()
        return new_tokens["access_token"], row.get("calendar_id") or "primary"
    return row["access_token"], row.get("calendar_id") or "primary"


def upsert_tokens(user_id: str, token_response: Dict[str, Any]) -> None:
    """Persist a freshly-issued OAuth bundle. Called from the callback handler."""
    expires_in = int(token_response.get("expires_in") or 3600)
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
    row = {
        "user_id": user_id,
        "google_user_email": token_response.get("email"),
        "calendar_id": "primary",
        "access_token": token_response["access_token"],
        # Google returns refresh_token only on the FIRST consent (or with
        # prompt=consent). Preserve the existing one if missing on reconnect.
        "refresh_token": token_response.get("refresh_token"),
        "token_expires_at": expires_at.isoformat(),
        "scope": token_response.get("scope"),
    }
    existing = (
        supabase.table("google_calendar_tokens")
        .select("refresh_token").eq("user_id", user_id).limit(1).execute()
    )
    if existing.data and not row["refresh_token"]:
        row["refresh_token"] = existing.data[0]["refresh_token"]
    if not row["refresh_token"]:
        raise GoogleSyncError("Google did not return a refresh_token; revoke + retry with prompt=consent")
    supabase.table("google_calendar_tokens").upsert(row, on_conflict="user_id").execute()


def disconnect_user(user_id: str) -> None:
    """Clear tokens + sync mappings for this user. Local events remain.
    Also stops any active Google watch channels (best-effort)."""
    # Stop watch channels before tokens are gone (events.stop needs auth).
    try:
        stop_user_watches(user_id)
    except Exception:
        logger.exception("disconnect_user: stop_user_watches failed for %s", user_id)
    supabase.table("google_calendar_tokens").delete().eq("user_id", user_id).execute()
    # Mappings persist on the event row only; clearing them is best-effort.
    # We scope to events created_by this user so we don't disturb other users.
    try:
        my_events = (
            supabase.table("calendar_events").select("event_id").eq("created_by", user_id).execute()
        )
        ids = [r["event_id"] for r in (my_events.data or [])]
        if ids:
            supabase.table("calendar_sync_mappings").delete().in_("event_id", ids).execute()
    except Exception:
        logger.exception("disconnect_user: mapping cleanup failed for %s", user_id)


def status_for_user(user_id: str) -> Dict[str, Any]:
    if not is_google_configured():
        return {"configured": False, "connected": False}
    res = (
        supabase.table("google_calendar_tokens")
        .select("google_user_email, calendar_id, last_synced_at, connected_at")
        .eq("user_id", user_id).limit(1).execute()
    )
    if not res.data:
        return {"configured": True, "connected": False}
    row = res.data[0]

    # Whether realtime push notifications (webhooks) are active.
    realtime = False
    watch_expires_at: Optional[str] = None
    try:
        ch = (
            supabase.table("google_watch_channels").select("expiration")
            .eq("user_id", user_id).order("expiration", desc=True).limit(1).execute()
        )
        if ch.data:
            watch_expires_at = ch.data[0].get("expiration")
            exp = _parse_iso(watch_expires_at)
            realtime = bool(exp and exp > datetime.now(timezone.utc))
    except Exception:
        pass

    return {
        "configured": True,
        "connected": True,
        "email": row.get("google_user_email"),
        "calendar_id": row.get("calendar_id"),
        "last_synced_at": row.get("last_synced_at"),
        "connected_at": row.get("connected_at"),
        "realtime": realtime,
        "watch_expires_at": watch_expires_at,
    }


# ============================================================================
# Google Watch — realtime push notifications (events.watch / events.stop)
# ============================================================================
# Google delivers a POST to GOOGLE_WEBHOOK_URL whenever the watched calendar
# changes (per https://developers.google.com/calendar/api/guides/push). The
# webhook headers carry X-Goog-Channel-ID + X-Goog-Resource-State; we look up
# the user by channel_id and trigger pull_sync. Channels expire after at most
# 7 days; renew_expiring_watches() refreshes any with <2 days to live.

WATCH_TTL_DAYS = 7
WATCH_RENEW_THRESHOLD_DAYS = 2


def _watch_url() -> str:
    explicit = os.getenv("GOOGLE_WEBHOOK_URL")
    if explicit:
        return explicit
    # Derive from the OAuth redirect URI as a sensible default:
    #   .../calendar/google/callback  ->  .../calendar/google/webhook
    redir = os.getenv("GOOGLE_REDIRECT_URI") or ""
    if redir.endswith("/calendar/google/callback"):
        return redir[: -len("/calendar/google/callback")] + "/calendar/google/webhook"
    return ""


def register_watch(user_id: str) -> Optional[Dict[str, Any]]:
    """Open an events.watch channel for the user's calendar. Stores the channel
    in google_watch_channels so the webhook can attribute incoming pings.

    Returns the new channel row, or None if not configured / call failed."""
    if not is_google_configured():
        return None
    webhook = _watch_url()
    if not webhook:
        logger.warning("register_watch skipped: GOOGLE_WEBHOOK_URL not derivable")
        return None
    try:
        access, calendar_id = _get_valid_access_token(user_id)
    except GoogleSyncError:
        return None

    channel_id = secrets.token_urlsafe(24)
    token = secrets.token_urlsafe(24)
    ttl_seconds = WATCH_TTL_DAYS * 24 * 3600
    body = {
        "id": channel_id,
        "type": "web_hook",
        "address": webhook,
        "token": token,
        "params": {"ttl": str(ttl_seconds)},
    }
    headers = {"Authorization": f"Bearer {access}"}
    try:
        with httpx.Client(timeout=15) as client:
            res = client.post(
                f"{_CAL_BASE}/calendars/{calendar_id}/events/watch",
                headers=headers, json=body,
            )
            if res.status_code >= 400:
                logger.warning("register_watch failed %s %s", res.status_code, res.text)
                return None
            data = res.json()
    except Exception:
        logger.exception("register_watch: HTTP error")
        return None

    expiration_ms = data.get("expiration")
    if expiration_ms:
        expiration = datetime.fromtimestamp(int(expiration_ms) / 1000, tz=timezone.utc)
    else:
        expiration = datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)

    row = {
        "channel_id": channel_id,
        "user_id": str(user_id),
        "calendar_id": calendar_id,
        "resource_id": data.get("resourceId", ""),
        "expiration": expiration.isoformat(),
        "token": token,
    }
    supabase.table("google_watch_channels").insert(row).execute()
    return row


def stop_watch(channel_id: str) -> None:
    """POST events.stop for a channel and delete the local row. Best-effort —
    network failures still result in the local row being removed so the
    bookkeeping stays accurate (Google will time the channel out anyway)."""
    ch_res = (
        supabase.table("google_watch_channels").select("*")
        .eq("channel_id", channel_id).limit(1).execute()
    )
    if not ch_res.data:
        return
    ch = ch_res.data[0]
    try:
        access, _ = _get_valid_access_token(str(ch["user_id"]))
        with httpx.Client(timeout=10) as client:
            client.post(
                "https://www.googleapis.com/calendar/v3/channels/stop",
                headers={"Authorization": f"Bearer {access}"},
                json={"id": ch["channel_id"], "resourceId": ch["resource_id"]},
            )
    except Exception:
        logger.exception("stop_watch: HTTP error for channel %s", channel_id)
    supabase.table("google_watch_channels").delete().eq("channel_id", channel_id).execute()


def stop_user_watches(user_id: str) -> None:
    res = (
        supabase.table("google_watch_channels").select("channel_id")
        .eq("user_id", user_id).execute()
    )
    for r in (res.data or []):
        try:
            stop_watch(r["channel_id"])
        except Exception:
            logger.exception("stop_user_watches: channel %s failed", r.get("channel_id"))


def lookup_user_by_channel(channel_id: str, token: Optional[str]) -> Optional[str]:
    """Webhook handler hook: turn X-Goog-Channel-ID into a user_id, optionally
    verifying the shared token (X-Goog-Channel-Token)."""
    res = (
        supabase.table("google_watch_channels").select("user_id, token")
        .eq("channel_id", channel_id).limit(1).execute()
    )
    if not res.data:
        return None
    row = res.data[0]
    if row.get("token") and token and row["token"] != token:
        logger.warning("lookup_user_by_channel: token mismatch for channel %s", channel_id)
        return None
    return str(row["user_id"])


def renew_expiring_watches() -> Dict[str, int]:
    """Renew every channel within WATCH_RENEW_THRESHOLD_DAYS of expiry. Returns
    a {renewed, errors, skipped} count for cron telemetry."""
    horizon = datetime.now(timezone.utc) + timedelta(days=WATCH_RENEW_THRESHOLD_DAYS)
    res = (
        supabase.table("google_watch_channels").select("*")
        .lte("expiration", horizon.isoformat()).execute()
    )
    renewed = 0
    errors = 0
    skipped = 0
    for ch in (res.data or []):
        try:
            new_ch = register_watch(str(ch["user_id"]))
            if new_ch:
                stop_watch(ch["channel_id"])
                renewed += 1
            else:
                skipped += 1
        except Exception:
            errors += 1
            logger.exception("renew_expiring_watches: failed for channel %s", ch.get("channel_id"))
    return {"renewed": renewed, "errors": errors, "skipped": skipped}


# ============================================================================
# Event payload mapping (local row <-> Google body)
# ============================================================================

def _local_to_google_body(row: Dict[str, Any]) -> Dict[str, Any]:
    """Map a calendar_events row to a Google Calendar event body."""
    body: Dict[str, Any] = {
        "summary": row.get("title") or "",
        "description": row.get("description") or None,
        "location": row.get("location") or None,
    }
    if row.get("all_day"):
        body["start"] = {"date": _iso_date(row.get("start_at"))}
        body["end"] = {"date": _iso_date(row.get("end_at"))}
    else:
        body["start"] = {"dateTime": row.get("start_at"), "timeZone": "UTC"}
        body["end"] = {"dateTime": row.get("end_at"), "timeZone": "UTC"}
    if row.get("rrule"):
        body["recurrence"] = [f"RRULE:{row['rrule']}"]
    if row.get("reminder_minutes"):
        body["reminders"] = {
            "useDefault": False,
            "overrides": [{"method": "popup", "minutes": int(row["reminder_minutes"])}],
        }
    return body


def _google_to_local_patch(g: Dict[str, Any]) -> Dict[str, Any]:
    """Map a Google event resource into a calendar_events update dict."""
    start_obj = g.get("start") or {}
    end_obj = g.get("end") or {}
    all_day = "date" in start_obj
    start_at = start_obj.get("dateTime") or (start_obj.get("date") + "T00:00:00Z" if start_obj.get("date") else None)
    end_at = end_obj.get("dateTime") or (end_obj.get("date") + "T00:00:00Z" if end_obj.get("date") else None)
    rec_list = g.get("recurrence") or []
    rrule = None
    for r in rec_list:
        if isinstance(r, str) and r.upper().startswith("RRULE:"):
            rrule = r[6:]
            break
    patch: Dict[str, Any] = {
        "title": g.get("summary") or "(no title)",
        "description": g.get("description") or None,
        "location": g.get("location") or None,
        "start_at": start_at,
        "end_at": end_at,
        "all_day": all_day,
        "rrule": rrule,
    }
    return patch


# ============================================================================
# Push (local -> Google)
# ============================================================================

def push_event(
    user_id: str,
    event_row: Dict[str, Any],
    *,
    force: bool = False,
) -> Optional[Dict[str, Any]]:
    """Insert-or-update the local event in Google. Returns the mapping row
    (or None if not connected). The mapping is annotated with sync_status so
    the UI can surface conflicts / pending pushes.

    If `force=True`, the existing etag is NOT sent (If-Match is omitted), which
    overwrites Google's version unconditionally. Used by the "Use mine" conflict
    resolution flow."""
    if not is_google_configured():
        return None
    try:
        access, calendar_id = _get_valid_access_token(user_id)
    except GoogleSyncError:
        return None
    event_id = str(event_row.get("event_id"))
    body = _local_to_google_body(event_row)

    existing_map = (
        supabase.table("calendar_sync_mappings").select("*")
        .eq("event_id", event_id).limit(1).execute()
    )
    headers = {"Authorization": f"Bearer {access}"}

    try:
        with httpx.Client(timeout=15) as client:
            if existing_map.data:
                m = existing_map.data[0]
                gid = m["google_event_id"]
                req_headers = dict(headers)
                if not force and m.get("google_etag"):
                    req_headers["If-Match"] = m["google_etag"]
                res = client.patch(
                    f"{_CAL_BASE}/calendars/{calendar_id}/events/{gid}",
                    headers=req_headers, json=body,
                )
                if res.status_code == 412:
                    # Etag mismatch — Google has a newer version. Mark as
                    # conflict so the UI can prompt; next pull-sync still runs.
                    logger.warning("push_event: 412 etag mismatch for %s; marking conflict", event_id)
                    supabase.table("calendar_sync_mappings").update({
                        "sync_status": "conflict",
                    }).eq("event_id", event_id).execute()
                    return {**m, "sync_status": "conflict"}
                if res.status_code >= 400:
                    logger.warning("push_event: update failed %s %s", res.status_code, res.text)
                    supabase.table("calendar_sync_mappings").update({
                        "sync_status": "push_failed",
                    }).eq("event_id", event_id).execute()
                    return {**m, "sync_status": "push_failed"}
                g = res.json()
            else:
                res = client.post(
                    f"{_CAL_BASE}/calendars/{calendar_id}/events",
                    headers=headers, json=body,
                )
                if res.status_code >= 400:
                    logger.warning("push_event: create failed %s %s", res.status_code, res.text)
                    return None
                g = res.json()
    except Exception:
        logger.exception("push_event: HTTP error")
        # If we already had a mapping, mark it pending so the cron retries.
        if existing_map.data:
            try:
                supabase.table("calendar_sync_mappings").update({
                    "sync_status": "pending",
                }).eq("event_id", event_id).execute()
            except Exception:
                pass
        return None

    mapping_row = {
        "event_id": event_id,
        "google_event_id": g["id"],
        "google_calendar_id": calendar_id,
        "google_etag": g.get("etag"),
        "sync_source": "local",
        "sync_status": "synced",
        "last_synced_at": datetime.now(timezone.utc).isoformat(),
        "last_local_update_at": event_row.get("updated_at"),
    }
    supabase.table("calendar_sync_mappings").upsert(mapping_row, on_conflict="event_id").execute()
    return mapping_row


def pull_single_event(user_id: str, event_id: str) -> Optional[Dict[str, Any]]:
    """Force-fetch one event from Google by mapping, applying it locally.
    Used by the "Use Google's version" conflict resolution flow."""
    if not is_google_configured():
        return None
    map_res = (
        supabase.table("calendar_sync_mappings").select("*")
        .eq("event_id", event_id).limit(1).execute()
    )
    if not map_res.data:
        return None
    m = map_res.data[0]
    try:
        access, _ = _get_valid_access_token(user_id)
    except GoogleSyncError:
        return None
    headers = {"Authorization": f"Bearer {access}"}
    try:
        with httpx.Client(timeout=15) as client:
            res = client.get(
                f"{_CAL_BASE}/calendars/{m['google_calendar_id']}/events/{m['google_event_id']}",
                headers=headers,
            )
            if res.status_code >= 400:
                logger.warning("pull_single_event: %s %s", res.status_code, res.text)
                return None
            g = res.json()
    except Exception:
        logger.exception("pull_single_event: HTTP error")
        return None

    patch = _google_to_local_patch(g)
    supabase.table("calendar_events").update(patch).eq("event_id", event_id).execute()
    supabase.table("calendar_sync_mappings").update({
        "google_etag": g.get("etag"),
        "sync_status": "synced",
        "last_synced_at": datetime.now(timezone.utc).isoformat(),
    }).eq("event_id", event_id).execute()
    return patch


def push_delete(user_id: str, event_id: str) -> None:
    """Remove the event from Google. Mapping row is removed by FK cascade
    when the local event is deleted; here we just call the API."""
    if not is_google_configured():
        return
    try:
        access, _ = _get_valid_access_token(user_id)
    except GoogleSyncError:
        return
    m = (
        supabase.table("calendar_sync_mappings").select("*")
        .eq("event_id", event_id).limit(1).execute()
    )
    if not m.data:
        return
    gid = m.data[0]["google_event_id"]
    calendar_id = m.data[0]["google_calendar_id"]
    headers = {"Authorization": f"Bearer {access}"}
    try:
        with httpx.Client(timeout=15) as client:
            client.delete(f"{_CAL_BASE}/calendars/{calendar_id}/events/{gid}", headers=headers)
    except Exception:
        logger.exception("push_delete: HTTP error")


# ============================================================================
# Pull (Google -> local) — incremental via syncToken
# ============================================================================

def pull_sync(user_id: str) -> Dict[str, int]:
    """Pull the user's Google events into the local DB. Idempotent — uses the
    stored sync_token for delta sync; on token expiry (410) restarts from a
    seed window."""
    if not is_google_configured():
        return {"created": 0, "updated": 0, "deleted": 0, "skipped": 0}

    access, calendar_id = _get_valid_access_token(user_id)
    tok_row_res = (
        supabase.table("google_calendar_tokens").select("sync_token")
        .eq("user_id", user_id).limit(1).execute()
    )
    sync_token = (tok_row_res.data[0].get("sync_token") if tok_row_res.data else None)

    headers = {"Authorization": f"Bearer {access}"}
    base_params: Dict[str, Any] = {"singleEvents": "false", "showDeleted": "true", "maxResults": 250}

    def fetch(params: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], Optional[str], Optional[str]]:
        items: List[Dict[str, Any]] = []
        page_token: Optional[str] = None
        next_sync_token: Optional[str] = None
        with httpx.Client(timeout=20) as client:
            while True:
                p = dict(params)
                if page_token:
                    p["pageToken"] = page_token
                res = client.get(
                    f"{_CAL_BASE}/calendars/{calendar_id}/events",
                    headers=headers, params=p,
                )
                if res.status_code == 410:
                    # Sync token expired; signal caller to restart.
                    return [], None, "__expired__"
                if res.status_code >= 400:
                    raise GoogleSyncError(f"events.list failed: {res.status_code} {res.text}")
                payload = res.json()
                items.extend(payload.get("items") or [])
                page_token = payload.get("nextPageToken")
                if not page_token:
                    next_sync_token = payload.get("nextSyncToken")
                    break
        return items, next_sync_token, None

    if sync_token:
        items, next_token, marker = fetch({**base_params, "syncToken": sync_token})
        if marker == "__expired__":
            sync_token = None  # fall through to seed
    if not sync_token:
        seed_from = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        items, next_token, _ = fetch({**base_params, "timeMin": seed_from})

    counts = {"created": 0, "updated": 0, "deleted": 0, "skipped": 0}
    for g in items:
        try:
            r = _apply_pulled_event(user_id, calendar_id, g)
            counts[r] = counts.get(r, 0) + 1
        except Exception:
            logger.exception("pull_sync: failed on event %s", g.get("id"))

    update_row: Dict[str, Any] = {"last_synced_at": datetime.now(timezone.utc).isoformat()}
    if next_token:
        update_row["sync_token"] = next_token
    supabase.table("google_calendar_tokens").update(update_row).eq("user_id", user_id).execute()

    return counts


def _apply_pulled_event(user_id: str, calendar_id: str, g: Dict[str, Any]) -> str:
    """Apply one Google event to our DB. Returns 'created'|'updated'|'deleted'|'skipped'."""
    gid = g.get("id")
    if not gid:
        return "skipped"

    map_res = (
        supabase.table("calendar_sync_mappings").select("*")
        .eq("google_calendar_id", calendar_id).eq("google_event_id", gid).limit(1).execute()
    )
    mapping = map_res.data[0] if map_res.data else None

    if g.get("status") == "cancelled":
        if mapping:
            supabase.table("calendar_events").delete().eq("event_id", mapping["event_id"]).execute()
            # Mapping cascades.
            return "deleted"
        return "skipped"

    patch = _google_to_local_patch(g)
    now_iso = datetime.now(timezone.utc).isoformat()

    if mapping:
        # Compare etag: if unchanged, skip.
        if mapping.get("google_etag") == g.get("etag"):
            return "skipped"
        supabase.table("calendar_events").update(patch).eq("event_id", mapping["event_id"]).execute()
        supabase.table("calendar_sync_mappings").update({
            "google_etag": g.get("etag"),
            "sync_status": "synced",
            "last_synced_at": now_iso,
        }).eq("event_id", mapping["event_id"]).execute()
        return "updated"

    # New event from Google — create locally.
    new_row = {
        **patch,
        "created_by": user_id,
        "visibility": "private",   # Google-originated events default to private locally
    }
    ins = supabase.table("calendar_events").insert(new_row).execute()
    if not ins.data:
        return "skipped"
    event_id = ins.data[0]["event_id"]
    supabase.table("calendar_sync_mappings").insert({
        "event_id": event_id,
        "google_event_id": gid,
        "google_calendar_id": calendar_id,
        "google_etag": g.get("etag"),
        "sync_source": "google",
        "sync_status": "synced",
        "last_synced_at": now_iso,
        "last_local_update_at": None,
    }).execute()
    return "created"


# ============================================================================
# Misc
# ============================================================================

def _iso_date(iso_ts: Optional[str]) -> str:
    """YYYY-MM-DD from an ISO timestamp (used for all-day events)."""
    if not iso_ts:
        return ""
    try:
        v = str(iso_ts).replace("Z", "+00:00")
        d = datetime.fromisoformat(v)
        return d.date().isoformat()
    except Exception:
        return str(iso_ts)[:10]


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
