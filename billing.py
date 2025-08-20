# billing.py
from __future__ import annotations
import os, time, json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional, Tuple, Dict, Any

# Stripe is optional at import time to avoid breaking deploys if not present yet.
try:
    import stripe  # type: ignore
except Exception:  # pragma: no cover
    stripe = None  # lazy-fail when used

# Read-only accessors we will call (all writes live in firebase.py)
from firebase import (
    get_user_doc,                # read user root doc
    get_user_billing,            # read users/{phone}.billing
    update_user_billing,         # WRITE (called by main.py, not here, but handy for utilities)
)

# ----------------------------
# Config
# ----------------------------
@dataclass(frozen=True)
# billing.py (add to BillingConfig)
from dataclasses import dataclass

@dataclass(frozen=True)
class BillingConfig:
    secret_key: str
    publishable_key: str
    price_id: str
    webhook_secret: str
    domain_url: str
    trial_days_default: int
    grace_days_default: int
    paywall_on_listinha: bool            # NEW
    allow_unverified_webhooks: bool      # NEW (for debugging only)
    webhook_idempotency: bool            # NEW

# billing.py (inside load_config)
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
        paywall_on_listinha=str2bool(os.getenv("PAYWALL_ON_LISTINHA", "true")),   # default ON
        allow_unverified_webhooks=str2bool(os.getenv("ALLOW_UNVERIFIED_WEBHOOKS", "false")),
        webhook_idempotency=str2bool(os.getenv("WEBHOOK_IDEMPOTENCY", "true")),
    )

# ----------------------------
# Status machine
# ----------------------------
def _now_ts() -> int:
    return int(datetime.now(timezone.utc).timestamp())

def compute_status(b: Dict[str, Any] | None) -> Tuple[str, Optional[int]]:
    """
    Normalize into: ACTIVE, TRIAL, GRACE, PAST_DUE, EXPIRED, CANCELED, NONE.
    Return (status, until_ts_optional)
    """
    if not b:
        return ("NONE", None)

    # priority by time windows
    ts_now = _now_ts()
    trial_end = _safe_int(b.get("trial_end"))
    grace_until = _safe_int(b.get("grace_until"))
    current_period_end = _safe_int(b.get("current_period_end"))
    stripe_status = (b.get("stripe_status") or "").upper()
    canceled = b.get("canceled") is True

    if canceled:
        return ("CANCELED", None)

    if trial_end and ts_now <= trial_end:
        return ("TRIAL", trial_end)

    if current_period_end and ts_now <= current_period_end and stripe_status in {"ACTIVE", "TRIALING"}:
        return ("ACTIVE", current_period_end)

    if grace_until and ts_now <= grace_until:
        return ("GRACE", grace_until)

    # Stripe may say past_due/unpaid; keep a readable local state
    if stripe_status in {"PAST_DUE", "UNPAID"}:
        return ("PAST_DUE", current_period_end or grace_until)

    return ("EXPIRED", None)

def _safe_int(v) -> Optional[int]:
    try:
        return int(v) if v is not None else None
    except Exception:
        return None

# ----------------------------
# Stripe helpers (pure)
# ----------------------------
def _require_stripe():
    if stripe is None:
        raise RuntimeError("Stripe SDK not installed. Add 'stripe' to requirements.txt and redeploy.")

def ensure_customer(phone: str) -> str:
    """
    Create or return a Stripe Customer for this phone.
    Caller must persist the returned ID via firebase.update_user_billing().
    """
    _require_stripe()
    cfg = load_config()
    stripe.api_key = cfg.secret_key

    # Try to find existing by phone (metadata) if we do not store the id yet
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
    """
    Create a Checkout Session and return {'url': ..., 'id': ...}.
    Caller persists 'last_checkout_url' and any IDs in Firestore.
    """
    _require_stripe()
    cfg = load_config()
    stripe.api_key = cfg.secret_key
    price = price_id or cfg.price_id

    customer_id = ensure_customer(phone)

    params: Dict[str, Any] = {
        "mode": "subscription",
        "customer": customer_id,
        "line_items": [{"price": price, "quantity": 1}],
        "success_url": f"{cfg.domain_url}/billing/success?phone={phone}",
        "cancel_url": f"{cfg.domain_url}/billing/cancel?phone={phone}",
        "metadata": {"phone": phone, "instance": instance_id, **(metadata or {})},
    }
    if trial_days and trial_days > 0:
        params["subscription_data"] = {"trial_period_days": int(trial_days)}

    session = stripe.checkout.Session.create(**params)
    return {"url": session["url"], "id": session["id"]}

# ----------------------------
# Gating helper (read-only)
# ----------------------------
def require_active_or_trial(phone: str) -> Tuple[bool, str, Optional[int]]:
    """
    Return (ok, state, until_ts).
    Main will decide how to message the user (messages.py).
    """
    b = get_user_billing(phone) or {}
    state, until_ts = compute_status(b)
    ok = state in {"ACTIVE", "TRIAL", "GRACE"}
    return ok, state, until_ts

# ----------------------------
# Webhook core (pure transform)
# ----------------------------
def handle_webhook_core(event: Dict[str, Any]) -> Dict[str, Any]:
    """
    Accepts a parsed Stripe event and returns a patch for users/{phone}.billing.
    The route will write it via firebase.update_user_billing().
    """
    typ = event.get("type", "")
    data = (event.get("data") or {}).get("object") or {}

    patch: Dict[str, Any] = {"last_updated": _now_ts()}

    # Extract phone from metadata if available
    phone = None
    if "metadata" in data and isinstance(data["metadata"], dict):
        phone = data["metadata"].get("phone")

    if typ == "checkout.session.completed":
        # Nothing definitive yet; subscription is created right after
        if phone:
            patch["last_checkout_session_id"] = data.get("id")
            patch["stripe_status"] = "CHECKOUT_COMPLETED"

    elif typ in ("customer.subscription.created", "customer.subscription.updated"):
        sub = data
        if "status" in sub:
            patch["stripe_status"] = str(sub["status"]).upper()
        if "id" in sub:
            patch["subscription_id"] = sub["id"]
        if "current_period_end" in sub:
            patch["current_period_end"] = int(sub["current_period_end"])
        if "trial_end" in sub and sub["trial_end"]:
            patch["trial_end"] = int(sub["trial_end"])
        # Try harder to find phone if not on current object
        if not phone and isinstance(sub.get("metadata"), dict):
            phone = sub["metadata"].get("phone")

    elif typ == "customer.subscription.deleted":
        patch["stripe_status"] = "CANCELED"
        patch["canceled"] = True

    elif typ == "invoice.paid":
        patch["stripe_status"] = "ACTIVE"

    elif typ in ("invoice.payment_failed", "invoice.marked_uncollectible"):
        patch["stripe_status"] = "PAST_DUE"

    # Attach phone back for the route to know who to update
    if phone:
        patch["_phone"] = phone
    return patch
