from fastapi import APIRouter, Depends, HTTPException, Form
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.responses import HTMLResponse
from starlette.status import HTTP_401_UNAUTHORIZED
from jinja2 import Template
import os, secrets
import phonenumbers
from phonenumbers import NumberParseException

from firebase import get_user_doc, admin_verify_password  # uses your store helpers

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
        return tpl.render(query=owner_phone, error="Invalid phone.", result=None, who=who)

    user = get_user_doc(e164)
    if not user:
        return tpl.render(query=owner_phone, error="User not found.", result=None, who=who)

    grp = (user.get("group") or {})
    derived = {
        "doc_id": e164,
        "name": user.get("name") or "",
        "group_role": grp.get("role"),
        "group_owner": grp.get("owner"),
        "group_list": grp.get("list"),
        "group_instance": grp.get("instance", "default"),
        "billing": (user.get("billing") or {}),  # Phase 2 will fill this
    }
    return tpl.render(query=e164, error="", result=derived, who=who)

@router.get("/admin/seed")
def seed_admin():
    from firebase import admin_set_password
    admin_set_password("Markus", "Ultimas1@")
    return {"ok": True}

