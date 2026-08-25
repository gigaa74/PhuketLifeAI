import json
import re
from dataclasses import asdict, dataclass, field

from case_engine import get_housing_missing_fields
from message_router import (
    HOUSING_INTENT_MARKERS,
    HOUSING_UPDATE_MARKERS,
    normalize_message,
)


CONVERSATION_STANDARD_ID = "STD-001"
CONVERSATION_STANDARD_VERSION = "1.1"

MODE_CONVERSATION = "conversation"
MODE_INFORMATION = "information"
MODE_ACTION = "action"

STATE_NEEDS_CLARIFICATION = "needs_clarification"
STATE_INFORMATION_ANSWER = "information_answer"
STATE_READY_FOR_ACTION = "ready_for_action"

CONTINUE_EXISTING = "continue_existing"
UPDATE_EXISTING = "update_existing"
NEW_CASE = "new_case"
CLARIFY_CONTINUITY = "clarify_continuity"
NOT_APPLICABLE = "not_applicable"

GREETING_PHRASES = {
    "привет", "здравствуйте", "добрый день", "добрый вечер", "доброе утро", "хай",
}
THANKS_PHRASES = {"спасибо", "спасибо большое", "благодарю"}
HUMAN_DIALOG_MARKERS = (
    "сомневаюсь", "переживаю", "не уверен", "не уверена",
    "расскажите подробнее", "объясните подробнее",
)
INFORMATION_MARKERS = (
    "что такое", "что лучше", "почему", "как добраться", "сколько ехать",
    "можно ли", "как работает", "что со связью", "esim", "e-sim", "tdac",
    "какие районы", "какие вообще районы", "какие пляжи", "чем отличается",
)
DYNAMIC_INFORMATION_MARKERS = (
    "актуальная цена", "текущая цена", "сколько стоит", "расписание",
    "действующие правила", "действует ли", "можно забронировать",
)
HOUSING_MARKERS = ("жиль", "отел", "квартир", "апартамент", "вилл")
SERVICE_CAPABILITY_MARKERS = (
    "чем phuket life может помочь",
    "что такое phuket life",
    "что вы умеете",
    "что вы делаете",
    "какие вопросы решаете",
    "какие услуги у вас есть",
    "какие услуги можете организовать",
    "как вы можете помочь",
    "что вообще делает phuket life",
    "расскажите о вашем сервисе",
    "расскажите о phuket life",
    "как работает ваш сервис",
)
SOCIAL_PHRASES = GREETING_PHRASES | THANKS_PHRASES | {
    "как дела", "круто", "отлично", "понятно",
}


def is_explicit_information_intent(user_message):
    normalized = normalize_message(user_message)
    return (
        any(marker in normalized for marker in INFORMATION_MARKERS)
        or any(marker in normalized for marker in DYNAMIC_INFORMATION_MARKERS)
        or re.search(r"^чем\s+.+\s+отличается(?:\s+от\s+.+)?$", normalized)
        is not None
        or re.search(r"^какие\s+(?:вообще\s+)?(?:районы|пляжи)\b", normalized)
        is not None
    )


def is_pure_greeting(user_message):
    return normalize_message(user_message) in GREETING_PHRASES


def is_service_capability_intent(user_message):
    normalized = normalize_message(user_message)
    return any(marker in normalized for marker in SERVICE_CAPABILITY_MARKERS)


def is_social_conversation(user_message):
    return normalize_message(user_message) in SOCIAL_PHRASES


def has_pending_housing_input(conversation_history):
    history = list(conversation_history or [])
    if not history or history[-1].get("role") != "assistant":
        return False
    prompt = normalize_message(history[-1].get("content"))
    return any(marker in prompt for marker in (
        "количество гостей", "сколько будет гостей", "сколько гостей",
        "дату заезда", "дату выезда", "даты поездки", "бюджет",
        "недостающие параметры поиска жилья",
    ))


def current_message_relates_to_housing(
    user_message, active_case=None, conversation_history=None
):
    if not active_case or active_case.get("category") != "housing":
        return any(
            marker in normalize_message(user_message)
            for marker in HOUSING_INTENT_MARKERS
        )
    normalized = normalize_message(user_message)
    explicit_continuation = (
        "продолжаем", "продолжить", "тот же", "тот же запрос",
        "по старым параметрам", "оставь тот же", "оставьте тот же",
        "покажи ещё", "покажи еще", "поищи ещё", "поищи еще",
        "ещё варианты", "еще варианты", "да, тот же",
    )
    explicit_housing = any(marker in normalized for marker in HOUSING_INTENT_MARKERS)
    explicit_update = any(marker in normalized for marker in HOUSING_UPDATE_MARKERS)
    pending_answer = has_pending_housing_input(conversation_history)
    return bool(
        explicit_housing
        or explicit_update
        or any(marker in normalized for marker in explicit_continuation)
        or pending_answer
    )


