"""Client presentation built exclusively from trusted reference data."""

from travel_reference import format_safe_tdac_answer


def _sentence_fragment(value):
    fragment = str(value or "").strip().rstrip(".")
    if not fragment:
        return fragment
    return fragment[0].upper() + fragment[1:]


def format_reference_answer(reference_result):
    topic = reference_result.get("topic")
    intent = reference_result.get("reference_intent")
    if topic == "tdac" and intent == "tdac_basic":
        return format_safe_tdac_answer()
    if topic == "phuket_life" and intent == "service_capabilities":
        return (
            "Phuket Life — concierge-компаньон. Вы рассказываете, что Вам "
            "нужно, а мы помогаем найти решение, организовать процесс и "
            "сопровождаем до результата.\n\n"
            "Можем помочь с жильём, трансфером и транспортом, связью, "
            "активностями, бытовыми задачами и другими запросами на Пхукете."
        )
    if topic == "phuket_districts" and intent in {
        "district_list", "district_detail", "district_clarification"
    }:
        return _format_districts(reference_result, intent)
    raise ValueError("Unsupported trusted reference result")


def _format_districts(reference_result, intent):
    districts = reference_result.get("districts") or {}
    if not districts:
        raise ValueError("Trusted district reference is empty")

    if intent == "district_list":
        lines = ["Коротко о районах Пхукета:"]
        for name, facts in districts.items():
            lines.append(
                f"• {name} — {_sentence_fragment(facts['profile'])}. "
                f"{_sentence_fragment(facts['practical_features'])}."
            )
        lines.append(
            "Если расскажете, что для Вас важнее — пляж, активная жизнь, "
            "тишина или длительное проживание, мы поможем сузить выбор "
            "до 2–3 районов."
        )
        return "\n\n".join(lines)

    if intent == "district_clarification":
        name = next(iter(districts))
        return (
            f"Вы рассматриваете {name} как район для отдыха или проживания, "
            "или хотите изменить параметры текущего запроса на жильё?"
        )

    name, facts = next(iter(districts.items()))
    return (
        f"{name} — {_sentence_fragment(facts['location'])}. "
        f"{_sentence_fragment(facts['profile'])}. "
        f"{_sentence_fragment(facts['practical_features'])}.\n\n"
        "Если расскажете, что для Вас важнее в поездке, мы подскажем, "
        "насколько этот район подходит под Ваши задачи."
    )
