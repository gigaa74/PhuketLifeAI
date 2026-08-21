import unittest

from conversation_policy import (
    CONVERSATION_STANDARD_ID,
    CONVERSATION_STANDARD_VERSION,
    CLARIFY_CONTINUITY,
    CONTINUE_EXISTING,
    MODE_ACTION,
    MODE_CONVERSATION,
    MODE_INFORMATION,
    NEW_CASE as CONTINUITY_NEW_CASE,
    STATE_NEEDS_CLARIFICATION,
    STATE_READY_FOR_ACTION,
    UPDATE_EXISTING,
    actionability_check,
    apply_case_continuity,
    build_continuity_question,
    continuity_allows_case_action,
    decide_case_continuity,
    evaluate_information_state,
    guard_policy_answer,
    information_answer_has_unsupported_precision,
    current_message_relates_to_housing,
    has_pending_housing_input,
    is_service_capability_intent,
    is_pure_greeting,
    pure_greeting_response,
    route_with_conversation_policy,
    should_use_conversation_flow,
    trusted_answer_has_unsupported_precision,
)
from conversation_prompts import build_conversation_policy_prompt
from phuket_reference import PHUKET_DISTRICTS, get_phuket_reference_context
from travel_reference import TDAC_REFERENCE
from truthfulness import FORMAL_GREETING, TRUTHFUL_FALLBACK, guard_client_voice
from message_router import CASE_UPDATE, CONVERSATION, NEW_CASE, SEARCH_REQUEST, route_message


def housing_case(data, status="active"):
    return {
        "id": 1,
        "category": "housing",
        "status": status,
        "data": data,
        "missing_data": [],
    }


