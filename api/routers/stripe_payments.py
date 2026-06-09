"""
Stripe Payments Router
Creates Checkout Sessions for client invoice payments, and receives Stripe
webhooks to confirm payments server-side (the reliable source of truth — the
client-side return page can be closed before it calls /mark-paid).
"""
import logging
import os
from datetime import datetime, timezone

import stripe
from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from pydantic import BaseModel
from typing import Optional

from api.supabase_client import supabase
from api.services import portal_notify

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/stripe", tags=["stripe"])

stripe.api_key = os.getenv("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")


# ── Request / Response models ──────────────────────────────────

class CheckoutRequest(BaseModel):
    invoice_id: str
    amount_cents: int  # total in cents (e.g. 25000 = $250.00)
    description: str  # project name or invoice description
    client_name: Optional[str] = None
    client_email: Optional[str] = None
    success_url: str  # full URL to redirect after payment
    cancel_url: str   # full URL to redirect on cancel


class PlanCheckoutRequest(BaseModel):
    plan_id: str           # starter | professional | enterprise
    plan_name: str         # display name
    amount_cents: int      # price in cents
    customer_email: Optional[str] = None
    success_url: str
    cancel_url: str


# ── POST /stripe/create-checkout-session ───────────────────────

@router.post("/create-checkout-session")
async def create_checkout_session(req: CheckoutRequest):
    """
    Create a Stripe Checkout Session (redirect mode).
    Returns the Stripe-hosted checkout URL.
    """
    if not stripe.api_key:
        raise HTTPException(status_code=500, detail="Stripe is not configured")

    if req.amount_cents <= 0:
        raise HTTPException(status_code=400, detail="Amount must be greater than zero")

    try:
        session = stripe.checkout.Session.create(
            mode="payment",
            payment_method_types=["card"],
            line_items=[{
                "price_data": {
                    "currency": "usd",
                    "unit_amount": req.amount_cents,
                    "product_data": {
                        "name": req.invoice_id,
                        "description": req.description,
                    },
                },
                "quantity": 1,
            }],
            metadata={
                "invoice_id": req.invoice_id,
            },
            customer_email=req.client_email or None,
            success_url=req.success_url,
            cancel_url=req.cancel_url,
        )

        return {"url": session.url, "session_id": session.id}

    except stripe.error.StripeError as e:
        raise HTTPException(status_code=502, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── POST /stripe/create-plan-checkout ──────────────────────────

@router.post("/create-plan-checkout")
async def create_plan_checkout(req: PlanCheckoutRequest):
    """
    Create a Stripe Checkout Session for a pricing plan purchase.
    Public endpoint — no auth required.
    """
    if not stripe.api_key:
        raise HTTPException(status_code=500, detail="Stripe is not configured")

    if req.amount_cents <= 0:
        raise HTTPException(status_code=400, detail="Amount must be greater than zero")

    try:
        session = stripe.checkout.Session.create(
            mode="payment",
            payment_method_types=["card"],
            line_items=[{
                "price_data": {
                    "currency": "usd",
                    "unit_amount": req.amount_cents,
                    "product_data": {
                        "name": f"NGM HUB — {req.plan_name} Plan",
                        "description": f"Monthly subscription - {req.plan_name}",
                    },
                },
                "quantity": 1,
            }],
            metadata={
                "plan_id": req.plan_id,
                "plan_name": req.plan_name,
            },
            customer_email=req.customer_email or None,
            success_url=req.success_url,
            cancel_url=req.cancel_url,
        )

        return {"url": session.url, "session_id": session.id}

    except stripe.error.StripeError as e:
        raise HTTPException(status_code=502, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── POST /stripe/webhook ───────────────────────────────────────

def _resolve_invoice_link(session: dict):
    """Find the invoice_links row a checkout session maps to, via
    metadata.invoice_id (its id or invoice_ref). Returns the row or None."""
    invoice_id = (session.get("metadata") or {}).get("invoice_id")
    if not invoice_id:
        return None  # plan checkout or unrelated session
    try:
        rows = supabase.table("invoice_links").select("*").eq("id", invoice_id).limit(1).execute().data or []
        if not rows:
            rows = supabase.table("invoice_links").select("*").eq("invoice_ref", invoice_id).limit(1).execute().data or []
    except Exception as e:
        logger.error("[stripe] webhook link lookup failed: %s", e)
        return None
    if not rows:
        logger.warning("[stripe] webhook: no invoice_link for invoice_id=%s", invoice_id)
        return None
    return rows[0]


def _portal_project_for_link(link_id: str):
    """The portal project this link is billed on, or None if it isn't a portal invoice."""
    try:
        pis = (
            supabase.table("portal_invoices")
            .select("project_id, client_id").eq("invoice_link_id", link_id).limit(1).execute()
        ).data or []
        return pis[0] if pis else None
    except Exception as e:
        logger.error("[stripe] webhook portal lookup failed: %s", e)
        return None


def _handle_invoice_paid(session: dict, background_tasks: BackgroundTasks) -> None:
    """Mark the invoice_link paid (idempotent) and email the portal client a
    receipt when the paid checkout maps to a portal invoice."""
    link = _resolve_invoice_link(session)
    if not link:
        return
    if link.get("status") == "paid":
        return  # already settled — idempotent

    try:
        supabase.table("invoice_links").update({
            "status": "paid",
            "paid_at": datetime.now(timezone.utc).isoformat(),
            "stripe_session_id": session.get("id"),
            "paid_amount": session.get("amount_total"),
        }).eq("id", link["id"]).execute()
    except Exception as e:
        logger.error("[stripe] webhook mark-paid failed: %s", e)
        return

    # If this link backs a portal invoice, email the client a receipt.
    pi = _portal_project_for_link(link["id"])
    if pi:
        background_tasks.add_task(
            portal_notify.notify_client_payment_received,
            pi["project_id"], session.get("amount_total"), link.get("description") or "",
        )


def _handle_payment_failed(session: dict, background_tasks: BackgroundTasks) -> None:
    """A delayed (async) payment failed. The invoice link stays active so the
    client can retry; we just nudge them by email with the pay link again."""
    link = _resolve_invoice_link(session)
    if not link or link.get("status") == "paid":
        return
    pi = _portal_project_for_link(link["id"])
    if not pi:
        return
    pay_url = f"{portal_notify.FRONTEND_URL}/client-billing.html?token={link.get('token')}"
    background_tasks.add_task(
        portal_notify.notify_client_payment_failed,
        pi["project_id"], link.get("amount_cents"), link.get("description") or "", pay_url,
    )


def _handle_session_expired(session: dict) -> None:
    """A checkout session timed out. NOTE: this does NOT expire the invoice — the
    payment link is still valid for a fresh checkout — so we only log it."""
    link = _resolve_invoice_link(session)
    if link:
        logger.info("[stripe] checkout session expired for invoice_link=%s (link still payable)", link.get("id"))


@router.post("/webhook")
async def stripe_webhook(request: Request, background_tasks: BackgroundTasks):
    """Stripe webhook receiver (public; verified by signature). Confirms invoice
    payments server-side so a closed return tab never leaves an invoice 'unpaid'."""
    if not STRIPE_WEBHOOK_SECRET:
        logger.error("[stripe] STRIPE_WEBHOOK_SECRET not set — webhook disabled")
        raise HTTPException(status_code=500, detail="Webhook not configured")
    payload = await request.body()
    sig = request.headers.get("stripe-signature", "")
    try:
        event = stripe.Webhook.construct_event(payload, sig, STRIPE_WEBHOOK_SECRET)
    except Exception as e:
        logger.warning("[stripe] webhook signature verification failed: %s", e)
        raise HTTPException(status_code=400, detail="Invalid signature")

    etype = event.get("type")
    session = (event.get("data") or {}).get("object") or {}
    if etype in ("checkout.session.completed", "checkout.session.async_payment_succeeded"):
        if session.get("payment_status") == "paid":
            _handle_invoice_paid(session, background_tasks)
    elif etype == "checkout.session.async_payment_failed":
        _handle_payment_failed(session, background_tasks)
    elif etype == "checkout.session.expired":
        _handle_session_expired(session)
    return {"received": True}


# ── GET /stripe/session-status/{session_id} ────────────────────

@router.get("/session-status/{session_id}")
async def get_session_status(session_id: str):
    """
    Check the payment status of a Checkout Session.
    Returns: paid | unpaid | expired
    """
    if not stripe.api_key:
        raise HTTPException(status_code=500, detail="Stripe is not configured")

    try:
        session = stripe.checkout.Session.retrieve(session_id)
        return {
            "status": session.payment_status,  # "paid" | "unpaid" | "no_payment_required"
            "invoice_id": session.metadata.get("invoice_id"),
            "amount_total": session.amount_total,
            "customer_email": session.customer_email,
        }
    except stripe.error.StripeError as e:
        raise HTTPException(status_code=502, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