def pure_greeting_response(user_message):
    if not is_pure_greeting(user_message):
        return None
    from truthfulness import FORMAL_GREETING

    return FORMAL_GREETING


@dataclass(frozen=True)
class ResponsePlan:
    mode: str
    information_state: str
    goal: str
    known_facts: dict = field(default_factory=dict)
    missing_required: list = field(default_factory=list)
    next_action: str = "answer"
    case_continuity: str = NOT_APPLICABLE
    trusted_facts: dict = field(default_factory=dict)
    current_case_relevant: bool = False
    standard_id: str = CONVERSATION_STANDARD_ID
    standard_version: str = CONVERSATION_STANDARD_VERSION

    def to_dict(self):
        return asdict(self)


def evaluate_information_state(
    user_message,
    active_case=None,
    provider_results=None,
    approved_partner_offer=None,
    trusted_context=None,
    conversation_history=None,
):
    normalized = normalize_message(user_message)
    case_data = (active_case or {}).get("data") or {}
    case_relevant = current_message_relates_to_housing(
        user_message, active_case, conversation_history
    )
    continuity = (
        decide_case_continuity(user_message, active_case)
        if case_relevant
        else NOT_APPLICABLE
    )
    from phuket_reference import get_phuket_reference_context
    from travel_reference import get_travel_reference_context

    trusted_facts = get_phuket_reference_context(
        user_message, conversation_history=conversation_history
    )
    trusted_facts.update(get_travel_reference_context(user_message))
    trusted_facts.update(dict(trusted_context or {}))
    if is_service_capability_intent(user_message):
        return ResponsePlan(
            MODE_INFORMATION, STATE_INFORMATION_ANSWER,
            "Коротко объяснить возможности сервиса Phuket Life.",
            known_facts=case_data, next_action="answer",
            case_continuity=NOT_APPLICABLE,
            trusted_facts={
                "topic": "phuket_life",
                "reference_intent": "service_capabilities",
            },
            current_case_relevant=False,
        )
    if is_social_conversation(user_message):
        return ResponsePlan(
            MODE_CONVERSATION, STATE_NEEDS_CLARIFICATION,
            "Ответить коротко и по-человечески, без анкеты и инструкции.",
            known_facts=case_data, next_action="answer",
            case_continuity=continuity,
            trusted_facts=trusted_facts,
            current_case_relevant=False,
        )
    if any(marker in normalized for marker in HUMAN_DIALOG_MARKERS):
        return ResponsePlan(
            MODE_CONVERSATION, STATE_NEEDS_CLARIFICATION,
            "Сначала спокойно ответить на сомнение или просьбу клиента, затем при уместности предложить действие.",
            known_facts=case_data, next_action="answer",
            case_continuity=continuity,
            trusted_facts=trusted_facts,
            current_case_relevant=False,
        )
    if trusted_facts or is_explicit_information_intent(user_message):
        return ResponsePlan(
            MODE_INFORMATION, STATE_INFORMATION_ANSWER,
            "Дать прямой краткий ответ и полезный следующий шаг, если он уместен.",
            known_facts=case_data, next_action="answer",
            case_continuity=continuity,
            trusted_facts=trusted_facts,
            current_case_relevant=False,
        )
    if provider_results:
        return ResponsePlan(
            MODE_ACTION, STATE_READY_FOR_ACTION,
            "Показать только реальные provider results и следующий шаг клиента.",
            known_facts=case_data, next_action="show_result",
            case_continuity=continuity,
            trusted_facts=trusted_facts,
        )
    if approved_partner_offer:
        return ResponsePlan(
            MODE_ACTION, STATE_READY_FOR_ACTION,
            "Представить только существующее безопасное предложение партнёра.",
            known_facts=case_data, next_action="handoff",
            case_continuity=continuity,
            trusted_facts=trusted_facts,
        )
    category = (active_case or {}).get("category")
    if category == "housing" and case_relevant:
        missing = get_housing_missing_fields(case_data)
        if missing:
            return ResponsePlan(
                MODE_CONVERSATION, STATE_NEEDS_CLARIFICATION,
                "Уточнить только обязательные параметры, которых ещё нет в кейсе.",
                known_facts=case_data, missing_required=missing, next_action="ask",
                case_continuity=continuity,
                trusted_facts=trusted_facts,
                current_case_relevant=True,
            )
        return ResponsePlan(
            MODE_ACTION, STATE_READY_FOR_ACTION,
            "Запустить поиск жилья через Search Engine без optional-вопросов.",
            known_facts=case_data, next_action="search",
            case_continuity=continuity,
            trusted_facts=trusted_facts,
            current_case_relevant=True,
        )
    if any(marker in normalized for marker in HOUSING_MARKERS):
        missing = ["arrival_date", "departure_date", "people", "budget"]
        return ResponsePlan(
            MODE_CONVERSATION, STATE_NEEDS_CLARIFICATION,
            "Собрать минимальные обязательные параметры поиска жилья.",
            missing_required=missing, next_action="ask",
            case_continuity=continuity,
            trusted_facts=trusted_facts,
            current_case_relevant=True,
        )
    return ResponsePlan(
        MODE_CONVERSATION, STATE_NEEDS_CLARIFICATION,
        "Понять потребность естественным коротким диалогом.",
        known_facts=case_data, next_action="answer",
        case_continuity=continuity,
        trusted_facts=trusted_facts,
        current_case_relevant=False,
    )


