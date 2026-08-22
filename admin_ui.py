from admin_i18n import (
    format_handoff_decision_ru,
    format_offer_status_ru,
    format_partner_status_ru,
    format_service_category_ru,
)
from partner_handoff import reject_offer, send_offer_to_client
from partner_network import is_admin, send_case_to_partner, set_partner_auto_handoff
from partner_authority import decide_proposal


def can_access_admin_panel(admin_user_id, user_id):
    return is_admin(admin_user_id, user_id)


def admin_panel_buttons():
    return [
        [("🗂 Кейсы", "admin:cases"), ("📋 Предложения", "admin:offers")],
        [("🤝 Партнёры", "admin:partners")],
        [("📩 Запросы партнёрам", "admin:requests")],
        [("⚙️ Настройки", "admin:settings")],
    ]


def offer_action_buttons(offer_id, case_id, partner_id):
    return [
        [("📤 Отправить клиенту", f"offer:send:{offer_id}")],
        [("❌ Отклонить", f"offer:reject:{offer_id}")],
        [
            ("🗂 Открыть кейс", f"case:view:{case_id}"),
            ("🤝 Открыть партнёра", f"partner:view:{partner_id}:{case_id}"),
        ],
        [("⬅️ В панель", "admin:panel")],
    ]


def partner_action_buttons(partner_id, auto_handoff_enabled, case_id=None,
                           pending_count=0):
    rows = []
    if case_id is not None:
        rows.append([("📩 Отправить запрос", f"partner:send:{case_id}:{partner_id}")])
    if pending_count:
        rows.append([(
            f"⚠️ Ожидают решения ({pending_count})",
            f"terms:list:{partner_id}",
        )])
    if auto_handoff_enabled:
        rows.append([("⛔ Выключить автоотправку", f"partner:auto:{partner_id}:off")])
    else:
        rows.append([("✅ Включить автоотправку", f"partner:auto:{partner_id}:on")])
    rows.append([("⬅️ В панель", "admin:panel")])
    return rows


def commercial_proposal_buttons(proposal_id, partner_id):
    return [
        [("✅ Утвердить", f"terms:approve:{proposal_id}"),
         ("❌ Отклонить", f"terms:reject:{proposal_id}")],
        [("⬅️ Назад к партнёру", f"partner:view:{partner_id}")],
    ]


def pending_proposal_list_buttons(proposals, partner_id):
    rows = []
    for proposal in proposals:
        changes = _format_terms(proposal.get("proposed_changes"))
        rows.append([(
            f"⚠️ {changes}", f"terms:view:{proposal['id']}"
        )])
    rows.append([("⬅️ Назад к партнёру", f"partner:view:{partner_id}")])
    return rows


def _format_terms(terms):
    if not terms:
        return "Не указаны"
    labels = {
        "commission": "Комиссия", "discount": "Скидка",
        "payment_method": "Способ оплаты", "exclusivity": "Эксклюзивность",
        "liability": "Ответственность/компенсация",
        "contractual_obligation": "Договорное обязательство",
    }
    return "; ".join(f"{labels.get(k, k)}: {v}" for k, v in terms.items())


def format_commercial_proposal_card(proposal, partner):
    return (
        "⚠️ Партнёр предложил новые коммерческие условия\n\n"
        f"Партнёр: {partner['name']}\n"
        f"Старые утверждённые условия: {_format_terms(partner.get('approved_terms'))}\n"
        f"Предложенные изменения: {_format_terms(proposal['proposed_changes'])}\n"
        f"Источник: {proposal['source']}\n"
        f"Сообщение: {proposal['source_message']}\n"
        f"Дата: {proposal['created_at']}\n\n"
        "Статус: ожидает решения владельца"
    )


def format_partner_card(partner):
    services = ", ".join(
        format_service_category_ru(item) for item in partner.get("services", [])
    ) or "Не указаны"
    auto_state = "Включена" if partner.get("auto_handoff_enabled") else "Выключена"
    return (
        f"Партнёр №{partner['id']} — {partner['name']}\n\n"
        f"Тип: {partner.get('partner_type', 'service_provider')}\n"
        f"Статус: {format_partner_status_ru(partner.get('status'))}\n"
        f"Услуги: {services}\n"
        f"Районы: {', '.join(partner.get('areas', [])) or 'Не указаны'}\n"
        f"Утверждённые условия: {_format_terms(partner.get('approved_terms'))}\n"
        f"Ожидают решения: {len(partner.get('pending_terms', []))}\n"
        f"Разрешённые действия: {', '.join(partner.get('allowed_actions', [])) or 'Не указаны'}\n"
        f"Операционные заметки: {partner.get('operational_notes') or 'Нет'}\n"
        f"Автоотправка: {auto_state}"
    )


def format_offer_list_item(offer):
    return (
        f"Предложение №{offer['id']} — кейс №{offer['case_id']}\n"
        f"Партнёр: {offer.get('partner_name', offer['partner_id'])}\n"
        f"Статус: {format_offer_status_ru(offer.get('status'))}\n"
        f"Решение: {format_handoff_decision_ru(offer.get('handoff_decision'))}"
    )


async def execute_offer_send(offer_id, telegram_sender, db_path=None):
    return await send_offer_to_client(
        offer_id, telegram_sender, manual_approval=True, db_path=db_path
    )


def execute_offer_reject(offer_id, db_path=None):
    return reject_offer(offer_id, db_path)


def execute_partner_auto_toggle(partner_id, enabled, db_path=None):
    return set_partner_auto_handoff(partner_id, enabled, db_path)


async def execute_partner_send(case_id, partner_id, telegram_sender, db_path=None):
    return await send_case_to_partner(case_id, partner_id, telegram_sender, db_path)


def execute_commercial_decision(proposal_id, approve, owner_id=None, db_path=None):
    return decide_proposal(proposal_id, approve, owner_id=owner_id, db_path=db_path)
