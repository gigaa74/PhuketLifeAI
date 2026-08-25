"""Client persistence and GigaChat integration for Phuket Life."""

import json
import time
import uuid

import requests
from telegram import Update

from case_engine import format_case_for_ai
from config import load_settings
from conversation_prompts import build_conversation_policy_prompt
from database import (
    get_connection,
    get_or_create_client as db_get_or_create_client,
)
from gigachat_provider import generate_text
from reliability import retry_call
from token_cache import AccessTokenCache


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

    def request_token():
        response = requests.post(
            url,
            headers=headers,
            data=data,
            timeout=30,
            verify=SETTINGS.gigachat_tls_verify,
        )
        response.raise_for_status()
        return response

    response = retry_call(
        request_token,
        attempts=SETTINGS.external_retry_attempts,
        base_delay_seconds=SETTINGS.external_retry_base_delay_seconds,
    )

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
        retry_attempts=SETTINGS.external_retry_attempts,
        retry_base_delay_seconds=SETTINGS.external_retry_base_delay_seconds,
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
        retry_attempts=SETTINGS.external_retry_attempts,
        retry_base_delay_seconds=SETTINGS.external_retry_base_delay_seconds,
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
