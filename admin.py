# admin.py
from fastapi import APIRouter, Depends, HTTPException, Form
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.responses import HTMLResponse
from starlette.status import HTTP_401_UNAUTHORIZED
from jinja2 import Template
import os, secrets
import phonenumbers
from phonenumbers import NumberParseException

from firebase import get_user_doc, admin_verify_password  # uses your store helpers
# ⬇️ NEW: for status computation and PT-BR labels
from billing import compute_status                   # state machine (ACTIVE/TRIAL/...)
from datetime import datetime
import pytz
try:
    # Optional: pretty PT-BR labels in the template
    from messages import STATUS_NAMES_PT  # if you added it with STATUS_SUMMARY
except Exception:
    STATUS_NAMES_PT = {}  # fallback: template will show raw state

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

@router.get("/admin", response_class=HTMLResponse)
def admin_home(who: str = Depends(require_admin)):   # pass who
    with open("templates/admin.html", encoding="utf-8") as f:
        tpl = Template(f.read())
    return tpl.render(query="", error="", result=None, who=who)

@router.post("/admin/lookup", response_class=HTMLResponse)
def admin_lookup(
    owner_phone: str = Form(...),
    who: str = Depends(require_admin),
):
    with open("templates/admin.html", encoding="utf-8") as f:
        tpl = Template(f.read())

    e164 = _normalize_phone(owner_phone)
    if not e164:
        return tpl.render(query=owner_phone, error="Invalid phone.", result=None, who=who)  # :contentReference[oaicite:0]{index=0}

    user = get_user_doc(e164)
    if not user:
        return tpl.render(query=owner_phone, error="User not found.", result=None, who=who)  # :contentReference[oaicite:1]{index=1}

    grp = (user.get("group") or {})
    billing = (user.get("billing") or {})  # previously Phase 2 placeholder  :contentReference[oaicite:2]{index=2}

    # Compute friendly state & dates
    state, until_ts = compute_status(billing)
    state_pt = STATUS_NAMES_PT.get(state, state)
    derived = {
        "doc_id": e164,
        "name": user.get("name") or "",
        "group_role": grp.get("role"),
        "group_owner": grp.get("owner"),
        "group_list": grp.get("list"),
        "group_instance": grp.get("instance", "default"),

        # --- Billing block for template ---
        "billing_raw": billing,                   # full dict (debug)
        "billing_state": state,                   # e.g., ACTIVE
        "billing_state_pt": state_pt,             # e.g., Ativa
        "billing_until_ts": until_ts,             # int or None
        "billing_until_fmt": _fmt_ts(until_ts),   # formatted or '-'

        # common Stripe fields (also formatted)
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
    }

    return tpl.render(query=e164, error="", result=derived, who=who)
