from fastapi import FastAPI, Request
from firebase import add_item, get_items, delete_item, clear_items
from firebase import set_default_group_if_missing
from firebase import get_items, get_user_group
from twilio.rest import Client
from fastapi.responses import HTMLResponse, Response
from jinja2 import Template
import weasyprint
import os
from urllib.parse import quote
from urllib.parse import unquote_plus

app = FastAPI()

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
    from firebase_admin import firestore
    ref = firestore.client().collection("listas").document(g)
    doc = ref.get()
    if not doc.exists:
        print(f"⚠️ Document not found: {g}")
        return HTMLResponse("❌ Lista não encontrada.")

    data = doc.to_dict()
    print(f"📦 Found doc with {len(data.get('itens', []))} items")
    return HTMLResponse(content=render_list_page(g, data.get("itens", [])))

@app.get("/view/pdf")
def view_pdf(g: str):
    print(f"📥 Raw g: {repr(g)}")  # Isso mostra exatamente o que chegou
    doc_id = unquote_plus(g)
    print("📄 doc_id final:", repr(doc_id))
    print(f"📄 Generating PDF for doc_id: '{g}'")
    from firebase_admin import firestore

    doc_id = unquote_plus(g)
    ref = firestore.client().collection("listas").document(doc_id)
    doc = ref.get()

    if not doc.exists:
        print(f"❌ Document not found: {doc_id}")
        return Response(content="Documento não encontrado.", media_type="text/plain")

    data = doc.to_dict()
    items = data.get("itens", [])
    print(f"📄 PDF includes {len(items)} items")
    html = render_list_page(doc_id_encoded, items)

    pdf = weasyprint.HTML(string=html).write_pdf()

    return Response(content=pdf, media_type="application/pdf", headers={
        "Content-Disposition": f"inline; filename=listinha_{doc_id}.pdf"
    })


@app.post("/webhook")
async def whatsapp_webhook(request: Request):
    form = await request.form()
    from_number = form.get("From")  # "whatsapp:+551199999999"
    message = form.get("Body").strip()
    phone = from_number.replace("whatsapp:", "")
    set_default_group_if_missing(phone)

    print("📞 From:", from_number)
    print("📲 Message:", message)

    # 👇 Add slash if missing
    if not message.startswith("/"):
        command = "/" + message.strip().lower()
    else:
        command = message.strip().lower()

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
            raw_doc_id = f"{group['owner']}__{group['list']}"
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
            #send_message(from_number, f"✅ Adicionado: *{message}*")
            print("Adicionado.")
        else:
            send_message(from_number, f"⚠️ O item *{message}* já está na listinha.")

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
    from firebase_admin import firestore
    ref = firestore.client().collection("listas").document(doc_id)
    doc = ref.get()
    return doc.to_dict()["itens"] if doc.exists else []

def render_list_page(doc_id, items):
    with open("templates/list.html", encoding="utf-8") as f:
        html = f.read()
    template = Template(html)
    return template.render(doc_id=doc_id, items=items, count=len(items))

