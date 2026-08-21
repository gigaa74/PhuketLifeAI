import unittest

from message_router import (
    CASE_UPDATE,
    CONVERSATION,
    NEW_CASE,
    SEARCH_REQUEST,
    route_message,
    should_start_search,
)


READY_HOUSING_CASE = {
    "id": 1,
    "category": "housing",
    "status": "ready_for_search",
    "data": {
        "arrival_date": "1 сентября",
        "departure_date": "30 сентября",
        "people": "2",
        "budget": "60 000 рублей",
    },
}


class RoutingTests(unittest.TestCase):
    def test_greeting_does_not_start_existing_search(self):
        route = route_message("Привет", READY_HOUSING_CASE)
        self.assertEqual(route["intent"], CONVERSATION)
        self.assertFalse(
            should_start_search(
                route["intent"], "housing", "ready_for_search"
            )
        )

    def test_thanks_does_not_start_existing_search(self):
        route = route_message("Спасибо!", READY_HOUSING_CASE)
        self.assertEqual(route["intent"], CONVERSATION)
        self.assertFalse(
            should_start_search(
                route["intent"], "housing", "ready_for_search"
            )
        )

    def test_case_parameter_change_can_start_search(self):
        route = route_message("Давайте лучше Карон", READY_HOUSING_CASE)
        self.assertEqual(route["intent"], CASE_UPDATE)
        self.assertTrue(
            should_start_search(
                route["intent"], "housing", "ready_for_search"
            )
        )

    def test_explicit_repeat_search_can_start_search(self):
        route = route_message(
            "Поищи еще варианты жилья", READY_HOUSING_CASE
        )
        self.assertEqual(route["intent"], SEARCH_REQUEST)
        self.assertTrue(
            should_start_search(
                route["intent"], "housing", "ready_for_search"
            )
        )

    def test_short_contextual_repeat_search(self):
        presented_case = dict(READY_HOUSING_CASE, status="results_presented")
        route = route_message("А еще?", presented_case)
        self.assertEqual(route["intent"], SEARCH_REQUEST)

    def test_short_repeat_without_housing_context_is_conversation(self):
        route = route_message("А еще?", None)
        self.assertEqual(route["intent"], CONVERSATION)

    def test_natural_repeat_phrases_use_housing_search_context(self):
        presented_case = dict(READY_HOUSING_CASE, status="results_presented")
        examples = (
            "Можешь показать еще?",
            "Можешь показать ещё варианты?",
            "Покажи еще",
            "Покажи ещё варианты",
            "Можешь найти еще?",
            "Есть что-нибудь еще?",
            "Давай еще",
            "Давай ещё варианты",
            "Найди другие варианты",
        )
        for text in examples:
            with self.subTest(text=text):
                route = route_message(text, presented_case)
                self.assertEqual(route["intent"], SEARCH_REQUEST)

    def test_repeat_routing_is_insensitive_to_spacing_case_and_punctuation(self):
        presented_case = dict(READY_HOUSING_CASE, status="results_presented")
        examples = (
            "Можешь показать еще?",
            "Можешь показать еще ?",
            " можешь показать ещё? ",
            "Можешь   показать   еще???",
            "ПОКАЖИ ЕЩЁ",
            "Давай еще!",
            "Есть что-нибудь ещё ?",
        )
        for text in examples:
            with self.subTest(text=text):
                route = route_message(text, presented_case)
                self.assertEqual(route["intent"], SEARCH_REQUEST)

    def test_normalized_repeat_variants_without_context_are_conversation(self):
        for text in (
            "Можешь показать еще ?",
            " Можешь   показать ещё??? ",
            "ПОКАЖИ ЕЩЁ",
            "Есть что-нибудь ещё ?",
        ):
            with self.subTest(text=text):
                self.assertEqual(route_message(text, None)["intent"], CONVERSATION)

    def test_natural_repeat_phrases_without_context_are_conversation(self):
        for text in (
            "Можешь показать еще?",
            "Можешь показать ещё варианты?",
            "Давай еще",
        ):
            with self.subTest(text=text):
                route = route_message(text, None)
                self.assertEqual(route["intent"], CONVERSATION)

    def test_quantified_repeat_extracts_requested_limit(self):
        presented_case = dict(READY_HOUSING_CASE, status="results_presented")
        examples = (
            ("Скинь сразу 5", 5),
            ("Покажи ещё 3", 3),
        )
        for text, expected_limit in examples:
            with self.subTest(text=text):
                route = route_message(text, presented_case)
                self.assertEqual(route["intent"], SEARCH_REQUEST)
                self.assertEqual(
                    route["requested_result_limit"], expected_limit
                )

    def test_quantified_request_without_housing_context_is_conversation(self):
        route = route_message("Скинь 5", None)
        self.assertEqual(route["intent"], CONVERSATION)

    def test_requested_limit_is_capped(self):
        presented_case = dict(READY_HOUSING_CASE, status="results_presented")
        route = route_message("Найди ещё 50", presented_case)
        self.assertEqual(route["intent"], SEARCH_REQUEST)
        self.assertEqual(route["requested_result_limit"], 10)

    def test_parameter_with_word_more_is_not_misrouted_as_repeat(self):
        for text in ("Еще лучше Rawai", "Ещё лучше Rawai?"):
            with self.subTest(text=text):
                route = route_message(text, READY_HOUSING_CASE)
                self.assertEqual(route["intent"], CASE_UPDATE)

    def test_natural_housing_requests_route_to_new_housing_case(self):
        examples = (
            "Ищу жильё в Rawai с 1 сентября",
            "Нужна квартира на Пхукете",
            "Подбери отель в Кате",
            "Хотим снять апартаменты",
            "Найди жильё с 1 по 15 сентября",
            "Нужно жильё для двух человек",
        )
        for text in examples:
            with self.subTest(text=text):
                route = route_message(text, None)
                self.assertEqual(route["intent"], NEW_CASE)
                self.assertEqual(route["category"], "housing")

    def test_new_category_is_separated_from_housing(self):
        route = route_message(
            "Теперь мне нужен трансфер из аэропорта",
            READY_HOUSING_CASE,
        )
        self.assertEqual(route["intent"], NEW_CASE)
        self.assertEqual(route["category"], "transfer")

    def test_search_hard_gate_requires_current_case_relevance(self):
        self.assertFalse(
            should_start_search(
                CASE_UPDATE,
                "housing",
                "ready_for_search",
                current_case_relevant=False,
            )
        )

    def test_search_hard_gate_requires_resolved_continuity(self):
        self.assertFalse(
            should_start_search(
                CASE_UPDATE,
                "housing",
                "ready_for_search",
                current_case_relevant=True,
                continuity_resolved=False,
            )
        )


if __name__ == "__main__":
    unittest.main()
