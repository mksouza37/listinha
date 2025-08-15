from fastapi import FastAPI, Request, Query
from firebase import (
    add_item, get_items, delete_item, clear_items,
    get_user_group, create_new_list, user_in_list,
    is_admin, add_user_to_list, propose_admin_transfer, accept_admin_transfer,
    remove_user_from_list, remove_self_from_list
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
from datetime import datetime
import time
import pytz
import phonenumbers
from phonenumbers import NumberParseException
from messages import *

app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")
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
META_MESSAGES_URL   = f"https://graph.facebook.com/{META_API_VERSION}/{META_PHONE_NUMBER_ID}/messages"
# Token de verificação para o GET do webhook (Meta)
VERIFY_TOKEN = os.getenv("META_VERIFY_TOKEN", "listinha-verify")

def digits_only(s: str) -> str:
    return "".join(ch for ch in s if ch.isdigit())

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

        # ===== A partir daqui, seu fluxo original =====
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
            name = arg.strip()[:20] if arg else ""

            if user_in_list(phone):
                send_message(from_number, ALREADY_IN_LIST)
            else:
                create_new_list(phone, instance_id, name)
                display_name = f"*{name}*" if name else phone
                send_message(from_number, list_created(display_name))

            return {"status": "ok"}

        # Check if user exists before other commands
        if not user_in_list(phone):
            send_message(from_number, NOT_IN_LIST)
            return {"status": "ok"}

        # Add item to list (i <text>)
        if cmd == "/i" and arg:
            added = add_item(phone, arg)
            if added:
                send_message(from_number, item_added_log(arg))
                print(f"✅ Item adicionado: {arg}")
            else:
                send_message(from_number, item_already_exists(arg))
            return {"status": "ok"}

        # Delete item: a <item>
        if cmd == "/a" and arg:
            deleted = delete_item(phone, arg)
            if deleted:
                send_message(from_number, item_removed(arg))
            else:
                send_message(from_number, item_not_found(arg))
            return {"status": "ok"}

        # Add new user (u <phone> [name])
        if cmd == "/u" and arg:

            parts = arg.strip().split(maxsplit=1)
            phone_part = parts[0]
            name = parts[1].strip()[:20] if len(parts) > 1 else ""

            target_phone = normalize_phone(phone_part, phone)
            if not target_phone:
                send_message(from_number, INVALID_NUMBER)
                return {"status": "ok"}

            if not is_admin(phone):
                send_message(from_number, NOT_ADMIN)
                return {"status": "ok"}

            success, status = add_user_to_list(phone, target_phone, name=name)
            if success:

                send_message(from_number, guest_added(name, target_phone))

                admin_data = firestore.client().collection("users").document(phone).get().to_dict()
                admin_name = admin_data.get("name", "").strip()
                admin_display_name = f"*{admin_name}*" if admin_name else phone

                send_message(f"whatsapp:{target_phone}", WELCOME_MESSAGE(name, admin_display_name))

            elif status == "already_in_list":
                send_message(from_number, guest_already_in_other_list(target_phone))
            return {"status": "ok"}

        # Remove user (admin): e <phone>
        if cmd == "/e" and arg:

            target_phone = normalize_phone(arg, phone)
            if not target_phone:
                send_message(from_number, INVALID_NUMBER)
                return {"status": "ok"}
            if not is_admin(phone):
                send_message(from_number, NOT_OWNER_CANNOT_REMOVE)
                return {"status": "ok"}
            if remove_user_from_list(phone, target_phone):
                send_message(from_number, guest_removed("", target_phone))
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

            if remove_self_from_list(phone):
                send_message(from_number, LEFT_LIST)
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

        # Admin can define custom list title: b <title>
        if cmd == "/b" and arg:
            if not is_admin(phone):
                send_message(from_number, NOT_OWNER_CANNOT_RENAME)
                return {"status": "ok"}

            group = get_user_group(phone)
            doc_id = f"{group.get('instance', 'default')}__{group['owner']}__{group['list']}"
            ref = firestore.client().collection("listas").document(doc_id)
            ref.update({"title": arg.strip().capitalize()})
            send_message(from_number, list_title_updated(arg.strip().capitalize()))
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

        # Consultar pessoas na lista: p (all)
        # /p — listar participantes: "Nome — +telefone" (ou só +telefone se sem nome)
        if cmd == "/p":
            group = get_user_group(phone)  # {"owner","list","instance",...}

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

                # se tem nome → "Nome — +telefone"; senão → "+telefone"
                line = f"{name} — {phone_e164}" if name else f"{phone_e164}"
                if is_owner:
                    line += " (Dono)"

                # Dono primeiro; depois ordena por nome (ou telefone se sem nome)
                sort_key = (0 if is_owner else 1, (name or phone_e164).lower())
                members.append((sort_key, line))

            members.sort(key=lambda t: t[0])
            member_lines = [line for _, line in members]

            send_message(from_number, list_members(member_lines))
            return {"status": "ok"}

        # View list
        if cmd == "/v":
            raw_items = get_items(phone)

            # Support both dict-style and legacy string-style items
            items = [
                entry["item"] if isinstance(entry, dict) and "item" in entry else str(entry)
                for entry in raw_items
            ]

            group = get_user_group(phone)
            raw_doc_id = f"{group.get('instance', 'default')}__{group['owner']}__{group['list']}"
            doc_id = quote(raw_doc_id, safe="")

            # Get title from list document
            ref = firestore.client().collection("listas").document(raw_doc_id)
            doc = ref.get()
            title = doc.to_dict().get("title", "Sua Listinha") if doc.exists else "Sua Listinha"

            if len(items) > 20:
                html_url = f"https://listinha-t5ga.onrender.com/view?g={doc_id}&t={int(time.time())}"
                send_message(from_number, list_download_pdf(title, len(items), html_url))
            else:
                send_message(from_number, list_shown(title, items))

            return {"status": "ok"}

        # Download PDF: d
        if cmd == "/d":
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
            if not is_admin(phone):
                send_message(from_number, NOT_OWNER_CANNOT_CLEAR)
                return {"status": "ok"}

            clear_items(phone)
            send_message(from_number, LIST_CLEARED)
            return {"status": "ok"}

        if cmd == "/z":
            # 1) Envia o cartão de contato do Listinha
            send_contact(from_number, "Listinha", PUBLIC_DISPLAY_NUMBER)

            # 2) Instruções logo abaixo do cartão (texto exato que você pediu)
            send_message(
                from_number,
                'Escreva: *listinha "seu nome"* e envie mensagem.\n'
                "Feito isso, já poderá usar a sua listinha."
            )
            return {"status": "ok"}

    except Exception as e:
        print("❌ Erro processando webhook da Meta:", str(e))
        return {"status": "ok"}

def send_contact(to_e164: str, contact_name: str, business_e164: str):
    """
    Sends a WhatsApp contact card (native 'Mensagem' / 'Adicionar contato' buttons).
    business_e164 must be in E.164, e.g. '+55 11 91270-5543'.
    """
    payload = {
        "messaging_product": "whatsapp",
        "to": to_e164,
        "type": "contacts",
        "contacts": [{
            "name": {"formatted_name": contact_name},
            "phones": [{
                "phone": business_e164,
                "type": "CELL",
                "wa_id": digits_only(business_e164)
            }]
        }]
    }
    headers = {
        "Authorization": f"Bearer {META_ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }
    requests.post(META_MESSAGES_URL, json=payload, headers=headers, timeout=10)

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

def get_items_from_doc_id(doc_id):
    ref = firestore.client().collection("listas").document(doc_id)
    doc = ref.get()
    items = doc.to_dict()["itens"] if doc.exists else []
    return sorted(items, key=collator.getSortKey)

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

