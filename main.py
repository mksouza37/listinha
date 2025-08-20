from fastapi import FastAPI, Request, Query
from firebase import (
    add_item, get_items, delete_item, clear_items,
    get_user_group, create_new_list, user_in_list,
    is_admin, add_user_to_list, propose_admin_transfer, accept_admin_transfer,
    remove_user_from_list, remove_self_from_list, get_user_billing, update_user_billing,
    find_phone_by_customer_or_subscription
)
from firebase_admin import firestore
from fastapi.responses import HTMLResponse, Response, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from jinja2 import Template
import weasyprint
import os
import requests
from urllib.parse import quote
from icu import Collator, Locale
collator = Collator.createInstance(Locale("pt_BR"))
from datetime import datetime, timezone, timedelta
import time
import pytz
import phonenumbers
from phonenumbers import NumberParseException
import unicodedata
from messages import (
    ALREADY_IN_LIST, NAMELESS_OPENING, ADD_USER_USAGE, NOT_IN_LIST,
    INVALID_NUMBER, NOT_ADMIN, INVALID_SELF_EXIT,
    CANNOT_EXIT_AS_ADMIN, NO_PENDING_TRANSFER, LIST_EMPTY_PDF, UNKNOWN_COMMAND,
    NOT_OWNER_CANNOT_REMOVE, SELF_EXIT_INSTRUCTION, NOT_OWNER_CANNOT_TRANSFER,
    NOT_OWNER_CANNOT_RENAME, NOT_OWNER_CANNOT_CLEAR,

    REMOVED_FROM_LIST, MEMBER_LEFT_NOTIFICATION, list_created, item_added_log,
    item_already_exists, item_removed, item_not_found,
    guest_added, guest_removed, guest_already_in_other_list, transfer_proposed,
    not_a_guest, list_title_updated, list_download_url,
    list_shown, list_detailed_url, not_a_member, indication_text,
    z_step1_instructions, NEED_REFRESH_VIEW, item_index_invalid,
    LIST_CLEARED, WELCOME_MESSAGE, TRANSFER_ACCEPTED, TRANSFER_PREVIOUS_OWNER,
    LEFT_LIST, HELP_TEXT, MENU_TEXT, list_members, br_local_number,
    PAYMENT_REQUIRED, HOW_TO_PAY, CHECKOUT_LINK, STATUS_SUMMARY
)
from admin import router as admin_router
from billing import (
    load_config, create_checkout_session, require_active_or_trial, handle_webhook_core
)

app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")
app.include_router(admin_router)
PUBLIC_DISPLAY_NUMBER = os.getenv("PUBLIC_DISPLAY_NUMBER", "+55 11 91270-5543")  # your real number

# Map do seu número (phone_number_id da Meta) para a "instância" usada na sua lógica
# Descubra o phone_number_id no painel do WhatsApp Business (Meta).

_raw_map = {
    os.getenv("META_PHONE_NUMBER_ID", ""): "instance_1",
    os.getenv("META_PHONE_NUMBER_ID_2", ""): "instance_2",
}
NUMBER_MAP = {k: v for k, v in _raw_map.items() if k}


# WhatsApp Cloud API (Meta)
META_ACCESS_TOKEN = os.getenv("META_ACCESS_TOKEN", "")
META_PHONE_NUMBER_ID = os.getenv("META_PHONE_NUMBER_ID", "")
META_API_VERSION = os.getenv("META_API_VERSION", "v21.0")

# Token de verificação para o GET do webhook (Meta)
VERIFY_TOKEN = os.getenv("META_VERIFY_TOKEN", "listinha-verify")

# Commands that require active/trial/grace
GATED_COMMANDS = {"/i", "/a", "/u", "/e", "/r", "/l", "/d"}

@app.get("/")
def root():
    return {"message": "Listinha is running"}

def normalize_phone(raw_phone: str, admin_phone: str) -> str or None:
    """
    Normalizes a phone number based on the admin's country.

    - If the phone already starts with a valid +DDI, it’s parsed directly.
    - If not, we prepend the admin's DDI and then try parsing.
    - Returns fully internationalized E.164 format (e.g., +5511988888888)
    - Returns None if invalid.
    """

    # Get admin DDI from their phone
    try:
        admin_parsed = phonenumbers.parse(admin_phone, None)
        admin_region = phonenumbers.region_code_for_country_code(admin_parsed.country_code)
    except NumberParseException:
        return None  # Invalid admin phone

    # Check if raw_phone already includes a DDI (+ prefix)
    if raw_phone.startswith("+"):
        try:
            parsed = phonenumbers.parse(raw_phone, None)
            if phonenumbers.is_valid_number(parsed):
                return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
        except NumberParseException:
            return None
    else:
        # Try parsing using admin's region code
        try:
            parsed = phonenumbers.parse(raw_phone, admin_region)
            if phonenumbers.is_valid_number(parsed):
                return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
        except NumberParseException:
            return None

    return None  # Fallback for any failure

