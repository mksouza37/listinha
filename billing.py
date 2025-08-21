# billing.py
from __future__ import annotations
import os, time, json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional, Tuple, Dict, Any

try:
    import stripe  # type: ignore
except Exception:
    stripe = None

from firebase import (
    get_user_doc,
    get_user_billing,
    update_user_billing,
)

@dataclass(frozen=True)
class BillingConfig:
    secret_key: str
    publishable_key: str
    price_id: str
    webhook_secret: str
    domain_url: str
    trial_days_default: int
    grace_days_default: int
    paywall_on_listinha: bool
    allow_unverified_webhooks: bool
    webhook_idempotency: bool

def str2bool(s: str) -> bool:
    return str(s or "").strip().lower() in {"1","true","yes","on"}

def load_config() -> BillingConfig:
    trial = int(os.getenv("TRIAL_DAYS_DEFAULT", "0"))
    grace = int(os.getenv("GRACE_DAYS_DEFAULT", "0"))
    return BillingConfig(
        secret_key=os.getenv("STRIPE_SECRET_KEY", ""),
        publishable_key=os.getenv("STRIPE_PUBLISHABLE_KEY", ""),
        price_id=os.getenv("STRIPE_PRICE_ID", ""),
        webhook_secret=os.getenv("STRIPE_WEBHOOK_SECRET", ""),
        domain_url=os.getenv("DOMAIN_URL", "").rstrip("/"),
        trial_days_default=trial,
        grace_days_default=grace,
        paywall_on_listinha=str2bool(os.getenv("PAYWALL_ON_LISTINHA", "true")),
        allow_unverified_webhooks=str2bool(os.getenv("ALLOW_UNVERIFIED_WEBHOOKS", "false")),
        webhook_idempotency=str2bool(os.getenv("WEBHOOK_IDEMPOTENCY", "true")),
    )

def _now_ts() -> int:
    return int(datetime.now(timezone.utc).timestamp())

def _safe_int(v) -> Optional[int]:
    try:
        return int(v) if v is not None else None
    except Exception:
        return None

def compute_status(b: Dict[str, Any] | None) -> Tuple[str, Optional[int]]:
    if not b:
        return ("NONE", None)

    ts_now = _now_ts()
    trial_end = _safe_int(b.get("trial_end"))
    grace_until = _safe_int(b.get("grace_until"))
    current_period_end = _safe_int(b.get("current_period_end"))
    cancel_at = _safe_int(b.get("cancel_at"))
    cancel_at_period_end = bool(b.get("cancel_at_period_end"))
    canceled = bool(b.get("canceled"))
    stripe_status = (b.get("stripe_status") or "").upper()

    if stripe_status == "CANCELED" or (canceled and not cancel_at_period_end):
        return ("CANCELED", None)

    if trial_end and ts_now <= trial_end:
        return ("TRIAL", trial_end)

    if stripe_status in {"ACTIVE", "TRIALING"}:
        until = None
        if current_period_end and ts_now <= current_period_end:
            until = current_period_end
        elif cancel_at_period_end and cancel_at and ts_now <= cancel_at:
            until = cancel_at
        return ("ACTIVE", until)

    if grace_until and ts_now <= grace_until:
        return ("GRACE", grace_until)

    if stripe_status in {"PAST_DUE", "UNPAID"}:
        return ("PAST_DUE", current_period_end or grace_until)

    return ("EXPIRED", None)

def _require_stripe():
    if stripe is None:
        raise RuntimeError("Stripe SDK not installed. Add 'stripe' to requirements.txt and redeploy.")

def ensure_customer(phone: str) -> str:
    _require_stripe()
    cfg = load_config()
    stripe.api_key = cfg.secret_key

    user = get_user_doc(phone) or {}
    billing = (user.get("billing") or {})
    cid = billing.get("stripe_customer_id")
    if cid:
        return cid

    cust = stripe.Customer.create(
        metadata={"phone": phone},
        description=f"Listinha user {phone}",
    )
    return cust["id"]

