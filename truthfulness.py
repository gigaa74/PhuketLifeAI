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
