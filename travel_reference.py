"""Curated reference for stable, critical Thailand travel concepts."""

from message_router import normalize_message


TDAC_REFERENCE = {
    "full_name": "Thailand Digital Arrival Card",
    "meaning": "электронная карта прибытия в Таиланд",
    "who": "заполняется иностранными гражданами перед въездом в Таиланд",
    "when": "можно подать в пределах 3 дней до даты прибытия, включая дату прибытия",
    "required_data": [
        "паспортные данные",
        "данные о поездке",
        "данные о месте проживания в Таиланде",
        "адрес электронной почты",
    ],
    "visa_note": "TDAC не является визой",
    "official_domain": "tdac.immigration.go.th",
    "answer_structure": [
        "что это",
        "кому нужно",
        "когда заполнять",
        "что потребуется",
        "где заполнить",
        "оговорка о том, что TDAC не является визой",
        "краткий следующий шаг",
    ],
}


TDAC_CONTRADICTION_MARKERS = (
    "digital asset center",
    "digital assets",
    "cryptocurrency",
    "crypto",
)


def get_travel_reference_context(user_message):
    normalized = normalize_message(user_message)
    if "tdac" not in normalized:
        return {}
    return {
        "topic": "tdac",
        "reference_intent": "tdac_basic",
        "source": "curated_application_reference",
        "reference_metadata": {
            "source": "https://tdac.immigration.go.th",
            "verified_at": None,
            "freshness_policy": "manual_review_required",
        },
        "tdac": dict(TDAC_REFERENCE),
        "constraints": [
            "TDAC всегда расшифровывается только как Thailand Digital Arrival Card.",
            "Не добавлять требования, сроки или правила, которых нет в справке.",
            "Ответ должен быть кратким и следовать answer_structure.",
        ],
    }


def tdac_answer_contradicts_reference(text):
    normalized = normalize_message(text)
    return any(marker in normalized for marker in TDAC_CONTRADICTION_MARKERS)


def format_safe_tdac_answer():
    return (
        "TDAC — Thailand Digital Arrival Card, электронная карта прибытия "
        "в Таиланд. Её заполняют иностранные граждане перед въездом — "
        "в пределах 3 дней до даты прибытия, включая сам день прибытия.\n\n"
        "Потребуются паспортные данные, сведения о поездке и месте проживания "
        "в Таиланде, а также адрес электронной почты. Заполнить форму можно "
        "на официальном сайте: tdac.immigration.go.th.\n\n"
        "TDAC не является визой. Перед поездкой рекомендуем заполнить форму "
        "на официальном сайте и сохранить подтверждение."
    )
