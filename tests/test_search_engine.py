import unittest

from search_engine import (
    HousingSearchEngine,
    build_search_request,
    get_rental_type,
    has_pet,
    parse_budget_rub,
    parse_people,
    validate_case,
)


class StubProvider:
    name = "stub"

    def search(self, search_request):
        return [
            {
                "name": "Example",
                "url": "https://example.com/property/1",
                "description": "Pet friendly apartment",
                "price": 50000,
                "currency": "RUB",
            },
            {
                "name": "Duplicate",
                "url": "https://example.com/property/1",
                "description": "Same URL",
            },
        ]


class SearchEngineTests(unittest.TestCase):
    def test_basic_parsers(self):
        self.assertEqual(parse_budget_rub("до 50 тыс. рублей"), 50000)
        self.assertEqual(parse_budget_rub("100 000 рублей"), 100000)
        self.assertEqual(parse_people("нас 3 человека"), 3)
        self.assertTrue(has_pet({"pet": "собака"}))
        self.assertFalse(has_pet({"pet": "без животных"}))

    def test_case_validation_and_request_building(self):
        case = {
            "category": "housing",
            "data": {
                "arrival_date": "1 сентября 2026",
                "departure_date": "15 сентября 2026",
                "people": "2",
                "budget": "100 000 рублей",
                "location": "Rawai",
                "pet": "нет",
                "housing_type": "апартаменты",
            },
        }
        self.assertTrue(validate_case(case)["ready"])
        request = build_search_request(case)
        self.assertEqual(request["budget_rub"], 100000)
        self.assertEqual(request["people"], 2)
        self.assertFalse(request["has_pet"])
        self.assertEqual(get_rental_type(request), "short")

    def test_stub_provider_is_normalized_and_deduplicated(self):
        engine = HousingSearchEngine(providers=[StubProvider()])
        results = engine.search(
            {
                "arrival_date": "1 сентября 2026",
                "departure_date": "30 сентября 2026",
                "has_pet": True,
            }
        )
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["source"], "stub")
        self.assertGreater(results[0]["search_score"], 0)


if __name__ == "__main__":
    unittest.main()
