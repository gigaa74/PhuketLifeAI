import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import case_engine
from case_flow import persist_case_analysis
from database import get_connection, get_or_create_client, init_db
from housing_flow import build_housing_missing_question, execute_housing_search
from message_router import CONVERSATION, route_message, should_start_search
from search_engine import SEARCH_WITH_RESULTS


FULL_TEXT = (
    "Ищу жильё в Rawai с 1 сентября по 15 сентября 2026 года, "
    "2 человека, бюджет до 100 000 рублей."
)

FULL_ANALYSIS = {
    "category": "housing",
    "title": "Поиск жилья на Пхукете",
    "data": {
        "arrival_date": "1 сентября 2026",
        "departure_date": "15 сентября 2026",
        "people": "2",
        "budget": "до 100 000 рублей",
        "location": "Rawai",
    },
    "missing_data": [],
}


class NewHousingCaseFlowTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test.db"
        init_db(self.db_path)
        self.connection_patch = patch(
            "case_engine.get_connection",
            side_effect=lambda: get_connection(self.db_path),
        )
        self.connection_patch.start()
        self.client_id = get_or_create_client(123, db_path=self.db_path)

    def tearDown(self):
        self.connection_patch.stop()
        self.temp_dir.cleanup()

    @staticmethod
    def verified_search_mock():
        def result(*args):
            return {
                "success": True,
                "status": SEARCH_WITH_RESULTS,
                "request": {"page": args[3]},
                "results": [
                    {
                        "name": "Provider result",
                        "url": "https://example.com/verified",
                    }
                ],
            }

        return Mock(side_effect=result)

    async def run_ready_search(self, persisted):
        search = self.verified_search_mock()
        case_engine.set_case_status(persisted["id"], "searching")
        response, data, status = await execute_housing_search(
            persisted["data"],
            search_callable=search,
        )
        case_engine.update_case(
            persisted["id"], data, [], status
        )
        return search, response, case_engine.get_case(persisted["id"])

    async def test_full_new_housing_request_creates_case_and_searches_once(self):
        route = route_message(FULL_TEXT, None)
        self.assertNotEqual(route["intent"], CONVERSATION)
        persisted = persist_case_analysis(
            self.client_id, FULL_ANALYSIS, route, None
        )
        self.assertTrue(
            should_start_search(
                route["intent"], persisted["category"], persisted["status"]
            )
        )

        search, response, stored = await self.run_ready_search(persisted)
        search.assert_called_once()
        self.assertEqual(response["results"][0]["name"], "Provider result")
        self.assertEqual(stored["category"], "housing")
        self.assertEqual(stored["status"], "results_presented")

    async def test_active_transfer_is_cancelled_and_not_merged_into_housing(self):
        transfer_id = case_engine.get_or_create_case(
            self.client_id, "transfer", "Трансфер"
        )
        case_engine.update_case(
            transfer_id,
            {"airport": "HKT", "passengers": "4"},
            ["arrival_time"],
            "active",
        )
        transfer = case_engine.get_case(transfer_id)
        route = route_message(FULL_TEXT, transfer)
        persisted = persist_case_analysis(
            self.client_id, FULL_ANALYSIS, route, transfer
        )

        self.assertNotEqual(persisted["id"], transfer_id)
        self.assertNotIn("airport", persisted["data"])
        self.assertEqual(case_engine.get_case(transfer_id)["status"], "cancelled")
        search, _, stored = await self.run_ready_search(persisted)
        search.assert_called_once()
        self.assertEqual(stored["status"], "results_presented")

    def test_incomplete_housing_creates_case_without_search(self):
        text = "Нужно жильё в Rawai"
        route = route_message(text, None)
        persisted = persist_case_analysis(
            self.client_id,
            {
                "category": "housing",
                "title": "Поиск жилья",
                "data": {"location": "Rawai"},
                "missing_data": [],
            },
            route,
            None,
        )
        search = self.verified_search_mock()

        self.assertFalse(
            should_start_search(
                route["intent"], persisted["category"], persisted["status"]
            )
        )
        search.assert_not_called()
        question = build_housing_missing_question(persisted["missing_data"])
        self.assertIn("дату заезда", question)
        self.assertIn("бюджет", question)
        self.assertEqual(persisted["status"], "active")


if __name__ == "__main__":
    unittest.main()
