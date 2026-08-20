import unittest

from search_engine import classify_result_type
from search_presentation import (
    CONCRETE_PROPERTY,
    LISTING_PAGE,
    build_pre_search_message,
    build_results_message,
)


class SearchPresentationTests(unittest.TestCase):
    def test_listing_page_is_not_presented_as_specific_housing(self):
        result = {
            "name": "346 объявлений об аренде жилья в Rawai",
            "url": "https://example.com/property-for-rent/rawai",
            "description": "Каталог предложений",
        }
        self.assertEqual(classify_result_type(result), LISTING_PAGE)
        result["result_type"] = LISTING_PAGE
        message = build_results_message([result])
        self.assertIn("источники с предложениями", message)
        self.assertNotIn("подходящие варианты", message)
        self.assertNotIn("первые варианты жилья", message)

    def test_concrete_property_has_specific_variant_header(self):
        result = {
            "name": "Verified property",
            "url": "https://booking.com/hotel/th/verified-property.html",
            "result_type": CONCRETE_PROPERTY,
        }
        self.assertEqual(classify_result_type(result), CONCRETE_PROPERTY)
        self.assertIn("первые варианты жилья", build_results_message([result]))
        self.assertIn(
            "Вот ещё новые варианты:",
            build_results_message([result], repeat_search=True),
        )

    def test_repeat_search_has_no_case_summary(self):
        called = []

        def confirmation_builder(case_data):
            called.append(case_data)
            return "Все основные параметры собраны. Приступаем к поиску."

        message = build_pre_search_message(
            {"location": "Rawai"},
            repeat_search=True,
            confirmation_builder=confirmation_builder,
        )
        self.assertIsNone(message)
        self.assertEqual(called, [])

    def test_repeat_listing_page_header_is_truthful(self):
        message = build_results_message(
            [
                {
                    "name": "Rawai condos",
                    "url": "https://airbnb.com/rawai-thailand/stays/condos",
                    "result_type": LISTING_PAGE,
                }
            ],
            repeat_search=True,
        )
        self.assertIn("Вот ещё страницы с предложениями:", message)
        self.assertNotIn("основные параметры", message.lower())


if __name__ == "__main__":
    unittest.main()
