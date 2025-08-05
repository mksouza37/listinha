from fastapi import FastAPI, Request
from firebase import (
    add_item, get_items, delete_item, clear_items,
    get_user_group,
    is_admin, add_user_to_list,
    remove_user_from_list, remove_self_from_list
)
from firebase_admin import firestore
from twilio.rest import Client
from fastapi.responses import HTMLResponse, Response
from jinja2 import Template
import weasyprint
import os
from urllib.parse import quote
from urllib.parse import unquote_plus

app = FastAPI()

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

@app.get("/view")
def view_list(g: str):
    ref = firestore.client().collection("listas").document(g)
    doc = ref.get()
    if not doc.exists:
        print(f"⚠️ Document not found: {g}")
        return HTMLResponse("❌ Lista não encontrada.")

    data = doc.to_dict()
    print(f"📦 Found doc with {len(data.get('itens', []))} items")
    return HTMLResponse(content=render_list_page(g, data.get("itens", [])))

@app.get("/view/pdf")
def view_list(g: str):
    ref = firestore.client().collection("listas").document(g)
    doc = ref.get()
    if not doc.exists:
        print(f"⚠️ Document not found: {g}")
        return HTMLResponse("❌ Lista não encontrada.")

    data = doc.to_dict()
    print(f"📦 Found doc with {len(data.get('itens', []))} items")
    content = render_list_page(g, data.get("itens", []))

    pdf = weasyprint.HTML(string=content).write_pdf()

    return Response(content=pdf, media_type="application/pdf", headers={
        "Content-Disposition": f"inline; filename=listinha_{g}.pdf"
    })

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
        from firebase import user_in_list, create_new_list
        if user_in_list(phone):
            send_message(from_number, "⚠️ Você já participa de uma Listinha. Saia dela para criar uma nova.")
        else:
            create_new_list(phone, instance_id)
            send_message(from_number, "🎉 Sua nova Listinha foi criada! Agora você é o administrador.")
        return {"status": "ok"}

    # Check if user exists before other commands
    from firebase import user_in_list
    if not user_in_list(phone):
        send_message(from_number, "⚠️ Você ainda não participa de nenhuma Listinha. Envie 'listinha' para criar a sua.")
        return {"status": "ok"}

    # Add item to list (i <text>)
    if cmd == "/i" and arg:
        added = add_item(phone, arg)
        if added:
            #send_message(from_number, f"✅ Item adicionado: {arg}")
            print(f"✅ Item adicionado: {arg}")
        else:
            send_message(from_number, f"⚠️ O item '{arg}' já está na listinha.")
        return {"status": "ok"}

    # Add new user (u <phone>)
    if cmd == "/u" and arg:
        target_phone = arg.strip()
        if not target_phone.startswith("+"):
            target_phone = "+" + target_phone
        from firebase import is_admin, add_user_to_list
        if not is_admin(phone):
            send_message(from_number, "❌ Apenas o administrador pode adicionar usuários.")
            return {"status": "ok"}
        success, status = add_user_to_list(phone, target_phone)
        if success:
            send_message(from_number, f"📢 Usuário {target_phone} adicionado à sua Listinha.")
        elif status == "already_in_list":
            send_message(from_number, f"⚠️ O número {target_phone} já participa de outra Listinha.")
        return {"status": "ok"}

    # Remove user (admin): e <phone>
    if cmd == "/e" and arg:
        target_phone = arg.strip()
        if not target_phone.startswith("+"):
            target_phone = "+" + target_phone
        from firebase import is_admin, remove_user_from_list
        if not is_admin(phone):
            send_message(from_number, "❌ Apenas o administrador pode remover usuários.")
            return {"status": "ok"}
        if remove_user_from_list(phone, target_phone):
            send_message(from_number, f"🗑️ Usuário {target_phone} removido da sua Listinha.")
        else:
            send_message(from_number, f"⚠️ O número {target_phone} não é membro da sua Listinha.")
        return {"status": "ok"}

    # Self-remove: s
    if cmd == "/s":
        from firebase import remove_self_from_list
        if remove_self_from_list(phone):
            send_message(from_number, "👋 Você saiu da Listinha.")
        else:
            send_message(from_number, "⚠️ Administradores não podem sair — use a transferência de admin.")
        return {"status": "ok"}

    # Menu
    MENU_ALIASES = {"/m", "/menu", "/instruções", "/ajuda", "/help", "/opções"}
    if cmd in MENU_ALIASES:
        menu = (
            "📝 *Listinha Menu*:\n\n"
            "📥 Adicionar item: i <item>\n"
            "👤 Adicionar usuário: u <telefone>\n"
            "📋 Ver lista: v\n"
            "🧹 Limpar lista: l\n"
            "❌ Apagar item: a <item>\n"
        )
        send_message(from_number, menu)
        return {"status": "ok"}

    # View list
    if cmd == "/v":
        items = get_items(phone)
        if len(items) > 20:
            group = get_user_group(phone)
            raw_doc_id = f"{group.get('instance', 'default')}__{group['owner']}__{group['list']}"
            doc_id = quote(raw_doc_id, safe="")
            send_message(from_number,
                         f"📄 Sua listinha tem {len(items)} itens! Veja aqui: https://listinha-t5ga.onrender.com/view?g={doc_id}")
        else:
            text = "🛒 Sua Listinha:\n" + "\n".join(f"• {item}" for item in items) if items else "🗒️ Sua listinha está vazia."
            send_message(from_number, text)
        return {"status": "ok"}

    # Clear list
    if cmd == "/l":
        clear_items(phone)
        send_message(from_number, "✅ Sua listinha foi limpa!")
        return {"status": "ok"}

    # Delete item
    if cmd == "/a" and arg:
        deleted = delete_item(phone, arg)
        if deleted:
            send_message(from_number, f"❌ Item removido: {arg}")
        else:
            send_message(from_number, f"⚠️ Item não encontrado: {arg}")
        return {"status": "ok"}

    # Eliminate user (temporary)
    if cmd == "/el" and arg:
        from firebase import eliminate_user
        eliminate_user(arg)
        send_message(from_number, f"🗑️ Usuário {arg} removido do banco de dados.")
        return {"status": "ok"}

    # If no valid command matched
    send_message(from_number, "⚠️ Comando inválido. Envie /m para ver o menu.")
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
    return doc.to_dict()["itens"] if doc.exists else []

def render_list_page(doc_id, items):
    with open("templates/list.html", encoding="utf-8") as f:
        html = f.read()
    template = Template(html)

    doc_id_encoded = quote(doc_id, safe="")
    return template.render(doc_id=doc_id_encoded, items=items, count=len(items))