class ConversationPolicyTests(unittest.TestCase):
    def test_standard_identity_is_versioned(self):
        self.assertEqual(CONVERSATION_STANDARD_ID, "STD-001")
        self.assertEqual(CONVERSATION_STANDARD_VERSION, "1.0")

    def test_incomplete_housing_asks_only_required_missing_fields(self):
        plan = evaluate_information_state("Нужно жильё на Пхукете")
        self.assertEqual(plan.mode, MODE_CONVERSATION)
        self.assertEqual(plan.information_state, STATE_NEEDS_CLARIFICATION)
        self.assertEqual(
            plan.missing_required,
            ["arrival_date", "departure_date", "people", "budget"],
        )

    def test_existing_case_facts_are_not_requested_again(self):
        case = housing_case({
            "location": "Rawai", "people": "2",
            "arrival_date": "1 сентября", "departure_date": "15 сентября",
            "budget": "до 50 000 рублей",
        })
        plan = evaluate_information_state("До 50 тысяч рублей", case)
        self.assertEqual(plan.mode, MODE_ACTION)
        self.assertEqual(plan.information_state, STATE_READY_FOR_ACTION)
        self.assertEqual(plan.next_action, "search")
        self.assertEqual(plan.missing_required, [])

    def test_information_questions_use_information_mode(self):
        for question in (
            "Что такое TDAC?",
            "Сколько ехать из аэропорта до Kata?",
            "Что лучше: Kata или Patong?",
            "Как нам добраться из аэропорта и что со связью?",
        ):
            with self.subTest(question=question):
                plan = evaluate_information_state(question)
                self.assertEqual(plan.mode, MODE_INFORMATION)
                self.assertEqual(plan.next_action, "answer")

    def test_tdac_question_uses_trusted_travel_reference(self):
        plan = evaluate_information_state("Что такое TDAC?")
        self.assertEqual(plan.mode, MODE_INFORMATION)
        self.assertEqual(
            plan.trusted_facts["tdac"]["full_name"],
            "Thailand Digital Arrival Card",
        )
        self.assertEqual(
            plan.trusted_facts["tdac"]["official_domain"],
            "tdac.immigration.go.th",
        )
        self.assertEqual(TDAC_REFERENCE["visa_note"], "TDAC не является визой")

    def test_tdac_hallucinated_expansion_is_blocked(self):
        plan = evaluate_information_state("Что такое TDAC?")
        for false_claim in (
            "TDAC означает Thailand Digital Asset Center.",
            "TDAC связан с crypto и cryptocurrency.",
            "Это платформа для digital assets.",
        ):
            with self.subTest(false_claim=false_claim):
                guarded = guard_policy_answer(false_claim, plan)
                self.assertIn("Thailand Digital Arrival Card", guarded)
                self.assertNotIn("Digital Asset Center", guarded)
                self.assertNotIn("crypto", guarded.casefold())
                self.assertNotIn("digital assets", guarded.casefold())
                self.assertIn("не является визой", guarded)

    def test_open_phuket_area_and_beach_questions_are_information_only(self):
        active_case = housing_case({
            "location": "Rawai", "arrival_date": "1 сентября",
            "departure_date": "15 сентября", "people": "2",
            "budget": "100 000 рублей",
        }, "results_presented")
        for question in (
            "Какие вообще районы есть на Пхукете?",
            "Чем Kata отличается от Patong?",
            "Какие пляжи есть?",
        ):
            with self.subTest(question=question):
                plan = evaluate_information_state(question, active_case)
                routing = route_with_conversation_policy(
                    question, active_case, plan
                )
                self.assertEqual(plan.mode, MODE_INFORMATION)
                self.assertEqual(plan.next_action, "answer")
                self.assertEqual(routing["intent"], CONVERSATION)
                self.assertEqual(routing["policy_override"], MODE_INFORMATION)

    def test_explicit_housing_request_remains_operational(self):
        active_case = housing_case({
            "location": "Rawai", "arrival_date": "1 сентября",
            "departure_date": "15 сентября", "people": "2",
            "budget": "100 000 рублей",
        }, "results_presented")
        message = "Покажи жильё на Kata"
        plan = evaluate_information_state(message, active_case)
        routing = route_with_conversation_policy(message, active_case, plan)
        self.assertNotEqual(plan.mode, MODE_INFORMATION)
        self.assertEqual(routing["intent"], CASE_UPDATE)

    def test_information_policy_prevents_accidental_operational_case_route(self):
        message = "Сколько ехать из аэропорта до Kata?"
        routing = route_message(message, None)
        self.assertEqual(routing["intent"], NEW_CASE)
        plan = evaluate_information_state(message)
        self.assertTrue(should_use_conversation_flow(routing["intent"], plan))

    def test_provider_result_and_partner_offer_enable_action_mode(self):
        provider = evaluate_information_state(
            "Покажите результат", provider_results=[{"url": "https://example.com"}]
        )
        offer = evaluate_information_state(
            "Что предложил партнёр?", approved_partner_offer={"id": 12}
        )
        self.assertEqual(provider.next_action, "show_result")
        self.assertEqual(offer.next_action, "handoff")
        self.assertTrue(actionability_check("result", provider, True))
        self.assertFalse(actionability_check("result", provider, False))

    def test_no_provider_result_cannot_be_invented(self):
        plan = evaluate_information_state("Расскажите о жилье")
        guarded = guard_policy_answer(
            "Мы нашли отель Sunset за 20 000 рублей.", plan
        )
        self.assertEqual(guarded, TRUTHFUL_FALLBACK)

    def test_unsupported_precise_information_is_not_presented_as_fact(self):
        plan = evaluate_information_state(
            "Какие районы есть на Пхукете?"
        )
        generated = (
            "Naithon и Nai Harn находятся на севере Пхукета. "
            "Поездка занимает 20 минут."
        )
        self.assertTrue(information_answer_has_unsupported_precision(generated))
        guarded = guard_policy_answer(generated, plan)
        self.assertNotIn("находятся на севере", guarded)
        self.assertNotIn("20 минут", guarded)
        self.assertIn("нет подключённого проверенного источника", guarded)

    def test_trusted_information_context_allows_supported_precision(self):
        plan = evaluate_information_state(
            "Сколько ехать до Kata?",
            trusted_context={"travel_time": "20 минут"},
        )
        generated = "По данным подключённого источника, поездка занимает 20 минут."
        self.assertEqual(guard_policy_answer(generated, plan), generated)

    def test_known_phuket_district_question_uses_trusted_reference(self):
        plan = evaluate_information_state("Какие вообще районы есть на Пхукете?")
        self.assertEqual(plan.mode, MODE_INFORMATION)
        self.assertEqual(plan.trusted_facts["topic"], "phuket_districts")
        self.assertEqual(len(plan.trusted_facts["districts"]), 10)
        routing = route_with_conversation_policy(
            "Какие вообще районы есть на Пхукете?", None, plan
        )
        self.assertEqual(routing["intent"], CONVERSATION)

    def test_nai_harn_and_naithon_are_separate_reference_entries(self):
        context = get_phuket_reference_context("Чем Nai Harn отличается от Naithon?")
        districts = context["districts"]
        self.assertIn("Nai Harn", districts)
        self.assertIn("Naithon", districts)
        self.assertNotEqual(districts["Nai Harn"], districts["Naithon"])
        self.assertIn("южная", districts["Nai Harn"]["location"])
        self.assertIn("северо-западное", districts["Naithon"]["location"])

    def test_chalong_reference_does_not_call_it_phuket_center(self):
        description = " ".join(PHUKET_DISTRICTS["Chalong"].values()).casefold()
        self.assertNotIn("центр остров", description)
        self.assertNotIn("центр пхукет", description)

    def test_rawai_reference_has_practical_long_stay_context(self):
        description = " ".join(PHUKET_DISTRICTS["Rawai"].values()).casefold()
        self.assertIn("long-stay", description)
        self.assertIn("не как основной пляж для купания", description)

    def test_reference_prompt_forbids_extra_concrete_claims(self):
        plan = evaluate_information_state("Расскажите про Rawai")
        prompt = build_conversation_policy_prompt(plan)
        self.assertIn("curated_application_reference", prompt)
        self.assertIn("Нельзя", prompt)
        self.assertIn("точные расстояния или время", prompt)
        self.assertIn("без соответствующего источника", prompt)

    def test_district_name_in_housing_request_does_not_override_action_intent(self):
        plan = evaluate_information_state("Хочу снять жильё в Rawai")
        self.assertEqual(plan.trusted_facts, {})
        self.assertNotEqual(plan.mode, MODE_INFORMATION)

    def test_reference_does_not_authorize_unlisted_precise_claim(self):
        plan = evaluate_information_state("Какие районы есть на Пхукете?")
        generated = "До Naithon можно доехать за 20 минут."
        self.assertTrue(
            trusted_answer_has_unsupported_precision(
                generated, plan.trusted_facts
            )
        )
        self.assertNotEqual(guard_policy_answer(generated, plan), generated)

    def test_empty_promise_is_blocked(self):
        plan = evaluate_information_state("Найдите жильё")
        guarded = guard_policy_answer("Мы скоро найдём и вернёмся.", plan)
        self.assertEqual(guarded, TRUTHFUL_FALLBACK)

    def test_greeting_and_thanks_remain_human_conversation(self):
        for message in ("Привет", "Спасибо"):
            with self.subTest(message=message):
                plan = evaluate_information_state(message)
                self.assertEqual(plan.mode, MODE_CONVERSATION)
                self.assertEqual(plan.next_action, "answer")
                self.assertIn("по-человечески", plan.goal)

    def test_client_doubt_is_not_forced_into_action_mode(self):
        complete_case = housing_case({
            "arrival_date": "1 сентября", "departure_date": "15 сентября",
            "people": "2", "budget": "50 000 рублей", "location": "Kata",
        })
        plan = evaluate_information_state("Я пока сомневаюсь", complete_case)
        self.assertEqual(plan.mode, MODE_CONVERSATION)
        self.assertEqual(plan.next_action, "answer")

    def test_optional_housing_fields_do_not_block_action(self):
        case = housing_case({
            "arrival_date": "30 августа", "departure_date": "5 сентября",
            "people": "2", "budget": "20 000 рублей", "location": "Kata",
        })
        plan = evaluate_information_state("Подберите отель", case)
        self.assertEqual(plan.next_action, "search")
        self.assertNotIn("housing_type", plan.missing_required)
        self.assertNotIn("pet", plan.missing_required)

    def test_policy_prompt_contains_case_facts_but_not_hidden_plan_instruction(self):
        plan = evaluate_information_state(
            "Почему именно Kata?",
            housing_case({"location": "Kata", "people": "2"}),
        )
        prompt = build_conversation_policy_prompt(plan)
        self.assertIn("STD-001 v1.0", prompt)
        self.assertIn('"location": "Kata"', prompt)
        self.assertIn("не показывай клиенту", prompt)
        self.assertIn("Не повторяй", prompt)


