import re

from service_catalog import detect_service_categories
from service_labels import category_label_ru

PARTNER_INTENT = re.compile(
    r"\b(?:сдаю|сдаём|предлагаю|предлагаем|оказываю|оказываем|"
    r"организую|организуем|в наличии|собственник|агентство|наша компания)\b",
    re.I,
)
CLIENT_INTENT = re.compile(
    r"\b(?:ищу|ищем|нужен|нужна|нужно|нужны|подскажите|посоветуйте|"
    r"кто может|хочу снять|хотим снять|где найти|требуется)\b",
    re.I,
)


def classify_lead_message(lead_type, text):
    """Return a deterministic lead signal used by manual owner intake."""
    value = " ".join(str(text or "").split())
    if len(value) < 12:
        return None
    categories = detect_service_categories(value)
    intent = PARTNER_INTENT.search(value) if lead_type == "partner" else CLIENT_INTENT.search(value)
    if not categories or not intent:
        return None
    category = categories[0]
    reasons = [
        "Обнаружены категории: "
        + ", ".join(category_label_ru(item) for item in categories),
        "Обнаружено предложение услуги" if lead_type == "partner"
        else "Обнаружена явная потребность в услуге",
    ]
    return {
        "detected_category": category,
        "detected_categories": categories,
        "confidence": 0.9,
        "detection_reasons": reasons,
        "status": "needs_review",
    }