def decide_case_continuity(user_message, active_case=None):
    if not active_case:
        return NEW_CASE
    normalized = normalize_message(user_message)
    if active_case.get("category") != "housing":
        return NOT_APPLICABLE
    explicit_new = (
        "новый запрос", "отдельный запрос", "ещё один отдельный",
        "еще один отдельный", "для друзей", "для другого человека",
    )
    if any(marker in normalized for marker in explicit_new):
        return NEW_CASE
    continue_markers = (
        "покажи ещё", "покажи еще", "дай ещё", "дай еще", "есть дешевле",
        "что-нибудь дешевле", "что нибудь дешевле", "продолжаем",
        "продолжить подбор", "по старым параметрам",
        "тот же запрос", "тот же кейс", "продолжаем тот кейс",
        "прошлый вариант", "всё то же самое", "все то же самое",
        "оставь тот же", "оставьте тот же",
    )
    if any(marker in normalized for marker in continue_markers):
        return CONTINUE_EXISTING
    update_markers = ("вместо", "давай лучше", "изменим", "поменяем")
    if any(marker in normalized for marker in update_markers):
        return UPDATE_EXISTING
    ambiguous_housing = any(
        marker in normalized
        for marker in (
            "хочу снять жиль", "хочу жиль", "нужно жиль", "ищу жиль",
            "ищу апартамент", "нужна квартир", "нужен отел",
        )
    )
    if ambiguous_housing:
        return CLARIFY_CONTINUITY
    return NOT_APPLICABLE


def compact_case_snapshot(active_case):
    data = (active_case or {}).get("data") or {}
    parts = []
    if data.get("location"):
        parts.append(str(data["location"]))
    arrival = data.get("arrival_date")
    departure = data.get("departure_date")
    if arrival and departure:
        parts.append(f"{arrival} — {departure}")
    if data.get("people"):
        parts.append(f"гостей: {data['people']}")
    if data.get("budget"):
        parts.append(f"бюджет: {data['budget']}")
    return ", ".join(parts) or "параметры предыдущего запроса"


def build_continuity_question(active_case, proposed_request=None):
    question = (
        "У нас сохранился Ваш предыдущий запрос: "
        f"{compact_case_snapshot(active_case)}.\n\n"
    )
    if proposed_request:
        question += f"Сейчас Вы написали: «{proposed_request.strip()}».\n\n"
    return question + (
        "Продолжаем подбор по предыдущему запросу с изменениями или создадим "
        "новый запрос?"
    )


def continuity_allows_case_action(decision):
    return decision != CLARIFY_CONTINUITY


def apply_case_continuity(routing, decision, active_case=None):
    from message_router import CASE_UPDATE, CONVERSATION, NEW_CASE as ROUTER_NEW_CASE, SEARCH_REQUEST

    if decision == CONTINUE_EXISTING and routing.get("intent") == CONVERSATION:
        return {"intent": SEARCH_REQUEST, "category": None}
    if decision == UPDATE_EXISTING:
        return {
            "intent": CASE_UPDATE,
            "category": (active_case or {}).get("category"),
        }
    if decision == NEW_CASE and active_case:
        return {
            "intent": ROUTER_NEW_CASE,
            "category": "housing",
            "force_new_case": True,
        }
    return routing


