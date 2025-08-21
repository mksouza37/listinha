# admin.py
from fastapi import APIRouter, Depends, HTTPException, Form, Query
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.responses import HTMLResponse, RedirectResponse
from starlette.status import HTTP_401_UNAUTHORIZED
from jinja2 import Template
import os, secrets, time
import phonenumbers
from phonenumbers import NumberParseException
from datetime import datetime
import pytz
from typing import Any, Dict, Optional

# Firestore helpers
from firebase import get_user_doc, admin_verify_password, update_user_billing
# Audit helper (must exist in firebase.py as suggested; otherwise we raise clearly)
try:
    from firebase import append_admin_audit  # def append_admin_audit(phone: str, entry: dict) -> None
except Exception as _e:
    def append_admin_audit(*_args, **_kwargs):
        raise RuntimeError("append_admin_audit() is not defined in firebase.py. "
                           "Please add it as shown earlier (ArrayUnion into users/{phone}.admin_audit).")

# Billing helpers
from billing import compute_status, load_config  # state machine + Stripe config
try:
    # extend current trial in Stripe by N days
    from billing import extend_trial_days
except Exception as _e:
    def extend_trial_days(*_args, **_kwargs):
        raise RuntimeError("extend_trial_days() is not defined in billing.py. "
                           "Please add it as shown earlier to modify Subscription.trial_end.")

# Optional: pretty PT-BR labels for states
try:
    from messages import STATUS_NAMES_PT  # dict like {"ACTIVE": "Ativa", ..., "LIFETIME": "Vitalícia"}
except Exception:
    STATUS_NAMES_PT = {}

security = HTTPBasic()
router = APIRouter()


def require_admin(credentials: HTTPBasicCredentials = Depends(security)) -> str:
    """
    Auth check order:
    1) Firestore admins/{username} with hashed password (multi-admin)
    2) Optional fallback: single ADMIN_USER/ADMIN_PASSWORD from env (break-glass)
    """
    username = credentials.username
    password = credentials.password or ""

    if admin_verify_password(username, password):
        return username

    # Optional fallback (set env to empty to disable)
    env_user = os.getenv("ADMIN_USER", "")
    env_pwd  = os.getenv("ADMIN_PASSWORD", "")
    if env_user and env_pwd:
        ok_user = secrets.compare_digest(username, env_user)
        ok_pwd  = secrets.compare_digest(password, env_pwd)
        if ok_user and ok_pwd:
            return username

    raise HTTPException(
        status_code=HTTP_401_UNAUTHORIZED,
        detail="Unauthorized",
        headers={"WWW-Authenticate": "Basic"},
    )


def _normalize_phone(raw: str, default_region: str = "BR") -> str | None:
    raw = (raw or "").strip()
    try:
        parsed = phonenumbers.parse(raw, None if raw.startswith("+") else default_region)
        if not phonenumbers.is_valid_number(parsed):
            return None
        return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
    except NumberParseException:
        return None


def _fmt_ts(ts: int | None) -> str:
    """Format UNIX ts to America/Sao_Paulo dd/mm/yyyy HH:MM; return '-' if empty."""
    if not ts:
        return "-"
    try:
        tz = pytz.timezone("America/Sao_Paulo")
        return datetime.fromtimestamp(int(ts), tz).strftime("%d/%m/%Y %H:%M")
    except Exception:
        return str(ts)


