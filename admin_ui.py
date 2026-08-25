import json

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
        [("📝 Заявки партнёров", "admin:applications")],
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
    rows.extend([
        [("💼 Коммерческие условия", f"partner:commercial:{partner_id}")],
        [("🏠 Объекты и работа", f"partner:operations:{partner_id}")],
        [("❓ Открытые вопросы", f"partner:questions:{partner_id}")],
        [("⚙️ Разрешённые действия", f"partner:actions:{partner_id}")],
    ])
    if auto_handoff_enabled:
        rows.append([("⛔ Выключить автоотправку", f"partner:auto:{partner_id}:off")])
    rows.append([("⬅️ К списку партнёров", "admin:partners")])
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


PARTNER_TYPE_LABELS = {
    "service_provider": "Поставщик услуг",
    "b2b_channel": "B2B-партнёр",
    "hybrid": "Гибридный партнёр",
}

AREA_LABELS = {
    "karon": "Карон",
    "bang tao": "Банг Тао",
    "bang tao / bellevue": "Банг Тао / Bellevue",
}

ACTION_LABELS = {
    "ask_availability": "Запрашивать наличие",
    "ask_capacity": "Уточнять вместимость",
    "ask_location": "Уточнять расположение",
    "ask_operational_requirements": "Уточнять рабочие требования",
    "ask_schedule": "Уточнять график",
    "ask_service_details": "Запрашивать подробности услуги",
    "receive_offer": "Получать предложения",
    "send_approved_terms": "Отправлять утверждённые условия",
    "send_request": "Передавать запрос",
    "update_operational_status": "Обновлять рабочий статус",
}


def _area_label(value):
    text = str(value or "").strip()
    return AREA_LABELS.get(text.casefold(), text or "Не указан")


def _operational_data(partner):
    raw = partner.get("operational_notes")
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(raw or "{}")
        return parsed if isinstance(parsed, dict) else {}
    except (TypeError, json.JSONDecodeError):
        return {}


def format_partner_card(partner):
    services = ", ".join(
        format_service_category_ru(item) for item in partner.get("services", [])
    ) or "Не указаны"
    auto_state = "Включена" if partner.get("auto_handoff_enabled") else "Выключена"
    return (
        f"🤝 {partner['name']}\n\n"
        f"Статус: {format_partner_status_ru(partner.get('status'))}\n"
        "Формат: "
        f"{PARTNER_TYPE_LABELS.get(partner.get('partner_type'), 'Партнёр')}\n"
        f"Направление: {services}\n"
        "Районы: "
        f"{', '.join(_area_label(item) for item in partner.get('areas', [])) or 'Не указаны'}\n"
        f"Ожидают решения: {len(partner.get('pending_terms', []))}\n"
        f"Автоотправка: {auto_state}"
    )


def format_partner_commercial_terms(partner):
    terms = partner.get("approved_terms") or {}
    base = terms.get("base_commission_percent")
    lines = ["💼 Утверждённые коммерческие условия", ""]
    lines.append(f"Базовая комиссия: {base}%" if base else "Базовая комиссия: не указана")
    ladder = [
        (5, terms.get("commission_ladder_5_successful_deals_percent")),
        (10, terms.get("commission_ladder_10_successful_deals_percent")),
        (30, terms.get("commission_ladder_30_successful_deals_percent")),
    ]
    if any(value for _, value in ladder):
        lines.extend(["", "Рост комиссии:"])
        lines.extend(
            f"— после {count} успешных сделок: {value}%"
            for count, value in ladder if value
        )
    if terms.get("client_referral_before_booking"):
        lines.extend([
            "", "Перед бронированием:",
            "Phuket Life передаёт партнёру имя и контакт клиента и сообщает "
            "источник обращения.",
        ])
    if terms.get("housing_invoice_issuer"):
        lines.extend([
            "", "Счёт за проживание:",
            f"выставляет {str(terms['housing_invoice_issuer']).rstrip('.')}.",
        ])
    if terms.get("partner_reward_invoice_process"):
        lines.extend([
            "", "Партнёрское вознаграждение:",
            "после успешной сделки Phuket Life направляет отдельный счёт.",
        ])
    return "\n".join(lines)


def format_partner_operations(partner):
    data = _operational_data(partner)
    areas = data.get("areas_context") or partner.get("areas", [])
    inventory = data.get("inventory_context") or {}
    lines = [
        "🏠 Объекты и операционная информация", "",
        "Направление: "
        f"{str(data.get('direction') or 'не указано').replace(' / ', ' и ')}",
        "", "Районы:",
    ]
    lines.extend(f"— {_area_label(item)}" for item in areas)
    inventory_rows = [
        ("комплекс 1", inventory.get("complex_1_approx_apartments")),
        ("комплекс 2", inventory.get("complex_2_approx_apartments")),
    ]
    if any(value is not None for _, value in inventory_rows):
        lines.extend(["", "Ориентировочный фонд:"])
        lines.extend(
            f"— {label}: около {value} апартаментов"
            for label, value in inventory_rows if value is not None
        )
    warning = inventory.get("warning")
    if warning:
        lines.extend(["", "⚠️ Количество объектов может меняться. Это не подтверждённое наличие."])
    if data.get("live_availability_source"):
        lines.extend(["", "Источник актуального наличия:", data["live_availability_source"]])
    if data.get("availability_policy"):
        lines.extend(["", "Правило:", "Наличие и цены подтверждаются через актуальный источник или напрямую у партнёра."])
    capabilities = data.get("known_capabilities") or []
    if capabilities:
        lines.extend(["", "Известная возможность:", str(capabilities[0])])
    return "\n".join(lines)


def format_partner_open_questions(partner):
    questions = _operational_data(partner).get("open_questions") or []
    if not questions:
        return "❓ Требуют согласования\n\nВсе необходимые вопросы согласованы ✅"
    labels = {
        "комиссия при отмене бронирования": "условия комиссии при отмене",
        "комиссия при переносе бронирования": "условия комиссии при переносе",
        "конкретная crypto payment scheme": "схема оплаты криптовалютой",
        "валюты и networks для crypto": "поддерживаемые валюты и сети",
    }
    return "❓ Требуют согласования\n\n" + "\n".join(
        f"— {labels.get(str(item), str(item))};" for item in questions
    )


def format_partner_allowed_actions(partner):
    actions = partner.get("allowed_actions") or []
    if not actions:
        return "⚙️ Разрешённые действия\n\nРазрешённые действия не указаны."
    return "⚙️ Разрешённые действия\n\n" + "\n".join(
        "— " + ACTION_LABELS.get(
            action,
            "Другое действие: " + str(action).replace("_", " ").capitalize(),
        )
        for action in actions
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
