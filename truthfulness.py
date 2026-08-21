import re


TRUTHFUL_FALLBACK = (
    "Я могу дать общую рекомендацию или запустить поиск вариантов жилья "
    "в поисковых источниках. Конкретные объекты, цены и ссылки показываю "
    "только из результатов нашей поисковой системы."
)

INITIAL_NO_RESULTS_MESSAGE = (
    "По текущим параметрам пока не нашли подходящих вариантов в "
    "доступных нам источниках.\n\n"
    "Можем немного изменить район, бюджет или тип жилья и попробовать "
    "ещё раз."
)

REPEAT_NO_RESULTS_MESSAGE = (
    "Похоже, по текущим параметрам мы уже дошли до конца доступных "
    "результатов 🙂 Новых вариантов сейчас не нашли.\n\n"
    "Если хотите, можем немного изменить условия поиска — район, бюджет "
    "или тип жилья — и посмотреть ещё."
)

PROVIDER_ERROR_MESSAGE = (
    "Сейчас не удалось получить варианты из поисковых источников. "
    "Можно повторить поиск или изменить его параметры."
)


def get_no_results_message(repeat_search=False):
    return (
        REPEAT_NO_RESULTS_MESSAGE
        if repeat_search
        else INITIAL_NO_RESULTS_MESSAGE
    )

BACKGROUND_CLAIMS = (
    "сейчас ищу",
    "скоро вернусь",
    "как только появятся",
    "сообщу, когда",
    "активно ищу",
    "занимаюсь поиском",
    "ожидайте",
    "ждите",
    "мы скоро найдём",
    "мы скоро найдем",
    "найдём и вернёмся",
    "найдем и вернемся",
    "позже всё подберём",
    "позже все подберем",
    "сейчас займёмся",
    "сейчас займемся",
)

GENERATION_DELAY_MESSAGE = (
    "Сейчас возникла временная техническая задержка. "
    "Пожалуйста, повторите сообщение через несколько секунд."
)

ACTION_CLAIMS = (
    "я нашёл",
    "я нашел",
    "нашёл для вас",
    "нашел для вас",
    "доступно для бронирования",
    "можно забронировать",
    "партнёр подтвердил",
    "партнер подтвердил",
    "я забронировал",
    "я отправил партнёру",
    "я отправил партнеру",
    "подтверждённ",
    "подтвержденн",
    "запрос отправлен партн",
    "партнёр ответил",
    "партнер ответил",
    "партнёр предложил",
    "партнер предложил",
    "мы отправили клиент",
    "отправили клиенту",
    "вариант проверен",
    "вариант подтвержд",
)


def conversational_answer_is_safe(text):
    original = str(text or "")
    normalized = original.lower()
    if not normalized.strip():
        return False
    if re.search(r"https?://|www\.", normalized):
        return False
    if any(claim in normalized for claim in BACKGROUND_CLAIMS + ACTION_CLAIMS):
        return False
    if "%" in normalized:
        return False
    if re.search(
        r"\b(?i:apartment|condo|villa|hotel|апартаменты|квартира|вилла|отель)"
        r"\s+[A-ZА-Я][\w-]+",
        original,
    ):
        return False

    housing_words = (
        "квартир",
        "апартамент",
        "вилл",
        "кондо",
        "отел",
        "жиль",
    )
    precise_value = re.search(
        r"\d[\d\s.,]*(?:%|₽|руб|฿|бат|thb|usd|\$)",
        normalized,
    )
    if precise_value and any(word in normalized for word in housing_words):
        return False
    return True


def guard_conversational_answer(text):
    return text if conversational_answer_is_safe(text) else TRUTHFUL_FALLBACK


FORMAL_GREETING = "Здравствуйте! Рады Вас видеть 🙂 Чем можем помочь?"
FORMAL_VOICE_FALLBACK = (
    "Подскажите, пожалуйста, чем мы можем помочь?"
)


def guard_client_voice(text, user_message=None):
    original = str(text or "").strip()
    normalized = original.casefold()
    informal = re.search(
        r"\b(?:ты|тебя|тебе|тобой|твой|твоя|твои)\b", normalized
    )
    singular_team_voice = (
        re.search(r"\b(?:я|рад|рада|помогу|подскажу|рекомендую)\b", normalized)
        or re.search(
            r"\bготов(?:а)?\s+(?:вам\s+)?"
            r"(?:помочь|подсказать|ответить|рекомендовать)\b",
            normalized,
        )
        or re.search(
            r"\bмогу\s+(?:вам\s+)?"
            r"(?:помочь|подсказать|подобрать|рекомендовать)\b",
            normalized,
        )
    )
    if informal or singular_team_voice:
        greeting = str(user_message or "").casefold().strip(" .!?,")
        if greeting in {"привет", "здравствуйте", "добрый день", "добрый вечер"}:
            return FORMAL_GREETING
        return FORMAL_VOICE_FALLBACK
    replacements = {
        r"\bвы\b": "Вы", r"\bвас\b": "Вас", r"\bвам\b": "Вам",
        r"\bваш\b": "Ваш", r"\bваша\b": "Ваша", r"\bваши\b": "Ваши",
    }
    result = original
    for pattern, replacement in replacements.items():
        result = re.sub(pattern, replacement, result, flags=re.IGNORECASE)
    return result
