import unittest
from unittest.mock import Mock

from answer_source import (
    LLM_GENERAL,
    PROVIDER_SEARCH,
    TRUSTED_REFERENCE,
    format_current_source_requirement,
    select_answer_source,
)
from conversation_policy import (
    MODE_INFORMATION,
    evaluate_information_state,
    guard_policy_answer,
)
from reference_formatter import format_reference_answer
from message_router import NEW_CASE, route_message
from phuket_reference import DISTRICT_ALIASES, resolve_district_mentions


class AnswerSourceTests(unittest.TestCase):
    DISTRICT_EXPLORATION_HISTORY = [{
        "role": "assistant",
        "content": "Коротко о районах Пхукета: можно сравнить несколько районов.",
    }]

    def test_all_district_aliases_resolve_to_canonical_keys(self):
        for alias, canonical in DISTRICT_ALIASES.items():
            with self.subTest(alias=alias):
                self.assertEqual(resolve_district_mentions(alias), [canonical])

    def test_natural_district_phrases_in_exploration_context(self):
        primary_aliases = {
            "Patong": "Патонг", "Kata": "Ката", "Kata Noi": "Ката Ной",
            "Karon": "Карон", "Rawai": "Раваи", "Nai Harn": "Най Харн",
            "Chalong": "Чалонг", "Kamala": "Камала",
            "Bang Tao": "Банг Тао", "Naithon": "Най Тон",
        }
        templates = (
            "расскажи про {district}", "что по {district}?",
            "а {district}?", "давай {district}",
        )
        for canonical, alias in primary_aliases.items():
            for template in templates:
                message = template.format(district=alias)
                with self.subTest(canonical=canonical, message=message):
                    plan = evaluate_information_state(
                        message,
                        conversation_history=self.DISTRICT_EXPLORATION_HISTORY,
                    )
                    self.assertEqual(
                        set(plan.trusted_facts["districts"]), {canonical}
                    )
                    self.assertEqual(
                        select_answer_source(message, plan), TRUSTED_REFERENCE
                    )

    def test_contextual_short_without_context_asks_clarification(self):
        message = "мб Раваи?"
        plan = evaluate_information_state(message)
        self.assertEqual(
            plan.trusted_facts["reference_intent"], "district_clarification"
        )
        answer = format_reference_answer(plan.trusted_facts)
        self.assertIn("район для отдыха или проживания", answer)

    def test_district_list_is_deterministic_without_gigachat_or_search(self):
        plan = evaluate_information_state("Какие районы есть на Пхукете?")
        gigachat = Mock(side_effect=RuntimeError("unavailable"))
        search = Mock()

        source = select_answer_source("Какие районы есть на Пхукете?", plan)
        answer = format_reference_answer(plan.trusted_facts)

        self.assertEqual(source, TRUSTED_REFERENCE)
        self.assertIn("Patong", answer)
        self.assertIn("Nai Harn", answer)
        self.assertIn("Naithon", answer)
        gigachat.assert_not_called()
        search.assert_not_called()

    def test_rawai_detail_uses_reference(self):
        message = "Расскажите про Rawai"
        plan = evaluate_information_state(message)
        self.assertEqual(select_answer_source(message, plan), TRUSTED_REFERENCE)
        answer = format_reference_answer(plan.trusted_facts)
        self.assertIn("long-stay", answer)
        self.assertIn("Rawai Beach", answer)

    def test_reference_formatter_capitalizes_each_composed_sentence(self):
        plan = evaluate_information_state("Расскажите про Rawai")
        answer = format_reference_answer(plan.trusted_facts)
        sentences = [part.strip() for part in answer.split(".") if part.strip()]
        for sentence in sentences[1:]:
            first = next((char for char in sentence if char.isalpha()), "")
            self.assertTrue(first.isupper(), sentence)

    def test_personalized_district_comparison_keeps_reference_for_llm(self):
        message = "Что лучше Kata или Rawai для спокойного отдыха?"
        plan = evaluate_information_state(message)
        self.assertEqual(plan.mode, MODE_INFORMATION)
        self.assertEqual(select_answer_source(message, plan), LLM_GENERAL)
        self.assertEqual(
            set(plan.trusted_facts["districts"]), {"Kata", "Rawai"}
        )

    def test_short_named_comparison_uses_llm_with_reference(self):
        message = "Kata или Rawai?"
        plan = evaluate_information_state(message)
        self.assertEqual(select_answer_source(message, plan), LLM_GENERAL)
        self.assertEqual(set(plan.trusted_facts["districts"]), {"Kata", "Rawai"})

    def test_explicit_housing_action_has_priority_over_district_reference(self):
        for message in ("хочу жильё в Rawai", "снять квартиру в Kata"):
            with self.subTest(message=message):
                plan = evaluate_information_state(message)
                self.assertEqual(plan.trusted_facts, {})
                self.assertEqual(route_message(message, None)["intent"], NEW_CASE)

    def test_tdac_basic_answer_is_deterministic(self):
        message = "Что такое TDAC?"
        plan = evaluate_information_state(message)
        self.assertEqual(select_answer_source(message, plan), TRUSTED_REFERENCE)
        answer = format_reference_answer(plan.trusted_facts)
        self.assertIn("Thailand Digital Arrival Card", answer)
        self.assertIn("tdac.immigration.go.th", answer)
        self.assertIn("не является визой", answer)

    def test_phuket_life_capability_answer_is_deterministic(self):
        message = "Привет, чем Phuket Life может помочь?"
        plan = evaluate_information_state(message)
        self.assertEqual(select_answer_source(message, plan), TRUSTED_REFERENCE)
        answer = format_reference_answer(plan.trusted_facts)
        self.assertIn("concierge-компаньон", answer)
        self.assertIn("жильём", answer)

    def test_unknown_low_risk_information_can_use_llm(self):
        message = "Почему в самолёте закладывает уши?"
        plan = evaluate_information_state(message)
        self.assertEqual(select_answer_source(message, plan), LLM_GENERAL)

    def test_dynamic_fact_without_source_is_not_deterministic_reference(self):
        message = "Какая сегодня актуальная цена трансфера?"
        plan = evaluate_information_state(message)
        self.assertEqual(plan.mode, MODE_INFORMATION)
        self.assertEqual(select_answer_source(message, plan), LLM_GENERAL)
        generated = "Актуальная цена трансфера составляет 1 000 рублей."
        self.assertNotEqual(guard_policy_answer(generated, plan), generated)

    def test_operational_district_claims_require_source(self):
        scenarios = (
            ("до Rawai 7 минут?", "До Rawai ровно 7 минут."),
            (
                "сколько сейчас стоит жильё в Rawai?",
                "Актуальная цена жилья составляет 30 000 рублей.",
            ),
            (
                "есть ли сейчас свободные квартиры в Kata?",
                "Да, есть свободные квартиры.",
            ),
        )
        for question, generated in scenarios:
            with self.subTest(question=question):
                plan = evaluate_information_state(question)
                self.assertEqual(
                    select_answer_source(question, plan), PROVIDER_SEARCH
                )
                self.assertNotEqual(guard_policy_answer(generated, plan), generated)

    def test_current_price_comparison_asks_for_minimum_search_data(self):
        message = "В Rawai сейчас дешевле чем в Kata?"
        plan = evaluate_information_state(message)
        self.assertEqual(select_answer_source(message, plan), PROVIDER_SEARCH)
        answer = format_current_source_requirement(message)
        lower = answer.casefold()
        self.assertIn("актуальных предложений", lower)
        self.assertIn("даты", lower)
        self.assertIn("тип жилья", lower)
        self.assertNotIn("rawai дешевле", lower)
        self.assertNotIn("kata дешевле", lower)
        self.assertNotIn("обычно дешевле", lower)

    def test_low_risk_district_recommendation_can_use_llm(self):
        message = "какой район спокойнее?"
        plan = evaluate_information_state(message)
        self.assertEqual(select_answer_source(message, plan), LLM_GENERAL)
        self.assertEqual(len(plan.trusted_facts["districts"]), 10)


if __name__ == "__main__":
    unittest.main()
