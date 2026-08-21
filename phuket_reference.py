"""Small, curated reference for stable Phuket district information."""

import re

from message_router import normalize_message


PHUKET_DISTRICTS = {
    "Patong": {
        "location": "западное побережье Пхукета",
        "profile": "туристический пляжный район с активной ночной жизнью",
        "practical_features": "плотная инфраструктура ресторанов, магазинов и туристических услуг; в центральной части обычно оживлённо",
    },
    "Kata": {
        "location": "юго-западное побережье Пхукета, севернее Kata Noi",
        "profile": "пляжный туристический район с жилыми объектами для короткого и длительного проживания",
        "practical_features": "рестораны, магазины и повседневные услуги находятся рядом с основной туристической зоной",
    },
    "Kata Noi": {
        "location": "юго-западное побережье Пхукета, в отдельной бухте южнее Kata",
        "profile": "небольшой пляжный район с более компактной застройкой",
        "practical_features": "выбор повседневной инфраструктуры меньше, чем в соседней Kata",
    },
    "Karon": {
        "location": "западное побережье Пхукета между Patong и Kata",
        "profile": "пляжный туристический район вдоль протяжённого пляжа",
        "practical_features": "жильё и инфраструктура распределены вдоль побережья, поэтому конкретное расположение объекта важно для поездок пешком",
    },
    "Rawai": {
        "location": "южная часть Пхукета",
        "profile": "жилой район с выраженным long-stay профилем, ресторанами и повседневными услугами",
        "practical_features": "Rawai Beach чаще используют как место для лодок, поездок к островам и заведений у моря, а не как основной пляж для купания",
    },
    "Nai Harn": {
        "location": "южная часть Пхукета, рядом с пляжем Nai Harn",
        "profile": "пляжно-жилой район, востребованный как для отдыха, так и для длительного проживания",
        "practical_features": "основная инфраструктура распределена между зоной пляжа и соседними жилыми улицами",
    },
    "Chalong": {
        "location": "юго-восточная часть Пхукета у Chalong Bay",
        "profile": "жилой и сервисный район с пирсом и удобными связями с южной частью острова",
        "practical_features": "Chalong Pier служит отправной точкой многих морских поездок; район не является единым пляжным курортом",
    },
    "Kamala": {
        "location": "западное побережье Пхукета, севернее Patong",
        "profile": "пляжный район, сочетающий туристическую и жилую среду",
        "practical_features": "инфраструктура сосредоточена вокруг пляжа и основной дороги; удалённые объекты могут требовать транспорта",
    },
    "Bang Tao": {
        "location": "северо-западная часть западного побережья Пхукета",
        "profile": "протяжённый пляжный район со смешанной застройкой: местные кварталы, кондоминиумы, отели и виллы",
        "practical_features": "есть инфраструктура для отдыха и длительного проживания; расстояния между отдельными частями района заметны",
    },
    "Naithon": {
        "location": "северо-западное побережье Пхукета, в отдельной пляжной бухте",
        "profile": "небольшой спокойный пляжный район",
        "practical_features": "повседневная инфраструктура компактнее и выбор услуг меньше, чем в крупных туристических районах; это отдельный район, не Nai Harn",
    },
}


DISTRICT_ALIASES = {
    "патонг": "Patong", "patong": "Patong",
    "ката ной": "Kata Noi", "kata noi": "Kata Noi",
    "ката": "Kata", "kata": "Kata",
    "карон": "Karon", "karon": "Karon",
    "раваи": "Rawai", "равай": "Rawai", "rawai": "Rawai",
    "най харн": "Nai Harn", "nai harn": "Nai Harn", "найхарн": "Nai Harn",
    "чалонг": "Chalong", "chalong": "Chalong",
    "камала": "Kamala", "kamala": "Kamala",
    "банг тао": "Bang Tao", "bang tao": "Bang Tao",
    "бангтао": "Bang Tao", "bangtao": "Bang Tao",
    "найтон": "Naithon", "най тон": "Naithon",
    "naithon": "Naithon", "nai thon": "Naithon",
}


