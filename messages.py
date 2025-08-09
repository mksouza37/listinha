# messages.py

# General static messages
ALREADY_IN_LIST = (
    "⚠️ Você já está participando de uma Listinha 😊\n"
    "Se quiser criar outra, primeiro saia da atual com o comando s 11999999999."
)
NOT_IN_LIST = (
    "⚠️ Você ainda não participa de nenhuma Listinha.\n"
    "Para começar, digite: listinha"
)
INVALID_NUMBER = (
    "❌ O número informado não parece correto 📱\n"
    "Digite assim: 11999999999 (apenas números, DDD + telefone)."
)
NOT_ADMIN = "❌ Esse comando só pode ser usado pelo Dono da Listinha."
INVALID_SELF_EXIT = "❌ Esse número não é o seu. Por favor, envie novamente usando o seu próprio número."
CANNOT_EXIT_AS_ADMIN = (
    "⚠️ Você é o Dono desta Listinha.\n"
    "Se quiser sair, primeiro transfira para outra pessoa com o comando: t <número>."
)
NO_PENDING_TRANSFER = "⚠️ Nenhuma transferência de Dono está pendente para você."
LIST_EMPTY_PDF = "🗒️ Sua Listinha está vazia no momento.\nAdicione um item antes de gerar o PDF."
UNKNOWN_COMMAND = (
    "❓ Não entendi 🤔\n"
    "Para adicionar um item, digite: i <nome do item>\n"
    "Para ver todos os comandos, digite: m."
)
NOT_OWNER_CANNOT_REMOVE = "❌ Só o Dono da Listinha pode remover Convidados."
SELF_EXIT_INSTRUCTION = "⚠️ Para sair da Listinha, envie: s <seu número>\nExemplo: s 11999999999"
NOT_OWNER_CANNOT_TRANSFER = "❌ Só o Dono da Listinha pode transferir o controle para outra pessoa."
TRANSFER_RECEIVED = (
    "📢 O Dono da sua Listinha quer passar o controle para você.\n"
    "Para aceitar, digite: o."
)
NOT_OWNER_CANNOT_RENAME = "❌ Só o Dono da Listinha pode mudar o título."
NOT_OWNER_CANNOT_CLEAR = "❌ Só o Dono da Listinha pode limpar todos os itens."

# Dynamic messages
def list_created(name_or_phone):
    return (
        f"🎉 Prontinho! Sua *Listinha* foi criada e você é o *Dono*, {name_or_phone}. "
        "Vamos começar? Digite `i <item>` para incluir o primeiro item."
    )

def item_added_log(item):
    return f"✅ Item *{item}* incluído na sua Listinha."

def item_already_exists(item):
    return f"⚠️ O item *{item}* já está na sua Listinha 😉"

def item_removed(item):
    return f"🗑️ Item *{item}* removido da sua Listinha."

def item_not_found(item):
    return f"🔎 Não encontrei o item *{item}* na sua Listinha."

def guest_added(name, phone):
    return f"👥 Convidado *{name or phone}* adicionado à sua Listinha. Bem-vindo(a)! ✨"

def guest_removed(name, phone):
    return f"👋 Convidado *{name or phone}* foi removido da sua Listinha."

def guest_already_in_other_list(phone):
    return (
        f"⚠️ O número *{phone}* já participa de outra *Listinha*. "
        "Peça para a pessoa sair da outra antes de entrar aqui."
    )

def transfer_proposed(phone):
    return (
        f"📤 Enviamos o convite para *{phone}* se tornar o *Dono* desta Listinha. "
        "Aguarde a aceitação."
    )

def not_a_guest(phone):
    return f"⚠️ O número *{phone}* não faz parte desta Listinha."

def list_title_updated(title):
    return f"🏷️ Título atualizado com sucesso: *{title}*"

def list_download_pdf(title, count, url):
    return f"📄 *{title}* tem {count} itens.\nAbra aqui para visualizar: {url}"

def list_shown(title, items):
    """
    Exibe a lista em bullets quando há itens;
    senão, instrui o primeiro passo com clareza.
    """
    if items:
        bullets = "\n".join(f"• {item}" for item in items)
        return f"📝 *{title}*\n{bullets}"
    else:
        return "🗒️ *{title}* está vazia. Digite `i <item>` para incluir o primeiro item.".format(title=title)

def list_download_url(url):
    return f"📎 Aqui está o PDF da sua Listinha:\n{url}"

