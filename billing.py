import os
import stripe
from fastapi import APIRouter, Request, HTTPException
from firebase import set_subscription_status
from datetime import datetime, timedelta

# Configure Stripe
stripe.api_key = os.getenv("STRIPE_SECRET_KEY", "")
STRIPE_PRICE_ID = os.getenv("STRIPE_PRICE_ID", "")
DOMAIN_URL = os.getenv("DOMAIN_URL", "http://localhost:8000")

router = APIRouter(prefix="/billing", tags=["billing"])


# -------- Helpers --------

async def create_checkout_session(user_id: str) -> str:
    """
    Creates a Stripe Checkout Session for subscription.
    Returns the session URL where the user can pay.
    """
    try:
        session = stripe.checkout.Session.create(
            mode="subscription",
            payment_method_types=["card"],
            line_items=[{"price": STRIPE_PRICE_ID, "quantity": 1}],
            success_url=f"{DOMAIN_URL}/billing/success?session_id={{CHECKOUT_SESSION_ID}}",
            cancel_url=f"{DOMAIN_URL}/billing/cancel",
            metadata={"user_id": user_id},
        )
        return session.url
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


async def create_portal_session(customer_id: str) -> str:
    """
    Creates a Stripe Billing Portal session for an existing customer.
    Allows them to update payment method or cancel.
    """
    try:
        session = stripe.billing_portal.Session.create(
            customer=customer_id,
            return_url=DOMAIN_URL,
        )
        return session.url
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


# -------- Webhook handler --------

@router.post("/webhook")
async def stripe_webhook(request: Request):
    """
    Stripe will call this endpoint to notify about subscription events.
    """
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")
    webhook_secret = os.getenv("STRIPE_WEBHOOK_SECRET", "")

    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, webhook_secret
        )
    except stripe.error.SignatureVerificationError:
        raise HTTPException(status_code=400, detail="Invalid signature")

    # Handle event types
    if event["type"] == "checkout.session.completed":
        session = event["data"]["object"]
        user_id = session["metadata"].get("user_id")
        set_subscription_status(user_id, "active")

    elif event["type"] == "customer.subscription.deleted":
        subscription = event["data"]["object"]
        user_id = subscription["metadata"].get("user_id", None)
        if user_id:
            set_subscription_status(user_id, "canceled")

    elif event["type"] == "invoice.payment_failed":
        subscription = event["data"]["object"]["subscription"]
        # You could look up which user this belongs to, and mark as "past_due"
        # For now: log only
        print(f"Payment failed for subscription {subscription}")

    return {"status": "success"}