def _gate_if_needed(cmd: str, phone: str) -> bool:
    """Returns True if processing should STOP (i.e., not allowed)."""
    if cmd not in GATED_COMMANDS:
        return False  # open command
    ok, state, until_ts = require_active_or_trial(phone)
    if ok:
        return False
    # Not ok -> tell how to pay and stop
    from messages import PAYMENT_REQUIRED, HOW_TO_PAY
    send_message(f"whatsapp:{phone}", f"{PAYMENT_REQUIRED}\n{HOW_TO_PAY}")
    return True


# ---------- Helpers to send updated views ----------

def current_doc_id(phone: str) -> str:
    group = get_user_group(phone) or {}
    return f"{group.get('instance','default')}__{group.get('owner', phone)}__{group.get('list','default')}"

def save_view_snapshot(phone: str, items: list[str]) -> None:
    """Persist the user's last alphabetized view as a 1..N → text mapping."""
    firestore.client().collection("users").document(phone).set({
        "last_view_snapshot": {
            "doc_id": current_doc_id(phone),
            "items": items,
            "ts_epoch": int(datetime.now(timezone.utc).timestamp()),
        }
    }, merge=True)

def load_view_snapshot(phone: str):
    doc = firestore.client().collection("users").document(phone).get()
    if not doc.exists:
        return None
    return (doc.to_dict() or {}).get("last_view_snapshot") or {}

SNAPSHOT_TTL_SECONDS = 600  # 10 minutos

def snapshot_is_fresh(ts_epoch) -> bool:
    try:
        now = int(datetime.now(timezone.utc).timestamp())
        return (now - int(ts_epoch)) <= SNAPSHOT_TTL_SECONDS
    except Exception:
        return False

def send_message(to, body):
    """
    Envia mensagem de texto via WhatsApp Cloud API (Meta).
    Aceita 'to' como "whatsapp:+55119..." ou "+55119..." ou "55119...".
    """
    try:
        to_norm = (to or "").replace("whatsapp:", "").strip()
        if to_norm.startswith("+"):
            to_norm = to_norm[1:]

        url = f"https://graph.facebook.com/{META_API_VERSION}/{META_PHONE_NUMBER_ID}/messages"
        headers = {
            "Authorization": f"Bearer {META_ACCESS_TOKEN}",
            "Content-Type": "application/json"
        }
        payload = {
            "messaging_product": "whatsapp",
            "to": to_norm,
            "type": "text",
            "text": {"body": body[:4096]}  # pequeno limite de segurança
        }
        print(f"📤 META OUT → to=+{to_norm} chars={len(body)}")
        r = requests.post(url, headers=headers, json=payload, timeout=15)
        try:
            r.raise_for_status()
        except Exception:
            # 🔎 Extra debug: show Graph API error payload to pinpoint cause
            try:
                print("META ERROR BODY:", r.text)
            except Exception:
                pass
            raise  # rethrow for the outer except

        resp = r.json()
        msg_id = (resp.get("messages") or [{}])[0].get("id")
        print(f"✅ Enviado via Meta. id={msg_id}")
    except Exception as e:
        print("❌ Erro ao enviar via Meta:", str(e))

def send_video(to, video_url, caption=""):
    """
    Envia um pequeno vídeo via WhatsApp Cloud API.
    'video_url' deve ser HTTPS público (ex.: https://.../static/listinha-demo.mp4)
    """
    try:
        to_norm = (to or "").replace("whatsapp:", "").strip()
        if to_norm.startswith("+"):
            to_norm = to_norm[1:]

        url = f"https://graph.facebook.com/{META_API_VERSION}/{META_PHONE_NUMBER_ID}/messages"
        headers = {
            "Authorization": f"Bearer {META_ACCESS_TOKEN}",
            "Content-Type": "application/json"
        }
        payload = {
            "messaging_product": "whatsapp",
            "to": to_norm,
            "type": "video",
            "video": {
                "link": video_url,
                "caption": caption[:1024] if caption else None
            }
        }
        print(f"📤 META OUT (video) → to=+{to_norm} url={video_url}")
        r = requests.post(url, headers=headers, json=payload, timeout=20)
        r.raise_for_status()
        resp = r.json()
        msg_id = (resp.get("messages") or [{}])[0].get("id")
        print(f"✅ Vídeo enviado via Meta. id={msg_id}")
    except Exception as e:
        try:
            print("META ERROR BODY (video):", r.text)
        except Exception:
            pass
        print("❌ Erro ao enviar vídeo via Meta:", str(e))

