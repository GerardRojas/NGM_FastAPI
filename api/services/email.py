"""
Transactional email — the one place NGM sends mail (client portal invites,
notifications). Provider: Resend (HTTP API). Fail-soft by design: a missing key
or a provider error NEVER raises, so a notification can't break the action that
triggered it. Configure via env:

  RESEND_API_KEY   provider key (no key -> emails are skipped, logged)
  EMAIL_FROM       "NGM Managements <noreply@ngmanagements.com>" (verified sender)
  FRONTEND_URL     base for portal deep-links (default www.ngmanagements.com)
"""

import os
import html as _html
import logging
from typing import Iterable, Optional, Union

import httpx

logger = logging.getLogger(__name__)

RESEND_API_KEY = os.getenv("RESEND_API_KEY")
EMAIL_FROM = os.getenv("EMAIL_FROM", "NGM Managements <noreply@ngmanagements.com>")
FRONTEND_URL = os.getenv("FRONTEND_URL", "https://www.ngmanagements.com").rstrip("/")


def email_enabled() -> bool:
    return bool(RESEND_API_KEY)


def send_email(
    to: Union[str, Iterable[str]],
    subject: str,
    html: str,
    text: Optional[str] = None,
) -> bool:
    """Send one email. Returns True on success; False (logged) when not configured
    or on error. Never raises."""
    recipients = [to] if isinstance(to, str) else [r for r in to if r]
    recipients = [r for r in recipients if r]
    if not recipients:
        return False
    if not RESEND_API_KEY:
        logger.info("[email] RESEND_API_KEY unset — skipped %r to %s", subject, recipients)
        return False
    payload = {"from": EMAIL_FROM, "to": recipients, "subject": subject, "html": html}
    if text:
        payload["text"] = text
    try:
        resp = httpx.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {RESEND_API_KEY}", "Content-Type": "application/json"},
            json=payload,
            timeout=15,
        )
        if resp.status_code >= 300:
            logger.error("[email] send failed (%s): %s", resp.status_code, resp.text[:300])
            return False
        return True
    except Exception as e:
        logger.error("[email] send error: %s", e)
        return False


def esc(value: object) -> str:
    return _html.escape(str(value or ""))


def render_email(title: str, body_html: str, cta_label: Optional[str] = None, cta_url: Optional[str] = None) -> str:
    """Branded NGM wrapper around a body fragment. `body_html` is trusted HTML the
    caller has already escaped where needed."""
    button = ""
    if cta_label and cta_url:
        button = (
            f'<tr><td style="padding:8px 0 4px">'
            f'<a href="{esc(cta_url)}" style="display:inline-block;background:#3dca8b;color:#0b0f17;'
            f'text-decoration:none;font-weight:600;padding:11px 22px;border-radius:8px;font-size:14px">'
            f"{esc(cta_label)}</a></td></tr>"
        )
    return f"""\
<!doctype html><html><body style="margin:0;background:#f4f5f7;padding:24px;font-family:Arial,Helvetica,sans-serif;color:#1f2430">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
    <tr><td align="center">
      <table role="presentation" width="100%" style="max-width:520px;background:#ffffff;border:1px solid #e6e8ec;border-radius:14px;overflow:hidden">
        <tr><td style="background:#0b0f17;padding:16px 22px;color:#ffffff;font-weight:700;font-size:16px;letter-spacing:.02em">NGM Managements</td></tr>
        <tr><td style="padding:22px">
          <h1 style="margin:0 0 12px;font-size:18px;color:#1f2430">{esc(title)}</h1>
          <div style="font-size:14px;line-height:1.55;color:#3a3f4b">{body_html}</div>
          <table role="presentation" cellpadding="0" cellspacing="0">{button}</table>
        </td></tr>
        <tr><td style="padding:14px 22px;border-top:1px solid #eef0f3;color:#9aa1ad;font-size:12px">
          You're receiving this because your project team shared a workspace with you.
        </td></tr>
      </table>
    </td></tr>
  </table>
</body></html>"""