def resolve_district_mentions(user_message):
    """Resolve all non-overlapping district aliases to canonical keys."""
    normalized = normalize_message(user_message)
    selected = []
    occupied_spans = []
    # Longer aliases must win, so "Kata Noi" is not reduced to "Kata".
    for alias, canonical in sorted(
        DISTRICT_ALIASES.items(), key=lambda item: len(item[0]), reverse=True
    ):
        for match in re.finditer(rf"(?<!\w){re.escape(alias)}(?!\w)", normalized):
            span = match.span()
            if any(span[0] < end and span[1] > start for start, end in occupied_spans):
                continue
            occupied_spans.append(span)
            if canonical not in selected:
                selected.append(canonical)
    return selected


def has_district_exploration_context(conversation_history):
    """Recognize recent district discussion without consulting an old case."""
    for message in reversed(list(conversation_history or [])[-4:]):
        content = normalize_message(
            message.get("content") if isinstance(message, dict) else message
        )
        if any(marker in content for marker in (
            "коротко о районах пхукета", "какой район", "какие районы",
            "выбор района", "сравнить районы", "насколько этот район подходит",
        )):
            return True
        if resolve_district_mentions(content) and any(
            marker in content
            for marker in ("что лучше", "или", "расскажи", "что по")
        ):
            return True
    return False


def _has_explicit_housing_action(normalized):
    return bool(
        re.search(
            r"(?:хочу|нужн\w*|ищу|найди\w*|подбери\w*|снять|арендова\w*)"
            r".*(?:жиль\w*|квартир\w*|апартамент\w*|отел\w*|вилл\w*)",
            normalized,
        )
        or re.search(
            r"^(?:отел\w*|квартир\w*|апартамент\w*|вилл\w*)\b.*"
            r"(?:на\s+\w+|с\s+\d|до\s+\d)",
            normalized,
        )
    )


def get_phuket_reference_context(user_message, conversation_history=None):
    """Resolve district topic while preserving explicit service intent priority."""
    normalized = normalize_message(user_message)
    if _has_explicit_housing_action(normalized):
        return {}
    selected = resolve_district_mentions(user_message)

    broad_district_question = (
        "какие районы" in normalized
        or "какие вообще районы" in normalized
        or "районы пхукета" in normalized
    )
    if broad_district_question:
        selected = list(PHUKET_DISTRICTS)

    recommendation_without_names = not selected and any(
        marker in normalized
        for marker in (
            "какой район лучше", "какой район спокойнее", "что выбрать с",
            "где будет спокойнее", "куда лучше", "какой район подойдёт",
            "какой район подойдет",
        )
    )
    if recommendation_without_names:
        selected = list(PHUKET_DISTRICTS)

    high_risk_question = any(marker in normalized for marker in (
        "сколько", "цена", "стоит", "дешевле", "дороже", "свободн",
        "наличи", "забронировать", "минут", "километр", "расстояни",
    ))
    comparison_request = (
        len(selected) > 1
        and any(marker in normalized for marker in (
            "или", "чем ", "что лучше", "что выбрать", "подойдёт", "подойдет",
            "для нас", "спокойнее", "на месяц",
        ))
    ) or recommendation_without_names
    strong_detail_request = any(marker in normalized for marker in (
        "расскажи", "расскажите", "что по", "как тебе", "как вам",
        "подробнее", "интересует", "что за район", "про район", "о районе",
    ))
    bare_district = len(selected) == 1 and normalized in DISTRICT_ALIASES
    contextual_short = len(selected) == 1 and bool(
        re.match(r"^(?:а|мб|может|давай)\b", normalized)
    )
    has_context = has_district_exploration_context(conversation_history)

    if not selected:
        return {}
    if high_risk_question:
        reference_intent = "district_operational_question"
    elif comparison_request:
        reference_intent = "district_comparison"
    elif broad_district_question:
        reference_intent = "district_list"
    elif strong_detail_request or bare_district or (contextual_short and has_context):
        reference_intent = "district_detail"
    elif contextual_short:
        reference_intent = "district_clarification"
    else:
        return {}

    return {
        "topic": "phuket_districts",
        "reference_intent": reference_intent,
        "source": "curated_application_reference",
        "districts": {
            name: dict(PHUKET_DISTRICTS[name]) for name in selected
        },
        "constraints": [
            "Nai Harn и Naithon — разные районы; не объединять их.",
            "Не добавлять конкретные факты, которых нет в этих записях.",
            "Не делать выводов о цене, доступности жилья или времени в пути.",
        ],
    }
