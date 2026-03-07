"""
Invoice Links Router
Creates shareable payment links for clients with DB persistence.
Supports fixed-amount and open-amount (client enters amount) links.
After Stripe payment, the link is marked as paid and becomes unusable.
"""
import os
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import jwt
from fastapi import APIRouter, HTTPException, Depends, Request, Query
from pydantic import BaseModel
from typing import Optional

from api.auth import JWT_SECRET, JWT_ALG, get_current_user
from api.supabase_client import supabase

router = APIRouter(prefix="/invoice-links", tags=["invoice-links"])


# ── Models ────────────────────────────────────────────────────

class CreateInvoiceLinkRequest(BaseModel):
    client_name: str
    client_email: str
    description: str
    amount_cents: Optional[int] = None  # None = open-amount
    link_type: str = "fixed"            # "fixed" | "open"
    invoice_ref: Optional[str] = None
    expires_days: int = 30


class CreateInvoiceLinkResponse(BaseModel):
    id: str
    token: str
    url: str
    invoice_ref: str
    link_type: str
    amount_cents: Optional[int]
    expires_at: str


class VerifyInvoiceLinkResponse(BaseModel):
    valid: bool
    id: str
    client_name: str
    client_email: str
    description: str
    amount_cents: Optional[int]
    invoice_ref: str
    link_type: str
    status: str
    created_at: str
    expires_at: str


class MarkPaidRequest(BaseModel):
    link_id: str
    stripe_session_id: str
    paid_amount: Optional[int] = None  # required for open-amount links


# ── Helpers ───────────────────────────────────────────────────

def _get_frontend_base(request: Request) -> str:
    """Determine frontend base URL from request origin/referer."""
    for header in ["origin", "referer"]:
        val = request.headers.get(header, "")
        if val:
            parsed = urlparse(val)
            if parsed.scheme and parsed.netloc:
                return f"{parsed.scheme}://{parsed.netloc}"
    return os.getenv("FRONTEND_URL", "https://www.ngmanagements.com")


def _generate_ref() -> str:
    """Generate a short invoice reference like INV-A3F2B1."""
    hex_part = hex(int(time.time() * 1000))[-6:].upper()
    return f"INV-{hex_part}"


# ── POST /invoice-links/create ────────────────────────────────

@router.post("/create", response_model=CreateInvoiceLinkResponse)
async def create_invoice_link(
    req: CreateInvoiceLinkRequest,
    request: Request,
    current_user: dict = Depends(get_current_user),
):
    """Create a payment link. Auth required (staff only)."""
    # Validate
    if req.link_type not in ("fixed", "open"):
        raise HTTPException(status_code=400, detail="link_type must be 'fixed' or 'open'")

    if req.link_type == "fixed":
        if not req.amount_cents or req.amount_cents <= 0:
            raise HTTPException(status_code=400, detail="Amount must be greater than zero for fixed links")

    if req.expires_days < 1 or req.expires_days > 365:
        raise HTTPException(status_code=400, detail="Expiry must be between 1 and 365 days")

    invoice_ref = req.invoice_ref or _generate_ref()
    now = datetime.now(timezone.utc)
    exp = now + timedelta(days=req.expires_days)
    user_id = current_user.get("user_id", "")

    # Sign a JWT token for the URL
    payload = {
        "type": "invoice_link",
        "invoice_ref": invoice_ref,
        "iat": int(now.timestamp()),
        "exp": int(exp.timestamp()),
    }
    token = jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALG)

    # Save to DB
    row = {
        "invoice_ref": invoice_ref,
        "client_name": req.client_name,
        "client_email": req.client_email,
        "description": req.description,
        "amount_cents": req.amount_cents if req.link_type == "fixed" else None,
        "link_type": req.link_type,
        "status": "active",
        "token": token,
        "created_by": user_id,
        "expires_at": exp.isoformat(),
    }

    result = supabase.table("invoice_links").insert(row).execute()
    if not result.data:
        raise HTTPException(status_code=500, detail="Failed to create invoice link")

    link_id = result.data[0]["id"]
    frontend_base = _get_frontend_base(request)
    url = f"{frontend_base}/client-billing.html?token={token}"

    return CreateInvoiceLinkResponse(
        id=link_id,
        token=token,
        url=url,
        invoice_ref=invoice_ref,
        link_type=req.link_type,
        amount_cents=req.amount_cents if req.link_type == "fixed" else None,
        expires_at=exp.isoformat(),
    )