def render_list_page(doc_id, items, title="Sua Listinha", updated_at="", show_footer=True, mode="normal"):
    with open("templates/list.html", encoding="utf-8") as f:
        html = f.read()
    template = Template(html)

    doc_id_encoded = quote(doc_id, safe="")
    return template.render(
        doc_id=doc_id_encoded,
        items=items,
        count=len(items),
        title=title,
        updated_at=updated_at,
        show_footer=show_footer,
        timestamp=int(datetime.now().timestamp()),
        mode=mode
    )

def normalize_text(s: str) -> str:
    """
    Lowercase, remove accents/diacritics, and collapse inner spaces.
    Ex.: '  PÃO   de   Açúcar  ' -> 'pao de acucar'
    """
    if not s:
        return ""
    # NFD split + remove combining marks (accents)
    s = unicodedata.normalize("NFD", s)
    s = "".join(ch for ch in s if unicodedata.category(ch) != "Mn")
    # lowercase + collapse whitespace
    s = " ".join(s.lower().split())
    return s

def _send_current_list(from_number: str, phone: str) -> None:
    """Send the current list view (same behavior as /v) right after a change."""
    raw_items = get_items(phone)  # already A→Z
    items = [entry["item"] if isinstance(entry, dict) and "item" in entry else str(entry) for entry in raw_items]

    # Save snapshot for numbered deletes
    save_view_snapshot(phone, items)

    # Build doc id & fetch title
    group = get_user_group(phone)
    raw_doc_id = current_doc_id(phone)

    # Title fallback
    title = "Sua Listinha"
    try:
        ref = firestore.client().collection("listas").document(raw_doc_id)
        doc = ref.get()
        if doc.exists:
            data = doc.to_dict() or {}
            title = data.get("title") or title
    except Exception:
        pass

    # If many items, send PDF link; else bullets
    if len(items) > 20:
        timestamp = int(time.time())
        doc_id = quote(raw_doc_id, safe="")
        pdf_url = f"https://listinha-t5ga.onrender.com/view?g={doc_id}&format=pdf&footer=true&&t={timestamp}"
        send_message(from_number, list_download_url(pdf_url))
    else:
        send_message(from_number, list_shown(title, items))

def _send_people_list(from_number: str, phone: str) -> None:
    """Send the numbered people list with local phone format (no +55)."""
    group = get_user_group(phone)

    users_ref = firestore.client().collection("users")
    same_list_users = (
        users_ref
        .where("group.owner", "==", group["owner"])
        .where("group.list", "==", group["list"])
        .where("group.instance", "==", group.get("instance", "default"))
        .stream()
    )

    members = []
    for udoc in same_list_users:
        data = udoc.to_dict() or {}
        grp = data.get("group", {})
        name = (data.get("name") or "").strip()
        phone_e164 = udoc.id  # ex.: +55119...

        is_owner = (grp.get("role") == "admin" and grp.get("owner") == group["owner"])

        # display phone without +55
        phone_display = br_local_number(phone_e164)

        line = f"{name} — {phone_display}" if name else f"{phone_display}"
        if is_owner:
            line += " (Dono)"

        sort_key = (0 if is_owner else 1, (name or phone_display).lower())
        members.append((sort_key, line))

    members.sort(key=lambda t: t[0])
    member_lines = [f"{i+1}. {line}" for i, (_, line) in enumerate(members)]

    send_message(from_number, list_members(member_lines))

