"""Deterministic selection of the truth source for a response."""


APPLICATION_STATE = "application_state"
TRUSTED_REFERENCE = "trusted_reference"
PROVIDER_SEARCH = "provider_search"
PARTNER = "partner"
LLM_GENERAL = "llm_general"


DETERMINISTIC_REFERENCE_INTENTS = {
    "district_list",
    "district_detail",
    "district_clarification",
    "tdac_basic",
    "service_capabilities",
}


def select_answer_source(message, response_plan, active_case=None):
    """Choose the source of truth; message/case are reserved for extension."""
    del message, active_case
    if response_plan.next_action == "show_result":
        return PROVIDER_SEARCH
    if response_plan.next_action == "handoff":
        return PARTNER
    reference_intent = response_plan.trusted_facts.get("reference_intent")
    if reference_intent == "district_operational_question":
        return PROVIDER_SEARCH
    if reference_intent in DETERMINISTIC_REFERENCE_INTENTS:
        return TRUSTED_REFERENCE
    if response_plan.mode == "action":
        return APPLICATION_STATE
    return LLM_GENERAL


def format_current_source_requirement(message):
    normalized = str(message or "").casefold()
    if any(marker in normalized for marker in (
        "дешевле", "дороже", "цена", "стоит",
    )):
        return (
            "Без сравнения актуальных предложений на одинаковые даты мы не "
            "будем утверждать, где сейчас дешевле. Назовите даты поездки и, "
            "если важно, тип жилья — мы сравним районы по реальным предложениям."
        )
    if any(marker in normalized for marker in ("свободн", "наличи", "забронировать")):
        return (
            "Чтобы проверить актуальные варианты, нужны даты поездки и основные "
            "параметры жилья. Подскажите даты — мы проверим доступные нам источники."
        )
    return (
        "Мы не будем подтверждать точное время или расстояние без актуального "
        "источника и конкретной точки отправления. Подскажите маршрут подробнее — "
        "тогда сможем проверить данные."
    )
