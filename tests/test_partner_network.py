import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from database import get_connection, init_db
from partner_network import (
    DuplicatePartnerRequestError,
    PartnerTelegramError,
    PartnerUnavailableError,
    create_partner,
    create_partner_invite,
    find_partners_for_case,
    format_partner_request,
    get_partner_request,
    is_admin,
    onboard_partner,
    record_partner_reply,
    send_case_to_partner,
    set_partner_status,
)


class PartnerNetworkTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "partners.db"
        init_db(self.db_path)
        connection = get_connection(self.db_path)
        try:
            with connection:
                client_id = connection.execute(
                    "INSERT INTO clients(telegram_id) VALUES (1001)"
                ).lastrowid
                self.case_id = connection.execute(
                    """
                    INSERT INTO cases
                        (client_id, title, category, data, missing_data, status)
                    VALUES (?, 'Housing', 'housing', ?, '[]', 'results_presented')
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
                                "preferences": "Рядом с пляжем",
                                "phone": "+79990000000",
                                "telegram_user_id": 1001,
                                "email": "private@example.com",
                            },
                            ensure_ascii=False,
                        ),
                    ),
                ).lastrowid
        finally:
            connection.close()

    def tearDown(self):
        self.temp_dir.cleanup()

    def _partner(self, name="Rawai Homes", services=None, status="active", user_id=None):
        partner = create_partner(
            name,
            services or ["housing"],
            areas=["Rawai"],
            status=status,
            db_path=self.db_path,
        )
        if user_id is not None:
            token = create_partner_invite(partner["id"], self.db_path)
            partner = onboard_partner(
                token, user_id, name.lower().replace(" ", "_"), self.db_path
            )
        return partner

    def _case(self, category="housing", location="Rawai"):
        return {
            "id": self.case_id,
            "category": category,
            "data": {"location": location},
        }

    def test_partner_supports_multiple_services(self):
        partner = self._partner(services=["housing", "transfer"])
        self.assertEqual(partner["services"], ["housing", "transfer"])
        self.assertIn(partner, find_partners_for_case(self._case(), self.db_path))
        self.assertIn(
            partner,
            find_partners_for_case(self._case("transfer"), self.db_path),
        )

    def test_partner_invite_can_only_be_claimed_once(self):
        partner = self._partner("Invite Partner")
        token = create_partner_invite(partner["id"], self.db_path)
        linked = onboard_partner(token, 778899, "invite_partner", self.db_path)
        self.assertEqual(linked["telegram_user_id"], 778899)
        with self.assertRaises(PartnerUnavailableError):
            onboard_partner(token, 998877, "attacker", self.db_path)

    def test_partner_invite_expires(self):
        partner = self._partner("Expiring Invite")
        issued_at = datetime(2026, 8, 25, tzinfo=timezone.utc)
        token = create_partner_invite(
            partner["id"], self.db_path, ttl_hours=1, now=issued_at
        )
        with self.assertRaises(PartnerUnavailableError):
            onboard_partner(
                token, 778899, "late_partner", self.db_path,
                now=issued_at + timedelta(hours=1, seconds=1),
            )

    def test_matching_filters_service_area_and_status(self):
        housing = self._partner("Housing")
        self._partner("Transfer", ["transfer"])
        paused = self._partner("Paused", status="paused")
        blocked = self._partner("Blocked", status="blocked")
        matches = find_partners_for_case(self._case(), self.db_path)
        self.assertEqual([item["id"] for item in matches], [housing["id"]])
        self.assertNotIn(paused, matches)
        self.assertNotIn(blocked, matches)
        self.assertEqual(
            find_partners_for_case(self._case(location="Kata"), self.db_path),
            [],
        )

    async def test_successful_send_is_marked_sent_with_message_id(self):
        partner = self._partner(user_id=2001)
        sender = AsyncMock(return_value=SimpleNamespace(message_id=501))
        request = await send_case_to_partner(
            self.case_id, partner["id"], sender, self.db_path
        )
        self.assertEqual(request["status"], "sent")
        self.assertEqual(request["telegram_message_id"], 501)
        self.assertIsNotNone(request["sent_at"])
        sender.assert_awaited_once()

    async def test_telegram_error_is_failed_not_sent(self):
        partner = self._partner(user_id=2002)
        sender = AsyncMock(side_effect=RuntimeError("secret transport detail"))
        with self.assertRaises(PartnerTelegramError) as caught:
            await send_case_to_partner(
                self.case_id, partner["id"], sender, self.db_path
            )
        request = get_partner_request(caught.exception.request_id, self.db_path)
        self.assertEqual(request["status"], "failed")
        self.assertNotEqual(request["status"], "sent")
        self.assertEqual(request["error_code"], "telegram_failure")
        self.assertEqual(request["error_message"], "RuntimeError")

    async def test_one_case_can_be_sent_to_two_partners(self):
        first = self._partner("First", user_id=2011)
        second = self._partner("Second", user_id=2012)
        sender = AsyncMock(
            side_effect=[SimpleNamespace(message_id=601), SimpleNamespace(message_id=602)]
        )
        first_request = await send_case_to_partner(
            self.case_id, first["id"], sender, self.db_path
        )
        second_request = await send_case_to_partner(
            self.case_id, second["id"], sender, self.db_path
        )
        self.assertNotEqual(first_request["id"], second_request["id"])
        self.assertNotEqual(first_request["partner_id"], second_request["partner_id"])

    async def test_reply_is_linked_and_classified(self):
        partner = self._partner(user_id=2021)
        sender = AsyncMock(return_value=SimpleNamespace(message_id=701))
        request = await send_case_to_partner(
            self.case_id, partner["id"], sender, self.db_path
        )
        responded = record_partner_reply(
            2021, 701, "Есть вилла за 45 000 THB https://example.com/villa",
            self.db_path,
        )
        self.assertEqual(responded["id"], request["id"])
        self.assertEqual(responded["status"], "responded")
        self.assertIn("45 000 THB", responded["partner_response"])

    async def test_decline_reply_is_declined(self):
        partner = self._partner(user_id=2022)
        sender = AsyncMock(return_value=SimpleNamespace(message_id=702))
        await send_case_to_partner(self.case_id, partner["id"], sender, self.db_path)
        declined = record_partner_reply(
            2022, 702, "Нет вариантов", self.db_path
        )
        self.assertEqual(declined["status"], "declined")

    def test_partner_payload_excludes_client_pii(self):
        connection = get_connection(self.db_path)
        try:
            data = json.loads(
                connection.execute(
                    "SELECT data FROM cases WHERE id = ?", (self.case_id,)
                ).fetchone()[0]
            )
        finally:
            connection.close()
        payload = format_partner_request(
            {"id": self.case_id, "category": "housing", "data": data}
        )
        for private_value in (
            "+79990000000", "1001", "private@example.com"
        ):
            self.assertNotIn(private_value, payload)
        self.assertIn("Rawai", payload)
        self.assertIn("Рядом с пляжем", payload)

    async def test_duplicate_active_send_is_rejected(self):
        partner = self._partner(user_id=2031)
        sender = AsyncMock(return_value=SimpleNamespace(message_id=801))
        await send_case_to_partner(self.case_id, partner["id"], sender, self.db_path)
        with self.assertRaises(DuplicatePartnerRequestError):
            await send_case_to_partner(
                self.case_id, partner["id"], sender, self.db_path
            )
        self.assertEqual(sender.await_count, 1)

    def test_admin_check_denies_normal_user(self):
        self.assertTrue(is_admin(999, 999))
        self.assertFalse(is_admin(999, 1000))
        self.assertFalse(is_admin(None, 999))

    def test_partner_status_management(self):
        partner = self._partner()
        paused = set_partner_status(partner["id"], "paused", self.db_path)
        self.assertEqual(paused["status"], "paused")


class PartnerMigrationTests(unittest.TestCase):
    def test_v4_migration_preserves_old_database_data(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "legacy.db"
            connection = sqlite3.connect(path)
            connection.executescript(
                """
                CREATE TABLE clients (
                    id INTEGER PRIMARY KEY, telegram_id INTEGER UNIQUE NOT NULL
                );
                CREATE TABLE messages (
                    id INTEGER PRIMARY KEY, client_id INTEGER, role TEXT, content TEXT
                );
                CREATE TABLE cases (
                    id INTEGER PRIMARY KEY, client_id INTEGER, title TEXT,
                    description TEXT, status TEXT, created_at TIMESTAMP,
                    updated_at TIMESTAMP
                );
                CREATE TABLE partners (id INTEGER PRIMARY KEY, name TEXT NOT NULL);
                INSERT INTO clients VALUES (1, 777);
                INSERT INTO cases VALUES (
                    1, 1, 'Legacy case', '', 'new', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                );
                INSERT INTO partners VALUES (1, 'Legacy partner');
                """
            )
            connection.commit()
            connection.close()

            init_db(path)
            connection = get_connection(path)
            try:
                case_title = connection.execute(
                    "SELECT title FROM cases WHERE id = 1"
                ).fetchone()[0]
                partner_name = connection.execute(
                    "SELECT name FROM partners WHERE id = 1"
                ).fetchone()[0]
                versions = [
                    row[0] for row in connection.execute(
                        "SELECT version FROM schema_migrations ORDER BY version"
                    )
                ]
                request_table = connection.execute(
                    "SELECT name FROM sqlite_master WHERE name = 'partner_requests'"
                ).fetchone()
            finally:
                connection.close()
            self.assertEqual(case_title, "Legacy case")
            self.assertEqual(partner_name, "Legacy partner")
            self.assertEqual(versions, list(range(1, 15)))
            self.assertIsNotNone(request_table)


if __name__ == "__main__":
    unittest.main()
