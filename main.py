from fastapi import FastAPI, Request, Query
from firebase import (
    add_item, get_items, delete_item, clear_items,
    get_user_group, create_new_list, user_in_list,
    is_admin, add_user_to_list, propose_admin_transfer, accept_admin_transfer,
    remove_user_from_list, remove_self_from_list
)
from firebase_admin import firestore
from twilio.rest import Client
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from jinja2 import Template
import weasyprint
import os
import urllib.parse
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
PUBLIC_DISPLAY_NUMBER = os.getenv("PUBLIC_DISPLAY_NUMBER", "1 415-523-8886")

# Map WhatsApp service numbers to instance IDs
NUMBER_MAP = {
    "whatsapp:+551199999999": "instance_1",  # Replace with your real numbers
    "whatsapp:+551188888888": "instance_2"
}

# Twilio setup
twilio_client = Client(
    os.getenv("TWILIO_ACCOUNT_SID"),
    os.getenv("TWILIO_AUTH_TOKEN")
)
TWILIO_NUMBER = os.getenv("TWILIO_PHONE_NUMBER")

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
    footer: str = Query("false"),              # "true" ou "false"
    download: str = Query("false"),            # força download do PDF
    t: str = "",                               # usado para cache busting
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

    # Novo modo: vc → lista com colunas
    if mode == "vc":
        # Extrair partes do doc_id
        try:
            instance_id, owner, list_name = g.split("__")
        except ValueError:
            return HTMLResponse("❌ ID de documento inválido.")

        # Buscar nomes dos usuários
        users_ref = firestore.client().collection("users")
        same_list_users = users_ref \
            .filter(field_path="group.owner", op_string="==", value=owner) \
            .filter(field_path="group.list", op_string="==", value=list_name) \
            .filter(field_path="group.instance", op_string="==", value=instance_id) \
            .stream()

        phone_name_map = {
            doc.id: doc.to_dict().get("name", "").strip() or doc.id
            for doc in same_list_users
        }

        # Montar lista com nome + timestamp
        items = [
            {
                "item": i["item"],
                "user": phone_name_map.get(i["user"], i["user"]),
                "timestamp": i["timestamp"]
            }
            for i in data.get("itens", [])
            if isinstance(i, dict) and all(k in i for k in ("item", "user", "timestamp"))
        ]

        html_content = render_list_page(g, items, title=title, updated_at=updated_at, show_footer=show_footer, mode="vc")

    else:
        # Modo normal (bullet list)
        items = sorted(
            [i for i in data.get("itens", []) if isinstance(i, dict) and "item" in i],
            key=lambda x: collator.getSortKey(x["item"])
        )

        html_content = render_list_page(g, items, title=title, updated_at=updated_at, show_footer=show_footer, mode="normal")

    # PDF output
    if format == "pdf":
        pdf = weasyprint.HTML(string=html_content).write_pdf()
        return Response(content=pdf, media_type="application/pdf", headers={
            "Content-Disposition": f"{'attachment' if download == 'true' else 'inline'}; filename=listinha_{g}.pdf",
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "Expires": "0"
        })

    # HTML output
    return Response(content=html_content, media_type="text/html", headers={
        "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
        "Pragma": "no-cache",
        "Expires": "0"
    })

@app.get("/comandos")
def show_commands():
    with open("static/comandos.html", encoding="utf-8") as f:
        html = f.read()
    return HTMLResponse(html)

@app.post("/webhook")
async def whatsapp_webhook(request: Request):
    form = await request.form()
    from_number = form.get("From")  # "whatsapp:+551199999999"
    to_number = form.get("To")      # "whatsapp:+5511XXXXXXX" (our service number)
    message = form.get("Body").strip()

    phone = from_number.replace("whatsapp:", "")
    instance_id = NUMBER_MAP.get(to_number, "default")

    print(f"📞 From: {from_number} (instance: {instance_id})")
    print(f"📲 Message: {message}")

    # Normalize command format (always with "/")
    parts = message.strip().split(maxsplit=1)
    if parts:
        cmd = "/" + parts[0].lower()
        arg = parts[1] if len(parts) > 1 else ""
    else:
        cmd = "/"
        arg = ""

    # LISTINHA command
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
            #send_message(item_added_log(arg)
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
    if cmd == "/p":
        group = get_user_group(phone)

        users_ref = firestore.client().collection("users")
        same_list_users = users_ref \
            .filter(field_path="group.owner", op_string="==", value=group["owner"]) \
            .filter(field_path="group.list", op_string="==", value=group["list"]) \
            .filter(field_path="group.instance", op_string="==", value=group["instance"]) \
            .stream()

        members_display = []
        for doc in same_list_users:
            data = doc.to_dict()
            group_data = data.get("group", {})
            role = "Dono" if group_data.get("owner") == group["owner"] and group_data.get(
                "role") == "admin" else "Convidado"
            name = data.get("name", "")
            entry = f"{doc.id} ({role})"
            if name:
                entry += f" — {name}"
            members_display.append(entry)

        # Sort: admin first
        members_display.sort(key=lambda x: "(admin)" not in x)

        send_message(from_number, list_members(members_display))
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
        text = indication_text(PUBLIC_DISPLAY_NUMBER)
        encoded = urllib.parse.quote(text, safe="")
        share_link = f"https://wa.me/?text={encoded}"

        reply = z_share_reply(share_link)
        send_message(from_number, reply)
        return {"status": "ok"}

    # ✅ Fallback for unknown commands
    send_message(from_number, UNKNOWN_COMMAND)
    return {"status": "ok"}

def send_message(to, body):
    try:
        print(f"📤 Sending to: {to}")
        print(f"📨 Message body: {body}")
        message = twilio_client.messages.create(
            from_=f"whatsapp:{TWILIO_NUMBER}",
            to=to,
            body=body
        )
        print(f"✅ Message sent successfully. SID: {message.sid}")
    except Exception as e:
        print("❌ Error sending message via Twilio:")
        print(str(e))

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