class GoldenConversationPolicyTests(unittest.TestCase):
    def test_complete_hotel_request_is_ready_for_search(self):
        case = housing_case({
            "arrival_date": "30 августа", "departure_date": "5 сентября",
            "people": "2", "budget": "до 20 000 рублей",
            "location": "Kata", "housing_type": "отель",
        }, status="ready_for_search")
        plan = evaluate_information_state(
            "Мы прилетаем 30 августа, нас двое. Нужен отель на Kata на 6 дней до 20 тысяч рублей.",
            case,
        )
        self.assertEqual(plan.mode, MODE_ACTION)
        self.assertEqual(plan.next_action, "search")
        self.assertEqual(plan.missing_required, [])

    def test_transport_and_connectivity_question_stays_truthful_information(self):
        plan = evaluate_information_state(
            "Как нам добраться из аэропорта и что со связью?",
            housing_case({"location": "Kata", "people": "2"}, "results_presented"),
        )
        self.assertEqual(plan.mode, MODE_INFORMATION)
        prompt = build_conversation_policy_prompt(plan)
        self.assertIn("только факты", prompt)
        self.assertIn("не выполняет", prompt)


class CaseContinuityPolicyTests(unittest.TestCase):
    def setUp(self):
        self.case = housing_case({
            "location": "Rawai", "arrival_date": "1 сентября",
            "departure_date": "15 сентября", "people": "2",
            "budget": "до 100 000 ₽",
        }, "results_presented")

    def test_explicit_repeat_continues_existing_case(self):
        for message in ("Покажи ещё", "Есть дешевле?"):
            with self.subTest(message=message):
                decision = decide_case_continuity(message, self.case)
                self.assertEqual(decision, CONTINUE_EXISTING)
                routing = apply_case_continuity(
                    {"intent": CONVERSATION, "category": None}, decision, self.case
                )
                self.assertEqual(routing["intent"], SEARCH_REQUEST)

    def test_parameter_replacement_updates_existing_case(self):
        decision = decide_case_continuity("Давай Kata вместо Rawai", self.case)
        self.assertEqual(decision, UPDATE_EXISTING)
        routing = apply_case_continuity(
            route_message("Давай Kata вместо Rawai", self.case), decision, self.case
        )
        self.assertEqual(routing["intent"], CASE_UPDATE)

    def test_ambiguous_same_category_requires_one_continuity_question(self):
        decision = decide_case_continuity(
            "Хочу снять жильё на Пхукете", self.case
        )
        self.assertEqual(decision, CLARIFY_CONTINUITY)
        self.assertFalse(continuity_allows_case_action(decision))
        question = build_continuity_question(self.case)
        for fact in ("Rawai", "1 сентября", "15 сентября", "гостей: 2", "100 000 ₽"):
            self.assertIn(fact, question)
        self.assertIn("Продолжаем подбор", question)
        self.assertEqual(question.count("?"), 1)

    def test_new_style_housing_request_does_not_inherit_old_case_silently(self):
        message = "Хочу жильё в Rawai на две недели"
        decision = decide_case_continuity(message, self.case)
        self.assertEqual(decision, CLARIFY_CONTINUITY)
        question = build_continuity_question(self.case, message)
        self.assertIn("предыдущий запрос", question)
        self.assertIn(message, question)
        self.assertIn("создадим новый запрос", question)

    def test_explicit_inheritance_can_update_existing_case(self):
        for message in (
            "тот же запрос, но Rawai на две недели",
            "оставь тот же бюджет, но Kata",
        ):
            with self.subTest(message=message):
                decision = decide_case_continuity(message, self.case)
                self.assertEqual(decision, CONTINUE_EXISTING)
                self.assertNotEqual(decision, CLARIFY_CONTINUITY)

    def test_explicit_separate_request_forces_new_case(self):
        decision = decide_case_continuity(
            "Нужен ещё один отдельный вариант жилья для друзей", self.case
        )
        self.assertEqual(decision, CONTINUITY_NEW_CASE)
        routing = apply_case_continuity(
            {"intent": CASE_UPDATE, "category": "housing"}, decision, self.case
        )
        self.assertEqual(routing["intent"], NEW_CASE)
        self.assertTrue(routing["force_new_case"])

    def test_no_existing_case_uses_normal_clarification_flow(self):
        decision = decide_case_continuity("Хочу снять жильё на Пхукете", None)
        self.assertEqual(decision, CONTINUITY_NEW_CASE)
        plan = evaluate_information_state("Хочу снять жильё на Пхукете", None)
        self.assertEqual(plan.mode, MODE_CONVERSATION)
        self.assertEqual(plan.next_action, "ask")


