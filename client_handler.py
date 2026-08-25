"""Client conversation handler, isolated from admin and partner flows."""

from telegram import Update
from telegram.ext import ContextTypes

from answer_source import (
    PROVIDER_SEARCH,
    TRUSTED_REFERENCE,
    format_current_source_requirement,
    select_answer_source,
)
from async_utils import run_blocking
from case_engine import (
    close_active_case,
    format_case_for_ai,
    get_client_active_case,
    set_case_status,
    update_case,
)
from case_flow import persist_case_analysis
from client_ai import (
    SETTINGS,
    analyze_case,
    ask_gigachat,
    build_search_confirmation,
    clear_history,
    get_history,
    get_or_create_client,
    save_message,
)
from conversation_policy import (
    CLARIFY_CONTINUITY,
    apply_case_continuity,
    build_continuity_question,
    guard_policy_answer,
    plan_response,
    pure_greeting_response,
    route_with_conversation_policy,
    should_use_conversation_flow,
)
from housing_flow import build_housing_missing_question, execute_housing_search
from message_router import NEW_CASE, SEARCH_REQUEST, should_start_search
from reference_formatter import format_reference_answer
from reliability import SlidingWindowRateLimiter, safe_log
from search_engine import SEARCH_PROVIDER_ERROR
from search_presentation import (
    build_pre_search_message,
    build_results_message,
)
from truthfulness import (
    GENERATION_DELAY_MESSAGE,
    PROVIDER_ERROR_MESSAGE,
    get_no_results_message,
    guard_client_voice,
)
from gigachat_provider import GigaChatGenerationError


CLIENT_RATE_LIMITER = SlidingWindowRateLimiter(
    SETTINGS.client_rate_limit_requests,
    SETTINGS.client_rate_limit_window_seconds,
)
RATE_LIMIT_MESSAGE = (
    "Вы отправляете сообщения слишком быстро. "
    "Подождите немного и попробуйте ещё раз."
)


# =========================================================
# CLEAR
# =========================================================