@app.get("/view")
def unified_view(
    g: str,
    format: str = Query("html"),               # "html" ou "pdf"
    footer: str = Query("false"),
    download: str = Query("false"),
    mode: str = Query("normal")                # "normal" ou "vc"
):
    ref = firestore.client().collection("listas").document(g)
    doc = ref.get()
    if not doc.exists:
        return HTMLResponse("❌ Lista não encontrada.")

    data = doc.to_dict()
    title = data.get("title", "Sua Listinha")

    show_footer = footer.lower() == "true"
    sao_paulo = pytz.timezone("America/Sao_Paulo")
    updated_at = datetime.now(sao_paulo).strftime("Atualizado em: %d/%m/%Y às %H:%M") if show_footer else ""

    # Modo detalhado (vc): item + quem + quando (mostra NOME; se vazio, cai no telefone)
    if mode == "vc":
        try:
            instance_id, owner, list_name = g.split("__")
        except ValueError:
            return HTMLResponse("❌ ID de documento inválido.")

        # Busca todos os usuários da mesma listinha
        users_ref = firestore.client().collection("users")
        same_list_users = (
            users_ref
            .where("group.owner", "==", owner)
            .where("group.list", "==", list_name)
            .where("group.instance", "==", instance_id)
            .stream()
        )

        # Mapeia várias variantes do telefone → nome
        phone_name_map = {}
        for udoc in same_list_users:
            data_u = udoc.to_dict() or {}
            name = (data_u.get("name") or "").strip()
            if not name:
                continue  # sem nome, não ajuda no display

            raw = (udoc.id or "").strip()  # ex.: "+5511999999999"
            variants = set()
            if raw:
                variants.add(raw)
                # sem '+'
                if raw.startswith("+"):
                    variants.add(raw[1:])
                else:
                    variants.add("+" + raw)
                # com "whatsapp:"
                variants.add("whatsapp:" + raw)
                if raw.startswith("+"):
                    variants.add("whatsapp:" + raw[1:])
                else:
                    variants.add("whatsapp:+" + raw)
                # só dígitos (para normalizações agressivas)
                digits = "".join(ch for ch in raw if ch.isdigit())
                if digits:
                    variants.add(digits)
                    variants.add("+" + digits)
                    variants.add("whatsapp:" + digits)
                    variants.add("whatsapp:+" + digits)

            for v in variants:
                phone_name_map[v] = name

        def resolve_user_display(u: str) -> str:
            """Retorna nome se existir; caso contrário, devolve o próprio telefone."""
            u = (u or "").strip()
            if not u:
                return u
            if u in phone_name_map:
                return phone_name_map[u]
            # tenta variações na hora do lookup
            cand = set()
            cand.add(u.replace("whatsapp:", ""))
            if u.startswith("whatsapp:+"):
                cand.add(u.replace("whatsapp:+", "+"))
            elif u.startswith("whatsapp:"):
                cand.add("+" + u.split("whatsapp:", 1)[1])
            if u.startswith("+"):
                cand.add(u[1:])
            else:
                cand.add("+" + u)
            digits = "".join(ch for ch in u if ch.isdigit())
            if digits:
                cand.update({digits, "+" + digits, "whatsapp:" + digits, "whatsapp:+" + digits})
            for c in cand:
                if c in phone_name_map:
                    return phone_name_map[c]
            return u  # fallback: mostra telefone

        # Monta itens com nome (ou telefone se não houver nome)
        items = []
        for i in data.get("itens", []):
            if not (isinstance(i, dict) and all(k in i for k in ("item", "user", "timestamp"))):
                continue
            display_user = resolve_user_display(i["user"])
            items.append(
                {"item": i["item"], "user": display_user, "timestamp": i["timestamp"]}
            )

        html_content = render_list_page(
            g, items, title=title, updated_at=updated_at, show_footer=show_footer, mode="vc"
        )

    else:
        # Modo normal (bullet list)
        items = sorted(
            [i for i in data.get("itens", []) if isinstance(i, dict) and "item" in i],
            key=lambda x: collator.getSortKey(x["item"])
        )

        html_content = render_list_page(
            g, items, title=title, updated_at=updated_at, show_footer=show_footer, mode="normal"
        )

    # PDF output
    if format == "pdf":
        pdf = weasyprint.HTML(string=html_content).write_pdf()
        return Response(
            content=pdf,
            media_type="application/pdf",
            headers={
                "Content-Disposition": f"{'attachment' if download == 'true' else 'inline'}; filename=listinha_{g}.pdf",
                "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
                "Pragma": "no-cache",
                "Expires": "0",
            },
        )

    # HTML output
    return Response(
        content=html_content,
        media_type="text/html",
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )

@app.get("/comandos")
def show_commands():
    with open("static/comandos.html", encoding="utf-8") as f:
        html = f.read()
    return HTMLResponse(html)

# === ✅ NEW: ALIASES so calls to /meta-webhook don't 404 ===
@app.get("/meta-webhook")
async def meta_verify_alias(request: Request):
    # delegate to the existing verifier
    return await meta_verify(request)

@app.post("/meta-webhook")
async def whatsapp_webhook_alias(request: Request):
    # delegate to the existing message handler
    return await whatsapp_webhook(request)

@app.get("/webhook")
async def meta_verify(request: Request):
    qp = request.query_params
    mode = qp.get("hub.mode") or ""
    token = qp.get("hub.verify_token") or ""
    challenge = qp.get("hub.challenge") or ""

    print(f"[META VERIFY] mode={mode} token={token} challenge={challenge}", flush=True)

    if mode == "subscribe" and token == VERIFY_TOKEN:
        return PlainTextResponse(challenge)
    return PlainTextResponse("Forbidden", status_code=403)