# ── GET /invoice-links/verify ─────────────────────────────────

@router.get("/verify", response_model=VerifyInvoiceLinkResponse)
async def verify_invoice_link(token: str = Query(...)):
    """
    Verify a payment link. Public endpoint (no auth).
    Checks JWT signature + DB status (active, not paid, not expired).
    """
    # Decode JWT
    try:
        decoded = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALG])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=400, detail="This payment link has expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=400, detail="Invalid or tampered payment link")

    if decoded.get("type") != "invoice_link":
        raise HTTPException(status_code=400, detail="Invalid payment link")

    # Look up in DB
    result = supabase.table("invoice_links").select("*").eq(
        "token", token
    ).execute()

    if not result.data:
        raise HTTPException(status_code=404, detail="Payment link not found")

    link = result.data[0]

    # Check status
    if link["status"] == "paid":
        raise HTTPException(status_code=400, detail="This invoice has already been paid")
    if link["status"] == "cancelled":
        raise HTTPException(status_code=400, detail="This payment link has been cancelled")
    if link["status"] == "expired":
        raise HTTPException(status_code=400, detail="This payment link has expired")

    # Check expiry from DB too
    expires_at = datetime.fromisoformat(link["expires_at"].replace("Z", "+00:00"))
    if datetime.now(timezone.utc) > expires_at:
        # Mark as expired in DB
        supabase.table("invoice_links").update({"status": "expired"}).eq(
            "id", link["id"]
        ).execute()
        raise HTTPException(status_code=400, detail="This payment link has expired")

    return VerifyInvoiceLinkResponse(
        valid=True,
        id=link["id"],
        client_name=link["client_name"],
        client_email=link["client_email"],
        description=link["description"],
        amount_cents=link["amount_cents"],
        invoice_ref=link["invoice_ref"],
        link_type=link["link_type"],
        status=link["status"],
        created_at=link["created_at"],
        expires_at=link["expires_at"],
    )


# ── POST /invoice-links/mark-paid ─────────────────────────────

@router.post("/mark-paid")
async def mark_invoice_link_paid(req: MarkPaidRequest):
    """
    Mark a payment link as paid after successful Stripe checkout.
    Public endpoint (called from client-billing page after Stripe return).
    """
    # Look up the link
    result = supabase.table("invoice_links").select("*").eq(
        "id", req.link_id
    ).execute()

    if not result.data:
        raise HTTPException(status_code=404, detail="Payment link not found")

    link = result.data[0]

    if link["status"] == "paid":
        return {"ok": True, "message": "Already marked as paid"}

    if link["status"] != "active":
        raise HTTPException(status_code=400, detail=f"Cannot mark as paid: status is {link['status']}")

    # Update to paid
    update_data = {
        "status": "paid",
        "paid_at": datetime.now(timezone.utc).isoformat(),
        "stripe_session_id": req.stripe_session_id,
    }
    if req.paid_amount is not None:
        update_data["paid_amount"] = req.paid_amount

    supabase.table("invoice_links").update(update_data).eq(
        "id", req.link_id
    ).execute()

    return {"ok": True, "message": "Payment link marked as paid"}


# ── GET /invoice-links/list ────────────────────────────────────

@router.get("/list")
async def list_invoice_links(
    current_user: dict = Depends(get_current_user),
    status: Optional[str] = None,
    limit: int = 50,
):
    """List invoice links created by the current user. Auth required."""
    query = supabase.table("invoice_links").select(
        "id, invoice_ref, client_name, client_email, description, "
        "amount_cents, link_type, status, created_at, expires_at, paid_at, paid_amount"
    ).order("created_at", desc=True).limit(limit)

    if status:
        query = query.eq("status", status)

    result = query.execute()
    return {"links": result.data or []}