class ClientVoicePolicyTests(unittest.TestCase):
    def test_informal_greeting_is_replaced_with_formal_team_voice(self):
        guarded = guard_client_voice(
            "Привет! Рад тебя видеть. Я помогу.", "привет"
        )
        self.assertEqual(guarded, FORMAL_GREETING)
        normalized = guarded.casefold()
        for forbidden in (" тебя ", " ты ", " я ", " помогу"):
            self.assertNotIn(forbidden, f" {normalized} ")
        self.assertIn("Вас", guarded)
        self.assertIn("можем", guarded)

    def test_real_smoke_greeting_uses_team_voice(self):
        guarded = guard_client_voice(
            "Привет! Готов помочь Вам разобраться.", "привет"
        )
        self.assertEqual(guarded, FORMAL_GREETING)
        self.assertNotIn("готов помочь", guarded.casefold())
        self.assertIn("Рады", guarded)
        self.assertIn("можем", guarded)

    def test_formal_pronouns_are_normalized_for_client_ui(self):
        guarded = guard_client_voice("Мы расскажем вам, что важно для вас.")
        self.assertEqual(guarded, "Мы расскажем Вам, что важно для Вас.")

    def test_singular_phuket_life_voice_is_blocked(self):
        for reply in ("Я помогу Вам.", "Рад Вас видеть.", "Помогу выбрать район."):
            with self.subTest(reply=reply):
                guarded = guard_client_voice(reply)
                self.assertNotRegex(guarded.casefold(), r"\b(?:я|рад|помогу)\b")
                self.assertIn("мы", guarded.casefold())


