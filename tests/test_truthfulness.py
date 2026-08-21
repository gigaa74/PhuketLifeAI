import unittest

from truthfulness import (
    INITIAL_NO_RESULTS_MESSAGE,
    PROVIDER_ERROR_MESSAGE,
    REPEAT_NO_RESULTS_MESSAGE,
    TRUTHFUL_FALLBACK,
    get_no_results_message,
    guard_conversational_answer,
)


class TruthfulnessTests(unittest.TestCase):
    def test_conversational_model_cannot_create_housing_result(self):
        generated = (
            "Я нашёл Apartment X за 18 000 бат: "
            "https://example.com/fake"
        )
        guarded = guard_conversational_answer(generated)
        self.assertEqual(guarded, TRUTHFUL_FALLBACK)
        self.assertNotIn("Apartment X", guarded)
        self.assertNotIn("18 000", guarded)
        self.assertNotIn("https://", guarded)

    def test_unverified_precise_advice_is_blocked(self):
        guarded = guard_conversational_answer(
            "Увеличение бюджета на 15% точно повысит шанс."
        )
        self.assertEqual(guarded, TRUTHFUL_FALLBACK)

    def test_unverified_named_property_is_blocked(self):
        guarded = guard_conversational_answer(
            "Apartment Sunset расположен рядом с пляжем."
        )
        self.assertEqual(guarded, TRUTHFUL_FALLBACK)

    def test_conversational_model_cannot_claim_confirmed_options(self):
        guarded = guard_conversational_answer(
            "По текущим параметрам новых подтверждённых вариантов пока нет."
        )
        self.assertEqual(guarded, TRUTHFUL_FALLBACK)
        self.assertNotIn("подтверждённые варианты", guarded.lower())

    def test_conversational_model_cannot_invent_partner_actions(self):
        for generated in (
            "Запрос отправлен партнёру.",
            "Партнёр ответил и предложил виллу.",
            "Партнер предложил вариант трансфера.",
        ):
            with self.subTest(generated=generated):
                self.assertEqual(
                    guard_conversational_answer(generated), TRUTHFUL_FALLBACK
                )

    def test_conversational_model_cannot_invent_client_handoff(self):
        for generated in (
            "Мы отправили клиенту предложение.",
            "Вариант проверен и доступен.",
            "Вариант подтверждён партнёром.",
        ):
            with self.subTest(generated=generated):
                self.assertEqual(
                    guard_conversational_answer(generated), TRUTHFUL_FALLBACK
                )

    def test_error_and_no_results_do_not_promise_background_work(self):
        combined = (
            INITIAL_NO_RESULTS_MESSAGE
            + " "
            + REPEAT_NO_RESULTS_MESSAGE
            + " "
            + PROVIDER_ERROR_MESSAGE
        ).lower()
        for forbidden in (
            "скоро вернусь",
            "как только появятся",
            "сообщу",
            "занимаюсь поиском",
        ):
            self.assertNotIn(forbidden, combined)

    def test_error_and_no_results_do_not_claim_verified_availability(self):
        combined = (
            TRUTHFUL_FALLBACK
            + " "
            + INITIAL_NO_RESULTS_MESSAGE
            + " "
            + REPEAT_NO_RESULTS_MESSAGE
            + " "
            + PROVIDER_ERROR_MESSAGE
        ).lower()
        self.assertNotIn("подтверждён", combined)
        self.assertNotIn("подтвержден", combined)

    def test_initial_search_uses_initial_no_results_copy(self):
        self.assertEqual(
            get_no_results_message(repeat_search=False),
            INITIAL_NO_RESULTS_MESSAGE,
        )
        self.assertIn("доступных нам источниках", INITIAL_NO_RESULTS_MESSAGE)
        self.assertNotIn("дошли до конца", INITIAL_NO_RESULTS_MESSAGE)

    def test_repeat_search_uses_exhausted_no_results_copy(self):
        self.assertEqual(
            get_no_results_message(repeat_search=True),
            REPEAT_NO_RESULTS_MESSAGE,
        )
        self.assertIn("дошли до конца доступных результатов", REPEAT_NO_RESULTS_MESSAGE)
        self.assertNotIn("просмотрели все варианты", REPEAT_NO_RESULTS_MESSAGE.lower())


if __name__ == "__main__":
    unittest.main()
