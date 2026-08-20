import re


CONVERSATION = "conversation"
CASE_UPDATE = "case_update"
SEARCH_REQUEST = "search_request"
NEW_CASE = "new_case"


CONVERSATION_PHRASES = {
    "привет",
    "здравствуйте",
    "добрый день",
    "добрый вечер",
    "доброе утро",
    "спасибо",
    "спасибо большое",
    "благодарю",
    "как дела",
    "как дела?",
}

SEARCH_PHRASES = (
    "продолжай поиск",
    "продолжить поиск",
    "поищи ещё",
    "поищи еще",
    "ещё варианты",
    "еще варианты",
    "давай искать",
    "начинай поиск",
    "начать поиск",
    "покажи другие",
)

SHORT_SEARCH_PHRASES = {
    "а ещё",
    "а еще",
    "ещё",
    "еще",
    "другие",
    "есть ещё",
    "есть еще",
}

NATURAL_REPEAT_PATTERNS = (
    r"^(?:мож(?:ешь|ете)\s+)?(?:показать|покажи|найти|найди)\s+"
    r"(?:ещ[её]|другие)(?:\s+вариант\w*)?$",
    r"^есть\s+(?:что-нибудь|что\s+нибудь)\s+ещ[её]"
    r"(?:\s+вариант\w*)?$",
    r"^давай\s+ещ[её](?:\s+вариант\w*)?$",
)

QUANTIFIED_SEARCH_MARKERS = (
    "скинь",
    "покажи",
    "дай",
    "найди",
)

NEW_CATEGORY_MARKERS = {
    "transfer": (
        "трансфер",
        "из аэропорта",
        "в аэропорт",
        "такси",
    ),
    "transport": (
        "арендовать машину",
        "аренда машины",
        "аренда авто",
        "арендовать авто",
        "скутер",
        "мотоцикл",
    ),
}

HOUSING_INTENT_MARKERS = (
    "ищу жиль",
    "найди жиль",
    "найдите жиль",
    "нужно жиль",
    "нужна квартир",
    "нужен отел",
    "подбери отел",
    "подберите отел",
    "снять апартамент",
    "снять квартир",
    "снять жиль",
    "хотим снять",
    "арендовать квартир",
    "арендовать жиль",
)

HOUSING_UPDATE_MARKERS = (
    "бюджет",
    "руб",
    "бат",
    "доллар",
    "человек",
    "гост",
    "нас будет",
    "нас двое",
    "нас трое",
    "район",
    "карон",
    "ката",
    "патонг",
    "раваи",
    "rawai",
    "заезд",
    "выезд",
    "сентябр",
    "октябр",
    "ноябр",
    "декабр",
    "январ",
    "феврал",
    "март",
    "апрел",
    "мая",
    "июн",
    "июл",
    "август",
    "с собак",
    "с кош",
    "без живот",
    "апартамент",
    "квартир",
    "вилл",
    "дом",
    "студи",
    "жиль",
    "отел",
    "снять",
    "подбери",
    "найди",
)


def normalize_message(text):
    normalized = str(text or "").casefold().strip()
    normalized = " ".join(normalized.split())
    normalized = re.sub(r"\s+([?!.,;:])", r"\1", normalized)
    return normalized.rstrip(".!?,;:").rstrip()


def detect_new_category(text, existing_category=None):
    normalized = normalize_message(text)
    for category, markers in NEW_CATEGORY_MARKERS.items():
        if category != existing_category and any(
            marker in normalized for marker in markers
        ):
            return category
    return None


def is_repeat_search_request(normalized):
    return (
        normalized in SHORT_SEARCH_PHRASES
        or any(phrase in normalized for phrase in SEARCH_PHRASES)
        or any(
            re.search(pattern, normalized)
            for pattern in NATURAL_REPEAT_PATTERNS
        )
    )


def extract_requested_result_limit(normalized, max_limit=10):
    marker_pattern = "|".join(
        re.escape(marker) for marker in QUANTIFIED_SEARCH_MARKERS
    )
    match = re.search(
        rf"\b(?:{marker_pattern})\b"
        r"(?:\s+(?:сразу|ещ[её]))?\s+(\d{1,2})\b",
        normalized,
    )
    if not match:
        return None
    return max(1, min(int(match.group(1)), max_limit))


def route_message(text, existing_case=None):
    """Return an explainable routing decision for the current MVP."""
    normalized = normalize_message(text)
    if normalized in CONVERSATION_PHRASES:
        return {"intent": CONVERSATION, "category": None}

    existing_category = existing_case.get("category") if existing_case else None
    new_category = detect_new_category(normalized, existing_category)
    if new_category:
        return {"intent": NEW_CASE, "category": new_category}

    has_search_context = bool(
        existing_case
        and existing_case.get("category") == "housing"
        and existing_case.get("status")
        in ("ready_for_search", "results_presented")
    )
    requested_result_limit = extract_requested_result_limit(normalized)
    repeat_search = (
        is_repeat_search_request(normalized)
        or requested_result_limit is not None
    )
    if has_search_context and repeat_search:
        return {
            "intent": SEARCH_REQUEST,
            "category": None,
            "requested_result_limit": requested_result_limit,
        }

    if repeat_search:
        return {"intent": CONVERSATION, "category": None}

    housing_intent = any(
        marker in normalized for marker in HOUSING_INTENT_MARKERS
    )
    if housing_intent and existing_category != "housing":
        return {"intent": NEW_CASE, "category": "housing"}

    if not existing_case:
        return {"intent": NEW_CASE, "category": None}

    if existing_category == "housing":
        if any(marker in normalized for marker in HOUSING_UPDATE_MARKERS):
            return {"intent": CASE_UPDATE, "category": "housing"}
        if existing_case.get("status") == "active" and re.search(r"\d", normalized):
            return {"intent": CASE_UPDATE, "category": "housing"}

    return {"intent": CONVERSATION, "category": None}


def should_start_search(intent, category, status):
    if category != "housing" or status != "ready_for_search":
        return False
    return intent in (CASE_UPDATE, SEARCH_REQUEST, NEW_CASE)
