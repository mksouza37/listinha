from fastapi import FastAPI, Request
from firebase import add_item, get_items, delete_item, clear_items
from firebase import set_default_group_if_missing
from firebase import get_user_group
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

    # 👇 Add slash if missing
    if not message.startswith("/"):
        command = "/" + message.strip().lower()
    else:
        command = message.strip().lower()

    # --- Step 2: LISTINHA command ---
    if command == "/listinha":
        from firebase import user_in_list, create_new_list
        if user_in_list(phone):
            send_message(from_number, "⚠️ Você já participa de uma Listinha. Saia dela para criar uma nova.")
        else:
            doc_id = create_new_list(phone, instance_id)
            send_message(from_number, "🎉 Sua nova Listinha foi criada! Agora você é o administrador.")
        return {"status": "ok"}

    # --- Check if user exists in DB before other commands ---
    from firebase import user_in_list
    if not user_in_list(phone):
        send_message(from_number, "⚠️ Você ainda não participa de nenhuma Listinha. Envie 'listinha' para criar a sua.")
        return {"status": "ok"}

    # ADD USER: i <phone>
    elif command.startswith("/i "):
        target_phone = command[3:].strip()
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

    # MENU:
    MENU_ALIASES = {"/m", "/menu", "/instruções", "/ajuda", "/help", "/opções"}
    if command in MENU_ALIASES:
        menu = (
            "📝 *Listinha Menu*:\n\n"
            "📥 Adicionar item: digite o nome diretamente\n"
            "📋 Ver lista: v\n"
            "🧹 Limpar lista: l\n"
            "❌ Apagar item: a nome_do_item\n"
        )
        send_message(from_number, menu)

    # VIEW: /v
    elif command == "/v":
        items = get_items(phone)
        if len(items) > 20:
            group = get_user_group(phone)
            raw_doc_id = f"{group.get('instance', 'default')}__{group['owner']}__{group['list']}"
            doc_id = quote(raw_doc_id, safe="")
            send_message(from_number,
                         f"📄 Sua listinha tem {len(items)} itens! Veja aqui: https://listinha-t5ga.onrender.com/view?g={doc_id}")
        else:
            text = "🛒 Sua Listinha:\n" + "\n".join(
                f"• {item}" for item in items) if items else "🗒️ Sua listinha está vazia."
            send_message(from_number, text)

    # DELETE ALL: /l
    elif command == "/l":
        clear_items(phone)
        send_message(from_number, "✅ Sua listinha foi limpa!")

    # DELETE ITEM: /a arroz
    elif command.startswith("/a "):
        item = command[3:].strip()
        if item:
            deleted = delete_item(phone, item)
            if deleted:
                send_message(from_number, f"❌ Item removido: *{item}*")
            else:
                send_message(from_number, f"⚠️ Item não encontrado: *{item}*")
        else:
            send_message(from_number, "⚠️ Especifique o item: `/d nome_do_item`")

    # ADD ITEM: if original input had no slash
    elif not message.startswith("/"):
        added = add_item(phone, message)
        if added:
            print("Adicionado.")
        else:
            send_message(from_number, f"⚠️ O item *{message}* já está na listinha.")

    # ELIMINATE USER (temporary admin command)
    elif command.startswith("/el "):
        target_phone = command[4:].strip()
        from firebase import eliminate_user
        eliminate_user(target_phone)
        send_message(from_number, f"🗑️ Usuário {target_phone} removido do banco de dados.")
        return {"status": "ok"}

    else:
        send_message(from_number, "❓ Comando não reconhecido. Envie /m para ver o menu.")

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
