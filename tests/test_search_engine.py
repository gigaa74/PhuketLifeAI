import unittest

from case_engine import get_repeat_search_options, record_search_results
from search_engine import (
    HousingSearchEngine,
    SEARCH_NO_RESULTS,
    SEARCH_PROVIDER_ERROR,
    build_concrete_search_queries,
    build_search_request,
    classify_result_type,
    get_rental_type,
    has_pet,
    normalize_result,
    parse_budget_rub,
    parse_people,
    search_housing,
    validate_case,
)
from search_presentation import CONCRETE_PROPERTY, LISTING_PAGE


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


class EmptyStubProvider:
    name = "empty"

    def search(self, search_request):
        return []


class FailingStubProvider:
    name = "failing"

    def search(self, search_request):
        raise RuntimeError("provider unavailable")


class SequencedStubProvider:
    name = "sequenced"

    def __init__(self):
        self.calls = 0

    def search(self, search_request):
        self.calls += 1
        names = ("A", "B", "C", "D", "E") if self.calls == 1 else (
            "A", "B", "C", "D", "E", "F", "G", "H", "I", "J"
        )
        return [
            {
                "name": name,
                "url": f"https://example.com/{name.lower()}",
                "description": name,
            }
            for name in names
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

    def test_concrete_property_ranks_above_category_page(self):
        class MixedProvider:
            name = "mixed"

            def search(self, search_request):
                return [
                    {
                        "name": "Rawai property catalog",
                        "url": "https://example.com/property-for-rent/rawai",
                    },
                    {
                        "name": "Specific Rawai residence",
                        "url": "https://booking.com/hotel/th/rawai-residence.html",
                    },
                ]

        results = HousingSearchEngine([MixedProvider()]).search(
            {"arrival_date": "1 сентября 2026", "departure_date": "15 сентября 2026"}
        )
        self.assertEqual(results[0]["result_type"], CONCRETE_PROPERTY)
        self.assertEqual(results[1]["result_type"], LISTING_PAGE)

    def test_concrete_query_builder_creates_targeted_discovery_queries(self):
        queries = build_concrete_search_queries(
            {
                "location": "Rawai",
                "housing_type": "apartment",
                "arrival_date": "1 September 2026",
                "departure_date": "15 September 2026",
            }
        )
        self.assertGreaterEqual(len(queries), 5)
        self.assertTrue(any("site:booking.com/hotel" in query for query in queries))
        self.assertTrue(any("site:airbnb.com/rooms" in query for query in queries))
        self.assertTrue(any("specific property" in query for query in queries))

    def test_booking_hotel_page_is_concrete(self):
        self.assertEqual(
            classify_result_type(
                {"url": "https://www.booking.com/hotel/th/rawai-residence.html"}
            ),
            CONCRETE_PROPERTY,
        )

    def test_booking_city_apartments_page_is_listing(self):
        self.assertEqual(
            classify_result_type(
                {"url": "https://www.booking.com/apartments/city/th/phuket.html"}
            ),
            LISTING_PAGE,
        )

    def test_price_text_is_preserved_only_from_provider_data(self):
        without_provider_price = normalize_result(
            {
                "name": "Rawai residence",
                "url": "https://example.com/property/rawai-residence",
                "description": "Apartments near the beach from 5000 THB",
            },
            "stub",
        )
        with_provider_price = normalize_result(
            {
                "name": "Rawai residence",
                "url": "https://example.com/property/rawai-residence-2",
                "price_text": "5 000 THB",
            },
            "stub",
        )
        self.assertEqual(without_provider_price["price_text"], "")
        self.assertEqual(with_provider_price["price_text"], "5 000 THB")
        self.assertIn("T", with_provider_price["retrieved_at"])

    def test_repeat_excludes_already_shown_concrete_url(self):
        concrete_url = "https://booking.com/hotel/th/rawai-residence.html"

        class ConcreteProvider:
            name = "concrete"

            def search(self, search_request):
                return [
                    {"name": "Old", "url": concrete_url},
                    {
                        "name": "New",
                        "url": "https://airbnb.com/rooms/123456",
                    },
                ]

        result = search_housing(
            self._ready_case(),
            providers=[ConcreteProvider()],
            excluded_urls=[concrete_url],
            repeat_search=True,
        )
        self.assertEqual([item["name"] for item in result["results"]], ["New"])

    def test_requested_limit_is_filled_with_separate_listing_fallbacks(self):
        class ThreeConcreteProvider:
            name = "mixed"

            def search(self, search_request):
                concrete = [
                    {
                        "name": f"Concrete {index}",
                        "url": f"https://booking.com/hotel/th/concrete-{index}.html",
                    }
                    for index in range(3)
                ]
                listings = [
                    {
                        "name": f"Catalog {index}",
                        "url": f"https://example.com/property-for-rent/{index}",
                    }
                    for index in range(3)
                ]
                return listings + concrete

        result = search_housing(
            self._ready_case(),
            providers=[ThreeConcreteProvider()],
            result_limit=5,
        )
        self.assertEqual(len(result["results"]), 5)
        self.assertEqual(
            [item["result_type"] for item in result["results"]],
            [CONCRETE_PROPERTY] * 3 + [LISTING_PAGE] * 2,
        )

    def test_empty_provider_is_no_results(self):
        result = search_housing(
            self._ready_case(),
            providers=[EmptyStubProvider()],
        )
        self.assertTrue(result["success"])
        self.assertEqual(result["status"], SEARCH_NO_RESULTS)
        self.assertEqual(result["results"], [])

    def test_provider_failure_is_error_not_no_results(self):
        result = search_housing(
            self._ready_case(),
            providers=[FailingStubProvider()],
        )
        self.assertFalse(result["success"])
        self.assertEqual(result["status"], SEARCH_PROVIDER_ERROR)
        self.assertEqual(result["results"], [])

    def test_repeat_search_excludes_previously_presented_urls(self):
        case = self._ready_case()
        provider = SequencedStubProvider()

        first_options = get_repeat_search_options(case["data"])
        first = search_housing(
            case,
            providers=[provider],
            excluded_urls=first_options["excluded_urls"],
            page=first_options["page"],
        )
        self.assertEqual(
            [item["name"] for item in first["results"]],
            ["A", "B", "C", "D", "E"],
        )

        case["data"] = record_search_results(
            case["data"],
            first["results"],
            first["request"]["page"],
            first_options["fingerprint"],
        )
        repeat_options = get_repeat_search_options(
            case["data"], repeat_search=True
        )
        repeated = search_housing(
            case,
            providers=[provider],
            excluded_urls=repeat_options["excluded_urls"],
            page=repeat_options["page"],
            repeat_search=True,
        )

        self.assertEqual(repeat_options["page"], 1)
        self.assertEqual(
            [item["name"] for item in repeated["results"]],
            ["F", "G", "H", "I", "J"],
        )

        case["data"]["location"] = "Karon"
        changed_options = get_repeat_search_options(
            case["data"], repeat_search=True
        )
        self.assertEqual(changed_options["excluded_urls"], [])
        self.assertEqual(changed_options["page"], 0)

    @staticmethod
    def _ready_case():
        return {
            "category": "housing",
            "data": {
                "arrival_date": "1 сентября 2026",
                "departure_date": "15 сентября 2026",
                "people": "2",
                "budget": "100 000 рублей",
            },
        }


if __name__ == "__main__":
    unittest.main()
