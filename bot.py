from dotenv import load_dotenv

load_dotenv()

import uuid
import json
import time
import requests

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    ApplicationHandlerStop,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)
from partner_network import (
    DuplicatePartnerRequestError,
    PartnerNetworkError,
    PartnerTelegramError,
    PartnerUnavailableError,
    create_partner,
    create_partner_invite,
    find_partners_for_case,
    get_case_for_partner,
    get_partner,
    is_admin,
    list_partner_requests,
    list_partners,
    onboard_partner,
    record_partner_reply,
    send_case_to_partner,
    set_partner_auto_handoff,
    set_partner_status,
    update_partner,
)
from partner_handoff import (
    DuplicateOfferSendError,
    OfferHandoffError,
    OfferTelegramError,
    create_offer_from_partner_response,
    format_client_offer,
    get_offer,
    get_offer_context,
    list_offers,
    reject_offer,
    send_offer_to_client,
)
from admin_case import (
    AdminCaseNotFoundError,
    format_offer_review_card,
    get_admin_case_snapshot,
    list_admin_cases,
)
from admin_i18n import (
    format_partner_request_status_ru,
    format_partner_status_ru,
    format_service_category_ru,
)
from admin_ui import (
    admin_panel_buttons,
    can_access_admin_panel,
    execute_offer_reject,
    execute_offer_send,
    execute_partner_auto_toggle,
    execute_partner_send,
    format_offer_list_item,
    format_partner_card,
    offer_action_buttons,
    partner_action_buttons,
)

from case_engine import (
    update_case,
    get_client_active_case,
    format_case_for_ai,
    close_active_case,
    set_case_status,
)
from search_engine import SEARCH_PROVIDER_ERROR
from message_router import (
    CONVERSATION,
    NEW_CASE,
    SEARCH_REQUEST,
    should_start_search,
)
from token_cache import AccessTokenCache
from async_utils import run_blocking
from housing_flow import execute_housing_search
from housing_flow import build_housing_missing_question
from case_flow import persist_case_analysis
from truthfulness import (
    GENERATION_DELAY_MESSAGE,
    PROVIDER_ERROR_MESSAGE,
    get_no_results_message,
    guard_client_voice,
)
from search_presentation import (
    build_pre_search_message,
    build_results_message,
)
from conversation_policy import (
    CLARIFY_CONTINUITY,
    apply_case_continuity,
    build_continuity_question,
    guard_policy_answer,
    plan_response,
    pure_greeting_response,
    route_with_conversation_policy,
    should_use_conversation_flow,
)
from conversation_prompts import build_conversation_policy_prompt
from gigachat_provider import GigaChatGenerationError, generate_text
from answer_source import (
    PROVIDER_SEARCH,
    TRUSTED_REFERENCE,
    format_current_source_requirement,
    select_answer_source,
)
from reference_formatter import format_reference_answer
from config import load_settings
from database import (
    init_db,
    get_connection,
    get_or_create_client as db_get_or_create_client,
)


# =========================================================
# НАСТРОЙКИ
# =========================================================

SETTINGS = load_settings()

MODEL = "GigaChat-2-Max"

MAX_HISTORY = 20


# =========================================================
# SYSTEM PROMPT
# =========================================================

SYSTEM_PROMPT = """
Ты — AI-консьерж проекта Phuket Life.

Phuket Life помогает туристам и экспатам в Таиланде
с жильём, трансферами, экскурсиями, поездками,
спортом, бытовыми и организационными вопросами.

Твоя задача:

1. Понять реальную потребность клиента.
2. Использовать всю информацию из текущего кейса.
3. Не спрашивать повторно уже известную информацию.
4. Если не хватает важных данных — уточнить только их.
5. Общаться естественно, дружелюбно и профессионально.
6. Отвечать на русском языке.

=========================================================
КРИТИЧЕСКОЕ ПРАВИЛО ДОСТОВЕРНОСТИ
=========================================================

НИКОГДА не выдумывай:

- квартиры;
- дома;
- кондоминиумы;
- отели;
- цены;
- адреса;
- телефоны;
- контакты;
- наличие;
- свободные даты;
- объявления;
- компании;
- партнёров;
- подтверждение партнёров;
- результаты поиска.

Особенно запрещено придумывать конкретные варианты жилья.

Нельзя писать:

"Я нашёл..."
"Доступно..."
"В наличии..."
"Проверено..."
"Наш партнёр подтвердил..."
"Можно забронировать..."

если такая информация действительно не была получена
из подключённой системы Phuket Life.

Если реальных предложений нет, честно сообщай об этом.

=========================================================
ПАМЯТЬ КЛИЕНТА
=========================================================

Если передан текущий кейс клиента,
используй его как основную память.

Не спрашивай повторно:

- даты;
- количество людей;
- бюджет;
- питомца;
- район;
- другие параметры,

если они уже присутствуют в кейсе.

Не изменяй ранее сохранённые данные без причины.

=========================================================
ЖИЛЬЁ
=========================================================

Для поиска жилья необходимо собрать:

- дату заезда;
- дату выезда;
- количество проживающих;
- бюджет.

Дополнительные параметры:

- район;
- питомец;
- тип жилья.

Если основные параметры собраны,
не продолжай бесконечно задавать вопросы.

Сформируй итоговый запрос клиента.

В итоговом запросе покажи:

1. количество гостей;
2. период проживания;
3. бюджет;
4. район;
5. питомца, если есть;
6. тип жилья, если указан.

Если тип жилья не указан,
можно написать:

"Тип жилья: подберём оптимальный вариант
исходя из остальных условий."

После этого:

"Все параметры зафиксированы.
Приступаем к поиску подходящих вариантов. 🔎"

ВАЖНО:

Это сообщение означает только начало поиска.

Не утверждай, что конкретное жильё уже найдено,
если оно реально не было получено из поисковой системы.

=========================================================
СТИЛЬ
=========================================================

Пиши кратко и естественно.

Не перечисляй без необходимости все услуги Phuket Life.

Если нужно задать вопросы — задавай только действительно
необходимые вопросы.

Не говори клиенту, что ты языковая модель.

Представляйся AI-консьержем Phuket Life.

Не утверждай, что внешнее действие выполнено, если приложение не передало
подтверждённый результат этого действия. Не обещай фоновый поиск или будущую
отправку результата: фоновых задач в системе нет. Общие рекомендации не
подкрепляй точными процентами и числами без подтверждённых данных.
"""