@app.post("/webhook")
async def whatsapp_webhook(request: Request):
    """
    Recebe mensagens do WhatsApp Cloud API (Meta).
    """
    try:
        body = await request.json()
    except Exception:
        print("❌ Payload inválido (não-JSON) no /webhook")
        return {"status": "ok"}

    # Estrutura Meta: entry[0].changes[0].value.messages[0]
    try:
        entry = (body.get("entry") or [])[0]
        change = (entry.get("changes") or [])[0]
        value = change.get("value") or {}
        metadata = value.get("metadata") or {}
        messages = value.get("messages") or []

        if not messages:
            # Pode ser read/delivery/status etc. Não há texto para processar.
            return {"status": "ok"}

        msg = messages[0]
        text = (msg.get("text") or {}).get("body", "")
        wa_from = str(msg.get("from") or "").strip()  # ex.: "5511999999999" (sem +)
        phone_number_id = str(metadata.get("phone_number_id") or "").strip()

        # Normaliza formato de origem para seu código (mantém compatibilidade):
        from_number = f"whatsapp:+{wa_from}" if wa_from else ""
        # Resolve instance via phone_number_id
        instance_id = NUMBER_MAP.get(phone_number_id, "default")

        print(f"📦 META IN: from={from_number} pid={phone_number_id} instance={instance_id}")
        print(f"📲 Message: {text}")

        message = (text or "").strip()
        parts = message.split(maxsplit=1)
        if parts:
            cmd = "/" + parts[0].lower()
            arg = parts[1] if len(parts) > 1 else ""
        else:
            cmd = "/"
            arg = ""

        phone = from_number.replace("whatsapp:", "")

        # LISTINHA commands

        if cmd == "/listinha":
            raw = (arg or "").strip()
            # Keep quotes, but normalize capitalization of first letter as a courtesy
            name = raw.strip('"').strip()[:20]
            if name:
                name = name.capitalize()

            if not name:
                send_message(from_number,NAMELESS_OPENING)
                return {"status": "ok"}

            if user_in_list(phone):
                send_message(from_number, ALREADY_IN_LIST)
            else:

                cfg = load_config()
                if cfg.paywall_on_listinha:
                    ok, state, _ = require_active_or_trial(phone)
                    if not ok:
                        # Send a fresh checkout link
                        group = get_user_group(phone)
                        instance_id = group.get("instance", "default") if group else "default"
                        try:
                            sess = create_checkout_session(phone, instance_id)
                            url = sess["url"]
                            update_user_billing(phone, {"last_checkout_url": url, "last_updated": int(time.time())})
                            send_message(from_number, f"{PAYMENT_REQUIRED}\n{HOW_TO_PAY}\n\n{CHECKOUT_LINK(url)}")
                        except Exception as e:
                            print("Stripe error on listinha paywall:", str(e))
                            send_message(from_number, f"{PAYMENT_REQUIRED}\n{HOW_TO_PAY}")
                        return {"status": "ok"}

                create_new_list(phone, instance_id, name)
                send_message(from_number, list_created(f"*{name}*"))
                # Optional: immediately show empty (or default) list
                _send_current_list(from_number, phone)
            return {"status": "ok"}

        # Check if user exists before other commands
        if not user_in_list(phone):
            send_message(from_number, NOT_IN_LIST)
            return {"status": "ok"}

        # Add item to list (i <text>)
        if cmd == "/i" and arg:
            if _gate_if_needed(cmd, phone):
                return {"status": "ok"}
            added = add_item(phone, arg)
            if added:
                send_message(from_number, item_added_log(arg))
                print(f"✅ Item adicionado: {arg}")
                # (1) Show updated list right after action
                _send_current_list(from_number, phone)
            else:
                send_message(from_number, item_already_exists(arg))
            return {"status": "ok"}

        # Delete item: a <número | texto>
        if cmd == "/a" and arg:
            if _gate_if_needed(cmd, phone):
                return {"status": "ok"}
            wanted = arg.strip()

            # If it's a number, resolve via last snapshot (no race with live reordering)
            if wanted.isdigit():
                idx = int(wanted)

                snap = load_view_snapshot(phone)
                snap_items = snap.get("items")
                snap_doc_id = snap.get("doc_id")
                snap_ts = snap.get("ts_epoch")

                if not (snap_items and snapshot_is_fresh(snap_ts) and snap_doc_id == current_doc_id(phone)):
                    send_message(from_number, NEED_REFRESH_VIEW)
                    return {"status": "ok"}

                if idx < 1 or idx > len(snap_items):
                    send_message(from_number, item_index_invalid(idx, len(snap_items)))
                    return {"status": "ok"}

                canonical = snap_items[idx - 1]
                delete_item(phone, canonical)
                send_message(from_number, item_removed(canonical))
                # (1) Show updated list right after action
                _send_current_list(from_number, phone)
                return {"status": "ok"}

            # Otherwise fall back to text delete (accent-insensitive)
            items = get_items(phone) or []

            def _norm(s: str) -> str:
                return normalize_text(s)  # your existing normalizer

            match_text = None
            for txt in items:
                if _norm(txt) == _norm(wanted):
                    match_text = txt
                    break

            if not match_text:
                send_message(from_number, item_not_found(wanted))
                return {"status": "ok"}

            delete_item(phone, match_text)
            send_message(from_number, item_removed(match_text))
            # (1) Show updated list right after action
            _send_current_list(from_number, phone)
            return {"status": "ok"}

        # Add new user (u <phone> [name])
        if cmd == "/u":
            # Requer telefone + nome
            if _gate_if_needed(cmd, phone):
                return {"status": "ok"}
            if not arg:
                send_message(from_number, ADD_USER_USAGE)
                return {"status": "ok"}

            parts = arg.strip().split(maxsplit=1)
            if len(parts) < 2 or not parts[1].strip():
                send_message(from_number, ADD_USER_USAGE)
                return {"status": "ok"}

            phone_part, name_raw = parts[0], parts[1].strip()
            # (5) Capitalize first letter of user's name
            name = name_raw[:20].capitalize()

            target_phone = normalize_phone(phone_part, phone)
            if not target_phone:
                send_message(from_number, INVALID_NUMBER)
                return {"status": "ok"}

            if not is_admin(phone):
                send_message(from_number, NOT_ADMIN)
                return {"status": "ok"}

            success, status = add_user_to_list(phone, target_phone, name=name)
            if success:
                # Confirma ao dono
                send_message(from_number, guest_added(name, target_phone))

                # Dá boas-vindas ao convidado com o nome do dono (se existir)
                admin_data = firestore.client().collection("users").document(phone).get().to_dict()
                admin_name = (admin_data or {}).get("name", "").strip()
                admin_display_name = f"*{admin_name}*" if admin_name else phone

                send_message(f"whatsapp:{target_phone}", WELCOME_MESSAGE(name, admin_display_name))

                # (3) Show updated people list
                _send_people_list(from_number, phone)

            elif status == "already_in_list":
                send_message(from_number, guest_already_in_other_list(target_phone))

            return {"status": "ok"}

        # Remove user (admin): e <phone>
        if cmd == "/e" and arg:
            if _gate_if_needed(cmd, phone):
                return {"status": "ok"}
            target_phone = normalize_phone(arg, phone)
            if not target_phone:
                send_message(from_number, INVALID_NUMBER)
                return {"status": "ok"}

            if not is_admin(phone):
                send_message(from_number, NOT_OWNER_CANNOT_REMOVE)
                return {"status": "ok"}

            # Fetch name BEFORE removal to display later
            tdoc = firestore.client().collection("users").document(target_phone).get()
            tname = ""
            if tdoc.exists:
                tname = (tdoc.to_dict() or {}).get("name", "").strip()

            if remove_user_from_list(phone, target_phone):
                # (6) confirm to admin with number and name
                send_message(from_number, guest_removed(tname, target_phone))

                # notify removed user with admin display name
                admin_data = firestore.client().collection("users").document(phone).get().to_dict()
                admin_name = (admin_data or {}).get("name", "").strip()
                admin_display_name = f"*{admin_name}*" if admin_name else phone

                send_message(f"whatsapp:{target_phone}", REMOVED_FROM_LIST(admin_display_name))

                # (3) Show updated people list
                _send_people_list(from_number, phone)
            else:
                send_message(from_number, not_a_member(target_phone))
            return {"status": "ok"}

        # Self-remove: s <your phone>
        if cmd == "/s":
            if not arg:
                send_message(from_number, SELF_EXIT_INSTRUCTION)
                return {"status": "ok"}

            target_phone = normalize_phone(arg, phone)
            if not target_phone:
                send_message(from_number, INVALID_NUMBER)
                return {"status": "ok"}

            if target_phone != phone:
                send_message(from_number, INVALID_SELF_EXIT)
                return {"status": "ok"}

            # Get group BEFORE removal so we know the owner to notify
            group = get_user_group(phone) or {}
            owner_phone = group.get("owner")

            if remove_self_from_list(phone):
                # tell the leaver
                send_message(from_number, LEFT_LIST)

                # politely notify the owner (if exists and not the same as the leaver)
                if owner_phone and owner_phone != phone:
                    # Try to show the leaver's saved name; fallback to phone
                    user_doc = firestore.client().collection("users").document(phone).get()
                    user_data = (user_doc.to_dict() or {}) if user_doc.exists else {}
                    leaver_name = (user_data.get("name") or "").strip()
                    leaver_display = f"*{leaver_name}*" if leaver_name else phone

                    send_message(f"whatsapp:{owner_phone}", MEMBER_LEFT_NOTIFICATION(leaver_display))
            else:
                send_message(from_number, CANNOT_EXIT_AS_ADMIN)

            return {"status": "ok"}

        # Transfer admin role: t <phone>
        if cmd == "/t" and arg:

            target_phone = normalize_phone(arg, phone)
            if not target_phone:
                send_message(from_number, INVALID_NUMBER)
                return {"status": "ok"}
            if not is_admin(phone):
                send_message(from_number, NOT_OWNER_CANNOT_TRANSFER)
                return {"status": "ok"}
            if propose_admin_transfer(phone, target_phone):
                send_message(from_number, transfer_proposed(target_phone))
                send_message(f"whatsapp:{target_phone}", TRANSFER_RECEIVED)
            else:
                send_message(from_number, not_a_guest(target_phone))
            return {"status": "ok"}

        # Accept admin role: o
        if cmd == "/o":
            result = accept_admin_transfer(phone)
            if result:
                from_phone = result["from"]  # now returns a dict instead of just True
                send_message(from_number, TRANSFER_ACCEPTED)
                send_message(from_phone, TRANSFER_PREVIOUS_OWNER)
            else:
                send_message(from_number, NO_PENDING_TRANSFER)
            return {"status": "ok"}

        # Admin can define custom list title: r <title>
        if cmd == "/r" and arg:
            if not is_admin(phone):
                send_message(from_number, NOT_OWNER_CANNOT_RENAME)
                return {"status": "ok"}

            group = get_user_group(phone)
            doc_id = f"{group.get('instance', 'default')}__{group['owner']}__{group['list']}"
            ref = firestore.client().collection("listas").document(doc_id)
            new_title = arg.strip().capitalize()
            ref.update({"title": new_title})
            send_message(from_number, list_title_updated(new_title))
            # (2) Show list with new title
            _send_current_list(from_number, phone)
            return {"status": "ok"}

        # Menu
        MENU_ALIASES = {"/m", "/menu", "/instruções", "/opções", "/?"}  # removed ajuda/help
        if cmd in MENU_ALIASES:
            send_message(from_number, MENU_TEXT)
            return {"status": "ok"}

        # Help text
        HELP_ALIASES = {"/h", "/ajuda", "/help"}
        if cmd in HELP_ALIASES:
            send_message(from_number, HELP_TEXT)
            return {"status": "ok"}

        # Consultar pessoas na lista: p (all) — numbered, no +55
        if cmd == "/p":
            _send_people_list(from_number, phone)
            return {"status": "ok"}

        # View list
        if cmd == "/v":
            raw_items = get_items(phone)  # already A→Z
            items = [entry["item"] if isinstance(entry, dict) and "item" in entry else str(entry) for entry in
                     raw_items]

            # Save the snapshot the user will see now
            save_view_snapshot(phone, items)

            # Build doc id for links and fetch title (same as you already do)
            group = get_user_group(phone)
            raw_doc_id = current_doc_id(phone)
            doc_id = quote(raw_doc_id, safe="")

            # Title fallback
            title = "Sua Listinha"
            try:
                ref = firestore.client().collection("listas").document(raw_doc_id)
                doc = ref.get()
                if doc.exists:
                    data = doc.to_dict() or {}
                    title = data.get("title") or title
            except Exception:
                pass

            # Short vs PDF (unchanged)
            if len(items) > 20:
                timestamp = int(time.time())
                pdf_url = f"https://listinha-t5ga.onrender.com/view?g={doc_id}&format=pdf&footer=true&&t={timestamp}"
                send_message(from_number, list_download_url(pdf_url))
            else:
                send_message(from_number, list_shown(title, items))
            return {"status": "ok"}

        # Download PDF: d
        if cmd == "/d":
            if _gate_if_needed(cmd, phone):
                return {"status": "ok"}
            group = get_user_group(phone)
            raw_doc_id = f"{group.get('instance', 'default')}__{group['owner']}__{group['list']}"
            doc_id = quote(raw_doc_id, safe="")

            # Optional: check if list has items
            ref = firestore.client().collection("listas").document(raw_doc_id)
            doc = ref.get()
            count = len(doc.to_dict().get("itens", [])) if doc.exists else 0

            if count == 0:
                send_message(from_number, LIST_EMPTY_PDF)
            else:
                timestamp = int(time.time())
                pdf_url = f"https://listinha-t5ga.onrender.com/view?g={doc_id}&format=pdf&footer=true&&t={timestamp}"
                send_message(from_number, list_download_url(pdf_url))

            return {"status": "ok"}

        # Comando /x – PDF com colunas (produto, usuário, hora)
        if cmd == "/x":
            group = get_user_group(phone)
            raw_doc_id = f"{group.get('instance', 'default')}__{group['owner']}__{group['list']}"
            doc_id = quote(raw_doc_id, safe="")
            timestamp = int(time.time())

            pdf_url = f"https://listinha-t5ga.onrender.com/view?g={doc_id}&format=pdf&mode=vc&footer=true&t={timestamp}"
            send_message(from_number, list_detailed_url(pdf_url))
            return {"status": "ok"}

        # Clear all items: l (admin only)
        if cmd == "/l":
            if _gate_if_needed(cmd, phone):
                return {"status": "ok"}
            if not is_admin(phone):
                send_message(from_number, NOT_OWNER_CANNOT_CLEAR)
                return {"status": "ok"}

            clear_items(phone)
            send_message(from_number, LIST_CLEARED)
            # (1) Show updated list right after action
            _send_current_list(from_number, phone)
            return {"status": "ok"}

        if cmd == "/z":
            # 1) Instruction message
            send_message(from_number, z_step1_instructions())

            # 2) Ready-to-copy message
            full_text = indication_text(PUBLIC_DISPLAY_NUMBER)
            #send_message(from_number, full_text)

            # 3) Short demo video
            demo_url = "https://listinha-t5ga.onrender.com/static/listinha-demo.mp4"
            send_video(from_number, demo_url, caption=full_text)
            #send_video(from_number, demo_url, caption="👀 Veja a Listinha em ação em poucos segundos.")

            return {"status": "ok"}

        # Payment link (/pagar)
        if cmd == "/pagar":
            try:
                # Instance resolution works even if the user doc doesn't exist yet
                group = get_user_group(phone) or {}
                instance_id = group.get("instance", "default")

                # Create a fresh Checkout Session (now returns customer_id and maybe subscription_id)
                sess = create_checkout_session(phone, instance_id)
                url = sess.get("url")
                cust_id = sess.get("customer_id")
                sub_id = sess.get("subscription_id")

                # Persist what we already know (keeps webhook mapping robust)
                patch = {
                    "last_checkout_url": url,
                    "last_updated": int(time.time()),
                }
                if cust_id:
                    patch["stripe_customer_id"] = cust_id
                if sub_id:
                    patch["subscription_id"] = sub_id
                update_user_billing(phone, patch)

                # Send the link to the user
                from messages import CHECKOUT_LINK
                send_message(from_number, CHECKOUT_LINK(url))

            except Exception as e:
                print("Stripe error on /pagar:", str(e))
                send_message(from_number, "⚠️ Não foi possível gerar o link agora. Tente novamente em instantes.")
            return {"status": "ok"}

        # Status summary
        if cmd == "/status":
            b = get_user_billing(phone) or {}
            from billing import compute_status
            state, until_ts = compute_status(b)
            from messages import STATUS_SUMMARY
            send_message(from_number, STATUS_SUMMARY(state, until_ts))
            return {"status": "ok"}

        # ✅ Fallback for unknown commands
        send_message(from_number, UNKNOWN_COMMAND)
        return {"status": "ok"}

    except Exception as e:
        print("❌ Erro processando webhook da Meta:", str(e))
        return {"status": "ok"}

