from fastapi import FastAPI, Request
from firebase import add_item, get_items
import os
from twilio.rest import Client

app = FastAPI()  # ✅ This must be before any @app.route usage

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
    from_number = form.get("From")
    message = form.get("Body").strip()

    phone = from_number.replace("whatsapp:", "")

    # 🔍 DEBUG PRINTS
    print("RAW From number:", from_number)
    print("Parsed phone:", phone)
    print("Incoming message:", message)
    print("TWILIO_NUMBER from env:", TWILIO_NUMBER)

    if message.lower() == "/view":
        items = get_items(phone)
        text = "🛒 Sua Listinha:\n" + "\n".join(f"• {item}" for item in items) if items else "🗒️ Sua listinha está vazia."
        print("Reply message text:", text)

        twilio_client.messages.create(
            from_=f"whatsapp:{TWILIO_NUMBER}",
            to=from_number,
            body=text
        )
    elif not message.startswith("/"):
        add_item(phone, message)

    return {"status": "ok"}