def _enrich_from_stripe(phone: str) -> Optional[Dict[str, Any]]:
    """
    Best-effort: fetch current subscription from Stripe and return a billing patch.
    Uses users/{phone}.billing.subscription_id when available; otherwise tries by customer.
    Also tries multiple fallbacks to recover current_period_end.
    """
    try:
        import stripe
    except Exception:
        return None

    cfg = load_config()
    if not cfg.secret_key:
        return None
    stripe.api_key = cfg.secret_key

    user = get_user_doc(phone) or {}
    b = (user.get("billing") or {})
    sub_id = b.get("subscription_id")
    cust_id = b.get("stripe_customer_id")

    sub = None
    try:
        if sub_id:
            sub = stripe.Subscription.retrieve(sub_id)
        elif cust_id:
            subs = stripe.Subscription.list(customer=cust_id, status="all", limit=10)
            items = subs.get("data") if hasattr(subs, "get") else getattr(subs, "data", [])
            items = list(items or [])

            def _prio(s):
                st = (s.get("status") if hasattr(s, "get") else getattr(s, "status", "")) or ""
                rank = {"active": 0, "trialing": 1, "past_due": 2, "unpaid": 3, "incomplete": 4,
                        "incomplete_expired": 5, "canceled": 6}.get(st, 9)
                cpe = (s.get("current_period_end") if hasattr(s, "get") else getattr(s, "current_period_end", 0)) or 0
                return (rank, -int(cpe))
            items.sort(key=_prio)
            sub = items[0] if items else None
    except Exception as e:
        print("⚠️ Stripe retrieve/list error:", str(e))
        sub = None

    if not sub:
        return None

    def _g(obj, key):
        return obj.get(key) if hasattr(obj, "get") else getattr(obj, key, None)

    status = _g(sub, "status")
    cpe = _g(sub, "current_period_end")
    te  = _g(sub, "trial_end")

    # Fallback 1: upcoming invoice
    if not cpe:
        try:
            inv = stripe.Invoice.upcoming(customer=_g(sub, "customer"), subscription=_g(sub, "id"))
            cpe = (inv.get("period_end") if hasattr(inv, "get") else getattr(inv, "period_end", None)) or cpe
        except Exception as e:
            print("ℹ️ Upcoming invoice not available:", str(e))

    # Fallback 2: expand latest_invoice and use its period_end
    if not cpe:
        try:
            sub2 = stripe.Subscription.retrieve(_g(sub, "id"), expand=["latest_invoice"])
            li = _g(sub2, "latest_invoice")
            cpe = (li.get("period_end") if hasattr(li, "get") else getattr(li, "period_end", None)) or cpe
        except Exception as e:
            print("ℹ️ Expand latest_invoice failed:", str(e))

    patch = {
        "stripe_status": str(status or "").upper(),
        "subscription_id": _g(sub, "id"),
        "stripe_customer_id": _g(sub, "customer") or cust_id,
        "current_period_end": int(cpe) if cpe else None,
        "trial_end": int(te) if te else None,
        "cancel_at_period_end": bool(_g(sub, "cancel_at_period_end")),
        "cancel_at": (int(_g(sub, "cancel_at")) if _g(sub, "cancel_at") else None),
        "canceled_at": (int(_g(sub, "canceled_at")) if _g(sub, "canceled_at") else None),
        "canceled": str(status or "").upper() == "CANCELED",
        "last_updated": int(time.time()),
    }

    print("🧩 Enrich result:", patch)  # helpful server-side log
    return patch


def _render_lookup_page(owner_phone: str, who: str, url_error: str = "") -> str:
    """Shared renderer used by GET/POST lookup handlers."""
    with open("templates/admin.html", encoding="utf-8") as f:
        tpl = Template(f.read())

    e164 = _normalize_phone(owner_phone)
    if not e164:
        return tpl.render(query=owner_phone, error="Invalid phone.", result=None, who=who)

    user = get_user_doc(e164)
    if not user:
        return tpl.render(query=owner_phone, error="User not found.", result=None, who=who)

    grp = (user.get("group") or {})
    billing = (user.get("billing") or {})
    state, until_ts = compute_status(billing)
    state_pt = STATUS_NAMES_PT.get(state, state)
    audit = user.get("admin_audit", [])

    lifetime_flag = bool(billing.get("lifetime"))

    derived = {
        "doc_id": e164,
        "name": user.get("name") or "",
        "group_role": grp.get("role"),
        "group_owner": grp.get("owner"),
        "group_list": grp.get("list"),
        "group_instance": grp.get("instance", "default"),

        # Billing block
        "billing_raw": billing,
        "billing_state": state,                 # e.g., ACTIVE / LIFETIME / ...
        "billing_state_pt": state_pt,           # e.g., Ativa / Vitalícia
        "billing_until_ts": until_ts,
        "billing_until_fmt": "Para sempre" if state == "LIFETIME" else _fmt_ts(until_ts),

        "stripe_status": (billing.get("stripe_status") or ""),
        "subscription_id": billing.get("subscription_id"),
        "stripe_customer_id": billing.get("stripe_customer_id"),
        "last_event_id": billing.get("last_event_id"),
        "last_checkout_url": billing.get("last_checkout_url"),
        "cancel_at_period_end": bool(billing.get("cancel_at_period_end")),
        "canceled": bool(billing.get("canceled")),
        # Hide cancel dates if lifetime, or if not actually scheduled/canceled
        "cancel_at_fmt": "-" if lifetime_flag or not bool(billing.get("cancel_at_period_end")) else _fmt_ts(billing.get("cancel_at")),
        "canceled_at_fmt": "-" if lifetime_flag or not bool(billing.get("canceled")) else _fmt_ts(billing.get("canceled_at")),
        "current_period_end_fmt": _fmt_ts(billing.get("current_period_end")),
        "trial_end_fmt": _fmt_ts(billing.get("trial_end")),
        "grace_until_fmt": _fmt_ts(billing.get("grace_until")),
        "lifetime": lifetime_flag,

        # Admin audit history
        "admin_audit": audit,
    }

    # url_error (if any) shows as a banner via the same 'error' slot
    return tpl.render(query=e164, error=url_error, result=derived, who=who)


