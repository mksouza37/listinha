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
from jinja2 import Template
import weasyprint
import os
from urllib.parse import quote
from icu import Collator, Locale
collator = Collator.createInstance(Locale("pt_BR"))
from datetime import datetime
import time

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
def unified_view(
    g: str,
    format: str = Query("html"),               # "html" ou "pdf"
    footer: str = Query("false"),              # "true" ou "false"
    download: str = Query("false"),            # força download do PDF
    t: str = ""                                # usado para cache busting
):
    ref = firestore.client().collection("listas").document(g)
    doc = ref.get()
    if not doc.exists:
        return HTMLResponse("❌ Lista não encontrada.")

    data = doc.to_dict()
    items = sorted(data.get("itens", []), key=collator.getSortKey)
    title = data.get("title", "Sua Listinha")

    show_footer = footer.lower() == "true"
    updated_at = datetime.now().strftime("Atualizado em: %d/%m/%Y às %H:%M") if show_footer else ""

    html_content = render_list_page(g, items, title, updated_at=updated_at, show_footer=show_footer)

    if format == "pdf":
        pdf = weasyprint.HTML(string=html_content).write_pdf()

        return Response(content=pdf, media_type="application/pdf", headers={
            "Content-Disposition": f"{'attachment' if download == 'true' else 'inline'}; filename=listinha_{g}.pdf",
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "Expires": "0"
        })

    # HTML
    return Response(content=html_content, media_type="text/html", headers={
        "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
        "Pragma": "no-cache",
        "Expires": "0"
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
        if user_in_list(phone):
            send_message(from_number, "⚠️ Você já participa de uma Listinha. Saia dela para criar uma nova.")
        else:
            create_new_list(phone, instance_id)
            send_message(from_number, "🎉 Sua nova Listinha foi criada! Agora você é o administrador.")
        return {"status": "ok"}

    # Check if user exists before other commands
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

    # Delete item: a <item>
    if cmd == "/a" and arg:
        deleted = delete_item(phone, arg)
        if deleted:
            send_message(from_number, f"❌ Item removido: {arg}")
        else:
            send_message(from_number, f"⚠️ Item não encontrado: {arg}")
        return {"status": "ok"}

    # Add new user (u <phone>)
    if cmd == "/u" and arg:
        target_phone = arg.strip()
        if not target_phone.startswith("+"):
            target_phone = "+" + target_phone
        if not is_admin(phone):
            send_message(from_number, "❌ Apenas o administrador pode adicionar usuários.")
            return {"status": "ok"}
        success, status = add_user_to_list(phone, target_phone)
        if success:
            send_message(from_number, f"📢 Usuário {target_phone} adicionado à sua Listinha.")

            welcome = (
                "👋 Olá! Você foi adicionado a uma *Listinha compartilhada* no WhatsApp.\n\n"
                "🛒 Todos os membros podem adicionar ou remover itens de uma lista de compras.\n"
                "📌 Para ver os comandos disponíveis, envie: *m*\n"
                "ℹ️ A lista será atualizada automaticamente para todos.\n\n"
                "✅ Comece agora adicionando um item com: i pão"
            )
            send_message(f"whatsapp:{target_phone}", welcome)

        elif status == "already_in_list":
            send_message(from_number, f"⚠️ O número {target_phone} já participa de outra Listinha.")
        return {"status": "ok"}

    # Remove user (admin): e <phone>
    if cmd == "/e" and arg:
        target_phone = arg.strip()
        if not target_phone.startswith("+"):
            target_phone = "+" + target_phone
        if not is_admin(phone):
            send_message(from_number, "❌ Apenas o administrador pode remover usuários.")
            return {"status": "ok"}
        if remove_user_from_list(phone, target_phone):
            send_message(from_number, f"🗑️ Usuário {target_phone} removido da sua Listinha.")
        else:
            send_message(from_number, f"⚠️ O número {target_phone} não é membro da sua Listinha.")
        return {"status": "ok"}

    # Self-remove: s <your phone>
    if cmd == "/s":
        if not arg:
            send_message(from_number, "⚠️ Para sair da Listinha, envie: s <seu número>\nEx: s 551199999999")
            return {"status": "ok"}

        target_phone = arg.strip()
        if not target_phone.startswith("+"):
            target_phone = "+" + target_phone

        if target_phone != phone:
            send_message(from_number, "❌ O número informado não corresponde ao seu. Tente novamente.")
            return {"status": "ok"}

        if remove_self_from_list(phone):
            send_message(from_number, "👋 Você saiu da Listinha.")
        else:
            send_message(from_number, "⚠️ Administradores não podem sair — use a transferência de admin.")
        return {"status": "ok"}

    # Transfer admin role: t <phone>
    if cmd == "/t" and arg:
        target_phone = arg.strip()
        if not target_phone.startswith("+"):
            target_phone = "+" + target_phone
        if not is_admin(phone):
            send_message(from_number, "❌ Apenas o administrador pode transferir o papel de admin.")
            return {"status": "ok"}
        if propose_admin_transfer(phone, target_phone):
            send_message(from_number, f"📢 Proposta de transferência enviada para {target_phone}.")
            send_message(f"whatsapp:{target_phone}",
                         "📢 Você foi indicado para se tornar administrador da Listinha. Envie 'ac' para aceitar.")
        else:
            send_message(from_number, f"⚠️ O número {target_phone} não é membro da sua Listinha.")
        return {"status": "ok"}

    # Accept admin role: o
    if cmd == "/o":
        result = accept_admin_transfer(phone)
        if result:
            from_phone = result["from"]  # now returns a dict instead of just True
            send_message(from_number, "✅ Agora você é o administrador da Listinha.")
            send_message(f"whatsapp:{from_phone}",
                         "📢 Sua função mudou para *usuário*. Se quiser sair da Listinha, use o comando 's'.")
        else:
            send_message(from_number, "⚠️ Não há nenhuma transferência de admin pendente para você.")
        return {"status": "ok"}

    # Admin can define custom list title: b <title>
    if cmd == "/b" and arg:
        if not is_admin(phone):
            send_message(from_number, "❌ Apenas o administrador pode modificar o título.")
            return {"status": "ok"}

        group = get_user_group(phone)
        doc_id = f"{group.get('instance', 'default')}__{group['owner']}__{group['list']}"
        ref = firestore.client().collection("listas").document(doc_id)
        ref.update({"title": arg.strip().capitalize()})
        send_message(from_number, f"🏷️ Título atualizado para: *{arg.strip().capitalize()}*")
        return {"status": "ok"}

    # Menu
    MENU_ALIASES = {"/m", "/menu", "/instruções", "/opções"}  # removed ajuda/help
    if cmd in MENU_ALIASES:
        menu = (
            "📝 *Listinha Menu*:\n\n"

            "📥 Adicionar item: i <item>\n"
            "❌ Apagar item: a <item>\n"
            "📋 Ver lista: v\n\n"
            
            "🧹 Limpar lista inteira: l\n"
            "🏷️ Alterar título da lista: b <título>\n"
            "📎 Gerar PDF da lista: d\n"
            "👤 Adicionar usuário: u <telefone>\n"
            "➖ Remover usuário: e <telefone>\n"
            "🔄 Transferir papel de admin: t <telefone>\n"
            "✅ Aceitar papel de admin: o\n"
            "👥 Consultar pessoas na lista: p\n"
            "🚪 Sair da lista: s\n\n"

            "ℹ️ Ajuda: h / ajuda / help\n"
        )
        send_message(from_number, menu)
        return {"status": "ok"}

    # Help text
    HELP_ALIASES = {"/h", "/ajuda", "/help"}
    if cmd in HELP_ALIASES:
        help_text = (
            "📖 *Como funciona a Listinha*\n"
            "A Listinha é uma lista de compras compartilhada no WhatsApp, "
            "onde todos os membros podem ver e adicionar itens em tempo real.\n\n"

            "👥 *Funcionamento básico:*\n"
            "1️⃣ O administrador cria a Listinha e adiciona os membros.\n"
            "2️⃣ Qualquer membro pode incluir ou remover itens.\n"
            "3️⃣ O administrador pode limpar a lista inteira ou remover membros.\n"
            "4️⃣ A lista é atualizada para todos instantaneamente.\n\n"

            "💡 *Dica:* Use /m para ver todos os comandos disponíveis."
        )
        send_message(from_number, help_text)
        return {"status": "ok"}

    # Consultar pessoas na lista: p (all)
    if cmd == "/p":
        group = get_user_group(phone)

        # Query users collection for same list/owner/instance
        users_ref = firestore.client().collection("users")
        same_list_users = users_ref.where("group.owner", "==", group["owner"]) \
            .where("group.list", "==", group["list"]) \
            .where("group.instance", "==", group["instance"]) \
            .stream()

        members_display = []
        for doc in same_list_users:
            data = doc.to_dict()
            role = data["group"].get("role", "user")
            members_display.append(f"{doc.id} ({role})")

        # Sort so admin appears first
        members_display.sort(key=lambda x: "(admin)" not in x)

        text = "👥 *Pessoas na Listinha:*\n\n" + "\n".join(members_display)
        send_message(from_number, text)
        return {"status": "ok"}

    # View list
    if cmd == "/v":
        items = get_items(phone)  # already sorted + capitalized in firebase.py

        group = get_user_group(phone)
        raw_doc_id = f"{group.get('instance', 'default')}__{group['owner']}__{group['list']}"
        doc_id = quote(raw_doc_id, safe="")

        # Get title from list document
        ref = firestore.client().collection("listas").document(raw_doc_id)
        doc = ref.get()
        title = doc.to_dict().get("title", "Sua Listinha") if doc.exists else "Sua Listinha"

        if len(items) > 20:
            html_url = f"https://listinha-t5ga.onrender.com/view?g={doc_id}&t={int(time.time())}"
            send_message(from_number, f"📄 *{title}* tem {len(items)} itens! Veja aqui:\n{html_url}")

        else:
            if items:
                text = f"📝 *{title}:*\n" + "\n".join(f"• {item}" for item in items)
            else:
                text = f"🗒️ *{title}* está vazia."
            send_message(from_number, text)

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
            send_message(from_number, "🗒️ Sua listinha está vazia. Adicione itens antes de gerar o PDF.")
        else:
            timestamp = int(time.time())
            pdf_url = f"https://listinha-t5ga.onrender.com/view?g={doc_id}&format=pdf&footer=true&download=true&t={timestamp}"
            send_message(from_number, f"📎 Aqui está sua listinha em PDF:\n{pdf_url}")

        return {"status": "ok"}

    # Clear all items: l (admin only)
    if cmd == "/l":
        if not is_admin(phone):
            send_message(from_number, "❌ Apenas o administrador pode limpar a listinha inteira.")
            return {"status": "ok"}

        clear_items(phone)
        send_message(from_number, "✅ Sua listinha foi limpa!")
        return {"status": "ok"}

    # ✅ Fallback for unknown commands
    send_message(from_number, "❓ Não entendi. Quer adicionar um item? Use i seguido do nome. Veja o menu com m.")
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

def render_list_page(doc_id, items, title="Sua Listinha", updated_at=""):
    with open("templates/list.html", encoding="utf-8") as f:
        html = f.read()
    template = Template(html)

    doc_id_encoded = quote(doc_id, safe="")
    return template.render(
        doc_id=doc_id_encoded,
        items=items,
        count=len(items),
        title=title,
        updated_at=updated_at
    )

