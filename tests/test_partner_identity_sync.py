import sqlite3
import tempfile
import unittest
from pathlib import Path

from database import get_connection, init_db
from partner_handoff import format_client_offer
from partner_network import (
    create_partner,
    get_partner,
    sync_partner_telegram_identity,
)


class PartnerIdentitySyncTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp.name) / "identity.db"
        init_db(self.db_path)
        self.partner = create_partner(
            "Lera Example", ["housing"], partner_type="hybrid",
            telegram_username="@lerikaDi", db_path=self.db_path,
        )

    def tearDown(self):
        self.temp.cleanup()

    def count_partners(self):
        connection = get_connection(self.db_path)
        try:
            return connection.execute("SELECT COUNT(*) FROM partners").fetchone()[0]
        finally:
            connection.close()

    def audit_count(self):
        connection = get_connection(self.db_path)
        try:
            return connection.execute(
                "SELECT COUNT(*) FROM partner_commercial_audit WHERE partner_id=?",
                (self.partner["id"],),
            ).fetchone()[0]
        finally:
            connection.close()

    def test_partner_initially_exists_only_with_username(self):
        self.assertIsNone(self.partner["telegram_user_id"])
        self.assertEqual(self.partner["telegram_username"], "lerikaDi")

    def test_first_interaction_saves_id_and_links_existing_without_duplicate(self):
        linked = sync_partner_telegram_identity(
            70001, "lerikaDi", db_path=self.db_path
        )
        self.assertEqual(linked["id"], self.partner["id"])
        self.assertEqual(linked["telegram_user_id"], 70001)
        self.assertEqual(self.count_partners(), 1)

    def test_same_id_and_username_leaves_record_unchanged(self):
        linked = sync_partner_telegram_identity(70001, "lerikaDi", self.db_path)
        audit_before = self.audit_count()
        same = sync_partner_telegram_identity(70001, "lerikaDi", self.db_path)
        self.assertEqual(same["id"], linked["id"])
        self.assertEqual(self.audit_count(), audit_before)

    def test_same_id_changed_username_updates_without_duplicate(self):
        sync_partner_telegram_identity(70001, "lerikaDi", self.db_path)
        updated = sync_partner_telegram_identity(70001, "leraNew", self.db_path)
        self.assertEqual(updated["id"], self.partner["id"])
        self.assertEqual(updated["telegram_username"], "leraNew")
        self.assertEqual(self.count_partners(), 1)

    def test_old_username_from_other_id_cannot_hijack_bound_partner(self):
        sync_partner_telegram_identity(70001, "lerikaDi", self.db_path)
        self.assertIsNone(sync_partner_telegram_identity(
            99999, "lerikaDi", self.db_path
        ))
        current = get_partner(self.partner["id"], self.db_path)
        self.assertEqual(current["telegram_user_id"], 70001)

    def test_username_none_preserves_id_and_removes_stale_contact(self):
        sync_partner_telegram_identity(70001, "lerikaDi", self.db_path)
        updated = sync_partner_telegram_identity(70001, None, self.db_path)
        self.assertEqual(updated["telegram_user_id"], 70001)
        self.assertIsNone(updated["telegram_username"])
        text = format_client_offer({}, {
            "category": "housing", "data": {},
            "partner_telegram_username": updated["telegram_username"],
        })
        self.assertNotIn("@lerikaDi", text)

    def test_current_username_is_exposed_to_client(self):
        sync_partner_telegram_identity(70001, "lerikaDi", self.db_path)
        current = sync_partner_telegram_identity(70001, "leraCurrent", self.db_path)
        text = format_client_offer({}, {
            "category": "housing", "data": {},
            "partner_telegram_username": current["telegram_username"],
        })
        self.assertIn("@leraCurrent", text)

    def test_lookup_prioritizes_user_id_over_username(self):
        first = sync_partner_telegram_identity(70001, "lerikaDi", self.db_path)
        second = create_partner(
            "Other", ["transfer"], telegram_username="otherName",
            db_path=self.db_path,
        )
        resolved = sync_partner_telegram_identity(70001, "otherName", self.db_path)
        self.assertEqual(resolved["id"], first["id"])
        self.assertNotEqual(resolved["id"], second["id"])
        self.assertEqual(resolved["telegram_username"], "otherName")

    def test_username_change_is_audited(self):
        sync_partner_telegram_identity(70001, "lerikaDi", self.db_path)
        sync_partner_telegram_identity(70001, "leraNew", self.db_path)
        connection = get_connection(self.db_path)
        try:
            action, details = connection.execute(
                """SELECT action, details FROM partner_commercial_audit
                   WHERE partner_id=? ORDER BY id DESC LIMIT 1""",
                (self.partner["id"],),
            ).fetchone()
        finally:
            connection.close()
        self.assertEqual(action, "telegram_username_updated")
        self.assertIn("leraNew", details)

    def test_migration_preserves_legacy_identity(self):
        legacy = Path(self.temp.name) / "legacy.db"
        connection = sqlite3.connect(legacy)
        connection.execute(
            "CREATE TABLE partners(id INTEGER PRIMARY KEY, name TEXT NOT NULL, telegram_username TEXT)"
        )
        connection.execute("INSERT INTO partners VALUES(5, 'Legacy', 'legacyName')")
        connection.commit()
        connection.close()
        init_db(legacy)
        connection = sqlite3.connect(legacy)
        try:
            row = connection.execute(
                "SELECT name, telegram_username, telegram_user_id FROM partners WHERE id=5"
            ).fetchone()
        finally:
            connection.close()
        self.assertEqual(row, ("Legacy", "legacyName", None))


if __name__ == "__main__":
    unittest.main()
