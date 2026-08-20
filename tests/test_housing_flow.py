import unittest
from unittest.mock import Mock

from case_engine import (
    build_search_fingerprint,
    merge_case_data,
    record_search_results,
)
from housing_flow import execute_housing_search
from search_engine import SEARCH_WITH_RESULTS


class HousingFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_parameter_change_resets_state_and_executes_search_once(self):
        original = {
            "arrival_date": "1 сентября",
            "departure_date": "1 октября",
            "people": "2",
            "budget": "50 000 рублей",
            "location": "Karon",
            "housing_type": "apartments",
        }
        original = record_search_results(
            original,
            [{"url": "https://example.com/old"}],
            page=0,
            fingerprint=build_search_fingerprint(original),
        )
        updated = merge_case_data(original, {"location": "Rawai"})

        def result(*args):
            return {
                "success": True,
                "status": SEARCH_WITH_RESULTS,
                "request": {"page": args[3]},
                "results": [
                    {
                        "name": "Verified Rawai result",
                        "url": "https://example.com/new",
                    }
                ],
            }

        search = Mock(side_effect=result)
        response, persisted, status = await execute_housing_search(
            updated,
            repeat_search=False,
            search_callable=search,
        )

        search.assert_called_once()
        call = search.call_args.args
        self.assertEqual(call[2], [])
        self.assertEqual(call[3], 0)
        self.assertEqual(response["results"][0]["name"], "Verified Rawai result")
        self.assertEqual(status, "results_presented")
        self.assertEqual(persisted["location"], "Rawai")
        self.assertEqual(
            persisted["_search_state"]["shown_urls"],
            ["https://example.com/new"],
        )

    async def test_repeat_search_calls_provider_exactly_once(self):
        case_data = {
            "arrival_date": "1 сентября",
            "departure_date": "1 октября",
            "people": "2",
            "budget": "50 000 рублей",
        }

        def result(*args):
            return {
                "success": True,
                "status": SEARCH_WITH_RESULTS,
                "request": {"page": args[3]},
                "results": [],
            }

        search = Mock(side_effect=result)
        await execute_housing_search(
            case_data,
            repeat_search=True,
            search_callable=search,
        )
        search.assert_called_once()

    async def test_requested_five_shows_only_two_returned_results(self):
        case_data = {
            "arrival_date": "1 сентября",
            "departure_date": "1 октября",
            "people": "2",
            "budget": "50 000 рублей",
        }
        provider_results = [
            {"name": "Result A", "url": "https://example.com/a"},
            {"name": "Result B", "url": "https://example.com/b"},
        ]

        def result(*args):
            return {
                "success": True,
                "status": SEARCH_WITH_RESULTS,
                "request": {"page": args[3], "result_limit": args[5]},
                "results": provider_results,
            }

        search = Mock(side_effect=result)
        response, persisted, status = await execute_housing_search(
            case_data,
            repeat_search=True,
            search_callable=search,
            requested_result_limit=5,
        )

        search.assert_called_once()
        self.assertEqual(search.call_args.args[5], 5)
        self.assertEqual(response["results"], provider_results)
        self.assertEqual(
            persisted["_search_state"]["shown_urls"],
            ["https://example.com/a", "https://example.com/b"],
        )
        self.assertEqual(status, "results_presented")


if __name__ == "__main__":
    unittest.main()