class GreetingIntentPolicyTests(unittest.TestCase):
    def test_pure_greetings_are_short_conversation_without_housing_questions(self):
        for message in ("Привет", "Здравствуйте", "Добрый день", "Добрый вечер", "Хай"):
            with self.subTest(message=message):
                self.assertTrue(is_pure_greeting(message))
                response = pure_greeting_response(message)
                self.assertEqual(response, FORMAL_GREETING)
                normalized = response.casefold()
                for housing_field in ("район", "срок", "дат", "бюджет", "гост"):
                    self.assertNotIn(housing_field, normalized)
                plan = evaluate_information_state(message)
                self.assertEqual(plan.mode, MODE_CONVERSATION)
                self.assertEqual(route_message(message, None)["intent"], CONVERSATION)

    def test_greeting_with_housing_intent_uses_housing_routing(self):
        message = "Привет, хочу жильё на Пхукете"
        self.assertFalse(is_pure_greeting(message))
        self.assertIsNone(pure_greeting_response(message))
        routing = route_message(message, None)
        self.assertEqual(routing["intent"], NEW_CASE)
        self.assertEqual(routing["category"], "housing")

    def test_greeting_with_transfer_intent_uses_transfer_routing(self):
        message = "Добрый день, нужен трансфер"
        self.assertFalse(is_pure_greeting(message))
        routing = route_message(message, None)
        self.assertEqual(routing["intent"], NEW_CASE)
        self.assertEqual(routing["category"], "transfer")

    def test_active_housing_case_does_not_change_pure_greeting(self):
        active_case = housing_case({
            "location": "Rawai", "arrival_date": "1 сентября",
            "departure_date": "15 сентября", "people": "2",
            "budget": "100 000 рублей",
        }, "results_presented")
        response = pure_greeting_response("Привет")
        self.assertEqual(response, FORMAL_GREETING)
        plan = evaluate_information_state("Привет", active_case)
        self.assertEqual(plan.mode, MODE_CONVERSATION)
        self.assertEqual(plan.next_action, "answer")
        self.assertNotEqual(plan.next_action, "search")

    def test_capability_intent_has_precedence_over_completed_housing_case(self):
        active_case = housing_case({
            "arrival_date": "15.09", "departure_date": "15.10",
            "people": "3", "budget": "150000",
        }, "ready_for_search")
        messages = (
            "Привет, чем Phuket Life может помочь?",
            "Что вы умеете?",
            "Какие услуги у вас есть?",
            "Как работает ваш сервис?",
        )
        for message in messages:
            with self.subTest(message=message):
                self.assertTrue(is_service_capability_intent(message))
                plan = evaluate_information_state(message, active_case)
                self.assertEqual(plan.mode, MODE_INFORMATION)
                self.assertEqual(plan.next_action, "answer")
                self.assertEqual(plan.case_continuity, "not_applicable")
                self.assertFalse(plan.current_case_relevant)

    def test_unrelated_intents_never_reuse_completed_housing_case(self):
        active_case = housing_case({
            "arrival_date": "15.09", "departure_date": "15.10",
            "people": "3", "budget": "150000",
        }, "ready_for_search")
        for message in (
            "Привет", "Спасибо", "Что такое TDAC?",
            "Какие районы есть на Пхукете?", "Расскажите про Rawai",
        ):
            with self.subTest(message=message):
                plan = evaluate_information_state(message, active_case)
                self.assertFalse(plan.current_case_relevant)
                self.assertNotEqual(plan.next_action, "search")

    def test_pending_housing_question_allows_short_answer(self):
        active_case = housing_case({
            "arrival_date": "15.09", "departure_date": "15.10",
            "people": "", "budget": "150000",
        })
        history = [{
            "role": "assistant",
            "content": "Уточните, пожалуйста, количество гостей.",
        }]
        self.assertTrue(has_pending_housing_input(history))
        self.assertTrue(
            current_message_relates_to_housing("3", active_case, history)
        )
        plan = evaluate_information_state(
            "3", active_case, conversation_history=history
        )
        self.assertTrue(plan.current_case_relevant)

    def test_bare_short_answer_without_pending_question_is_not_continuation(self):
        active_case = housing_case({
            "arrival_date": "15.09", "departure_date": "15.10",
            "people": "", "budget": "150000",
        })
        self.assertFalse(current_message_relates_to_housing("3", active_case, []))
        plan = evaluate_information_state("3", active_case, conversation_history=[])
        self.assertFalse(plan.current_case_relevant)
        self.assertEqual(plan.case_continuity, "not_applicable")


if __name__ == "__main__":
    unittest.main()
