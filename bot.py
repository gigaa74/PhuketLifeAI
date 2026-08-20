from dotenv import load_dotenv

load_dotenv()

import os
import uuid
import json
import requests
import sqlite3

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

from case_engine import (
    get_or_create_case,
    update_case,
    get_client_active_case,
    format_case_for_ai,
    close_active_case,
)
from search_engine import search_housing


# =========================================================
# НАСТРОЙКИ
# =========================================================

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
GIGACHAT_API_KEY = os.environ["GIGACHAT_API_KEY"]

MODEL = "GigaChat-2-Max"

DB_NAME = "phuketlife.db"

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
"""


# =========================================================
# DATABASE
# =========================================================

def get_connection():
    return sqlite3.connect(DB_NAME)


def get_or_create_client(update: Update):

    user = update.effective_user

    telegram_id = user.id
    username = user.username
    first_name = user.first_name
    last_name = user.last_name

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT id
        FROM clients
        WHERE telegram_id = ?
        """,
        (telegram_id,)
    )

    client = cursor.fetchone()

    if client:

        client_id = client[0]

    else:

        cursor.execute(
            """
            INSERT INTO clients
            (
                telegram_id,
                username,
                first_name,
                last_name
            )
            VALUES (?, ?, ?, ?)
            """,
            (
                telegram_id,
                username,
                first_name,
                last_name
            )
        )

        client_id = cursor.lastrowid

    connection.commit()
    connection.close()

    return client_id


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

def get_access_token():

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
            f"Basic {GIGACHAT_API_KEY}"
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
        verify=False
    )

    response.raise_for_status()

    return response.json()["access_token"]


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
    case_context=None
):

    access_token = get_access_token()

    url = (
        "https://api.giga.chat/"
        "v1/chat/completions"
    )

    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Authorization": (
            f"Bearer {access_token}"
        ),
    }

    system_content = SYSTEM_PROMPT

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

    data = {
        "model": MODEL,
        "messages": messages,
        "temperature": 0.7,
    }

    response = requests.post(
        url,
        headers=headers,
        json=data,
        timeout=60,
        verify=False
    )

    if not response.ok:

        print(
            "\n===== GIGACHAT ERROR ====="
        )

        print(
            "Status:",
            response.status_code
        )

        print(
            "Response:",
            response.text
        )

        print(
            "==========================\n"
        )

    response.raise_for_status()

    result = response.json()

    return (
        result["choices"][0]
        ["message"]["content"]
    )


# =========================================================
# АНАЛИЗ КЕЙСА
# =========================================================

def analyze_case(
    history,
    existing_case=None
):

    access_token = get_access_token()

    url = (
        "https://api.giga.chat/"
        "v1/chat/completions"
    )

    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Authorization": (
            f"Bearer {access_token}"
        ),
    }

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

    data = {
        "model": MODEL,
        "messages": messages,
        "temperature": 0,
    }

    response = requests.post(
        url,
        headers=headers,
        json=data,
        timeout=60,
        verify=False
    )

    if not response.ok:

        print(
            "\n===== CASE ANALYSIS ERROR ====="
        )

        print(
            "Status:",
            response.status_code
        )

        print(
            "Response:",
            response.text
        )

        print(
            "================================\n"
        )

    response.raise_for_status()

    result = response.json()

    content = (
        result["choices"][0]
        ["message"]["content"]
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

    get_or_create_client(update)

    await update.message.reply_text(
        "Здравствуйте! 👋\n\n"
        "Я AI-консьерж Phuket Life.\n\n"
        "Помогу организовать поездку и решить "
        "различные вопросы, связанные с "
        "пребыванием в Таиланде.\n\n"
        "Расскажите, что вам необходимо."
    )


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

        case_analysis = analyze_case(
            history,
            existing_case
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

        category = case_analysis.get(
            "category",
            "other"
        )

        title = case_analysis.get(
            "title",
            "Новый запрос"
        )

        new_case_data = case_analysis.get(
            "data",
            {}
        )

        new_missing_data = (
            case_analysis.get(
                "missing_data",
                []
            )
        )

        if not isinstance(
            new_case_data,
            dict
        ):

            new_case_data = {}

        if not isinstance(
            new_missing_data,
            list
        ):

            new_missing_data = []

        # -------------------------------------------------
        # Объединяем старые и новые данные
        # -------------------------------------------------

        case_data = {}

        if existing_case:

            old_data = existing_case.get(
                "data",
                {}
            )

            if isinstance(
                old_data,
                dict
            ):

                case_data.update(
                    old_data
                )

        for key, value in (
            new_case_data.items()
        ):

            if value not in (
                None,
                "",
                [],
                {}
            ):

                case_data[key] = value

        # -------------------------------------------------
        # Определяем обязательные поля
        # -------------------------------------------------

        if category == "housing":

            required_fields = [
                "arrival_date",
                "departure_date",
                "people",
                "budget"
            ]

            missing_data = []

            for field in required_fields:

                value = case_data.get(
                    field
                )

                if value in (
                    None,
                    "",
                    [],
                    {}
                ):

                    missing_data.append(
                        field
                    )

        else:

            missing_data = (
                new_missing_data
            )

        # -------------------------------------------------
        # Получаем или создаём кейс
        # -------------------------------------------------

        case_id = get_or_create_case(
            client_id,
            category,
            title
        )

        # -------------------------------------------------
        # Статус
        # -------------------------------------------------

        if missing_data:

            case_status = "active"

        else:

            case_status = (
                "ready_for_search"
            )

        # -------------------------------------------------
        # Сохраняем кейс
        # -------------------------------------------------

        update_case(
            case_id,
            case_data,
            missing_data,
            case_status
        )

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

        # -------------------------------------------------
        # ЕСЛИ КЕЙС ГОТОВ — ПОДТВЕРЖДАЕМ
        # -------------------------------------------------

        if (
            category == "housing"
            and case_status == "ready_for_search"
        ):

            confirmation = (
                build_search_confirmation(
                    case_data
                )
            )

            print(
                "\n===== SEARCH REQUEST ====="
            )

            print(
                confirmation
            )

            print(
                "==========================\n"
            )

            save_message(
                client_id,
                "assistant",
                confirmation
            )

            await update.message.reply_text(
                confirmation
            )

            search_result = search_housing(
                {
                    "category": category,
                    "data": case_data,
                }
            )

            results = search_result.get(
                "results",
                []
            )

            if results:

                lines = [
                    "Нашёл первые подходящие варианты:\n"
                ]

                for index, item in enumerate(
                    results[:5],
                    start=1
                ):
                    name = item.get(
                        "name",
                        "Вариант жилья"
                    )

                    url = item.get(
                        "url",
                        ""
                    )

                    description = item.get(
                        "description",
                        ""
                    )

                    if len(description) > 250:
                        description = (
                            description[:250]
                            + "..."
                        )

                    lines.append(
                        f"{index}. {name}\n"
                        f"{description}\n"
                        f"{url}\n"
                    )

                search_message = "\n".join(
                    lines
                )

            else:

                search_message = (
                    "По вашему запросу пока не удалось "
                    "найти подходящие варианты."
                )

            save_message(
                client_id,
                "assistant",
                search_message
            )

            await update.message.reply_text(
                search_message
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

        answer = ask_gigachat(
            history,
            case_context
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

    app = (
        Application
        .builder()
        .token(
            TELEGRAM_BOT_TOKEN
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