# =========================================================
# DATABASE
# =========================================================

def get_or_create_client(update: Update):

    user = update.effective_user

    telegram_id = user.id
    username = user.username
    first_name = user.first_name
    last_name = user.last_name

    return db_get_or_create_client(
        telegram_id,
        username,
        first_name,
        last_name,
    )


def save_message(client_id, role, content):

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        """
        INSERT INTO messages
        (
            client_id,
            role,
            content
        )
        VALUES (?, ?, ?)
        """,
        (
            client_id,
            role,
            content
        )
    )

    connection.commit()
    connection.close()


def get_history(client_id):

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT role, content
        FROM messages
        WHERE client_id = ?
        ORDER BY id DESC
        LIMIT ?
        """,
        (
            client_id,
            MAX_HISTORY
        )
    )

    rows = cursor.fetchall()

    connection.close()

    rows.reverse()

    return [
        {
            "role": role,
            "content": content
        }
        for role, content in rows
    ]


def clear_history(client_id):

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        """
        DELETE FROM messages
        WHERE client_id = ?
        """,
        (client_id,)
    )

    connection.commit()
    connection.close()


# =========================================================
# GIGACHAT TOKEN
# =========================================================

def _fetch_access_token():

    url = (
        "https://ngw.devices.sberbank.ru:9443/"
        "api/v2/oauth"
    )

    headers = {
        "Content-Type": (
            "application/x-www-form-urlencoded"
        ),
        "Accept": "application/json",
        "RqUID": str(uuid.uuid4()),
        "Authorization": (
            f"Basic {SETTINGS.gigachat_api_key}"
        ),
    }

    data = {
        "scope": "GIGACHAT_API_PERS"
    }

    response = requests.post(
        url,
        headers=headers,
        data=data,
        timeout=30,
        verify=SETTINGS.gigachat_tls_verify
    )

    response.raise_for_status()

    result = response.json()
    return (
        result["access_token"],
        result.get("expires_at", time.time() + 1800),
    )


GIGACHAT_TOKEN_CACHE = AccessTokenCache(
    _fetch_access_token,
    refresh_margin_seconds=60,
)


def get_access_token():
    return GIGACHAT_TOKEN_CACHE.get()


# =========================================================
# CLEAN JSON
# =========================================================

def clean_json_response(content):

    if not content:

        raise ValueError(
            "GigaChat вернул пустой ответ"
        )

    content = content.strip()

    if content.startswith("```"):

        lines = content.splitlines()

        cleaned_lines = []

        for line in lines:

            if line.strip().lower() in (
                "```",
                "```json"
            ):

                continue

            cleaned_lines.append(line)

        content = "\n".join(
            cleaned_lines
        ).strip()

    return content


# =========================================================
# GIGACHAT — ОТВЕТ КЛИЕНТУ
# =========================================================

def ask_gigachat(
    history,
    case_context=None,
    response_plan=None,
    correlation_id=None,
):

    access_token = get_access_token()

    system_content = SYSTEM_PROMPT

    if response_plan:
        system_content += "\n\n" + build_conversation_policy_prompt(response_plan)

    if case_context:

        system_content += (
            "\n\n"
            "=================================================\n"
            "ТЕКУЩИЙ КЕЙС КЛИЕНТА\n"
            "=================================================\n"
            f"{case_context}\n"
            "\n"
            "Используй эти данные как память клиента.\n"
            "Не спрашивай повторно уже известные параметры.\n"
        )

    messages = [
        {
            "role": "system",
            "content": system_content
        }
    ]

    for message in history:

        role = message.get("role")
        content = message.get("content")

        if role in (
            "user",
            "assistant"
        ) and content:

            messages.append(
                {
                    "role": role,
                    "content": content
                }
            )

    return generate_text(
        messages,
        access_token=access_token,
        model=MODEL,
        timeout=SETTINGS.gigachat_timeout_seconds,
        ca_bundle=SETTINGS.gigachat_ca_bundle,
        temperature=0.7,
        stage="conversation",
        correlation_id=correlation_id,
    )


# =========================================================
# АНАЛИЗ КЕЙСА
# =========================================================

def analyze_case(
    history,
    existing_case=None,
    correlation_id=None,
):

    access_token = get_access_token()

    existing_case_context = ""

    if existing_case:

        existing_case_context = (
            "\n\n"
            "УЖЕ СОХРАНЁННЫЙ КЕЙС:\n"
            f"{format_case_for_ai(existing_case)}\n"
            "\n"
            "ВАЖНО: не теряй эти данные."
        )

    analysis_prompt = f"""
Ты анализируешь запрос клиента Phuket Life.

Твоя задача — определить активный запрос клиента
и вернуть ТОЛЬКО корректный JSON.

