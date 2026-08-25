import asyncio
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from database import get_connection, init_db
from partner_handoff import (
    DeterministicOfferExtractor,
    DuplicateOfferSendError,
    OfferHandoffError,
    OfferTelegramError,
    create_offer_from_partner_response,
    format_client_offer,
    get_offer,
    reject_offer,
    send_offer_to_client,
    validate_partner_offer,
)
from partner_network import create_partner


class PartnerHandoffTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "handoff.db"
        init_db(self.db_path)
        connection = get_connection(self.db_path)
        try:
            with connection:
                self.client_id = connection.execute(
                    "INSERT INTO clients(telegram_id, username) VALUES (9001, 'private_client')"
                ).lastrowid
                self.case_id = connection.execute(
                    """
                    INSERT INTO cases
                        (client_id, title, category, data, missing_data, status)
                    VALUES (?, 'Rawai housing', 'housing', ?, '[]', 'results_presented')
                    """,
                    (
                        self.client_id,
                        json.dumps(
                            {"location": "Rawai", "budget": "100 000 RUB"},
                            ensure_ascii=False,
                        ),
                    ),
                ).lastrowid
        finally:
            connection.close()
        self.partner = create_partner(
            "Rawai Homes", ["housing"], status="active",
            commission_notes="internal 10%", notes="private partner note",
            db_path=self.db_path,
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def _enable_legacy_auto_flag(self):
        connection = get_connection(self.db_path)
        try:
            with connection:
                connection.execute(
                    "UPDATE partners SET auto_handoff_enabled=1 WHERE id=?",
                    (self.partner["id"],),
                )
        finally:
            connection.close()

    def _request(self, response, status="responded", partner_id=None):
        connection = get_connection(self.db_path)
        try:
            with connection:
                return connection.execute(
                    """
                    INSERT INTO partner_requests
                        (case_id, partner_id, service_category, status,
                         request_payload, partner_response, responded_at)
                    VALUES (?, ?, 'housing', ?, 'safe request', ?, CURRENT_TIMESTAMP)
                    """,
                    (self.case_id, partner_id or self.partner["id"], status, response),
                ).lastrowid
        finally:
            connection.close()

    def _case(self):
        return {
            "id": self.case_id,
            "client_id": self.client_id,
            "category": "housing",
            "status": "results_presented",
            "data": {"location": "Rawai", "budget": "100 000 RUB"},
        }

    def _valid_response(self):
        return "Есть апартаменты в Rawai за 45 000 THB.\nhttps://example.com/test"

    def test_responded_request_creates_offer_and_preserves_raw(self):
        raw = self._valid_response()
        offer = create_offer_from_partner_response(
            self._request(raw), "review", db_path=self.db_path
        )
        self.assertEqual(offer["raw_partner_response"], raw)
        self.assertEqual(offer["url"], "https://example.com/test")
        self.assertEqual(offer["price_text"], "45 000 THB")
        self.assertEqual(offer["currency"], "THB")

    def test_declined_request_does_not_create_offer(self):
        request_id = self._request("Нет вариантов", status="declined")
        self.assertIsNone(
            create_offer_from_partner_response(request_id, db_path=self.db_path)
        )
        connection = get_connection(self.db_path)
        try:
            count = connection.execute("SELECT COUNT(*) FROM partner_offers").fetchone()[0]
        finally:
            connection.close()
        self.assertEqual(count, 0)

    def test_decline_text_does_not_create_offer_even_if_status_is_responded(self):
        request_id = self._request("Не могу помочь", status="responded")
        self.assertIsNone(
            create_offer_from_partner_response(request_id, db_path=self.db_path)
        )

    def test_extractor_error_is_preserved_for_review(self):
        class FailingExtractor:
            def extract(self, raw_response):
                raise RuntimeError("parser failure")

        raw = self._valid_response()
        offer = create_offer_from_partner_response(
            self._request(raw), "hybrid", FailingExtractor(), self.db_path
        )
        self.assertEqual(offer["raw_partner_response"], raw)
        self.assertEqual(offer["handoff_decision"], "review_required")
        self.assertIn("parser_error", offer["validation_reasons"])

    def test_extractor_never_invents_url_price_or_currency(self):
        extracted = DeterministicOfferExtractor().extract(
            "Есть хорошие апартаменты рядом с пляжем"
        )
        self.assertIsNone(extracted["url"])
        self.assertIsNone(extracted["price_text"])
        self.assertIsNone(extracted["currency"])

    def test_untrusted_partner_requires_review(self):
        offer = create_offer_from_partner_response(
            self._request(self._valid_response()), "hybrid", db_path=self.db_path
        )
        self.assertEqual(offer["handoff_decision"], "review_required")
        self.assertIn("partner_auto_handoff_disabled", offer["validation_reasons"])

    def test_global_review_overrides_trusted_partner(self):
        self._enable_legacy_auto_flag()
        offer = create_offer_from_partner_response(
            self._request(self._valid_response()), "review", db_path=self.db_path
        )
        self.assertEqual(offer["handoff_decision"], "review_required")
        self.assertIn("global_review_mode", offer["validation_reasons"])

    def test_trusted_partner_hybrid_still_requires_owner_approval(self):
        self._enable_legacy_auto_flag()
        offer = create_offer_from_partner_response(
            self._request(self._valid_response()), "hybrid", db_path=self.db_path
        )
        self.assertEqual(offer["handoff_decision"], "review_required")
        self.assertEqual(offer["status"], "needs_review")
        self.assertIn("owner_approval_required", offer["validation_reasons"])

    def test_ambiguous_multi_option_requires_review(self):
        self._enable_legacy_auto_flag()
        raw = (
            "Вариант 1: 40 000 THB https://example.com/one\n"
            "Вариант 2: 50 000 THB https://example.com/two"
        )
        offer = create_offer_from_partner_response(
            self._request(raw), "hybrid", db_path=self.db_path
        )
        self.assertEqual(offer["handoff_decision"], "review_required")
        self.assertIn("multiple_options_require_review", offer["validation_reasons"])

    def test_location_conflict_and_partner_contact_require_review(self):
        self._enable_legacy_auto_flag()
        raw = "Вариант в Kata 45 000 THB, пишите @partner_private"
        offer = create_offer_from_partner_response(
            self._request(raw), "hybrid", db_path=self.db_path
        )
        self.assertEqual(offer["handoff_decision"], "review_required")
        self.assertIn("location_conflict", offer["validation_reasons"])
        self.assertIn("partner_contact_detected", offer["validation_reasons"])
        message = format_client_offer(offer, self._case())
        self.assertNotIn("@partner_private", message)

    def test_budget_currency_is_not_compared_without_fx(self):
        extracted = DeterministicOfferExtractor().extract(self._valid_response())
        offer = {
            **extracted,
            "raw_partner_response": self._valid_response(),
            "telegram_metadata": None,
        }
        partner = {**self.partner, "auto_handoff_enabled": 1}
        result = validate_partner_offer(
            offer, self._case(), partner, "hybrid", {"status": "responded"}, extracted
        )
        self.assertEqual(result["decision"], "review_required")
        self.assertIn("owner_approval_required", result["reasons"])
        self.assertFalse(any("budget" in reason for reason in result["reasons"]))

    def test_same_currency_does_not_generate_in_budget_claim(self):
        offer = {
            "price_text": "120 000 RUB",
            "currency": "RUB",
            "url": "https://example.com/test",
            "offer_description": "Апартаменты 120 000 RUB",
        }
        message = format_client_offer(offer, self._case())
        self.assertNotIn("в бюджете", message.casefold())
        self.assertNotIn("дороже бюджета", message.casefold())

    def test_client_formatter_excludes_internal_data(self):
        offer = create_offer_from_partner_response(
            self._request(self._valid_response()), "review", db_path=self.db_path
        )
        message = format_client_offer(offer, self._case())
        for forbidden in (
            "partner_id", "partner_request_id", "internal 10%",
            "private partner note", "private_client", "9001",
            "validation", "needs_review",
        ):
            self.assertNotIn(forbidden, message)
        self.assertIn("45 000 THB", message)

    async def test_successful_send_sets_sent_and_duplicate_is_blocked(self):
        offer = create_offer_from_partner_response(
            self._request(self._valid_response()), "review", db_path=self.db_path
        )
        sender = AsyncMock(return_value=SimpleNamespace(message_id=555))
        sent = await send_offer_to_client(
            offer["id"], sender, manual_approval=True, db_path=self.db_path
        )
        self.assertEqual(sent["status"], "sent_to_client")
        self.assertEqual(sent["client_telegram_message_id"], 555)
        with self.assertRaises(DuplicateOfferSendError):
            await send_offer_to_client(
                offer["id"], sender, manual_approval=True, db_path=self.db_path
            )
        self.assertEqual(sender.await_count, 1)

    async def test_telegram_failure_does_not_mark_sent(self):
        offer = create_offer_from_partner_response(
            self._request(self._valid_response()), "review", db_path=self.db_path
        )
        with self.assertRaises(OfferTelegramError):
            await send_offer_to_client(
                offer["id"], AsyncMock(side_effect=RuntimeError("private")),
                manual_approval=True, db_path=self.db_path,
            )
        failed = get_offer(offer["id"], self.db_path)
        self.assertNotEqual(failed["status"], "sent_to_client")
        self.assertEqual(failed["error_message"], "RuntimeError")

    async def test_concurrent_send_is_claimed_only_once(self):
        offer = create_offer_from_partner_response(
            self._request(self._valid_response()), "review", db_path=self.db_path
        )
        entered = asyncio.Event()
        release = asyncio.Event()

        async def sender(**kwargs):
            entered.set()
            await release.wait()
            return SimpleNamespace(message_id=556)

        first = asyncio.create_task(send_offer_to_client(
            offer["id"], sender, manual_approval=True, db_path=self.db_path
        ))
        await entered.wait()
        with self.assertRaises(DuplicateOfferSendError):
            await send_offer_to_client(
                offer["id"], sender, manual_approval=True, db_path=self.db_path
            )
        with self.assertRaises(OfferHandoffError):
            reject_offer(offer["id"], self.db_path)
        release.set()
        sent = await first
        self.assertEqual(sent["status"], "sent_to_client")

    def test_two_partners_create_independent_offers(self):
        second = create_partner(
            "Second", ["housing"], status="active", db_path=self.db_path
        )
        first_offer = create_offer_from_partner_response(
            self._request(self._valid_response()), db_path=self.db_path
        )
        second_offer = create_offer_from_partner_response(
            self._request("Вилла 50 000 THB https://example.com/two", partner_id=second["id"]),
            db_path=self.db_path,
        )
        self.assertNotEqual(first_offer["id"], second_offer["id"])
        self.assertNotEqual(first_offer["partner_id"], second_offer["partner_id"])


class PartnerHandoffMigrationTests(unittest.TestCase):
    def test_v5_preserves_sprint4_partner_and_request(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "v4.db"
            connection = sqlite3.connect(path)
            connection.executescript("""
                CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY);
                INSERT INTO schema_migrations VALUES (1),(2),(3),(4);
                CREATE TABLE clients(id INTEGER PRIMARY KEY, telegram_id INTEGER);
                CREATE TABLE cases(
                    id INTEGER PRIMARY KEY, client_id INTEGER, category TEXT,
                    data TEXT, status TEXT
                );
                CREATE TABLE partners(
                    id INTEGER PRIMARY KEY, name TEXT, status TEXT,
                    services TEXT, updated_at TEXT
                );
                CREATE TABLE partner_requests(
                    id INTEGER PRIMARY KEY, case_id INTEGER, partner_id INTEGER,
                    service_category TEXT, status TEXT, request_payload TEXT,
                    partner_response TEXT
                );
                INSERT INTO clients VALUES (1, 777);
                INSERT INTO cases VALUES (5, 1, 'housing', '{}', 'results_presented');
                INSERT INTO partners VALUES (2, 'Test Partner 2', 'active', '["housing"]', 'old');
                INSERT INTO partner_requests VALUES (9, 5, 2, 'housing', 'responded', 'payload', 'raw');
            """)
            connection.commit()
            connection.close()
            init_db(path)
            connection = get_connection(path)
            try:
                partner = connection.execute(
                    "SELECT name, auto_handoff_enabled FROM partners WHERE id = 2"
                ).fetchone()
                request = connection.execute(
                    "SELECT partner_response FROM partner_requests WHERE id = 9"
                ).fetchone()
                versions = [row[0] for row in connection.execute(
                    "SELECT version FROM schema_migrations ORDER BY version"
                )]
            finally:
                connection.close()
            self.assertEqual(partner, ("Test Partner 2", 0))
            self.assertEqual(request[0], "raw")
            self.assertEqual(versions, list(range(1, 14)))


if __name__ == "__main__":
    unittest.main()