async def clear(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    client_id = get_or_create_client(
        update
    )

    clear_history(client_id)

    await update.message.reply_text(
        "История диалога очищена.\n\n"
        "Сохранённые данные активного кейса "
        "при этом не удалены.\n\n"
        "Можем продолжить работу."
    )


# =========================================================
# MESSAGE
# =========================================================

def is_reset_request(text):
    if not text:
        return False

    text = text.lower().strip()

    reset_phrases = [
        "давай все заново",
        "давай всё заново",
        "начнем сначала",
        "начнём сначала",
        "начать сначала",
        "начнем заново",
        "начнём заново",
        "начать заново",
        "все заново",
        "всё заново",
        "сбрось все",
        "сбрось всё",
        "забудь все",
        "забудь всё",
        "новый запрос",
        "начнем новый запрос",
        "начнём новый запрос",
    ]

    return any(
        phrase in text
        for phrase in reset_phrases
    )
async def handle_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user = getattr(update, "effective_user", None)
    if user is None:
        user = getattr(getattr(update, "message", None), "from_user", None)
    user_id = getattr(user, "id", None)
    if (
        user_id is not None
        and user_id != SETTINGS.telegram_admin_user_id
        and not CLIENT_RATE_LIMITER.allow(user_id)
    ):
        safe_log("client_rate_limited", user_id_present=True)
        await update.message.reply_text(RATE_LIMIT_MESSAGE)
        return

    correlation_id = getattr(update, "update_id", None)
    if correlation_id is None:
        correlation_id = getattr(update.message, "message_id", None)

    client_id = get_or_create_client(
        update
    )

    user_message = update.message.text
    # =====================================================
    # RESET ACTIVE CASE
    # =====================================================

    if is_reset_request(user_message):

        close_active_case(
            client_id
        )

        clear_history(
            client_id
        )

        await update.message.reply_text(
            "Хорошо 👍 Начинаем с чистого листа.\n\n"
            "Предыдущий запрос закрыт.\n"
            "Расскажите, что вам нужно."
        )

        return

    # -----------------------------------------------------
    # Сохраняем сообщение клиента
    # -----------------------------------------------------

    save_message(
        client_id,
        "user",
        user_message
    )

    greeting = pure_greeting_response(user_message)
    if greeting:
        save_message(client_id, "assistant", greeting)
        await update.message.reply_text(greeting)
        return

    # -----------------------------------------------------
    # Получаем историю
    # -----------------------------------------------------

    history = get_history(
        client_id
    )

    # -----------------------------------------------------
    # Получаем существующий кейс
    # -----------------------------------------------------

    existing_case = (
        get_client_active_case(
            client_id
        )
    )

    response_plan = plan_response(
        user_message,
        existing_case,
        conversation_history=history[:-1],
    )
    answer_source = select_answer_source(
        user_message, response_plan, existing_case
    )
    reference_intent = response_plan.trusted_facts.get("reference_intent")
    use_case_context = not (
        reference_intent == "district_comparison"
        and existing_case
        and existing_case.get("category") != "housing"
    )
    case_context = (
        format_case_for_ai(existing_case)
        if existing_case and use_case_context
        else None
    )
    safe_log(
        "conversation_policy",
        mode=response_plan.mode,
        standard=response_plan.standard_id,
        version=response_plan.standard_version,
        decision=response_plan.next_action,
        source=answer_source,
    )

    if answer_source == TRUSTED_REFERENCE:
        answer = format_reference_answer(response_plan.trusted_facts)
        save_message(client_id, "assistant", answer)
        await update.message.reply_text(answer)
        return

    if (
        answer_source == PROVIDER_SEARCH
        and reference_intent == "district_operational_question"
    ):
        answer = format_current_source_requirement(user_message)
        save_message(client_id, "assistant", answer)
        await update.message.reply_text(answer)
        return

    routing = route_with_conversation_policy(
        user_message, existing_case, response_plan
    )

    continuity = response_plan.case_continuity
    if continuity == CLARIFY_CONTINUITY:
        question = build_continuity_question(existing_case, user_message)
        save_message(client_id, "assistant", question)
        await update.message.reply_text(question)
        return
    routing = apply_case_continuity(routing, continuity, existing_case)

    if should_use_conversation_flow(routing["intent"], response_plan):
        try:
            answer = await run_blocking(
                ask_gigachat,
                history,
                case_context,
                response_plan,
                correlation_id,
            )
            answer = guard_client_voice(
                guard_policy_answer(answer, response_plan), user_message
            )
            save_message(client_id, "assistant", answer)
            await update.message.reply_text(answer)
        except Exception as error:
            safe_log("conversation_generation_failed", level="error", error=error)
            await update.message.reply_text(
                "Извините, произошла техническая "
                "ошибка. Попробуйте написать ещё раз."
            )
        return

    if existing_case:
        safe_log(
            "existing_case_loaded",
            case_id=existing_case.get("id"),
            category=existing_case.get("category"),
            status=existing_case.get("status"),
        )

    # -----------------------------------------------------
    # Анализируем кейс
    # -----------------------------------------------------

    try:

        if routing["intent"] == SEARCH_REQUEST and existing_case:
            case_analysis = {
                "category": existing_case["category"],
                "title": existing_case["title"],
                "data": existing_case["data"],
                "missing_data": existing_case["missing_data"],
            }
        else:
            case_for_analysis = (
                None
                if routing["intent"] == NEW_CASE
                else existing_case
            )
            case_analysis = await run_blocking(
                analyze_case,
                history,
                case_for_analysis,
                correlation_id,
            )

        if not isinstance(
            case_analysis,
            dict
        ):

            raise ValueError(
                "Анализ кейса должен вернуть словарь"
            )

        persisted_case = persist_case_analysis(
            client_id,
            case_analysis,
            routing,
            existing_case,
        )
        case_id = persisted_case["id"]
        category = persisted_case["category"]
        case_data = persisted_case["data"]
        missing_data = persisted_case["missing_data"]
        case_status = persisted_case["status"]

        safe_log(
            "case_persisted",
            case_id=case_id,
            category=category,
            status=case_status,
        )

        # -------------------------------------------------
        # Получаем обновлённый кейс
        # -------------------------------------------------

        active_case = (
            get_client_active_case(
                client_id
            )
        )

        if active_case:

            case_context = (
                format_case_for_ai(
                    active_case
                )
            )

        else:

            case_context = ""

        if category == "housing" and missing_data:
            question = build_housing_missing_question(missing_data)
            save_message(client_id, "assistant", question)
            await update.message.reply_text(question)
            return

        # -------------------------------------------------
        # ЕСЛИ КЕЙС ГОТОВ — ПОДТВЕРЖДАЕМ
        # -------------------------------------------------

        if (
            should_start_search(
                routing["intent"],
                category,
                case_status,
                current_case_relevant=response_plan.current_case_relevant,
                continuity_resolved=(continuity != CLARIFY_CONTINUITY),
            )
        ):

            repeat_search = routing["intent"] == SEARCH_REQUEST
            requested_result_limit = (
                routing.get("requested_result_limit") or 5
            )
            confirmation = build_pre_search_message(
                case_data,
                repeat_search,
                build_search_confirmation,
            )

            if confirmation:
                safe_log(
                    "housing_search_requested",
                    case_id=case_id,
                    result_limit=requested_result_limit,
                    repeat_search=repeat_search,
                )
                save_message(client_id, "assistant", confirmation)
                await update.message.reply_text(confirmation)

            set_case_status(case_id, "searching")

            try:
                (
                    search_result,
                    case_data,
                    search_status,
                ) = await execute_housing_search(
                    case_data,
                    repeat_search,
                    requested_result_limit=requested_result_limit,
                )
            except Exception as error:
                safe_log("housing_search_failed", level="error", error=error)
                set_case_status(case_id, "ready_for_search")
                search_message = PROVIDER_ERROR_MESSAGE
                save_message(client_id, "assistant", search_message)
                await update.message.reply_text(search_message)
                return

            if search_result.get("status") == SEARCH_PROVIDER_ERROR:
                set_case_status(case_id, "ready_for_search")
                search_message = PROVIDER_ERROR_MESSAGE
                save_message(client_id, "assistant", search_message)
                await update.message.reply_text(search_message)
                return

            results = search_result.get(
                "results",
                []
            )

            shown_results = results[:requested_result_limit]
            update_case(
                case_id,
                case_data,
                missing_data,
                search_status,
            )

            if results:
                search_message = build_results_message(
                    shown_results,
                    repeat_search=repeat_search,
                )

            else:

                search_message = get_no_results_message(repeat_search)

            save_message(
                client_id,
                "assistant",
                search_message
            )

            await update.message.reply_text(
                search_message
            )

            return

    except GigaChatGenerationError:

        await update.message.reply_text(
            GENERATION_DELAY_MESSAGE
        )
        return

    except Exception as error:

        safe_log("case_analysis_failed", level="error", error=error)

        case_context = ""

    # -----------------------------------------------------
    # Обычный ответ GIGACHAT
    # -----------------------------------------------------

    try:
        policy_case = active_case if "active_case" in locals() else existing_case
        fallback_plan = plan_response(user_message, policy_case)
        answer = await run_blocking(
            ask_gigachat,
            history,
            case_context,
            fallback_plan,
            correlation_id,
        )
        answer = guard_client_voice(
            guard_policy_answer(answer, fallback_plan), user_message
        )

        save_message(
            client_id,
            "assistant",
            answer
        )

        await update.message.reply_text(
            answer
        )

    except Exception as error:

        safe_log("conversation_generation_failed", level="error", error=error)

        await update.message.reply_text(
            "Извините, произошла техническая "
            "ошибка. Попробуйте написать ещё раз."
        )
