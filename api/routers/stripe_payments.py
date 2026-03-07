"""
Stripe Payments Router
Creates Checkout Sessions for client invoice payments.
"""
import os
import stripe
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional

router = APIRouter(prefix="/stripe", tags=["stripe"])

stripe.api_key = os.getenv("STRIPE_SECRET_KEY", "")


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
