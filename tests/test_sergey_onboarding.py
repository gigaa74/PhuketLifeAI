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
from scripts.onboard_inna_partner import onboard_inna
from scripts.onboard_lera_partner import onboard_lera
from scripts.onboard_sergey_partner import (
    ADDITIONAL_REFERENCE_USERNAME,
    APPROVED_SERVICES,
    PARTNER_USERNAME,
    SYSTEM_SERVICE_CATEGORIES,
    PartnerOnboardingConflict,
    onboard_sergey,
)


class SergeyOnboardingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp.name) / "sergey.db"
        init_db(self.db_path)
        self.lera, _ = onboard_lera(self.db_path)
        self.inna, _ = onboard_inna(self.db_path)
        self.other = create_partner(
            "Existing Partner", ["transfer"],
            telegram_username="existing", db_path=self.db_path,
        )

    def tearDown(self):
        self.temp.cleanup()

    def _partner_count(self):
        connection = get_connection(self.db_path)
        try:
            return connection.execute(
                "SELECT COUNT(*) FROM partners"
            ).fetchone()[0]
        finally:
            connection.close()

    def test_created_once_with_safe_identity_and_approved_services(self):
        partner, created = onboard_sergey(self.db_path)
        rerun, created_again = onboard_sergey(self.db_path)
        self.assertTrue(created)
        self.assertFalse(created_again)
        self.assertEqual(partner["id"], rerun["id"])
        self.assertEqual(partner["name"], "Сергей")
        self.assertEqual(partner["partner_type"], "hybrid")
        self.assertEqual(partner["status"], "active")
        self.assertEqual(partner["telegram_username"], PARTNER_USERNAME)
        self.assertIsNone(partner["telegram_user_id"])
        self.assertEqual(partner["services"], SYSTEM_SERVICE_CATEGORIES)
        self.assertEqual(partner["areas"], ["Phuket"])
        self.assertNotIn("transfer", partner["services"])
        self.assertNotIn("other", partner["services"])
        self.assertEqual(self._partner_count(), 4)

    def test_reference_account_is_contact_only_and_not_primary_identity(self):
        partner, _ = onboard_sergey(self.db_path)
        contacts = json.loads(partner["contacts"])
        self.assertEqual(
            contacts["telegram_reference_username"],
            ADDITIONAL_REFERENCE_USERNAME,
        )
        self.assertEqual(contacts["identity_role"], "reference_only")
        self.assertNotEqual(
            partner["telegram_username"], ADDITIONAL_REFERENCE_USERNAME
        )
        self.assertIsNone(sync_partner_telegram_identity(
            88002, ADDITIONAL_REFERENCE_USERNAME, self.db_path
        ))

    def test_operational_model_context_and_open_questions_are_separate(self):
        partner, _ = onboard_sergey(self.db_path)
        notes = json.loads(partner["operational_notes"])
        self.assertEqual(notes["approved_service_scope"], APPROVED_SERVICES)
        self.assertEqual(notes["service_area"], "Phuket / весь Пхукет")
        self.assertIn("доставка", notes["delivery_model"]["direct"])
        self.assertIn(
            "аренда жилья", notes["delivery_model"]["through_partners"]
        )
        self.assertTrue(
            notes["delivery_model"]["direct_client_contact_by_subcontractors"]
        )
        self.assertIn(
            "размер или формула вознаграждения Phuket Life",
            notes["open_questions"],
        )
        self.assertIn("получает процент", notes["commercial_context"])
        self.assertEqual(partner["approved_terms"], {})

    def test_compliance_exclusions_are_not_approved_capabilities(self):
        partner, _ = onboard_sergey(self.db_path)
        notes = json.loads(partner["operational_notes"])
        exclusions = notes["compliance_exclusions"]
        self.assertIn("обмен рублей или валют", exclusions)
        self.assertIn("иммиграционные вопросы", exclusions)
        self.assertIn("международная доставка в РФ", exclusions)
        approved = " ".join(partner["services"]).casefold()
        for forbidden in (
            "currency", "exchange", "license", "police", "immigration",
            "legal", "international_delivery", "transfer",
        ):
            self.assertNotIn(forbidden, approved)

    def test_rerun_does_not_overwrite_owner_decisions_or_profile(self):
        partner, _ = onboard_sergey(self.db_path)
        connection = get_connection(self.db_path)
        try:
            with connection:
                connection.execute(
                    """INSERT INTO partner_approved_terms
                       (partner_id, term_key, term_value)
                       VALUES (?, 'owner_decision', 'approved later')""",
                    (partner["id"],),
                )
                connection.execute(
                    """UPDATE partners SET contacts='owner contacts',
                       operational_notes='owner notes', services='["excursions"]',
                       areas='["Rawai"]', status='paused' WHERE id=?""",
                    (partner["id"],),
                )
        finally:
            connection.close()
        rerun, created = onboard_sergey(self.db_path)
        self.assertFalse(created)
        self.assertEqual(rerun["approved_terms"], {
            "owner_decision": "approved later"
        })
        self.assertEqual(rerun["contacts"], "owner contacts")
        self.assertEqual(rerun["operational_notes"], "owner notes")
        self.assertEqual(rerun["services"], ["excursions"])
        self.assertEqual(rerun["areas"], ["Rawai"])
        self.assertEqual(rerun["status"], "paused")

    def test_username_matching_is_case_insensitive(self):
        connection = get_connection(self.db_path)
        try:
            with connection:
                connection.execute(
                    """INSERT INTO partners(name, telegram_username, status)
                       VALUES('Сергей', 'ChUdO_OsTrOv_PhUkEt', 'active')"""
                )
        finally:
            connection.close()
        partner, created = onboard_sergey(self.db_path)
        self.assertFalse(created)
        self.assertEqual(partner["telegram_username"], "ChUdO_OsTrOv_PhUkEt")
        self.assertEqual(self._partner_count(), 4)

    def test_name_and_username_conflicts_are_rejected(self):
        username_db = Path(self.temp.name) / "username-conflict.db"
        init_db(username_db)
        create_partner(
            "Someone Else", ["excursions"],
            telegram_username="CHUDO_OSTROV_PHUKET", db_path=username_db,
        )
        with self.assertRaises(PartnerOnboardingConflict):
            onboard_sergey(username_db)

        name_db = Path(self.temp.name) / "name-conflict.db"
        init_db(name_db)
        create_partner(
            "Сергей", ["excursions"], telegram_username="different",
            db_path=name_db,
        )
        with self.assertRaises(PartnerOnboardingConflict):
            onboard_sergey(name_db)

    def test_bound_identity_cannot_be_hijacked_by_same_username(self):
        partner, _ = onboard_sergey(self.db_path)
        linked = sync_partner_telegram_identity(
            88003, "CHUDO_OSTROV_PHUKET", self.db_path
        )
        self.assertEqual(linked["id"], partner["id"])
        self.assertIsNone(sync_partner_telegram_identity(
            99004, PARTNER_USERNAME, self.db_path
        ))
        current = get_partner(partner["id"], self.db_path)
        self.assertEqual(current["telegram_user_id"], 88003)

    def test_lera_inna_and_other_partner_are_unchanged(self):
        connection = get_connection(self.db_path)
        try:
            before = connection.execute(
                "SELECT * FROM partners WHERE id IN (?, ?, ?) ORDER BY id",
                (self.lera["id"], self.inna["id"], self.other["id"]),
            ).fetchall()
        finally:
            connection.close()
        onboard_sergey(self.db_path)
        connection = get_connection(self.db_path)
        try:
            after = connection.execute(
                "SELECT * FROM partners WHERE id IN (?, ?, ?) ORDER BY id",
                (self.lera["id"], self.inna["id"], self.other["id"]),
            ).fetchall()
        finally:
            connection.close()
        self.assertEqual(after, before)

    def test_no_client_case_or_internal_contact_exposure(self):
        client_id = get_or_create_client(555, db_path=self.db_path)
        connection = get_connection(self.db_path)
        try:
            cases_before = connection.execute(
                "SELECT COUNT(*) FROM cases WHERE client_id=?", (client_id,)
            ).fetchone()[0]
        finally:
            connection.close()
        partner, _ = onboard_sergey(self.db_path)
        connection = get_connection(self.db_path)
        try:
            cases_after = connection.execute(
                "SELECT COUNT(*) FROM cases WHERE client_id=?", (client_id,)
            ).fetchone()[0]
        finally:
            connection.close()
        self.assertEqual(cases_after, cases_before)
        message = format_client_offer(
            {"offer_description": "Экскурсия доступна"},
            {"category": "excursions", "data": {},
             "partner_telegram_username": partner["telegram_username"]},
        )
        self.assertNotIn(ADDITIONAL_REFERENCE_USERNAME, message)
        self.assertNotIn("процент", message.casefold())
        self.assertNotIn("комисси", message.casefold())


if __name__ == "__main__":
    unittest.main()
