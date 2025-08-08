# messages.py

# General static messages
ALREADY_IN_LIST = "⚠️ Você já participa de uma Listinha. Saia dela para criar uma nova."
NOT_IN_LIST = "⚠️ Você ainda não participa de nenhuma Listinha. Envie 'listinha' para criar a sua."
INVALID_NUMBER = "❌ Número inválido. Verifique o formato e tente novamente."
NOT_ADMIN = "❌ Apenas o dono da lista pode executar este comando."
INVALID_SELF_EXIT = "❌ O número informado não corresponde ao seu. Tente novamente."
CANNOT_EXIT_AS_ADMIN = "⚠️ Você é o dono da lista — use o comando t (transferência de dono.)"
NO_PENDING_TRANSFER = "⚠️ Não há nenhuma transferência de dono pendente para você."
LIST_EMPTY_PDF = "🗒️ Sua listinha está vazia. Adicione itens antes de gerar o PDF."
UNKNOWN_COMMAND = "❓ Não entendi. Quer adicionar um item? Use i seguido do nome. Veja o menu com m."
NOT_OWNER_CANNOT_REMOVE = "❌ Apenas o Dono da Listinha pode remover Convidados."
SELF_EXIT_INSTRUCTION = "⚠️ Para sair da Listinha, envie: s <seu número>\nEx: s 11999999999"
NOT_OWNER_CANNOT_TRANSFER = "❌ Apenas o Dono da Listinha pode transferir o papel de Dono."
TRANSFER_RECEIVED = "📢 Você foi indicado para se tornar o Dono da Listinha. Envie 'o' para aceitar."
NOT_OWNER_CANNOT_RENAME = "❌ Apenas o Dono da Listinha pode modificar o título."
NOT_OWNER_CANNOT_CLEAR = "❌ Apenas o Dono da Listinha pode limpar a lista inteira."


# Dynamic messages
def list_created(name_or_phone):
    return f"🎉 Sua nova Listinha foi criada e você é o Dono dela, {name_or_phone}."

def item_added_log(item):
    return f"✅ Item adicionado: {item}"

def item_already_exists(item):
    return f"⚠️ O item '{item}' já está na listinha."

def item_removed(item):
    return f"❌ Item removido: {item}"

def item_not_found(item):
    return f"⚠️ Item não encontrado: {item}"

def guest_added(name, phone):
    return f"📢 Convidado {name} ({phone}) incluído na sua Listinha."

def guest_removed(name, phone):
    return f"🗑️ Convidado {name} ({phone}) removido da sua Listinha."

def guest_already_in_other_list(phone):
    return f"⚠️ O número {phone} já participa de outra Listinha."

def transfer_proposed(phone):
    return f"📢 Proposta de transferência enviada para {phone}."

def not_a_guest(phone):
    return f"⚠️ O número {phone} não é convidado da sua Listinha."

def list_title_updated(title):
    return f"🏷️ Título atualizado para: *{title}*"

def list_download_pdf(title, count, url):
    return f"📄 *{title}* tem {count} itens! Veja aqui:\n{url}"

def list_shown(title, items):
    if items:
        return f"📝 *{title}:*\n" + "\n".join(f"• {item}" for item in items)
    else:
        return f"🗒️ *{title}* está vazia."

def list_download_url(url):
    return f"📎 Aqui está sua listinha em PDF:\n{url}"

def list_detailed_url(url):
    return f"📎 Sua Listinha completa está pronta:\n{url}"

def not_a_member(phone):
    return f"⚠️ O número {phone} não é membro da sua Listinha."

LIST_CLEARED = "✅ Sua listinha foi limpa!"

WELCOME_MESSAGE = lambda name, admin: (
    f"👋 Olá{name and f' *{name}*' or ''}! {admin} adicionou você a uma *Listinha compartilhada* no WhatsApp.\n\n"
    "📖 *Como funciona a Listinha*\n"
    "A Listinha é uma lista de compras compartilhada no WhatsApp, "
    "onde todos os membros podem ver e adicionar itens em tempo real.\n\n"
    "👥 *Funcionamento básico:*\n"
    "1️⃣ O DONO cria a Listinha e adiciona os CONVIDADOS.\n"
    "2️⃣ Os convidados podem incluir ou remover itens.\n"
    "3️⃣ O dono pode limpar a lista inteira ou remover convidados.\n"
    "4️⃣ A lista é atualizada para todos instantaneamente.\n\n"
    "💡 *Dica:* Use o comando m para ver todos os comandos disponíveis."
)

TRANSFER_ACCEPTED = "✅ Agora você é o dono da Listinha."
TRANSFER_PREVIOUS_OWNER = (
    "📢 Seu status na Listinha mudou de dono para *convidado*. "
    "Se quiser sair da Listinha, use o comando 's' seguido do seu número."
)

LEFT_LIST = "👋 Você saiu da Listinha."

HELP_TEXT = (
    "📖 *Como funciona a Listinha*\n"
    "A Listinha é uma lista de compras compartilhada no WhatsApp, "
    "onde todos os membros podem ver e adicionar itens em tempo real.\n\n"
    "👥 *Funcionamento básico:*\n"
    "1️⃣ O DONO cria a Listinha e adiciona os CONVIDADOS.\n"
    "2️⃣ Os convidados podem incluir ou remover itens.\n"
    "3️⃣ O dono pode limpar a lista inteira ou remover convidados.\n"
    "4️⃣ A lista é atualizada para todos instantaneamente.\n\n"
    "💡 *Dica:* Use o comando m para ver todos os comandos disponíveis."
)

MENU_TEXT = (
    "📝 *Listinha Menu*:\n\n"
    "📋 *Comandos principais*:\n\n"
    "• i — Adicionar um item\n"
    "• a — Apagar um item\n"
    "• v — Ver a lista\n"
    "• u — Incluir convidado\n"
    "• h — Ajuda\n\n\n"
    "Exemplos\n\n"
        "n água"
        "a laranja"
        "u 11999999999\n\n\n"    
    "📖 Ver todos os comandos:\n"
    "https://listinha.app/comandos"
)

'''
MENU_TEXT = (
    "📝 *Listinha Menu*:\n\n"
    "📥 Adicionar item: i <item>\n"
    "❌ Apagar item: a <item>\n"
    "📋 Ver lista: v\n\n"
    "🧹 Limpar todos os itens da lista: l\n"
    "🏷️ Alterar título da lista: b <título>\n"
    "📎 Gerar PDF da lista: d\n"
    "📊 Gerar PDF detalhado da lista: x\n"
    "👤 Adicionar convidado: u <telefone>\n"
    "➖ Remover convidado: e <telefone>\n"
    "🔄 Transferir papel de dono: t <telefone>\n"
    "✅ Aceitar papel de dono: o\n"
    "👥 Consultar todos que estão na lista: p\n"
    "🚪 Sair da lista: s\n\n"
    "ℹ️ Ajuda: h / ajuda / help\n"
)
'''

def list_members(entries):
    return "👥 *Pessoas na Listinha:*\n\n" + "\n".join(entries)
