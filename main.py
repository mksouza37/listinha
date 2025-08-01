from fastapi import FastAPI, Request
from firebase import add_item, get_items, delete_item, clear_items
from firebase import set_default_group_if_missing
import os
from twilio.rest import Client

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
        text = "🛒 Sua Listinha:\n" + "\n".join(f"• {item}" for item in items) if items else "🗒️ Sua listinha está vazia."
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
            send_message(from_number, f"✅ Adicionado: *{message}*")
        else:
            send_message(from_number, f"⚠️ O item *{message}* já está na listinha.")

    else:
        send_message(from_number, "❓ Comando não reconhecido. Envie /m para ver o menu.")

    return {"status": "ok"}

def send_message(to, body):
    twilio_client.messages.create(
        from_=f"whatsapp:{TWILIO_NUMBER}",
        to=to,
        body=body
    )