@app.post("/stripe/webhook")
async def stripe_webhook(request: Request):
    cfg = load_config()
    payload = await request.body()
    sig_header = request.headers.get("Stripe-Signature", "")

    try:
        import stripe
        event = stripe.Webhook.construct_event(
            payload=payload, sig_header=sig_header, secret=cfg.webhook_secret
        )
    except Exception as e:
        if not cfg.allow_unverified_webhooks:
            print("❌ Stripe webhook signature verification failed:", str(e))
            return PlainTextResponse("Invalid signature", status_code=400)
        event = await request.json()
        print("⚠️ Accepting unverified webhook due to ALLOW_UNVERIFIED_WEBHOOKS=true")

    ev = event if isinstance(event, dict) else event.to_dict()
    patch = handle_webhook_core(ev)

    # Try to resolve phone if not present
    phone = patch.pop("_phone", None)
    if not phone:
        obj = (ev.get("data") or {}).get("object") or {}
        # Event-specific fields:
        # - customer.subscription.* → obj["id"] == subscription_id, obj["customer"] == customer_id
        # - invoice.*               → obj["subscription"], obj["customer"]
        # - checkout.session.*      → obj["subscription"], obj["customer"]
        customer_id = obj.get("customer")
        subscription_id = obj.get("id") if ev.get("type","").startswith("customer.subscription.") else obj.get("subscription")
        phone = find_phone_by_customer_or_subscription(customer_id, subscription_id)

        # If we found the user by customer id but the billing doc doesn't store it yet, persist it
        if phone and customer_id:
            update_user_billing(phone, {"stripe_customer_id": customer_id})

    # Idempotency (unchanged)
    if cfg.webhook_idempotency and phone:
        b = get_user_billing(phone) or {}
        last = (b or {}).get("last_event_id")
        current_id = ev.get("id")
        if current_id and last == current_id:
            print(f"↩️ Duplicate webhook ignored: {current_id}")
            return {"received": True}
        if current_id:
            patch["last_event_id"] = current_id

    if phone:
        update_user_billing(phone, patch)
    else:
        print("ℹ️ Webhook still missing user mapping; patch not applied:", patch)

    return {"received": True}

# Rotas billing

@app.get("/billing/success")
def billing_success(phone: str):
    send_message(f"whatsapp:{phone}", "✅ Pagamento confirmado! Obrigado.")
    return PlainTextResponse("success")

@app.get("/billing/cancel")
def billing_cancel(phone: str):
    send_message(f"whatsapp:{phone}", "ℹ️ Pagamento cancelado. Se preferir, você pode tentar novamente enviando *pagar*.")
    return PlainTextResponse("cancel")

