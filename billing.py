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

    # Hard cancel (immediate) — sometimes Stripe sets canceled=True a moment
    # before the status flips to "canceled". Treat that as canceled now.
    if stripe_status == "CANCELED" or (canceled and not cancel_at_period_end):
        return ("CANCELED", None)

    # Trial window
    if trial_end and ts_now <= trial_end:
        return ("TRIAL", trial_end)

    # Active / trialing — be tolerant if we don't yet have a period end.
    if stripe_status in {"ACTIVE", "TRIALING"}:
        until = None
        if current_period_end and ts_now <= current_period_end:
            until = current_period_end
        elif cancel_at_period_end and cancel_at and ts_now <= cancel_at:
            # Scheduled to cancel at period end; show that as the "valid until"
            until = cancel_at
        # Fallback: still consider ACTIVE even if we don't have an "until" yet
        return ("ACTIVE", until)

    # Grace window (your local concept)
    if grace_until and ts_now <= grace_until:
        return ("GRACE", grace_until)

    # Billing issues
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
    _require_stripe()
    cfg = load_config()
    stripe.api_key = cfg.secret_key
    price = price_id or cfg.price_id

    customer_id = ensure_customer(phone)  # we already create customer with metadata={"phone": phone}

    # Build subscription_data and ensure it carries our phone/instance metadata
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
        "locale": "pt-BR",  # 👈 added here
    }

    session = stripe.checkout.Session.create(**params)
    # In some event types, Stripe includes 'subscription' here too; return what we have.
    return {
        "url": session["url"],
        "id": session["id"],
        "customer_id": customer_id,
        "subscription_id": session.get("subscription"),
    }

# ----------------------------
# Gating helper (read-only)
# ----------------------------
def require_active_or_trial(phone: str) -> Tuple[bool, str, Optional[int]]:
    """
    Return (ok, state, until_ts).
    """
    b = get_user_billing(phone) or {}

    # NEW: lifetime users always pass
    if bool(b.get("lifetime")):
        return True, "LIFETIME", None

    state, until_ts = compute_status(b)
    ok = state in {"ACTIVE", "TRIAL", "GRACE"}
    return ok, state, until_ts

# ----------------------------
# Webhook core (pure transform)
# ----------------------------
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

        # cancellation/scheduling signals
        if "cancel_at_period_end" in sub:
            patch["cancel_at_period_end"] = bool(sub.get("cancel_at_period_end"))
        if sub.get("cancel_at"):
            patch["cancel_at"] = _safe_int(sub.get("cancel_at"))
        if sub.get("canceled_at"):
            patch["canceled_at"] = _safe_int(sub.get("canceled_at"))

        # only mark "canceled" when Stripe says the subscription is canceled
        if status == "CANCELED":
            patch["canceled"] = True
        elif status in {"ACTIVE", "TRIALING"}:
            # ensure we don't keep stale cancel flags when we become active again
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

# abre o stripe para o usuário consultar sua assinatura
def create_billing_portal_session(phone: str, return_url: str) -> str:
    _require_stripe()
    cfg = load_config()
    stripe.api_key = cfg.secret_key
    customer_id = ensure_customer(phone)

    params = {
        "customer": customer_id,
        "return_url": return_url,
        "locale": "pt-BR",  # 👈 added here
    }

    session = stripe.billing_portal.Session.create(**params)
    return session["url"]

def extend_trial_days(phone: str, extra_days: int) -> int:
    """
    Empurra o próximo vencimento em +N dias.
    Estratégia:
      - Se já existe trial em andamento (trial_end > agora), soma N dias ao trial_end.
      - Senão, se a assinatura está ativa com current_period_end no futuro,
        cria um trial que termina em (current_period_end + N dias).
      - Senão, usa agora + N dias.
    Isso adia a próxima cobrança e muda o anchor do ciclo de cobrança para o novo trial_end.
    """
    if extra_days <= 0:
        raise ValueError("extra_days must be > 0")

    _require_stripe()
    cfg = load_config()
    stripe.api_key = cfg.secret_key

    b = get_user_billing(phone) or {}
    sub_id = b.get("subscription_id")
    if not sub_id:
        raise RuntimeError("Usuário não possui subscription_id ativo para estender.")

    now_ts = _now_ts()
    trial_end = _safe_int(b.get("trial_end"))
    cpe = _safe_int(b.get("current_period_end"))

    # Base para extensão
    if trial_end and trial_end > now_ts:
        base = trial_end
    elif cpe and cpe > now_ts:
        base = cpe
    else:
        base = now_ts

    new_trial_end = int(base + extra_days * 86400)

    # Atualiza a assinatura no Stripe (sem prorrata; apenas adia a cobrança)
    sub = stripe.Subscription.modify(
        sub_id,
        trial_end=new_trial_end,
        proration_behavior="none",
    )
    # status pode ser "trialing" após essa mudança
    status_val = sub.get("status") if hasattr(sub, "get") else getattr(sub, "status", None)

    # Persistimos localmente (o webhook também confirmará depois)
    update_user_billing(phone, {
        "trial_end": new_trial_end,
        "stripe_status": str(status_val or b.get("stripe_status") or "").upper(),
        "last_updated": now_ts,
    })

    return new_trial_end



