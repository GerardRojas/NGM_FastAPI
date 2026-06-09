"""
Client-portal notifications. Resolves who to email for a project's client(s),
honors per-client preferences (default-on; degrades gracefully if the prefs
table isn't there yet), and renders the branded emails. All entry points are
fire-and-forget safe — they swallow errors so a notification never breaks the
action that triggered it (called from FastAPI BackgroundTasks).
"""

import logging
from typing import List

from api.supabase_client import supabase
from api.services.email import send_email, render_email, esc, FRONTEND_URL

logger = logging.getLogger(__name__)


def _client_ids_for_project(project_id: str) -> List[str]:
    try:
        rows = (
            supabase.table("project_client_access")
            .select("client_id").eq("project_id", project_id).execute()
        ).data or []
    except Exception as e:
        logger.error("[notify] client lookup failed: %s", e)
        return []
    return list({str(r.get("client_id")) for r in rows if r.get("client_id")})


def _client_user_emails(client_id: str) -> List[str]:
    try:
        rows = (
            supabase.table("users")
            .select("user_name").eq("client_id", client_id).eq("account_type", "client").execute()
        ).data or []
    except Exception as e:
        logger.error("[notify] client emails failed: %s", e)
        return []
    return [r["user_name"] for r in rows if r.get("user_name")]


def client_wants(client_id: str, event: str) -> bool:
    """Per-client toggle for an event. Defaults to True when there's no prefs row
    (or the table doesn't exist yet) so clients are notified by default."""
    try:
        rows = (
            supabase.table("client_notification_prefs")
            .select(event).eq("client_id", client_id).limit(1).execute()
        ).data or []
    except Exception:
        return True  # table not migrated yet -> default on
    if not rows:
        return True
    val = rows[0].get(event)
    return True if val is None else bool(val)


def notify_client_new_message(project_id: str, sender_name: str, preview: str) -> None:
    """A team member posted in the client conversation — email the project's
    client(s) (whose `new_message` pref is on)."""
    try:
        recipients: List[str] = []
        for cid in _client_ids_for_project(project_id):
            if not client_wants(cid, "new_message"):
                continue
            recipients += _client_user_emails(cid)
        recipients = list({r for r in recipients if r})
        if not recipients:
            return
        snippet = (preview or "").strip()
        if len(snippet) > 240:
            snippet = snippet[:240] + "…"
        body = (
            f"<p><strong>{esc(sender_name or 'Your team')}</strong> sent you a message:</p>"
            + (f'<blockquote style="margin:8px 0;padding:8px 12px;border-left:3px solid #3dca8b;'
               f'background:#f4faf7;color:#3a3f4b">{esc(snippet)}</blockquote>' if snippet else "")
        )
        html = render_email("New message from your team", body, "Open conversation", f"{FRONTEND_URL}/workspace/messages")
        send_email(recipients, "New message on your project", html)
    except Exception as e:
        logger.error("[notify] new_message failed: %s", e)


def notify_client_new_invoice(project_id: str, amount_cents, description: str, pay_url: str) -> None:
    """A new invoice was shared — email the project's client(s) with the pay link."""
    try:
        recipients: List[str] = []
        for cid in _client_ids_for_project(project_id):
            if not client_wants(cid, "new_invoice"):
                continue
            recipients += _client_user_emails(cid)
        recipients = list({r for r in recipients if r})
        if not recipients:
            return
        amount = f"${amount_cents / 100:,.2f}" if amount_cents else "an amount you enter"
        body = (
            f"<p>Your team at NGM Managements sent you a new invoice:</p>"
            f"<p><strong>{esc(description)}</strong><br>Amount due: <strong>{esc(amount)}</strong></p>"
            f"<p>Review and pay it securely online.</p>"
        )
        html = render_email("New invoice from NGM Managements", body, "View & pay invoice", pay_url)
        send_email(recipients, "You have a new invoice", html)
    except Exception as e:
        logger.error("[notify] new_invoice failed: %s", e)


def notify_client_payment_received(project_id: str, amount_cents, description: str) -> None:
    """Stripe confirmed a payment — email the project's client(s) a receipt."""
    try:
        recipients: List[str] = []
        for cid in _client_ids_for_project(project_id):
            if not client_wants(cid, "new_invoice"):
                continue
            recipients += _client_user_emails(cid)
        recipients = list({r for r in recipients if r})
        if not recipients:
            return
        amount_html = f" of <strong>${amount_cents / 100:,.2f}</strong>" if amount_cents else ""
        body = (
            f"<p>We received your payment{amount_html}. Thank you!</p>"
            + (f"<p>{esc(description)}</p>" if description else "")
        )
        html = render_email("Payment received", body, "View your invoices", f"{FRONTEND_URL}/workspace/invoices")
        send_email(recipients, "Payment received — thank you", html)
    except Exception as e:
        logger.error("[notify] payment_received failed: %s", e)


def notify_client_payment_failed(project_id: str, amount_cents, description: str, pay_url: str) -> None:
    """A delayed payment failed — email the client to retry (link still valid)."""
    try:
        recipients: List[str] = []
        for cid in _client_ids_for_project(project_id):
            if not client_wants(cid, "new_invoice"):
                continue
            recipients += _client_user_emails(cid)
        recipients = list({r for r in recipients if r})
        if not recipients:
            return
        amount_html = f" of <strong>${amount_cents / 100:,.2f}</strong>" if amount_cents else ""
        body = (
            f"<p>We weren't able to process your payment{amount_html}. No charge was made.</p>"
            + (f"<p>{esc(description)}</p>" if description else "")
            + "<p>You can try again using the secure link below.</p>"
        )
        html = render_email("Payment didn't go through", body, "Try payment again", pay_url)
        send_email(recipients, "Your payment didn't go through", html)
    except Exception as e:
        logger.error("[notify] payment_failed failed: %s", e)


def send_invite_email(to_email: str, accept_path: str, client_name: str = "") -> None:
    """Email a magic-link invitation to a client (the invite endpoint used to only
    return the path; now we actually send it)."""
    try:
        if not to_email:
            return
        url = f"{FRONTEND_URL}{accept_path}"
        who = f" for {esc(client_name)}" if client_name else ""
        body = (
            f"<p>Your project team at NGM Managements has invited you to your client "
            f"workspace{who} — where you can follow progress, view photos and plans, and "
            f"message the team.</p><p>Click below to set your password and get started. "
            f"This link expires in a couple of weeks.</p>"
        )
        html = render_email("You're invited to your project workspace", body, "Accept invitation", url)
        send_email(to_email, "Your NGM project workspace invitation", html)
    except Exception as e:
        logger.error("[notify] invite email failed: %s", e)
