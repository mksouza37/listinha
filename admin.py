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

from firebase import get_user_doc, admin_verify_password, update_user_billing
try:
    from firebase import append_admin_audit
except Exception as _e:
    def append_admin_audit(*_args, **_kwargs):
        raise RuntimeError("append_admin_audit() is not defined in firebase.py. "
                           "Please add it (ArrayUnion into users/{phone}.admin_audit).")

from billing import compute_status, load_config
try:
    from messages import STATUS_NAMES_PT
except Exception:
    STATUS_NAMES_PT = {}

security = HTTPBasic()
router = APIRouter()


def require_admin(credentials: HTTPBasicCredentials = Depends(security)) -> str:
    username = credentials.username
    password = credentials.password or ""

    if admin_verify_password(username, password):
        return username

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
    if not ts:
        return "-"
    try:
        tz = pytz.timezone("America/Sao_Paulo")
        return datetime.fromtimestamp(int(ts), tz).strftime("%d/%m/%Y %H:%M")
    except Exception:
        return str(ts)


def _stripe_refresh_patch(phone: str) -> Optional[Dict[str, Any]]:
    try:
        import stripe
    except Exception:
        print("ℹ️ Stripe SDK not installed; cannot refresh.")
        return None

    cfg = load_config()
    if not cfg.secret_key:
        print("ℹ️ No STRIPE_SECRET_KEY configured; cannot refresh.")
        return None
    stripe.api_key = cfg.secret_key

    user = get_user_doc(phone) or {}
    b = (user.get("billing") or {})
    sub_id = b.get("subscription_id")
    cust_id = b.get("stripe_customer_id")

    sub = None
    try:
        if sub_id:
            sub = stripe.Subscription.retrieve(sub_id)  # plain retrieve first
        elif cust_id:
            subs = stripe.Subscription.list(customer=cust_id, status="all", limit=10)
            data = subs.get("data", [])
            priority = {"active": 0, "trialing": 1, "past_due": 2, "unpaid": 3, "canceled": 4}
            data = sorted(data, key=lambda s: (priority.get((s.get("status") or ""), 9), -(s.get("current_period_end") or 0)))
            sub = data[0] if data else None
    except Exception as e:
        print("⚠️ Stripe retrieve/list error:", str(e))
        sub = None

    if not sub:
        print("ℹ️ No subscription found for refresh.")
        return None

    def _g(obj, key):  # works for dict-like stripe objects
        return obj.get(key) if hasattr(obj, "get") else getattr(obj, key, None)

    status = (_g(sub, "status") or "").upper()
    cpe = _g(sub, "current_period_end")
    te  = _g(sub, "trial_end")

    # Fallback: derive from latest invoice if current_period_end is missing
    if not cpe:
        try:
            latest_inv_id = _g(sub, "latest_invoice")
            if latest_inv_id:
                inv = stripe.Invoice.retrieve(latest_inv_id, expand=["lines"])
                # Try invoice-level period first
                if inv and inv.get("period_end"):
                    cpe = inv["period_end"]
                # Otherwise derive from the first line period
                if not cpe and inv and inv.get("lines", {}).get("data"):
                    line0 = inv["lines"]["data"][0]
                    per = (line0.get("period") or {})
                    cpe = per.get("end") or per.get("end_time")
        except Exception as e:
            print("ℹ️ Could not derive period end from latest invoice:", str(e))

    patch: Dict[str, Any] = {
        "stripe_status": status,
        "subscription_id": _g(sub, "id"),
        "stripe_customer_id": _g(sub, "customer") or cust_id,
        "current_period_end": int(cpe) if cpe else None,
        "trial_end": int(te) if te else None,
        "cancel_at_period_end": bool(_g(sub, "cancel_at_period_end")),
        "cancel_at": int(_g(sub, "cancel_at")) if _g(sub, "cancel_at") else None,
        "canceled_at": int(_g(sub, "canceled_at")) if _g(sub, "canceled_at") else None,
        "canceled": status == "CANCELED",
        "last_updated": int(time.time()),
    }
    print("🧩 Stripe refresh patch (final):", patch)
    return patch

