import json
import tempfile
import unittest
from pathlib import Path

from database import get_connection, init_db
from partner_handoff import format_client_offer
from partner_network import create_partner
from scripts.onboard_lera_partner import APPROVED_TERMS, onboard_lera


class LeraOnboardingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp.name) / "lera.db"
        init_db(self.db_path)
        self.test_partner = create_partner(
            "Test Partner 2", ["housing"], telegram_username="gigaaa74",
            db_path=self.db_path,
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_lera_is_created_once_and_rerun_is_idempotent(self):
        first, created = onboard_lera(self.db_path)
        second, created_again = onboard_lera(self.db_path)
        self.assertTrue(created)
        self.assertFalse(created_again)
        self.assertEqual(first["id"], second["id"])
        connection = get_connection(self.db_path)
        try:
            count = connection.execute(
                "SELECT COUNT(*) FROM partners WHERE lower(name)=lower('Лера')"
            ).fetchone()[0]
        finally:
            connection.close()
        self.assertEqual(count, 1)

    def test_rerun_does_not_overwrite_later_owner_term(self):
        partner, _ = onboard_lera(self.db_path)
        connection = get_connection(self.db_path)
        try:
            with connection:
                connection.execute(
                    """UPDATE partner_approved_terms SET term_value='12'
                       WHERE partner_id=? AND term_key='base_commission_percent'""",
                    (partner["id"],),
                )
        finally:
            connection.close()
        rerun, created = onboard_lera(self.db_path)
        self.assertFalse(created)
        self.assertEqual(rerun["approved_terms"]["base_commission_percent"], "12")

    def test_identity_type_services_and_areas(self):
        partner, _ = onboard_lera(self.db_path)
        self.assertEqual(partner["partner_type"], "hybrid")
        self.assertEqual(partner["status"], "active")
        self.assertEqual(partner["telegram_username"], "lerikaDi")
        self.assertIsNone(partner["telegram_user_id"])
        self.assertEqual(partner["services"], ["housing"])
        self.assertEqual(partner["areas"], ["Karon", "Bang Tao"])

    def test_only_confirmed_terms_are_approved_and_ladder_is_preserved(self):
        partner, _ = onboard_lera(self.db_path)
        terms = partner["approved_terms"]
        self.assertEqual(terms["base_commission_percent"], "10")
        self.assertEqual(terms["commission_ladder_5_successful_deals_percent"], "11")
        self.assertEqual(terms["commission_ladder_10_successful_deals_percent"], "12")
        self.assertEqual(terms["commission_ladder_30_successful_deals_percent"], "14")
        for forbidden in ("payment_method", "cancellation_commission", "crypto_scheme"):
            self.assertNotIn(forbidden, terms)
        self.assertEqual(set(terms), set(APPROVED_TERMS))

    def test_open_questions_are_operational_not_approved(self):
        partner, _ = onboard_lera(self.db_path)
        notes = json.loads(partner["operational_notes"])
        self.assertIn("конкретная crypto payment scheme", notes["open_questions"])
        self.assertIn("не утверждена", notes["known_capabilities"][0])
        self.assertIn("это не live availability", notes["inventory_context"]["warning"])

    def test_internal_commission_is_not_exposed_to_client(self):
        partner, _ = onboard_lera(self.db_path)
        message = format_client_offer(
            {"offer_description": "Апартаменты доступны"},
            {"category": "housing", "data": {},
             "partner_telegram_username": partner["telegram_username"]},
        )
        self.assertNotIn("10%", message)
        self.assertNotIn("комисси", message.casefold())

    def test_existing_test_partner_is_unchanged(self):
        onboard_lera(self.db_path)
        connection = get_connection(self.db_path)
        try:
            row = connection.execute(
                """SELECT name, telegram_username, telegram_user_id
                   FROM partners WHERE id=?""",
                (self.test_partner["id"],),
            ).fetchone()
        finally:
            connection.close()
        self.assertEqual(row, ("Test Partner 2", "gigaaa74", None))


if __name__ == "__main__":
    unittest.main()
