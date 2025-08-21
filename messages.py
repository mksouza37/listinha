from datetime import datetime, timezone

# General static messages
ALREADY_IN_LIST = (
    "⚠️ Você já está participando de uma Listinha 😊\n"
    "Se quiser criar outra, primeiro saia da atual com o comando s 11999999999."
)

NAMELESS_OPENING = (
    "⚠️ Por favor, envie listinha mais o seu nome: ex.: listinha Patrícia"
)

ADD_USER_USAGE = (
    "Para adicionar alguém, envie:\n"
    "u <telefone> <nome>\n"
    "Ex.: u 11999999999 Patrícia"
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

# messages.py (near other static messages)
PAYMENT_REQUIRED = (
    "💳 Para usar a *Listinha*, é necessário ativar sua assinatura."
)
HOW_TO_PAY = "Envie *pagar* para receber o link ou use o link abaixo."
def CHECKOUT_LINK(url: str) -> str:
    return f"🔗 Ative aqui sua assinatura:\n{url}"

# Dynamic messages

# --- Billing messages (pt-BR) ---

STATUS_NAMES_PT = {
    "ACTIVE": "Ativa",
    "TRIAL": "Ativa para teste - 30 dias grátis",
    "TRIALING": "Ativa para teste - 30 dias grátis",
    "GRACE": "Ativa para teste - 60 dias grátis por indicação",
    "PAST_DUE": "Pagamento em atraso",
    "UNPAID": "Pagamento em atraso",
    "EXPIRED": "Expirada",
    "CANCELED": "Cancelada",
    "CHECKOUT_COMPLETED": "Checkout concluído",
}

try:
    from zoneinfo import ZoneInfo
    _TZ = ZoneInfo("America/Sao_Paulo")
except Exception:
    _TZ = timezone.utc

def _fmt_date(ts: int) -> str:
    try:
        dt = datetime.fromtimestamp(int(ts), tz=timezone.utc).astimezone(_TZ)
        return dt.strftime("%d/%m/%Y")
    except Exception:
        return str(ts)

CANCELLED_NOW = "❌ Sua assinatura foi cancelada e não será mais cobrada."
def CANCEL_SCHEDULED(until_ts: int | None) -> str:
    if until_ts:
        return f"⏳ Sua assinatura será cancelada em { _fmt_date(until_ts) }."
    return "⏳ Sua assinatura foi marcada para cancelamento ao final do período atual."

def PORTAL_LINK(url: str) -> str:
    return (
        "🔐 *Portal da assinatura*\n"
        f"{url}\n\n"
        "No portal você pode alterar cartão, ver faturas, cancelar ou retomar."
    )

def PORTAL_INACTIVE_CHECKOUT(url: str) -> str:
    return (
        "ℹ️ Você ainda não tem uma assinatura ativa.\n"
        "Para assinar, use este link de pagamento:\n"
        f"{url}"
    )

def STATUS_SUMMARY(state: str, until_ts: int | None) -> str:
    from datetime import datetime
    import pytz

    # Converte a sigla/estado técnico para PT-BR, mantendo fallback seguro
    key = (state or "").upper()
    state_pt = STATUS_NAMES_PT.get(key, state)

    if until_ts:
        dt = datetime.fromtimestamp(int(until_ts), pytz.timezone('America/Sao_Paulo'))
        until = dt.strftime('%d/%m/%Y %H:%M')
        return f"📦 Status da assinatura: *{state_pt}*\nVálida até: {until}"

    return f"📦 Status da assinatura: *{state_pt}*"


def RESUMED_STATUS(state: str, until_ts: int | None) -> str:
    # No imports here; reuse STATUS_SUMMARY from this same module
    base = "✅ Sua assinatura foi retomada e está ativa.\n"
    return base + STATUS_SUMMARY(state, until_ts)

# --- Other messages (pt-BR) ---

def REMOVED_FROM_LIST(admin_display_name: str) -> str:
    return (
        f"🚫 Você foi removido da *Listinha* de {admin_display_name}.\n"
        "Se foi engano, peça um novo convite."
    )


def MEMBER_LEFT_NOTIFICATION(leaver_display: str) -> str:
    return f"👋 {leaver_display} saiu da sua Listinha."


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


def br_local_number(num: str) -> str:
    """Return the Brazilian local form without country code.
    Ex.: '+55 11 91270-5543' -> '11912705543'"""
    digits = "".join(ch for ch in (num or "") if ch.isdigit())
    # Drop leading '55' only when it looks like E.164 (12–13 digits total for BR)
    if digits.startswith("55") and len(digits) >= 12:
        return digits[2:]
    return digits


def guest_added(name, phone):
    return f"👥 Convidado *{name or phone}* adicionado à sua Listinha. Bem-vindo(a)! ✨"


def guest_removed(name, phone):
    # ✅ Show both name and local phone without +55
    display = f"{name} — {br_local_number(phone)}" if name else br_local_number(phone)
    return f"👋 Convidado *{display}* foi removido da sua Listinha."


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


LIST_CLEARED = "✅ Sua listinha foi limpa!"

WELCOME_MESSAGE = lambda name, admin: (
    f"👋 Olá{name and f' *{name}*' or ''}! {admin} adicionou você a uma *Listinha* no WhatsApp.\n\n"
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
    "💡 *Dica:* Use o comando M para ver todos os comandos disponíveis."
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

def indication_text(display_number: str) -> str:
    local = br_local_number(display_number)  # e.g. '11999999999'
    return f"""Experimentei e achei interessante. Estou compartilhando.

    🛒 Conheça a Listinha: sua lista de compras no WhatsApp.

    Acabou aquela estória de chegar do supermercado e ver que esqueceu de comprar isso ou aquilo! 😄
    Com a Listinha, qualquer um da família pode adicionar itens pelo WhatsApp na hora que lembra. 
    A lista fica disponível para todos, a qualquer momento — e no dia da compra, já está prontinha!

    Gostaria de experimentar por 1 mês grátis?

    📞 No seu WhatsApp digite: {local} e acione conversar
    ✍️ Envie: listinha "seu nome". Ex.: listinha Patrícia

    Pronto! Sua listinha estará criada e você receberá orientações sobre como utilizá-la.

    Quer saber um pouco mais sobre a listinha? Visite nosso site: https://listinha-landing.onrender.com

    Dica: se após experimentar por um mês você gostar e indicar para amigos, ganhará mais 2 meses grátis.
    """

def list_members(entries):
    return "👥 *Pessoas na Listinha:*\n\n" + (
        "\n".join(entries) if entries else "(Ainda não há participantes além do Dono.)")


def z_step1_instructions() -> str:
    return (
        "📣 Ajude a divulgar a Listinha!\n\n"
        "1) COPIE a mensagem que enviei logo após essa.\n"
        "2) COLE em um grupo ou contato e envie.\n"

        "Muito obrigado por ajudar na divulgação!\n\n"

        "👇 Mensagem para copiar e enviar."
    )


NEED_REFRESH_VIEW = "📄 Sua visualização está desatualizada. Envie *v* para ver a lista numerada novamente."


def item_index_invalid(n: int, total: int) -> str:
    return f"❌ Número {n} não corresponde a nenhum item. Envie *v* para ver a lista numerada ({total} itens)."


def list_shown(title: str, items: list[str]) -> str:
    if not items:
        return f"📄 *{title}*\n(sem itens)"
    lines = [f"{i + 1}. {items[i]}" for i in range(len(items))]
    return f"📄 *{title}* ({len(items)} itens)\n\n" + "\n".join(lines)