def create_checkout_session(
    phone: str,
    instance_id: str,
    price_id: Optional[str] = None,
    trial_days: Optional[int] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    _require_stripe()
    cfg = load_config()
    stripe.api_key = cfg.secret_key
    price = price_id or cfg.price_id

    customer_id = ensure_customer(phone)

    subscription_data: Dict[str, Any] = {"metadata": {"phone": phone, "instance": instance_id}}
    if trial_days and trial_days > 0:
        subscription_data["trial_period_days"] = int(trial_days)

    params: Dict[str, Any] = {
        "mode": "subscription",
        "customer": customer_id,
        "line_items": [{"price": price, "quantity": 1}],
        "success_url": f"{cfg.domain_url}/billing/success?phone={phone}",
        "cancel_url": f"{cfg.domain_url}/billing/cancel?phone={phone}",
        "metadata": {"phone": phone, "instance": instance_id, **(metadata or {})},
        "subscription_data": subscription_data,
    }

    session = stripe.checkout.Session.create(**params)
    return {
        "url": session["url"],
        "id": session["id"],
        "customer_id": customer_id,
        "subscription_id": session.get("subscription"),
    }

def require_active_or_trial(phone: str) -> Tuple[bool, str, Optional[int]]:
    """
    Return (ok, state, until_ts).

    NEW: If the user has admin **Isenção** (billing.exempt = True),
    we bypass the paywall entirely and return ("EXEMPT", None).
    Stripe info remains untouched.
    """
    b = get_user_billing(phone) or {}

    # Back-compat: also honor an old "lifetime" flag if it still exists.
    if bool(b.get("exempt")) or bool(b.get("isencao")) or bool(b.get("lifetime")):
        return True, "EXEMPT", None

    state, until_ts = compute_status(b)
    ok = state in {"ACTIVE", "TRIAL", "GRACE"}
    return ok, state, until_ts

def handle_webhook_core(event: Dict[str, Any]) -> Dict[str, Any]:
    typ = event.get("type", "")
    data = (event.get("data") or {}).get("object") or {}

    patch: Dict[str, Any] = {"last_updated": _now_ts()}

    phone = None
    if isinstance(data.get("metadata"), dict):
        phone = data["metadata"].get("phone")

    if typ == "checkout.session.completed":
        if phone:
            patch["last_checkout_session_id"] = data.get("id")
            patch["stripe_status"] = "CHECKOUT_COMPLETED"
        if data.get("subscription"):
            patch["subscription_id"] = data.get("subscription")
        if data.get("customer"):
            patch["stripe_customer_id"] = data.get("customer")

    elif typ in ("customer.subscription.created", "customer.subscription.updated"):
        sub = data
        status = str(sub.get("status", "")).upper()
        if status:
            patch["stripe_status"] = status

        if sub.get("id"):
            patch["subscription_id"] = sub["id"]

        if sub.get("current_period_end"):
            patch["current_period_end"] = _safe_int(sub.get("current_period_end"))
        if sub.get("trial_end"):
            patch["trial_end"] = _safe_int(sub.get("trial_end"))

        if "cancel_at_period_end" in sub:
            patch["cancel_at_period_end"] = bool(sub.get("cancel_at_period_end"))
        if sub.get("cancel_at"):
            patch["cancel_at"] = _safe_int(sub.get("cancel_at"))
        if sub.get("canceled_at"):
            patch["canceled_at"] = _safe_int(sub.get("canceled_at"))

        if status == "CANCELED":
            patch["canceled"] = True
        elif status in {"ACTIVE", "TRIALING"}:
            patch["canceled"] = False
            patch["cancel_at_period_end"] = False

        if not phone and isinstance(sub.get("metadata"), dict):
            phone = sub["metadata"].get("phone")
        if sub.get("customer"):
            patch["stripe_customer_id"] = sub.get("customer")

    elif typ == "customer.subscription.deleted":
        patch["stripe_status"] = "CANCELED"
        patch["canceled"] = True
        if data.get("id"):
            patch["subscription_id"] = data.get("id")
        if data.get("canceled_at"):
            patch["canceled_at"] = _safe_int(data.get("canceled_at"))
        if data.get("customer"):
            patch["stripe_customer_id"] = data.get("customer")

    elif typ in ("invoice.paid", "invoice.payment_succeeded"):
        patch["stripe_status"] = "ACTIVE"
        if data.get("subscription"):
            patch["subscription_id"] = data.get("subscription")
        if data.get("customer"):
            patch["stripe_customer_id"] = data.get("customer")

    elif typ in ("invoice.payment_failed", "invoice.marked_uncollectible"):
        patch["stripe_status"] = "PAST_DUE"
        if data.get("subscription"):
            patch["subscription_id"] = data.get("subscription")
        if data.get("customer"):
            patch["stripe_customer_id"] = data.get("customer")

    if phone:
        patch["_phone"] = phone
    return patch

def create_billing_portal_session(phone: str, return_url: str) -> str:
    _require_stripe()
    cfg = load_config()
    stripe.api_key = cfg.secret_key
    customer_id = ensure_customer(phone)

    params = {
        "customer": customer_id,
        "return_url": return_url,
    }
    session = stripe.billing_portal.Session.create(**params)
    return session["url"]
