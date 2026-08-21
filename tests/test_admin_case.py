import json
import tempfile
import unittest
from pathlib import Path

from admin_case import (
    AdminCaseNotFoundError,
    format_offer_review_card,
    get_admin_case_snapshot,
)
from database import get_connection, init_db
from partner_network import is_admin


class AdminCaseSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "admin-case.db"
        init_db(self.db_path)
        connection = get_connection(self.db_path)
        try:
            with connection:
                client_id = connection.execute(
                    """
                    INSERT INTO clients(telegram_id, username, first_name, last_name)
                    VALUES (7001, 'anna_phuket', 'Анна', 'Иванова')
                    """
                ).lastrowid
                self.case_id = connection.execute(
                    """
                    INSERT INTO cases
                        (client_id, title, category, data, missing_data, status)
                    VALUES (?, 'Rawai housing', 'housing', ?, '[]', 'results_presented')
                    """,
                    (
                        client_id,
                        json.dumps(
                            {
                                "location": "Rawai",
                                "arrival_date": "01.09.2026",
                                "departure_date": "15.09.2026",
                                "people": "2",
                                "budget": "100 000 рублей",
                            },
                            ensure_ascii=False,
                        ),
                    ),
                ).lastrowid
                self.partial_case_id = connection.execute(
                    """
                    INSERT INTO cases
                        (client_id, title, category, data, missing_data, status)
                    VALUES (?, 'Partial', 'housing', ?, '[]', 'active')
                    """,
                    (client_id, json.dumps({"location": "Kata"})),
                ).lastrowid
        finally:
            connection.close()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_review_card_contains_housing_case_snapshot(self):
        snapshot = get_admin_case_snapshot(self.case_id, self.db_path)
        card = format_offer_review_card(
            {
                "id": 12,
                "case_id": self.case_id,
                "partner_id": 2,
                "status": "needs_review",
                "handoff_decision": "review_required",
                "raw_partner_response": "Есть вариант",
                "validation_reasons": ["global_review_mode"],
            },
            "Rawai Homes",
            snapshot,
        )
        for expected in (
            f"Кейс №{self.case_id} — Жильё",
            "Статус: Найдены варианты",
            "Клиент: Анна Иванова (@anna_phuket)",
            "Район: Rawai",
            "Дата заезда: 01.09.2026",
            "Дата выезда: 15.09.2026",
            "Гостей: 2",
            "Бюджет: 100 000 рублей",
        ):
            self.assertIn(expected, card)
        self.assertNotIn("global_review_mode", card)

    def test_sent_offer_card_reflects_current_state_and_historical_reasons(self):
        card = format_offer_review_card(
            {
                "id": 13,
                "case_id": self.case_id,
                "partner_id": 2,
                "status": "sent_to_client",
                "handoff_decision": "review_required",
                "raw_partner_response": "Есть вариант",
                "validation_reasons": ["global_review_mode"],
            },
            "Rawai Homes",
            "Кейс №1 — Жильё",
            "Вариант от партнёра",
        )
        self.assertIn("✅ Предложение отправлено клиенту", card)
        self.assertIn("Статус предложения: Отправлено клиенту", card)
        self.assertIn("Способ обработки: Ручная проверка", card)
        self.assertIn("Почему потребовалась ручная проверка:", card)
        self.assertIn("Отправлено клиенту:\nВариант от партнёра", card)
        self.assertNotIn("⚠️ Предложение требует проверки", card)
        self.assertNotIn("Причины проверки:", card)

    def test_needs_review_offer_keeps_pending_warning(self):
        card = format_offer_review_card(
            {
                "id": 14,
                "case_id": self.case_id,
                "partner_id": 2,
                "status": "needs_review",
                "handoff_decision": "review_required",
                "raw_partner_response": "Есть вариант",
                "validation_reasons": ["global_review_mode"],
            }
        )
        self.assertIn("⚠️ Предложение требует проверки", card)
        self.assertIn("Причины проверки:", card)

    def test_rejected_offer_card_reflects_rejected_state(self):
        card = format_offer_review_card(
            {
                "id": 15,
                "case_id": self.case_id,
                "partner_id": 2,
                "status": "rejected",
                "handoff_decision": "review_required",
                "raw_partner_response": "Есть вариант",
                "validation_reasons": [],
            }
        )
        self.assertIn("❌ Предложение отклонено", card)
        self.assertIn("Статус предложения: Отклонено", card)
        self.assertNotIn("требует проверки", card)

    def test_ready_to_send_auto_offer_has_correct_current_state(self):
        card = format_offer_review_card(
            {
                "id": 16,
                "case_id": self.case_id,
                "partner_id": 2,
                "status": "ready_to_send",
                "handoff_decision": "auto_send",
                "raw_partner_response": "Есть вариант",
                "validation_reasons": [],
            }
        )
        self.assertIn("🟢 Предложение готово к отправке", card)
        self.assertIn("Способ обработки: Автоматическая отправка", card)
        self.assertNotIn("требует проверки", card)

    def test_missing_fields_are_not_rendered_as_empty_sections(self):
        snapshot = get_admin_case_snapshot(self.partial_case_id, self.db_path)
        self.assertIn("Район: Kata", snapshot)
        self.assertNotIn("Дата заезда:", snapshot)
        self.assertNotIn("Дата выезда:", snapshot)
        self.assertNotIn("Гостей:", snapshot)
        self.assertNotIn("Бюджет:", snapshot)

    def test_case_command_access_uses_admin_identity(self):
        self.assertTrue(is_admin(7001, 7001))
        self.assertFalse(is_admin(7001, 7002))

    def test_unknown_case_is_reported(self):
        with self.assertRaisesRegex(AdminCaseNotFoundError, "Кейс не найден"):
            get_admin_case_snapshot(999999, self.db_path)


if __name__ == "__main__":
    unittest.main()