НЕ используй markdown.
НЕ используй ```json.
НЕ добавляй пояснения.

Формат:

{{
  "category": "housing",
  "title": "Поиск жилья на Пхукете",
  "data": {{
    "arrival_date": "",
    "departure_date": "",
    "budget": "",
    "location": "",
    "people": "",
    "pet": "",
    "housing_type": ""
  }},
  "missing_data": []
}}

Правила:

1. Не выдумывай данные.

2. Если информации нет — оставляй поле пустым.

3. Учитывай всю историю диалога.

4. Учитывай уже сохранённый кейс.

5. Не удаляй ранее известные данные.

6. Не меняй известные данные без основания.

7. Если клиент написал:
   "нас двое",
   people = "2".

8. Если клиент написал:
   "с собакой",
   pet = "собака".

9. Если клиент написал:
   "с 15 по 30 сентября",
   arrival_date = "15 сентября",
   departure_date = "30 сентября".

10. Если клиент сообщил бюджет,
    сохрани его максимально близко к словам клиента.

11. Не придумывай год.

12. Для housing обязательными являются:

    arrival_date
    departure_date
    people
    budget

13. Район, питомец и тип жилья являются дополнительными
    параметрами.

14. Если обязательные данные уже собраны,
    missing_data должен быть пустым.

15. Категории:

housing
transfer
pet
other

{existing_case_context}
"""

    messages = [
        {
            "role": "system",
            "content": analysis_prompt
        }
    ]

    for message in history:

        role = message.get("role")
        content = message.get("content")

        if role in (
            "user",
            "assistant"
        ) and content:

            messages.append(
                {
                    "role": role,
                    "content": content
                }
            )

    content = generate_text(
        messages,
        access_token=access_token,
        model=MODEL,
        timeout=SETTINGS.gigachat_timeout_seconds,
        ca_bundle=SETTINGS.gigachat_ca_bundle,
        temperature=0,
        stage="analyze_case",
        correlation_id=correlation_id,
    )

    content = clean_json_response(
        content
    )

    return json.loads(content)


# =========================================================
# ФОРМИРОВАНИЕ ПОДТВЕРЖДЕНИЯ ЗАПРОСА
# =========================================================

def build_search_confirmation(
    case_data
):

    people = case_data.get(
        "people",
        "не указано"
    )

    arrival_date = case_data.get(
        "arrival_date",
        "не указана"
    )

    departure_date = case_data.get(
        "departure_date",
        "не указана"
    )

    budget = case_data.get(
        "budget",
        "не указан"
    )

    location = case_data.get(
        "location",
        "не определён"
    )

    pet = case_data.get(
        "pet",
        ""
    )

    housing_type = case_data.get(
        "housing_type",
        ""
    )

    confirmation = (
        "Отлично, все основные параметры собраны. 🔎\n\n"
        "Ваш запрос:\n\n"
        f"1. 👥 Гости: {people} взрослых\n"
        f"2. 📅 Период: "
        f"{arrival_date} — {departure_date}\n"
        f"3. 💰 Бюджет: {budget}\n"
        f"4. 📍 Район: {location}\n"
    )

    if pet:

        confirmation += (
            f"5. 🐕 Питомец: {pet}\n"
        )

    else:

        confirmation += (
            "5. 🐕 Питомец: нет\n"
        )

    if housing_type:

        confirmation += (
            f"6. 🏠 Тип жилья: "
            f"{housing_type}\n"
        )

    else:

        confirmation += (
            "6. 🏠 Тип жилья: "
            "подберём оптимальный вариант "
            "исходя из остальных условий\n"
        )

    confirmation += (
        "\n"
        "Все параметры зафиксированы.\n"
        "Приступаем к поиску подходящих "
        "вариантов. 🔎"
    )

    return confirmation


# =========================================================
# START
# =========================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if context.args and context.args[0].startswith("partner_"):
        token = context.args[0][len("partner_"):]
        try:
            partner = onboard_partner(
                token,
                update.effective_user.id,
                update.effective_user.username,
            )
        except PartnerUnavailableError:
            await update.message.reply_text(
                "Ссылка приглашения недействительна или уже использована."
            )
            return
        await update.message.reply_text(
            f"Партнёр {partner['name']} подключён к Phuket Life. 🤝\n\n"
            "Теперь отвечайте reply на сообщения с запросами."
        )
        return

    get_or_create_client(update)

    await update.message.reply_text(
        "Здравствуйте! 👋\n\n"
        "Я AI-консьерж Phuket Life.\n\n"
        "Помогу организовать поездку и решить "
        "различные вопросы, связанные с "
        "пребыванием в Таиланде.\n\n"
        "Расскажите, что вам необходимо."
    )


async def _require_admin(update):
    if is_admin(SETTINGS.telegram_admin_user_id, update.effective_user.id):
        return True
    await update.message.reply_text("Команда доступна только администратору.")
    return False


def _admin_keyboard(button_rows):
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(text, callback_data=data) for text, data in row]
            for row in button_rows
        ]
    )


async def admin_command(update, context):
    if not await _require_admin(update):
        return
    await update.message.reply_text(
        "Панель администратора Phuket Life",
        reply_markup=_admin_keyboard(admin_panel_buttons()),
    )


async def partners_command(update, context):
    if not await _require_admin(update):
        return
    partners = list_partners()
    if not partners:
        await update.message.reply_text("Партнёры пока не добавлены.")
        return
    lines = ["🤝 Партнёры"]
    for partner in partners:
        connected = "Telegram подключён" if partner.get("telegram_user_id") else "не подключён"
        lines.append(
            f"Партнёр №{partner['id']} — {partner['name']}\n"
            f"Статус: {format_partner_status_ru(partner['status'])}\n"
            f"Услуги: {', '.join(format_service_category_ru(item) for item in partner['services']) or '—'}; {connected}"
        )
    await update.message.reply_text("\n\n".join(lines))


async def partner_requests_command(update, context):
    if not await _require_admin(update):
        return
    requests_list = list_partner_requests()
    if not requests_list:
        await update.message.reply_text("Запросов партнёрам пока нет.")
        return
    lines = ["📨 Последние запросы партнёрам"]
    for item in requests_list:
        lines.append(
            f"Запрос №{item['id']} · кейс №{item['case_id']} → "
            f"{item['partner_name']}\n"
            f"Статус: {format_partner_request_status_ru(item['status'])}\n"
            f"{get_admin_case_snapshot(item['case_id'])}"
        )
    await update.message.reply_text("\n".join(lines))


async def case_partners_command(update, context):
    if not await _require_admin(update):
        return
    if len(context.args) != 1 or not context.args[0].isdigit():
        await update.message.reply_text("Использование: /case_partners <case_id>")
        return
    try:
        case = get_case_for_partner(int(context.args[0]))
        partners = find_partners_for_case(case)
    except PartnerNetworkError as error:
        await update.message.reply_text(str(error))
        return
    if not partners:
        await update.message.reply_text("Подходящих активных партнёров не найдено.")
        return
    await update.message.reply_text(
        get_admin_case_snapshot(case["id"])
        + "\n\nПодходящие партнёры:\n"
        + "\n".join(
            f"Партнёр №{partner['id']} — {partner['name']} — "
            f"{', '.join(format_service_category_ru(item) for item in partner['services'])}"
            for partner in partners
        )
    )


async def send_partner_command(update, context):
    if not await _require_admin(update):
        return
    if len(context.args) != 2 or not all(arg.isdigit() for arg in context.args):
        await update.message.reply_text(
            "Использование: /send_partner <case_id> <partner_id>"
        )
        return
    case_id, partner_id = map(int, context.args)
    try:
        request = await execute_partner_send(
            case_id, partner_id, context.bot.send_message
        )
    except DuplicatePartnerRequestError:
        await update.message.reply_text(
            "Активный запрос этому партнёру уже существует."
        )
        return
    except PartnerTelegramError:
        await update.message.reply_text(
            "Telegram не подтвердил отправку. Запрос отмечен как неотправленный."
        )
        return
    except PartnerNetworkError as error:
        await update.message.reply_text(str(error))
        return
    await update.message.reply_text(
        f"Запрос №{request['id']} отправлен партнёру."
    )


async def partner_create_command(update, context):
    if not await _require_admin(update):
        return
    raw = update.message.text.partition(" ")[2]
    parts = [part.strip() for part in raw.split("|")]
    if len(parts) < 2 or not parts[0] or not parts[1]:
        await update.message.reply_text(
            "Использование: /partner_create Имя | housing,transfer | Rawai,Kata"
        )
        return
    try:
        partner = create_partner(
            parts[0], parts[1], parts[2] if len(parts) > 2 else None
        )
        token = create_partner_invite(partner["id"])
    except (ValueError, PartnerNetworkError) as error:
        await update.message.reply_text(str(error))
        return
    bot_username = context.bot.username
    link = f"https://t.me/{bot_username}?start=partner_{token}"
    await update.message.reply_text(
        f"Создан партнёр №{partner['id']} — {partner['name']}.\n"
        f"Одноразовая ссылка подключения:\n{link}"
    )


async def partner_status_command(update, context):
    if not await _require_admin(update):
        return
    if len(context.args) != 2 or not context.args[0].isdigit():
        await update.message.reply_text(
            "Использование: /partner_status <partner_id> <active|paused|blocked|candidate>"
        )
        return
    try:
        partner = set_partner_status(int(context.args[0]), context.args[1])
    except (ValueError, PartnerNetworkError) as error:
        await update.message.reply_text(str(error))
        return
    await update.message.reply_text(
        f"Партнёр №{partner['id']}: статус — {format_partner_status_ru(partner['status'])}."
    )


async def partner_update_command(update, context):
    if not await _require_admin(update):
        return
    raw = update.message.text.partition(" ")[2]
    parts = [part.strip() for part in raw.split("|")]
    if len(parts) < 2 or not parts[0].isdigit():
        await update.message.reply_text(
            "Использование: /partner_update <id> | services | areas | notes"
        )
        return
    try:
        partner = update_partner(
            int(parts[0]),
            services=parts[1] or None,
            areas=parts[2] if len(parts) > 2 else None,
            notes=parts[3] if len(parts) > 3 else None,
        )
    except (ValueError, PartnerNetworkError) as error:
        await update.message.reply_text(str(error))
        return
    await update.message.reply_text(f"Партнёр #{partner['id']} обновлён.")


async def partner_autohandoff_command(update, context):
    if not await _require_admin(update):
        return
    if (
        len(context.args) != 2
        or not context.args[0].isdigit()
        or context.args[1].casefold() not in ("on", "off")
    ):
        await update.message.reply_text(
            "Использование: /partner_autohandoff <partner_id> on|off"
        )
        return
    try:
        partner = execute_partner_auto_toggle(
            int(context.args[0]), context.args[1].casefold() == "on"
        )
    except PartnerNetworkError as error:
        await update.message.reply_text(str(error))
        return
    state = "включён" if partner["auto_handoff_enabled"] else "выключен"
    await update.message.reply_text(
        f"Автоотправка для партнёра №{partner['id']} {state}."
    )


async def offers_command(update, context):
    if not await _require_admin(update):
        return
    offers = list_offers()
    if not offers:
        await update.message.reply_text("Предложений партнёров пока нет.")
        return
    await update.message.reply_text(
        "\n\n".join(format_offer_list_item(offer) for offer in offers)
    )


async def case_command(update, context):
    if not await _require_admin(update):
        return
    if len(context.args) != 1 or not context.args[0].isdigit():
        await update.message.reply_text("Использование: /case <case_id>")
        return
    try:
        snapshot = get_admin_case_snapshot(int(context.args[0]))
    except AdminCaseNotFoundError:
        await update.message.reply_text("Кейс не найден.")
        return
    await update.message.reply_text(snapshot)


async def offer_command(update, context):
    if not await _require_admin(update):
        return
    if len(context.args) != 1 or not context.args[0].isdigit():
        await update.message.reply_text("Использование: /offer <offer_id>")
        return
    offer = get_offer(int(context.args[0]))
    if not offer:
        await update.message.reply_text("Предложение не найдено.")
        return
    context_data = get_offer_context(offer["id"])
    partner = get_partner(offer["partner_id"])
    client_message = format_client_offer(offer, context_data)
    await update.message.reply_text(
        format_offer_review_card(
            offer,
            partner["name"],
            get_admin_case_snapshot(offer["case_id"]),
            client_message if offer.get("status") == "sent_to_client" else None,
        )
        + ("" if offer.get("status") == "sent_to_client"
           else "\n\nКлиент увидит:\n" + client_message),
        reply_markup=_admin_keyboard(
            offer_action_buttons(offer["id"], offer["case_id"], offer["partner_id"])
        ),
    )


async def offer_send_command(update, context):
    if not await _require_admin(update):
        return
    if len(context.args) != 1 or not context.args[0].isdigit():
        await update.message.reply_text("Использование: /offer_send <offer_id>")
        return
    try:
        offer = await execute_offer_send(
            int(context.args[0]), context.bot.send_message
        )
    except DuplicateOfferSendError:
        await update.message.reply_text("Предложение уже отправлено клиенту.")
        return
    except OfferTelegramError:
        await update.message.reply_text(
            "Telegram не подтвердил отправку клиенту. Предложение не отмечено как отправленное."
        )
        return
    except OfferHandoffError as error:
        await update.message.reply_text(str(error))
        return
    await update.message.reply_text(
        f"Предложение №{offer['id']} отправлено клиенту."
    )


async def offer_reject_command(update, context):
    if not await _require_admin(update):
        return
    if len(context.args) != 1 or not context.args[0].isdigit():
        await update.message.reply_text("Использование: /offer_reject <offer_id>")
        return
    try:
        offer = execute_offer_reject(int(context.args[0]))
    except OfferHandoffError as error:
        await update.message.reply_text(str(error))
        return
    await update.message.reply_text(f"Предложение №{offer['id']} отклонено.")


async def _show_admin_panel(query):
    await query.edit_message_text(
        "Панель администратора Phuket Life",
        reply_markup=_admin_keyboard(admin_panel_buttons()),
    )


async def _show_admin_cases(query):
    cases = list_admin_cases()
    if not cases:
        await query.edit_message_text(
            "Кейсов пока нет.",
            reply_markup=_admin_keyboard([[('⬅️ В панель', 'admin:panel')]]),
        )
        return
    buttons = [
        [(f"Кейс №{case['id']} — {format_service_category_ru(case['category'])}",
          f"case:view:{case['id']}")]
        for case in cases
    ]
    buttons.append([("⬅️ В панель", "admin:panel")])
    await query.edit_message_text(
        "🗂 Последние кейсы",
        reply_markup=_admin_keyboard(buttons),
    )


async def _show_admin_offers(query):
    offers = list_offers()
    if not offers:
        await query.edit_message_text(
            "Предложений партнёров пока нет.",
            reply_markup=_admin_keyboard([[('⬅️ В панель', 'admin:panel')]]),
        )
        return
    text = "📋 Предложения\n\n" + "\n\n".join(
        format_offer_list_item(offer) for offer in offers
    )
    buttons = [[(f"Открыть предложение №{offer['id']}", f"offer:view:{offer['id']}")]
               for offer in offers]
    buttons.append([("⬅️ В панель", "admin:panel")])
    await query.edit_message_text(text, reply_markup=_admin_keyboard(buttons))


async def _show_offer(query, offer_id):
    offer = get_offer(offer_id)
    if not offer:
        await query.edit_message_text("Предложение не найдено.")
        return
    partner = get_partner(offer["partner_id"])
    context_data = get_offer_context(offer_id)
    client_message = format_client_offer(offer, context_data)
    text = (
        format_offer_review_card(
            offer,
            partner["name"],
            get_admin_case_snapshot(offer["case_id"]),
            client_message if offer.get("status") == "sent_to_client" else None,
        )
        + ("" if offer.get("status") == "sent_to_client"
           else "\n\nКлиент увидит:\n" + client_message)
    )
    await query.edit_message_text(
        text,
        reply_markup=_admin_keyboard(
            offer_action_buttons(offer_id, offer["case_id"], offer["partner_id"])
        ),
    )


async def _show_partner(query, partner_id, case_id=None):
    partner = get_partner(partner_id)
    if not partner:
        await query.edit_message_text("Партнёр не найден.")
        return
    await query.edit_message_text(
        format_partner_card(partner),
        reply_markup=_admin_keyboard(
            partner_action_buttons(
                partner_id, partner.get("auto_handoff_enabled"), case_id
            )
        ),
    )


async def admin_callback_handler(update, context):
    query = update.callback_query
    user_id = update.effective_user.id if update.effective_user else None
    if not can_access_admin_panel(SETTINGS.telegram_admin_user_id, user_id):
        await query.answer("Недостаточно прав.", show_alert=True)
        return
    await query.answer()
    parts = (query.data or "").split(":")
    try:
        if query.data == "admin:panel":
            await _show_admin_panel(query)
        elif query.data == "admin:cases":
            await _show_admin_cases(query)
        elif query.data == "admin:offers":
            await _show_admin_offers(query)
        elif query.data == "admin:partners":
            partners = list_partners()
            buttons = [[(f"Партнёр №{p['id']} — {p['name']}", f"partner:view:{p['id']}")]
                       for p in partners]
            buttons.append([("⬅️ В панель", "admin:panel")])
            await query.edit_message_text(
                "🤝 Партнёры" if partners else "Партнёры пока не добавлены.",
                reply_markup=_admin_keyboard(buttons),
            )
        elif query.data == "admin:requests":
            requests_list = list_partner_requests()
            text = "📩 Запросы партнёрам"
            if requests_list:
                text += "\n\n" + "\n\n".join(
                    f"Запрос №{item['id']} · кейс №{item['case_id']}\n"
                    f"Партнёр: {item['partner_name']}\n"
                    f"Статус: {format_partner_request_status_ru(item['status'])}"
                    for item in requests_list
                )
            else:
                text = "Запросов партнёрам пока нет."
            await query.edit_message_text(
                text,
                reply_markup=_admin_keyboard([[('⬅️ В панель', 'admin:panel')]]),
            )
        elif query.data == "admin:settings":
            mode = {
                "review": "Ручная проверка",
                "hybrid": "Гибридный режим",
            }.get(SETTINGS.partner_handoff_mode, "Неизвестно")
            await query.edit_message_text(
                f"⚙️ Настройки\n\nРежим обработки предложений: {mode}",
                reply_markup=_admin_keyboard([[('⬅️ В панель', 'admin:panel')]]),
            )
        elif parts[:2] == ["case", "view"]:
            case_id = int(parts[2])
            await query.edit_message_text(
                get_admin_case_snapshot(case_id),
                reply_markup=_admin_keyboard([
                    [("🤝 Подобрать партнёров", f"case:partners:{case_id}")],
                    [("⬅️ К кейсам", "admin:cases")],
                ]),
            )
        elif parts[:2] == ["case", "partners"]:
            case_id = int(parts[2])
            case = get_case_for_partner(case_id)
            partners = find_partners_for_case(case)
            buttons = [[
                (f"Партнёр №{partner['id']} — {partner['name']}",
                 f"partner:view:{partner['id']}:{case_id}")
            ] for partner in partners]
            buttons.append([("⬅️ К кейсу", f"case:view:{case_id}")])
            await query.edit_message_text(
                "Подходящие партнёры" if partners
                else "Подходящих активных партнёров не найдено.",
                reply_markup=_admin_keyboard(buttons),
            )
        elif parts[:2] == ["offer", "view"]:
            await _show_offer(query, int(parts[2]))
        elif parts[:2] == ["offer", "send"]:
            offer = await execute_offer_send(int(parts[2]), context.bot.send_message)
            await query.edit_message_text(
                f"Предложение №{offer['id']} отправлено клиенту.",
                reply_markup=_admin_keyboard([[('⬅️ К предложениям', 'admin:offers')]]),
            )
        elif parts[:2] == ["offer", "reject"]:
            offer = execute_offer_reject(int(parts[2]))
            await query.edit_message_text(
                f"Предложение №{offer['id']} отклонено.",
                reply_markup=_admin_keyboard([[('⬅️ К предложениям', 'admin:offers')]]),
            )
        elif parts[:2] == ["partner", "view"]:
            await _show_partner(
                query, int(parts[2]), int(parts[3]) if len(parts) > 3 else None
            )
        elif parts[:2] == ["partner", "auto"]:
            partner = execute_partner_auto_toggle(int(parts[2]), parts[3] == "on")
            await _show_partner(query, partner["id"])
        elif parts[:2] == ["partner", "send"]:
            request = await execute_partner_send(
                int(parts[2]), int(parts[3]), context.bot.send_message
            )
            await query.edit_message_text(
                f"Запрос №{request['id']} отправлен партнёру.",
                reply_markup=_admin_keyboard([[('⬅️ В панель', 'admin:panel')]]),
            )
    except AdminCaseNotFoundError:
        await query.edit_message_text("Кейс не найден.")
    except DuplicateOfferSendError:
        await query.edit_message_text("Предложение уже отправлено клиенту.")
    except DuplicatePartnerRequestError:
        await query.edit_message_text("Активный запрос этому партнёру уже существует.")
    except (OfferTelegramError, PartnerTelegramError):
        await query.edit_message_text("Telegram не подтвердил отправку. Попробуйте ещё раз.")
    except (OfferHandoffError, PartnerNetworkError) as error:
        await query.edit_message_text(f"Не удалось выполнить действие: {error}")


async def partner_reply_handler(update, context):
    message = update.message
    if not message or not message.reply_to_message:
        return
    response_text = message.text or message.caption or ""
    metadata = {
        "telegram_message_id": message.message_id,
        "has_media": bool(message.photo or message.video or message.document),
        "media_type": (
            "photo" if message.photo else
            "video" if message.video else
            "document" if message.document else None
        ),
    }
    request = record_partner_reply(
        update.effective_user.id,
        message.reply_to_message.message_id,
        response_text,
        response_metadata=metadata,
    )
    if not request:
        return
    await message.reply_text("Ответ сохранён в Phuket Life.")
    admin_id = SETTINGS.telegram_admin_user_id
    partner = get_partner(request["partner_id"])
    offer = None
    auto_send_succeeded = False
    auto_send_failed = False
    if request["status"] == "responded":
        try:
            offer = create_offer_from_partner_response(
                request["id"], SETTINGS.partner_handoff_mode
            )
            if offer and offer["handoff_decision"] == "auto_send":
                try:
                    offer = await send_offer_to_client(
                        offer["id"], context.bot.send_message
                    )
                    auto_send_succeeded = True
                except OfferTelegramError:
                    auto_send_failed = True
        except OfferHandoffError:
            offer = None
    if admin_id:
        if auto_send_succeeded:
            notification = (
                "✅ Новый ответ партнёра\n\n"
                f"Предложение №{offer['id']} проверено и отправлено клиенту."
            )
        elif auto_send_failed:
            notification = (
                "❌ Не удалось отправить предложение клиенту\n\n"
                f"Предложение №{offer['id']} не отмечено как отправленное."
            )
        elif offer:
            notification = format_offer_review_card(
                offer,
                partner["name"],
                get_admin_case_snapshot(offer["case_id"]),
            )
        else:
            notification = (
                "🤝 Ответ партнёра\n\n"
                f"Запрос №{request['id']} · кейс №{request['case_id']}\n"
                f"Партнёр: {partner['name']}\n"
                f"Статус: {format_partner_request_status_ru(request['status'])}\n\n"
                f"Ответ:\n{request['partner_response']}"
            )
        try:
            reply_markup = None
            if offer and not auto_send_succeeded:
                reply_markup = _admin_keyboard(
                    offer_action_buttons(
                        offer["id"], offer["case_id"], offer["partner_id"]
                    )
                )
            await context.bot.send_message(
                chat_id=admin_id, text=notification, reply_markup=reply_markup
            )
        except Exception as error:
            print("[PARTNER] Admin notification failed: " + type(error).__name__)
    raise ApplicationHandlerStop


# =========================================================
# CLEAR
# =========================================================

async def clear(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    client_id = get_or_create_client(
        update
    )

    clear_history(client_id)

    await update.message.reply_text(
        "История диалога очищена.\n\n"
        "Сохранённые данные активного кейса "
        "при этом не удалены.\n\n"
        "Можем продолжить работу."
    )


# =========================================================
# MESSAGE
# =========================================================

def is_reset_request(text):
    if not text:
        return False

    text = text.lower().strip()

    reset_phrases = [
        "давай все заново",
        "давай всё заново",
        "начнем сначала",
        "начнём сначала",
        "начать сначала",
        "начнем заново",
        "начнём заново",
        "начать заново",
        "все заново",
        "всё заново",
        "сбрось все",
        "сбрось всё",
        "забудь все",
        "забудь всё",
        "новый запрос",
        "начнем новый запрос",
        "начнём новый запрос",
    ]

    return any(
        phrase in text
        for phrase in reset_phrases
    )
async def handle_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    correlation_id = getattr(update, "update_id", None)
    if correlation_id is None:
        correlation_id = getattr(update.message, "message_id", None)

    client_id = get_or_create_client(
        update
    )

    user_message = update.message.text
    # =====================================================
    # RESET ACTIVE CASE
    # =====================================================

    if is_reset_request(user_message):

        close_active_case(
            client_id
        )

        clear_history(
            client_id
        )

        await update.message.reply_text(
            "Хорошо 👍 Начинаем с чистого листа.\n\n"
            "Предыдущий запрос закрыт.\n"
            "Расскажите, что вам нужно."
        )

        return

    # -----------------------------------------------------
    # Сохраняем сообщение клиента
    # -----------------------------------------------------

    save_message(
        client_id,
        "user",
        user_message
    )

    greeting = pure_greeting_response(user_message)
    if greeting:
        save_message(client_id, "assistant", greeting)
        await update.message.reply_text(greeting)
        return

    # -----------------------------------------------------
    # Получаем историю
    # -----------------------------------------------------

    history = get_history(
        client_id
    )

    # -----------------------------------------------------
    # Получаем существующий кейс
    # -----------------------------------------------------

    existing_case = (
        get_client_active_case(
            client_id
        )
    )

    response_plan = plan_response(
        user_message,
        existing_case,
        conversation_history=history[:-1],
    )
    answer_source = select_answer_source(
        user_message, response_plan, existing_case
    )
    reference_intent = response_plan.trusted_facts.get("reference_intent")
    use_case_context = not (
        reference_intent == "district_comparison"
        and existing_case
        and existing_case.get("category") != "housing"
    )
    case_context = (
        format_case_for_ai(existing_case)
        if existing_case and use_case_context
        else None
    )
    print(
        "[CONVERSATION_POLICY] "
        f"mode={response_plan.mode} "
        f"standard={response_plan.standard_id} "
        f"version={response_plan.standard_version} "
        f"decision={response_plan.next_action} "
        f"source={answer_source}"
    )

    if answer_source == TRUSTED_REFERENCE:
        answer = format_reference_answer(response_plan.trusted_facts)
        save_message(client_id, "assistant", answer)
        await update.message.reply_text(answer)
        return

    if (
        answer_source == PROVIDER_SEARCH
        and reference_intent == "district_operational_question"
    ):
        answer = format_current_source_requirement(user_message)
        save_message(client_id, "assistant", answer)
        await update.message.reply_text(answer)
        return

    routing = route_with_conversation_policy(
        user_message, existing_case, response_plan
    )

    continuity = response_plan.case_continuity
    if continuity == CLARIFY_CONTINUITY:
        question = build_continuity_question(existing_case, user_message)
        save_message(client_id, "assistant", question)
        await update.message.reply_text(question)
        return
    routing = apply_case_continuity(routing, continuity, existing_case)

    if should_use_conversation_flow(routing["intent"], response_plan):
        try:
            answer = await run_blocking(
                ask_gigachat,
                history,
                case_context,
                response_plan,
                correlation_id,
            )
            answer = guard_client_voice(
                guard_policy_answer(answer, response_plan), user_message
            )
            save_message(client_id, "assistant", answer)
            await update.message.reply_text(answer)
        except Exception as e:
            print(f"Ошибка GigaChat: {e}")
            await update.message.reply_text(
                "Извините, произошла техническая "
                "ошибка. Попробуйте написать ещё раз."
            )
        return

    if existing_case:

        print(
            "\n===== EXISTING CASE ====="
        )

        print(
            format_case_for_ai(
                existing_case
            )
        )

        print(
            "=========================\n"
        )

    # -----------------------------------------------------
    # Анализируем кейс
    # -----------------------------------------------------

    try:

        if routing["intent"] == SEARCH_REQUEST and existing_case:
            case_analysis = {
                "category": existing_case["category"],
                "title": existing_case["title"],
                "data": existing_case["data"],
                "missing_data": existing_case["missing_data"],
            }
        else:
            case_for_analysis = (
                None
                if routing["intent"] == NEW_CASE
                else existing_case
            )
            case_analysis = await run_blocking(
                analyze_case,
                history,
                case_for_analysis,
                correlation_id,
            )

        print(
            "\n===== CASE ANALYSIS ====="
        )

        print(
            case_analysis
        )

        print(
            "=========================\n"
        )

        if not isinstance(
            case_analysis,
            dict
        ):

            raise ValueError(
                "Анализ кейса должен вернуть словарь"
            )

        persisted_case = persist_case_analysis(
            client_id,
            case_analysis,
            routing,
            existing_case,
        )
        case_id = persisted_case["id"]
        category = persisted_case["category"]
        case_data = persisted_case["data"]
        missing_data = persisted_case["missing_data"]
        case_status = persisted_case["status"]

        print(
            f"Кейс сохранён в базе: {case_id}"
        )

        print(
            f"Статус кейса: {case_status}"
        )

        # -------------------------------------------------
        # Получаем обновлённый кейс
        # -------------------------------------------------

        active_case = (
            get_client_active_case(
                client_id
            )
        )

        if active_case:

            case_context = (
                format_case_for_ai(
                    active_case
                )
            )

        else:

            case_context = ""

        if category == "housing" and missing_data:
            question = build_housing_missing_question(missing_data)
            save_message(client_id, "assistant", question)
            await update.message.reply_text(question)
            return

        # -------------------------------------------------
        # ЕСЛИ КЕЙС ГОТОВ — ПОДТВЕРЖДАЕМ
        # -------------------------------------------------

        if (
            should_start_search(
                routing["intent"],
                category,
                case_status,
                current_case_relevant=response_plan.current_case_relevant,
                continuity_resolved=(continuity != CLARIFY_CONTINUITY),
            )
        ):

            repeat_search = routing["intent"] == SEARCH_REQUEST
            requested_result_limit = (
                routing.get("requested_result_limit") or 5
            )
            confirmation = build_pre_search_message(
                case_data,
                repeat_search,
                build_search_confirmation,
            )

            if confirmation:
                print("\n===== SEARCH REQUEST =====")
                print(confirmation)
                print("==========================\n")
                save_message(client_id, "assistant", confirmation)
                await update.message.reply_text(confirmation)

            set_case_status(case_id, "searching")

            try:
                (
                    search_result,
                    case_data,
                    search_status,
                ) = await execute_housing_search(
                    case_data,
                    repeat_search,
                    requested_result_limit=requested_result_limit,
                )
            except Exception as e:
                print(f"Ошибка поиска жилья: {e}")
                set_case_status(case_id, "ready_for_search")
                search_message = PROVIDER_ERROR_MESSAGE
                save_message(client_id, "assistant", search_message)
                await update.message.reply_text(search_message)
                return

            if search_result.get("status") == SEARCH_PROVIDER_ERROR:
                set_case_status(case_id, "ready_for_search")
                search_message = PROVIDER_ERROR_MESSAGE
                save_message(client_id, "assistant", search_message)
                await update.message.reply_text(search_message)
                return

            results = search_result.get(
                "results",
                []
            )

            shown_results = results[:requested_result_limit]
            update_case(
                case_id,
                case_data,
                missing_data,
                search_status,
            )

            if results:
                search_message = build_results_message(
                    shown_results,
                    repeat_search=repeat_search,
                )

            else:

                search_message = get_no_results_message(repeat_search)

            save_message(
                client_id,
                "assistant",
                search_message
            )

            await update.message.reply_text(
                search_message
            )

            return

    except GigaChatGenerationError:

        await update.message.reply_text(
            GENERATION_DELAY_MESSAGE
        )
        return

    except Exception as e:

        print(
            f"Ошибка анализа кейса: {e}"
        )

        case_context = ""

    # -----------------------------------------------------
    # Обычный ответ GIGACHAT
    # -----------------------------------------------------

    try:
        policy_case = active_case if "active_case" in locals() else existing_case
        fallback_plan = plan_response(user_message, policy_case)
        answer = await run_blocking(
            ask_gigachat,
            history,
            case_context,
            fallback_plan,
            correlation_id,
        )
        answer = guard_client_voice(
            guard_policy_answer(answer, fallback_plan), user_message
        )

        save_message(
            client_id,
            "assistant",
            answer
        )

        await update.message.reply_text(
            answer
        )

    except Exception as e:

        print(
            f"Ошибка GigaChat: {e}"
        )

        await update.message.reply_text(
            "Извините, произошла техническая "
            "ошибка. Попробуйте написать ещё раз."
        )


# =========================================================
# MAIN
# =========================================================

def main():

    init_db()

    app = (
        Application
        .builder()
        .token(
            SETTINGS.telegram_bot_token
        )
        .build()
    )

    app.add_handler(
        CommandHandler(
            "start",
            start
        )
    )

    app.add_handler(
        CommandHandler(
            "clear",
            clear
        )
    )

    for command, callback in (
        ("admin", admin_command),
        ("partners", partners_command),
        ("partner_requests", partner_requests_command),
        ("case_partners", case_partners_command),
        ("send_partner", send_partner_command),
        ("partner_create", partner_create_command),
        ("partner_status", partner_status_command),
        ("partner_update", partner_update_command),
        ("partner_autohandoff", partner_autohandoff_command),
        ("offers", offers_command),
        ("case", case_command),
        ("offer", offer_command),
        ("offer_send", offer_send_command),
        ("offer_reject", offer_reject_command),
    ):
        app.add_handler(CommandHandler(command, callback))

    app.add_handler(
        CallbackQueryHandler(
            admin_callback_handler,
            pattern=r"^(admin|case|offer|partner):",
        )
    )

    app.add_handler(
        MessageHandler(
            filters.REPLY & ~filters.COMMAND,
            partner_reply_handler,
        ),
        group=-1,
    )

    app.add_handler(
        MessageHandler(
            filters.TEXT
            & ~filters.COMMAND,
            handle_message
        )
    )

    print(
        "Phuket Life AI Concierge запущен!"
    )

    app.run_polling()


# =========================================================
# RUN
# =========================================================

if __name__ == "__main__":

    main()
