@app.post("/webhook")
async def whatsapp_webhook(request: Request):
    form = await request.form()
    from_number = form.get("From")  # "whatsapp:+551199999999"
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
