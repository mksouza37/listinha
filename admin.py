# admin.py
from fastapi import APIRouter, Depends, HTTPException, Form, Query
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.responses import HTMLResponse, RedirectResponse
from starlette.status import HTTP_401_UNAUTHORIZED
from jinja2 import Template
import os, secrets, time, calendar
import phonenumbers
from phonenumbers import NumberParseException
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, Optional

# Firestore helpers
from firebase import get_user_doc, admin_verify_password, update_user_billing
# Audit helper
try:
    from firebase import append_admin_audit
except Exception as _e:
    def append_admin_audit(*_args, **_kwargs):
        raise RuntimeError("append_admin_audit() is not defined in firebase.py. "
                           "Please add it as shown earlier (ArrayUnion into users/{phone}.admin_audit).")

# Billing helpers
from billing import compute_status, load_config
try:
    from billing import extend_trial_days
except Exception as _e:
    def extend_trial_days(*_args, **_kwargs):
        raise RuntimeError("extend_trial_days() is not defined in billing.py. "
                           "Please add it as shown earlier to modify Subscription.trial_end.")

# Optional: pretty PT-BR labels for states
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
        tz = pytz.timezone("America/Sao_Paulo")  # type: ignore
        return datetime.fromtimestamp(int(ts), tz).strftime("%d/%m/%Y %H:%M")
    except Exception:
        return str(ts)


# ---------- helpers to roll forward a past period end ----------
def _month_add(dt: datetime, months: int) -> datetime:
    y = dt.year + (dt.month - 1 + months) // 12
    m = (dt.month - 1 + months) % 12 + 1
    d = min(dt.day, calendar.monthrange(y, m)[1])
    return dt.replace(year=y, month=m, day=d)

def _predict_future_cpe(cpe_ts: int, interval: str, interval_count: int) -> int:
    dt = datetime.fromtimestamp(int(cpe_ts), timezone.utc)
    now = datetime.now(timezone.utc)
    if interval == "month":
        step = interval_count
        while dt <= now:
            dt = _month_add(dt, step)
    elif interval == "year":
        step = 12 * interval_count
        while dt <= now:
            dt = _month_add(dt, step)
    elif interval == "week":
        step = 7 * interval_count
        while dt <= now:
            dt = dt + timedelta(days=step)
    elif interval == "day":
        step = interval_count
        while dt <= now:
            dt = dt + timedelta(days=step)
    return int(dt.timestamp())


def _enrich_from_stripe(phone: str) -> Optional[Dict[str, Any]]:
    """
    Fetch the subscription and build a billing patch.
    If current_period_end is in the past but status is ACTIVE/TRIALING,
    roll it forward based on the plan/price recurring interval.
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
            sub = stripe.Subscription.retrieve(sub_id, expand=["items.data.price", "latest_invoice"])
        elif cust_id:
            subs = stripe.Subscription.list(customer=cust_id, status="all", limit=10, expand=["data.items.data.price", "data.latest_invoice"])
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

    status = (_g(sub, "status") or "").upper()
    cpe = _g(sub, "current_period_end")
    te  = _g(sub, "trial_end")

    # If cpe is missing or in the past, try to roll it forward by interval
    try:
        if (not cpe) or (int(cpe) <= int(datetime.now(timezone.utc).timestamp())):
            # prefer Price.recurring, fallback to Plan
            items = _g(_g(sub, "items") or {}, "data") or []
            interval = None
            interval_count = 1
            if items:
                price = _g(items[0], "price") or {}
                recurring = _g(price, "recurring") or {}
                interval = recurring.get("interval")
                interval_count = int(recurring.get("interval_count") or 1)
                if not interval:
                    plan = _g(items[0], "plan") or {}
                    interval = plan.get("interval")
                    interval_count = int(plan.get("interval_count") or 1)

            if interval and cpe:
                new_cpe = _predict_future_cpe(int(cpe), str(interval), int(interval_count))
                if new_cpe and new_cpe != int(cpe):
                    cpe = new_cpe
                    print(f"🔁 Rolled CPE forward via interval={interval}*{interval_count} → {new_cpe}")
    except Exception as e:
        print("ℹ️ Could not roll forward CPE:", str(e))

    patch = {
        "stripe_status": status,
        "subscription_id": _g(sub, "id"),
        "stripe_customer_id": _g(sub, "customer") or cust_id,
        "current_period_end": int(cpe) if cpe else None,
        "trial_end": int(te) if te else None,
        "cancel_at_period_end": bool(_g(sub, "cancel_at_period_end")),
        "cancel_at": (int(_g(sub, "cancel_at")) if _g(sub, "cancel_at") else None),
        "canceled_at": (int(_g(sub, "canceled_at")) if _g(sub, "canceled_at") else None),
        "canceled": status == "CANCELED",
        "last_updated": int(time.time()),
    }

    print("🧩 Enrich result:", patch)
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
        "billing_state": state,
        "billing_state_pt": state_pt,
        "billing_until_ts": until_ts,
        "billing_until_fmt": "Para sempre" if state == "LIFETIME" else _fmt_ts(until_ts),

        "stripe_status": (billing.get("stripe_status") or ""),
        "subscription_id": billing.get("subscription_id"),
        "stripe_customer_id": billing.get("stripe_customer_id"),
        "last_event_id": billing.get("last_event_id"),
        "last_checkout_url": billing.get("last_checkout_url"),
        "cancel_at_period_end": bool(billing.get("cancel_at_period_end")),
        "canceled": bool(billing.get("canceled")),
        "cancel_at_fmt": "-" if lifetime_flag or not bool(billing.get("cancel_at_period_end")) else _fmt_ts(billing.get("cancel_at")),
        "canceled_at_fmt": "-" if lifetime_flag or not bool(billing.get("canceled")) else _fmt_ts(billing.get("canceled_at")),
        "current_period_end_fmt": _fmt_ts(billing.get("current_period_end")),
        "trial_end_fmt": _fmt_ts(billing.get("trial_end")),
        "grace_until_fmt": _fmt_ts(billing.get("grace_until")),
        "lifetime": lifetime_flag,

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

@router.post("/admin/grant_lifetime")
def admin_grant_lifetime(
    owner_phone: str = Form(...),
    who: str = Depends(require_admin),
):
    e164 = _normalize_phone(owner_phone)
    if not e164:
        return RedirectResponse(url="/admin?err=invalid", status_code=302)

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

    return RedirectResponse(url=f"/admin/lookup?owner_phone={e164}", status_code=303)


@router.post("/admin/revoke_lifetime")
def admin_revoke_lifetime(
    owner_phone: str = Form(...),
    who: str = Depends(require_admin),
):
    e164 = _normalize_phone(owner_phone)
    if not e164:
        return RedirectResponse(url="/admin?err=invalid", status_code=302)

    patch: Dict[str, Any] = {"lifetime": False, "last_updated": int(time.time())}

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
        append_admin_audit(e164, {
            "ts": int(time.time()),
            "admin": who,
            "action": "extend_trial_failed",
            "details": {"days": days, "error": str(e)},
        })
        return RedirectResponse(url=f"/admin/lookup?owner_phone={e164}&err={str(e)}", status_code=303)

    append_admin_audit(e164, {
        "ts": int(time.time()),
        "admin": who,
        "action": "extend_trial",
        "details": {"days": int(days), "new_trial_end": int(new_ts)},
    })

    return RedirectResponse(url=f"/admin/lookup?owner_phone={e164}", status_code=303)
