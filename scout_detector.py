import re


CATEGORY_PATTERNS = {
    "housing": r"\b(?:квартир\w*|апартамент\w*|вилл\w*|жиль[её]|дом\w*)\b",
    "car_rental": r"\b(?:автомобил\w*|машин\w*|авто\b)",
    "bike_rental": r"\b(?:байк\w*|мотоцикл\w*|скутер\w*)\b",
    "transfer": r"\b(?:трансфер\w*|такси\b|водител\w*)\b",
    "excursions": r"\b(?:экскурси\w*|тур\w*|гид\w*)\b",
    "boats": r"\b(?:лодк\w*|яхт\w*|катер\w*)\b",
    "fishing": r"\b(?:рыбалк\w*|рыболов\w*)\b",
    "food": r"\b(?:ресторан\w*|кафе\b|доставк\w+ еды)\b",
    "wellness": r"\b(?:wellness|спа\b|массаж\w*|йог\w*)\b",
    "medical": r"\b(?:клиник\w*|врач\w*|медицин\w*|стоматолог\w*)\b",
    "legal_visa": r"\b(?:юрист\w*|виз\w*|адвокат\w*|легализаци\w*)\b",
    "relocation": r"\b(?:relocation|релокаци\w*|переезд\w*)\b",
}

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


def classify_scout_message(scout_type, text):
    value = " ".join(str(text or "").split())
    if len(value) < 12:
        return None
    categories = [
        category for category, pattern in CATEGORY_PATTERNS.items()
        if re.search(pattern, value, re.I)
    ]
    intent = PARTNER_INTENT.search(value) if scout_type == "partner" else CLIENT_INTENT.search(value)
    if not categories or not intent:
        return None
    category = categories[0]
    reasons = [
        f"Обнаружена категория: {category}",
        "Обнаружено предложение услуги" if scout_type == "partner"
        else "Обнаружена явная потребность в услуге",
    ]
    confidence = 0.9 if len(categories) == 1 else 0.8
    return {
        "detected_category": category,
        "confidence": confidence,
        "detection_reasons": reasons,
        "status": "needs_review",
    }