def plan_response(*args, **kwargs):
    return evaluate_information_state(*args, **kwargs)


def route_with_conversation_policy(user_message, active_case, response_plan):
    from message_router import CONVERSATION, route_message

    if response_plan.mode == MODE_INFORMATION:
        return {
            "intent": CONVERSATION,
            "category": None,
            "policy_override": MODE_INFORMATION,
        }
    return route_message(user_message, active_case)


def should_use_conversation_flow(routing_intent, response_plan):
    from message_router import CONVERSATION

    return routing_intent == CONVERSATION or response_plan.mode == MODE_INFORMATION


def actionability_check(text, response_plan, has_application_evidence=False):
    if response_plan.mode != MODE_ACTION:
        return True
    if response_plan.next_action in {"show_result", "handoff"}:
        return bool(has_application_evidence)
    normalized = normalize_message(text)
    action_markers = (
        "откройте", "перейдите", "нажмите", "уточните", "выберите",
        "напишите", "покажу", "запустить поиск", "начать поиск",
    )
    return any(marker in normalized for marker in action_markers)


def guard_policy_answer(text, response_plan, has_application_evidence=False):
    from truthfulness import guard_conversational_answer
    from travel_reference import (
        format_safe_tdac_answer,
        tdac_answer_contradicts_reference,
    )

    guarded = guard_conversational_answer(text)
    if (
        response_plan.mode == MODE_INFORMATION
        and response_plan.trusted_facts.get("topic") == "tdac"
        and tdac_answer_contradicts_reference(guarded)
    ):
        guarded = format_safe_tdac_answer()
    if (
        response_plan.mode == MODE_INFORMATION
        and information_answer_has_unsupported_precision(guarded)
        and (
            not response_plan.trusted_facts
            or trusted_answer_has_unsupported_precision(
                guarded, response_plan.trusted_facts
            )
        )
    ):
        return (
            "Можем дать общий ориентир, но сейчас у нас нет подключённого "
            "проверенного источника для точных географических, ценовых или "
            "правовых утверждений. Подскажите, что для Вас важнее — пляж, "
            "спокойная обстановка или удобство поездок — и мы поможем сузить выбор."
        )
    if not actionability_check(
        guarded, response_plan, has_application_evidence=has_application_evidence
    ):
        return (
            "Сейчас мы не можем подтвердить готовое решение через подключённые "
            "источники. Можно уточнить параметры или запустить доступное действие."
        )
    return guarded


def information_answer_has_unsupported_precision(text):
    normalized = normalize_message(text)
    precise_value = re.search(
        r"\b\d[\d\s.,]*(?:минут\w*|час\w*|бат\w*|руб\w*|thb|usd|%)\b",
        normalized,
    )
    confident_geo = any(marker in normalized for marker in (
        "на севере", "на юге", "на востоке", "на западе",
        "севернее", "южнее", "расположен", "находится между",
    ))
    legal_or_current = any(marker in normalized for marker in (
        "по закону", "обязательно по", "штраф составляет", "официально действует",
        "актуальная цена", "точная цена", "гарантированно",
        "сейчас дешевле", "сейчас дороже", "есть свободные",
        "свободные номера", "свободные квартиры", "можно забронировать",
    ))
    return bool(precise_value or confident_geo or legal_or_current)


def trusted_answer_has_unsupported_precision(text, trusted_facts):
    """Reject precise claims that cannot be found in the supplied facts."""
    normalized = normalize_message(text)
    trusted_text = normalize_message(
        json.dumps(trusted_facts, ensure_ascii=False)
    )
    precise_claims = re.findall(
        r"\b\d[\d\s.,]*(?:минут\w*|час\w*|бат\w*|руб\w*|thb|usd|%)\b",
        normalized,
    )
    if any(claim not in trusted_text for claim in precise_claims):
        return True

    geo_markers = (
        "на севере", "на юге", "на востоке", "на западе",
        "севернее", "южнее", "расположен", "находится между",
    )
    if any(marker in normalized and marker not in trusted_text for marker in geo_markers):
        return True

    factual_markers = (
        "по закону", "обязательно по", "штраф составляет", "официально действует",
        "актуальная цена", "точная цена", "гарантированно",
        "сейчас дешевле", "сейчас дороже", "есть свободные",
        "свободные номера", "свободные квартиры", "можно забронировать",
    )
    return any(
        marker in normalized and marker not in trusted_text
        for marker in factual_markers
    )
