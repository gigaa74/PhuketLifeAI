import json
import tempfile
import unittest
from pathlib import Path

from database import get_connection, get_or_create_client, init_db
from partner_handoff import format_client_offer
from partner_network import (
    create_partner,
    get_partner,
    sync_partner_telegram_identity,
)
from scripts.onboard_inna_partner import (
    APPROVED_TERMS,
    PARTNER_PHONE,
    PartnerOnboardingConflict,
    onboard_inna,
)
from scripts.onboard_lera_partner import onboard_lera


class InnaOnboardingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp.name) / "inna.db"
        init_db(self.db_path)
        self.lera, _ = onboard_lera(self.db_path)
        self.other = create_partner(
            "Existing Partner", ["transfer"], telegram_username="existing",
            db_path=self.db_path,
        )

    def tearDown(self):
        self.temp.cleanup()

    def _partner_count(self):
        connection = get_connection(self.db_path)
        try:
            return connection.execute("SELECT COUNT(*) FROM partners").fetchone()[0]
        finally:
            connection.close()

    def test_created_once_without_fake_numeric_identity_or_areas(self):
        partner, created = onboard_inna(self.db_path)
        rerun, created_again = onboard_inna(self.db_path)
        self.assertTrue(created)
        self.assertFalse(created_again)
        self.assertEqual(partner["id"], rerun["id"])
        self.assertEqual(partner["name"], "Инна")
        self.assertEqual(partner["partner_type"], "hybrid")
        self.assertEqual(partner["status"], "active")
        self.assertEqual(partner["telegram_username"], "WGoggins")
        self.assertIsNone(partner["telegram_user_id"])
        self.assertEqual(partner["services"], ["housing"])
        self.assertEqual(partner["areas"], [])
        self.assertEqual(self._partner_count(), 3)

    def test_phone_display_name_context_and_open_questions_are_operational(self):
        partner, _ = onboard_inna(self.db_path)
        self.assertEqual(json.loads(partner["contacts"])["phone"], PARTNER_PHONE)
        notes = json.loads(partner["operational_notes"])
        self.assertEqual(notes["display_name"], "Tranquillo")
        self.assertEqual(notes["approved_direction"], "аренда жилья")
        self.assertIn("районы работы", notes["open_questions"])
        self.assertIn("минимальный бюджет клиента", notes["open_questions"])
        self.assertEqual(
            notes["request_requirements"]["required"],
            ["даты", "бюджет", "район", "наличие транспорта"],
        )
        self.assertEqual(notes["public_profile_url"], "https://t.me/thainvest/8204")
        capability = notes["known_capabilities"][0]
        self.assertEqual(capability["capability"], "продажа недвижимости")
        self.assertIn("не утверждено", capability["approval_status"])

    def test_only_confirmed_revenue_share_terms_are_approved(self):
        partner, _ = onboard_inna(self.db_path)
        terms = partner["approved_terms"]
        self.assertEqual(set(terms), set(APPROVED_TERMS))
        self.assertEqual(terms["partner_commission_share_percent"], "70")
        self.assertEqual(terms["phuket_life_commission_share_percent"], "30")
        self.assertIn("фактически полученная", terms["commission_basis"])
        forbidden = (
            "minimum_commission", "payment_timing", "cancellation",
            "extensions", "repeat_business", "real_estate_sales",
        )
        for key in forbidden:
            self.assertNotIn(key, terms)

    def test_rerun_does_not_overwrite_owner_terms_contacts_or_notes(self):
        partner, _ = onboard_inna(self.db_path)
        connection = get_connection(self.db_path)
        try:
            with connection:
                connection.execute(
                    """UPDATE partner_approved_terms SET term_value='65'
                       WHERE partner_id=? AND term_key='partner_commission_share_percent'""",
                    (partner["id"],),
                )
                connection.execute(
                    """UPDATE partners SET contacts='owner-updated',
                       operational_notes='owner notes', status='paused'
                       WHERE id=?""",
                    (partner["id"],),
                )
        finally:
            connection.close()
        rerun, created = onboard_inna(self.db_path)
        self.assertFalse(created)
        self.assertEqual(
            rerun["approved_terms"]["partner_commission_share_percent"], "65"
        )
        self.assertEqual(rerun["contacts"], "owner-updated")
        self.assertEqual(rerun["operational_notes"], "owner notes")
        self.assertEqual(rerun["status"], "paused")

    def test_username_matching_is_case_insensitive(self):
        connection = get_connection(self.db_path)
        try:
            with connection:
                connection.execute(
                    """INSERT INTO partners(name, telegram_username, status)
                       VALUES('Инна', 'wGoGgInS', 'active')"""
                )
        finally:
            connection.close()
        partner, created = onboard_inna(self.db_path)
        self.assertFalse(created)
        self.assertEqual(partner["telegram_username"], "wGoGgInS")
        self.assertEqual(self._partner_count(), 3)

    def test_name_and_username_conflicts_are_rejected(self):
        conflict_db = Path(self.temp.name) / "conflicts.db"
        init_db(conflict_db)
        create_partner(
            "Someone Else", ["housing"], telegram_username="WGOGGINS",
            db_path=conflict_db,
        )
        with self.assertRaises(PartnerOnboardingConflict):
            onboard_inna(conflict_db)

        name_db = Path(self.temp.name) / "name-conflict.db"
        init_db(name_db)
        create_partner(
            "Инна", ["housing"], telegram_username="different",
            db_path=name_db,
        )
        with self.assertRaises(PartnerOnboardingConflict):
            onboard_inna(name_db)

    def test_bound_identity_cannot_be_hijacked_by_same_username(self):
        partner, _ = onboard_inna(self.db_path)
        linked = sync_partner_telegram_identity(
            88001, "wGoGgInS", self.db_path
        )
        self.assertEqual(linked["id"], partner["id"])
        self.assertIsNone(sync_partner_telegram_identity(
            99002, "WGoggins", self.db_path
        ))
        current = get_partner(partner["id"], self.db_path)
        self.assertEqual(current["telegram_user_id"], 88001)

    def test_lera_and_other_partner_are_unchanged(self):
        connection = get_connection(self.db_path)
        try:
            before = connection.execute(
                "SELECT * FROM partners WHERE id IN (?, ?) ORDER BY id",
                (self.lera["id"], self.other["id"]),
            ).fetchall()
        finally:
            connection.close()
        onboard_inna(self.db_path)
        connection = get_connection(self.db_path)
        try:
            after = connection.execute(
                "SELECT * FROM partners WHERE id IN (?, ?) ORDER BY id",
                (self.lera["id"], self.other["id"]),
            ).fetchall()
        finally:
            connection.close()
        self.assertEqual(after, before)

    def test_no_client_case_and_internal_commission_not_exposed(self):
        client_id = get_or_create_client(555, db_path=self.db_path)
        connection = get_connection(self.db_path)
        try:
            cases_before = connection.execute(
                "SELECT COUNT(*) FROM cases WHERE client_id=?", (client_id,)
            ).fetchone()[0]
        finally:
            connection.close()
        partner, _ = onboard_inna(self.db_path)
        connection = get_connection(self.db_path)
        try:
            cases_after = connection.execute(
                "SELECT COUNT(*) FROM cases WHERE client_id=?", (client_id,)
            ).fetchone()[0]
        finally:
            connection.close()
        self.assertEqual(cases_after, cases_before)
        message = format_client_offer(
            {"offer_description": "Апартаменты доступны"},
            {"category": "housing", "data": {},
             "partner_telegram_username": partner["telegram_username"]},
        )
        self.assertNotIn("70%", message)
        self.assertNotIn("30%", message)
        self.assertNotIn("комисси", message.casefold())
        self.assertNotIn(PARTNER_PHONE, message)
        self.assertNotIn("381628530214", message.replace(" ", ""))


if __name__ == "__main__":
    unittest.main()
