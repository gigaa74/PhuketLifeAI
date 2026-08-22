import sqlite3
import tempfile
import unittest
from pathlib import Path

from admin_ui import format_commercial_proposal_card, format_partner_card
from database import get_connection, init_db
from partner_authority import (
    ALLOW,
    REQUIRE_OWNER_APPROVAL,
    authority_for,
    create_pending_proposal,
    decide_proposal,
    get_approved_terms,
    guard_partner_response,
    list_pending_proposals,
)
from partner_handoff import format_client_offer
from partner_network import create_partner, get_partner


class PartnerOperatingSystemTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp.name) / "std002.db"
        init_db(self.db_path)
        self.partner = create_partner(
            "Hybrid Test", ["housing"], areas=["Rawai"], status="active",
            partner_type="hybrid", operational_notes="Проверять даты",
            db_path=self.db_path,
        )

    def tearDown(self):
        self.temp.cleanup()

    def proposal(self, text="Теперь комиссия будет 15%."):
        return create_pending_proposal(
            self.partner["id"], text, source="telegram_partner_reply",
            source_message_id=77, db_path=self.db_path,
        )

    def test_operational_action_allowed(self):
        self.assertEqual(authority_for("ask_availability"), ALLOW)

    def test_approved_terms_can_be_read_for_sending(self):
        proposal = self.proposal()
        decide_proposal(proposal["id"], True, owner_id=1, db_path=self.db_path)
        self.assertEqual(get_approved_terms(self.partner["id"], self.db_path)["commission"], "15%")
        self.assertEqual(authority_for("send_approved_terms"), ALLOW)

    def test_ai_cannot_change_or_approve_commission(self):
        self.assertEqual(authority_for("change_commission"), REQUIRE_OWNER_APPROVAL)
        self.assertEqual(authority_for("approve_commission"), REQUIRE_OWNER_APPROVAL)

    def test_ai_cannot_approve_discount(self):
        self.assertEqual(authority_for("approve_discount"), REQUIRE_OWNER_APPROVAL)

    def test_new_commission_is_pending_and_approved_unchanged(self):
        proposal = self.proposal()
        self.assertEqual(proposal["status"], "pending_owner_approval")
        self.assertEqual(get_approved_terms(self.partner["id"], self.db_path), {})

    def test_owner_approve_updates_terms(self):
        proposal = self.proposal()
        result = decide_proposal(proposal["id"], True, owner_id=42, db_path=self.db_path)
        self.assertEqual(result["status"], "approved")
        self.assertEqual(get_approved_terms(self.partner["id"], self.db_path)["commission"], "15%")

    def test_owner_reject_keeps_terms(self):
        proposal = self.proposal()
        result = decide_proposal(proposal["id"], False, owner_id=42, db_path=self.db_path)
        self.assertEqual(result["status"], "rejected")
        self.assertEqual(get_approved_terms(self.partner["id"], self.db_path), {})

    def test_decision_requires_owner_identity(self):
        with self.assertRaises(PermissionError):
            decide_proposal(self.proposal()["id"], True, db_path=self.db_path)

    def test_payment_method_requires_approval(self):
        proposal = self.proposal("Работаем только по предоплате в USDT.")
        self.assertIn("payment_method", proposal["proposed_changes"])
        self.assertEqual(authority_for("change_payment_method"), REQUIRE_OWNER_APPROVAL)

    def test_exclusivity_requires_approval(self):
        proposal = self.proposal("Работаем только через вас, эксклюзивно.")
        self.assertIn("exclusivity", proposal["proposed_changes"])
        self.assertEqual(authority_for("accept_exclusivity"), REQUIRE_OWNER_APPROVAL)

    def test_accidental_llm_agreement_is_replaced_and_cannot_mutate(self):
        self.proposal()
        safe = guard_partner_response("Хорошо, согласны. Договорились.", True)
        self.assertNotIn("согласны", safe.casefold())
        self.assertEqual(get_approved_terms(self.partner["id"], self.db_path), {})

    def test_all_partner_types_work(self):
        for kind in ("service_provider", "b2b_channel"):
            partner = create_partner(kind, ["other"], partner_type=kind, db_path=self.db_path)
            self.assertEqual(partner["partner_type"], kind)
        self.assertEqual(self.partner["partner_type"], "hybrid")

    def test_admin_notification_is_russian_and_contextual(self):
        text = format_commercial_proposal_card(self.proposal(), get_partner(self.partner["id"], self.db_path))
        self.assertIn("Партнёр предложил новые коммерческие условия", text)
        self.assertIn("Hybrid Test", text)
        self.assertIn("15%", text)

    def test_duplicate_pending_proposal_is_not_duplicated(self):
        first = self.proposal()
        second = self.proposal()
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(len(list_pending_proposals(self.partner["id"], self.db_path)), 1)

    def test_client_output_never_exposes_internal_commission(self):
        text = format_client_offer(
            {"offer_description": "Вилла доступна. Внутренняя комиссия 15%.",
             "price_text": "50 000 THB"},
            {"category": "housing", "data": {"location": "Rawai"}},
        )
        self.assertNotIn("15%", text)
        self.assertNotIn("комиссия", text.casefold())

    def test_source_message_timestamp_and_audit_are_traceable(self):
        proposal = self.proposal()
        self.assertEqual(proposal["source_message_id"], 77)
        self.assertEqual(proposal["source_message"], "Теперь комиссия будет 15%.")
        self.assertTrue(proposal["created_at"])
        connection = get_connection(self.db_path)
        try:
            audit = connection.execute(
                "SELECT action, actor_type FROM partner_commercial_audit WHERE proposal_id=?",
                (proposal["id"],),
            ).fetchone()
        finally:
            connection.close()
        self.assertEqual(audit, ("proposal_recorded", "partner"))

    def test_profile_keeps_internal_fields_but_main_card_is_concise(self):
        self.proposal()
        partner = get_partner(self.partner["id"], self.db_path)
        for field in ("partner_type", "approved_terms", "pending_terms",
                      "allowed_actions", "operational_notes"):
            self.assertIn(field, partner)
        card = format_partner_card(partner)
        self.assertIn("Формат: Гибридный партнёр", card)
        self.assertIn("Ожидают решения: 1", card)
        for hidden in ("Утверждённые условия:", "Разрешённые действия:",
                       "Операционные заметки:"):
            self.assertNotIn(hidden, card)

    def test_migration_is_non_destructive_for_legacy_partner(self):
        legacy_path = Path(self.temp.name) / "legacy.db"
        connection = sqlite3.connect(legacy_path)
        connection.execute("CREATE TABLE partners(id INTEGER PRIMARY KEY, name TEXT NOT NULL)")
        connection.execute("INSERT INTO partners VALUES(9, 'Legacy')")
        connection.commit()
        connection.close()
        init_db(legacy_path)
        connection = sqlite3.connect(legacy_path)
        try:
            self.assertEqual(connection.execute("SELECT name FROM partners WHERE id=9").fetchone()[0], "Legacy")
            self.assertIsNotNone(connection.execute(
                "SELECT name FROM sqlite_master WHERE name='partner_term_proposals'"
            ).fetchone())
        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main()
