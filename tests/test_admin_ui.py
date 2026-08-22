import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from admin_ui import (
    admin_panel_buttons,
    can_access_admin_panel,
    execute_offer_send,
    execute_partner_auto_toggle,
    format_offer_list_item,
    format_partner_card,
    offer_action_buttons,
)
from database import get_connection, init_db
from partner_handoff import (
    DuplicateOfferSendError,
    create_offer_from_partner_response,
)
from partner_network import create_partner


class AdminUiTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "admin-ui.db"
        init_db(self.db_path)
        connection = get_connection(self.db_path)
        try:
            with connection:
                client_id = connection.execute(
                    "INSERT INTO clients(telegram_id) VALUES (8001)"
                ).lastrowid
                self.case_id = connection.execute(
                    """
                    INSERT INTO cases
                        (client_id, title, category, data, missing_data, status)
                    VALUES (?, 'Housing', 'housing', ?, '[]', 'results_presented')
                    """,
                    (client_id, json.dumps({"location": "Rawai"})),
                ).lastrowid
        finally:
            connection.close()
        self.partner = create_partner(
            "Rawai Homes", ["housing", "transfer"], status="active",
            db_path=self.db_path,
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def _offer(self):
        connection = get_connection(self.db_path)
        try:
            with connection:
                request_id = connection.execute(
                    """
                    INSERT INTO partner_requests
                        (case_id, partner_id, service_category, status,
                         request_payload, partner_response, responded_at)
                    VALUES (?, ?, 'housing', 'responded', 'request', ?, CURRENT_TIMESTAMP)
                    """,
                    (
                        self.case_id,
                        self.partner["id"],
                        "Апартаменты 45 000 THB https://example.com/offer",
                    ),
                ).lastrowid
        finally:
            connection.close()
        return create_offer_from_partner_response(
            request_id, "review", db_path=self.db_path
        )

    def test_admin_panel_is_admin_only_and_has_russian_buttons(self):
        self.assertTrue(can_access_admin_panel(8001, 8001))
        self.assertFalse(can_access_admin_panel(8001, 8002))
        labels = [label for row in admin_panel_buttons() for label, _ in row]
        self.assertEqual(
            labels,
            ["🗂 Кейсы", "📋 Предложения", "🤝 Партнёры",
             "📝 Заявки партнёров",
             "📩 Запросы партнёрам", "⚙️ Настройки"],
        )

    def test_admin_cards_translate_internal_values(self):
        partner_card = format_partner_card(self.partner)
        self.assertIn("Статус: Активен", partner_card)
        self.assertIn("Услуги: Жильё, Трансфер", partner_card)
        self.assertNotIn("active", partner_card)
        offer_card = format_offer_list_item(
            {"id": 3, "case_id": self.case_id, "partner_id": self.partner["id"],
             "partner_name": self.partner["name"], "status": "needs_review",
             "handoff_decision": "review_required"}
        )
        self.assertIn("Требуется проверка", offer_card)
        self.assertIn("Ручная проверка", offer_card)
        self.assertNotIn("needs_review", offer_card)
        self.assertNotIn("review_required", offer_card)

    async def test_offer_button_action_preserves_duplicate_protection(self):
        offer = self._offer()
        callbacks = [data for row in offer_action_buttons(
            offer["id"], self.case_id, self.partner["id"]
        ) for _, data in row]
        self.assertIn(f"offer:send:{offer['id']}", callbacks)
        sender = AsyncMock(return_value=SimpleNamespace(message_id=77))
        await execute_offer_send(offer["id"], sender, self.db_path)
        with self.assertRaises(DuplicateOfferSendError):
            await execute_offer_send(offer["id"], sender, self.db_path)
        self.assertEqual(sender.await_count, 1)

    def test_partner_auto_handoff_toggle_uses_service_action(self):
        enabled = execute_partner_auto_toggle(
            self.partner["id"], True, self.db_path
        )
        self.assertEqual(enabled["auto_handoff_enabled"], 1)
        disabled = execute_partner_auto_toggle(
            self.partner["id"], False, self.db_path
        )
        self.assertEqual(disabled["auto_handoff_enabled"], 0)


if __name__ == "__main__":
    unittest.main()