def _render_lookup_page(owner_phone: str, who: str, url_error: str = "") -> str:
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

    exempt = bool(billing.get("exempt") or billing.get("isencao") or billing.get("lifetime"))

    derived = {
        "doc_id": e164,
        "name": user.get("name") or "",
        "group_role": grp.get("role"),
        "group_owner": grp.get("owner"),
        "group_list": grp.get("list"),
        "group_instance": grp.get("instance", "default"),

        "billing_raw": billing,
        "billing_state": state,
        "billing_state_pt": state_pt,
        "billing_until_ts": until_ts,
        "billing_until_fmt": _fmt_ts(until_ts),

        "stripe_status": (billing.get("stripe_status") or ""),
        "subscription_id": billing.get("subscription_id"),
        "stripe_customer_id": billing.get("stripe_customer_id"),
        "last_event_id": billing.get("last_event_id"),
        "last_checkout_url": billing.get("last_checkout_url"),
        "cancel_at_period_end": bool(billing.get("cancel_at_period_end")),
        "canceled": bool(billing.get("canceled")),
        "cancel_at_fmt": _fmt_ts(billing.get("cancel_at")),
        "canceled_at_fmt": _fmt_ts(billing.get("canceled_at")),
        "current_period_end_fmt": _fmt_ts(billing.get("current_period_end")),
        "trial_end_fmt": _fmt_ts(billing.get("trial_end")),
        "grace_until_fmt": _fmt_ts(billing.get("grace_until")),

        "exempt": exempt,
        "admin_audit": audit,
    }

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
        return admin_home(who)
    return _render_lookup_page(owner_phone, who, url_error=err)


# ----------------------------
# Admin Actions
# ----------------------------

@router.post("/admin/grant_exempt")
def admin_grant_exempt(
    owner_phone: str = Form(...),
    who: str = Depends(require_admin),
):
    e164 = _normalize_phone(owner_phone)
    if not e164:
        return RedirectResponse(url="/admin?err=invalid", status_code=302)

    update_user_billing(e164, {"exempt": True, "last_updated": int(time.time())})

    append_admin_audit(e164, {
        "ts": int(time.time()),
        "admin": who,
        "action": "grant_exempt",
        "details": {},
    })
    return RedirectResponse(url=f"/admin/lookup?owner_phone={e164}", status_code=303)


@router.post("/admin/revoke_exempt")
def admin_revoke_exempt(
    owner_phone: str = Form(...),
    who: str = Depends(require_admin),
):
    e164 = _normalize_phone(owner_phone)
    if not e164:
        return RedirectResponse(url="/admin?err=invalid", status_code=302)

    update_user_billing(e164, {"exempt": False, "last_updated": int(time.time())})

    append_admin_audit(e164, {
        "ts": int(time.time()),
        "admin": who,
        "action": "revoke_exempt",
        "details": {},
    })
    return RedirectResponse(url=f"/admin/lookup?owner_phone={e164}", status_code=303)


@router.post("/admin/refresh_from_stripe")
def admin_refresh_from_stripe(
    owner_phone: str = Form(...),
    who: str = Depends(require_admin),
):
    """
    One-click mirror: pull latest fields from Stripe and write to Firestore.
    Does NOT touch 'exempt' (isenção) or any local-only flags.
    """
    e164 = _normalize_phone(owner_phone)
    if not e164:
        return RedirectResponse(url="/admin?err=invalid", status_code=302)

    patch = _stripe_refresh_patch(e164)
    if patch:
        update_user_billing(e164, patch)
        append_admin_audit(e164, {
            "ts": int(time.time()),
            "admin": who,
            "action": "refresh_from_stripe",
            "details": {"applied": True},
        })
        return RedirectResponse(url=f"/admin/lookup?owner_phone={e164}", status_code=303)

    append_admin_audit(e164, {
        "ts": int(time.time()),
        "admin": who,
        "action": "refresh_from_stripe",
        "details": {"applied": False},
    })
    return RedirectResponse(url=f"/admin/lookup?owner_phone={e164}&err=Stripe%20sem%20dados", status_code=303)
