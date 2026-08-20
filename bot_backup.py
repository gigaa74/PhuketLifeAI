from dotenv import load_dotenv

load_dotenv()
import os
import uuid
import requests
import sqlite3
from case_engine import (
    get_or_create_case,
    update_case,
    get_client_active_case,
    format_case_for_ai,
)

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)


# =========================
# НАСТРОЙКИ
# =========================

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
GIGACHAT_API_KEY = os.environ["GIGACHAT_API_KEY"]

MODEL = "GigaChat-2-Max"

DB_NAME = "phuketlife.db"

MAX_HISTORY = 20


SYSTEM_PROMPT = """
Ты — AI-консьерж проекта Phuket Life.

Phuket Life помогает туристам и экспатам в Таиланде
решать бытовые, организационные и туристические вопросы.

Твоя задача:
1. Понять реальную потребность клиента.
2. Запоминать информацию, которую клиент уже сообщил.
3. Не спрашивать повторно то, что уже известно.
4. Если информации недостаточно — задать необходимые уточняющие вопросы.
5. Общаться профессионально, дружелюбно и естественно.
6. Отвечать на русском языке.

КРИТИЧЕСКОЕ ПРАВИЛО ДОСТОВЕРНОСТИ:

Ты НИКОГДА не должен выдумывать или предполагать реальные
объекты, цены, адреса, наличие, контакты, названия компаний,
отели, кондоминиумы, квартиры, дома, рестораны, услуги,
партнёров или результаты проверки.

Особенно запрещено придумывать конкретные варианты жилья.

Если у тебя нет подтверждённых данных из предоставленного
источника, НЕ выдавай конкретный объект как существующее
или доступное предложение.

Запрещено писать фразы:
- "я нашёл"
- "доступно"
- "в наличии"
- "проверено"
- "проверено нашими партнёрами"
- "партнёр подтвердил"
- "можно забронировать"

если соответствующая информация реально не была получена
из системы Phuket Life или подтверждённого источника.

Если клиент просит найти жильё, а актуальных данных ещё нет,
скажи, что для поиска необходимо получить актуальные предложения.

Можно обсуждать районы, критерии выбора и общие рекомендации,
но нельзя превращать общую информацию в конкретное предложение.

Например, допустимо:
"Патонг обычно подходит тем, кому важна развитая инфраструктура."

Недопустимо:
"Я нашёл студию в Patong City View Complex за 7500 бат."

Если клиент спрашивает о конкретном объекте, которого нет
в предоставленных данных, честно скажи, что не можешь подтвердить
его наличие или характеристики.

НИКОГДА не утверждай, что информация была проверена партнёрами,
если система действительно не получила такое подтверждение.

Ты находишься на этапе тестирования сервиса.

Представляйся AI-консьержем Phuket Life.
Не говори клиенту, что ты языковая модель.


# =========================
# DATABASE
# =========================

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
        "SELECT id FROM clients WHERE telegram_id = ?",
        (telegram_id,)
    )

    client = cursor.fetchone()

    if client:
        client_id = client[0]

    else:

        cursor.execute(
            """
            INSERT INTO clients
            (telegram_id, username, first_name, last_name)
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
        (client_id, role, content)
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


# =========================
# GIGACHAT TOKEN
# =========================

def get_access_token():

    url = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"

    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json",
        "RqUID": str(uuid.uuid4()),
        "Authorization": f"Basic {GIGACHAT_API_KEY}",
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


# =========================
# GIGACHAT
# =========================

def ask_gigachat(history):

    access_token = get_access_token()

    url = "https://api.giga.chat/v1/chat/completions"

    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Authorization": f"Bearer {access_token}",
    }

    messages = [
        {
            "role": "system",
            "content": SYSTEM_PROMPT
        }
    ]

    messages.extend(history)

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

    response.raise_for_status()

    result = response.json()

    return result["choices"][0]["message"]["content"]
def analyze_case(history):

    access_token = get_access_token()

    url = "https://api.giga.chat/v1/chat/completions"

    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Authorization": f"Bearer {access_token}",
    }

    analysis_prompt = """
Ты анализируешь запрос клиента Phuket Life.

Твоя задача — определить активный запрос клиента и вернуть
ТОЛЬКО JSON без пояснений.

Формат:

{
  "category": "housing",
  "title": "Поиск жилья на Пхукете",
  "data": {
    "arrival_date": "",
    "departure_date": "",
    "budget": "",
    "location": "",
    "people": "",
    "pet": "",
    "housing_type": ""
  },
  "missing_data": []
}

Правила:

1. Не выдумывай данные.
2. Если информации нет — оставляй поле пустым.
3. В missing_data указывай только действительно важные
   данные, которые необходимо узнать для решения запроса.
4. Если запрос относится к жилью — category = "housing".
5. Если запрос относится к трансферу — category = "transfer".
6. Если запрос относится к животным — category = "pet".
7. Если категория не определена — category = "other".
8. Учитывай всю историю диалога, а не только последнее сообщение.
"""

    messages = [
        {
            "role": "system",
            "content": analysis_prompt
        }
    ]

    messages.extend(history)

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

    response.raise_for_status()

    result = response.json()

    content = result["choices"][0]["message"]["content"]

    return content


# =========================
# START
# =========================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    client_id = get_or_create_client(update)

    await update.message.reply_text(
        "Здравствуйте! 👋\n\n"
        "Я AI-консьерж Phuket Life.\n\n"
        "Помогу организовать поездку и решить "
        "различные вопросы, связанные с пребыванием "
        "в Таиланде.\n\n"
        "Расскажите, что Вам необходимо."
    )


# =========================
# CLEAR
# =========================

async def clear(update: Update, context: ContextTypes.DEFAULT_TYPE):

    client_id = get_or_create_client(update)

    clear_history(client_id)

    await update.message.reply_text(
        "Контекст нашего диалога очищен.\n\n"
        "Можем начать новый запрос."
    )


# =========================
# MESSAGE
# =========================

async def handle_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    client_id = get_or_create_client(update)

    user_message = update.message.text

    # Сохраняем сообщение клиента
    save_message(
        client_id,
        "user",
        user_message
    )

    # Получаем историю из базы
    history = get_history(client_id)

    # =========================
    # АНАЛИЗ КЕЙСА
    # =========================

    try:
        case_analysis = analyze_case(history)

        print("\n===== CASE ANALYSIS =====")
        print(case_analysis)
        print("=========================\n")

    except Exception as e:
        print(f"Ошибка анализа кейса: {e}")
    try:

        answer = ask_gigachat(history)

        # Сохраняем ответ AI
        save_message(
            client_id,
            "assistant",
            answer
        )

        await update.message.reply_text(answer)

    except Exception as e:

        print(f"Ошибка GigaChat: {e}")

        await update.message.reply_text(
            "Извините, произошла техническая ошибка. "
            "Попробуйте написать ещё раз."
        )


# =========================
# MAIN
# =========================

def main():

    app = Application.builder().token(
        TELEGRAM_BOT_TOKEN
    ).build()

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
            filters.TEXT & ~filters.COMMAND,
            handle_message
        )
    )

    print("Phuket Life AI Concierge запущен!")

    app.run_polling()


if __name__ == "__main__":
    main()