@router.get("/admin", response_class=HTMLResponse)
def admin_home(who: str = Depends(require_admin)):
    with open("templates/admin.html", encoding="utf-8") as f:
        tpl = Template(f.read())
    return tpl.render(query="", error="", result=None, who=who)


@router.post("/admin/lookup", response_class=HTMLResponse)
def admin_lookup_post(
    owner_phone: str = Form(...),
    who: str = Depends(require_admin),
):
    return _render_lookup_page(owner_phone, who)


@router.get("/admin/lookup", response_class=HTMLResponse)
def admin_lookup_get(
    owner_phone: str = Query(None),
    err: str = Query("", alias="err"),
    who: str = Depends(require_admin),
):
    if not owner_phone:
        # If someone hits the URL directly without a phone, go back to home
        return admin_home(who)
    return _render_lookup_page(owner_phone, who, url_error=err)


# ----------------------------
# Admin Actions (exactly two)
# ----------------------------

@router.post("/admin/grant_lifetime")
def admin_grant_lifetime(
    owner_phone: str = Form(...),
    who: str = Depends(require_admin),
):
    e164 = _normalize_phone(owner_phone)
    if not e164:
        return RedirectResponse(url="/admin?err=invalid", status_code=302)

    # Set lifetime flag ON and clear stale cancel info to avoid confusion
    update_user_billing(e164, {
        "lifetime": True,
        "cancel_at_period_end": False,
        "cancel_at": None,
        "canceled": False,
        "canceled_at": None,
        "last_updated": int(time.time()),
    })

    append_admin_audit(e164, {
        "ts": int(time.time()),
        "admin": who,
        "action": "grant_lifetime",
        "details": {},
    })

    # Redirect back to lookup
    return RedirectResponse(url=f"/admin/lookup?owner_phone={e164}", status_code=303)


@router.post("/admin/revoke_lifetime")
def admin_revoke_lifetime(
    owner_phone: str = Form(...),
    who: str = Depends(require_admin),
):
    e164 = _normalize_phone(owner_phone)
    if not e164:
        return RedirectResponse(url="/admin?err=invalid", status_code=302)

    # Turn off lifetime
    patch: Dict[str, Any] = {"lifetime": False, "last_updated": int(time.time())}

    # Try to re-enrich Stripe fields so Admin shows correct next renewal etc.
    try:
        enrich = _enrich_from_stripe(e164)
        if enrich:
            patch.update(enrich)
    except Exception as e:
        print("⚠️ Enrich after revoke_lifetime failed:", str(e))

    update_user_billing(e164, patch)

    append_admin_audit(e164, {
        "ts": int(time.time()),
        "admin": who,
        "action": "revoke_lifetime",
        "details": {},
    })

    return RedirectResponse(url=f"/admin/lookup?owner_phone={e164}", status_code=303)


@router.post("/admin/extend_trial")
def admin_extend_trial(
    owner_phone: str = Form(...),
    days: int = Form(...),
    who: str = Depends(require_admin),
):
    e164 = _normalize_phone(owner_phone)
    if not e164:
        return RedirectResponse(url="/admin?err=invalid", status_code=302)

    try:
        new_ts = extend_trial_days(e164, int(days))
    except Exception as e:
        # Record failed attempt too
        append_admin_audit(e164, {
            "ts": int(time.time()),
            "admin": who,
            "action": "extend_trial_failed",
            "details": {"days": days, "error": str(e)},
        })
        # Redirect back with a simple error message on URL
        return RedirectResponse(url=f"/admin/lookup?owner_phone={e164}&err={str(e)}", status_code=303)

    append_admin_audit(e164, {
        "ts": int(time.time()),
        "admin": who,
        "action": "extend_trial",
        "details": {"days": int(days), "new_trial_end": int(new_ts)},
    })

    return RedirectResponse(url=f"/admin/lookup?owner_phone={e164}", status_code=303)