def list_detailed_url(url):
    return "📎 Sua Listinha detalhada (com quem incluiu e quando) está pronta:\n{url}".format(url=url)

def not_a_member(phone):
    return f"⚠️ O número *{phone}* não participa desta Listinha."

def indication_text(phone_number_display: str = "1 415-523-8886") -> str:
    return f"""Testei e recomendo. Veja abaixo. 👇

🛒 Listinha: sua lista de compras no WhatsApp

Acabou aquela estória de quem esqueceu de comprar o que no supermercado! 😄
Com a Listinha, qualquer um da família pode adicionar itens pelo WhatsApp na hora que lembra. 
A lista fica disponível para todos, a qualquer momento — e no dia da compra, já está prontinha!

Gostaria de experimentar por 1 mês grátis? 
📞 Salva: {phone_number_display}
✍️ Manda "oi"

Sua lista será criada e você receberá orientações sobre como utilizar.

Dica: se após experimentar por um mês você gostar e indicar para amigos, ganha mais 2 meses grátis.
"""

def z_share_reply(share_link: str) -> str:
    return (
        "📢 Encaminhe o Listinha com 1 toque!\n\n"
        "Clique no link abaixo, escolha os contatos/grupos e envie:\n"
        f"{share_link}\n\n"
        "O texto já vai pronto para o WhatsApp, sem aparecer como 'encaminhado'."
    )


LIST_CLEARED = "✅ Sua listinha foi limpa!"

WELCOME_MESSAGE = lambda name, admin: (
    f"👋 Olá{name and f' *{name}*' or ''}! {admin} adicionou você a uma *Listinha compartilhada* no WhatsApp.\n\n"
    "📖 *O que é a Listinha?*\n"
    "É uma lista de compras compartilhada onde todos podem ver e incluir itens em tempo real.\n\n"
    "🛠️ *Como funciona:*\n"
    "1️⃣ O *Dono* cria a Listinha e adiciona os *Convidados*.\n"
    "2️⃣ Todos podem incluir ou remover itens.\n"
    "3️⃣ O *Dono* pode limpar a lista ou remover convidados.\n"
    "4️⃣ Tudo é atualizado para todos na hora.\n\n"
    "💡 Dica: Digite `m` para ver todos os comandos."
)

TRANSFER_ACCEPTED = "✅ Agora você é o Dono da Listinha."
TRANSFER_PREVIOUS_OWNER = (
    "📢 Seu status na Listinha mudou de Dono para *Convidado*. "
    "Se quiser sair da Listinha, use o comando 's' seguido do seu número."
)

LEFT_LIST = "👋 Você saiu da Listinha."

HELP_TEXT = (
    "📖 *Como funciona a Listinha*\n"
    "A Listinha é uma lista de compras compartilhada no WhatsApp, "
    "onde todos os membros podem ver e adicionar itens em tempo real.\n\n"
    "👥 *Funcionamento básico:*\n"
    "1️⃣ O Dono cria a Listinha e adiciona os Convidados.\n"
    "2️⃣ Os convidados podem incluir ou remover itens.\n"
    "3️⃣ O Dono pode limpar a lista inteira ou remover convidados.\n"
    "4️⃣ A lista é atualizada para todos instantaneamente.\n\n"
    "💡 *Dica:* Use o comando m para ver todos os comandos disponíveis."
)

MENU_TEXT = (
    "📝 *Listinha Menu*:\n\n"    
    "• i — Incluir um item na lista\n"
    "   Formato: `i <item>`\n"
    "   📌 ex.: `i água`\n"
    "   📌 ex.: `i arroz - 5 kg`\n\n"
    "• a — Apagar um item da lista\n"
    "   Formato: `a <item>`\n"
    "   📌 ex.: `a laranja`\n\n"
    "• v — Ver todos os itens da lista\n\n"
    "• u — Incluir um convidado\n"
    "   Formato: `u <telefone> <nome>`\n"
    "   📌 ex.: `u 11999999999 Alice`\n\n"
    "• h — Ajuda e instruções\n\n"
    "📖 Demais comandos:\n"
    "https://listinha-t5ga.onrender.com/static/comandos.html"
)

def list_members(entries):
    return "👥 *Pessoas na Listinha:*\n\n" + ("\n".join(entries) if entries else "(Ainda não há participantes além do Dono.)")
