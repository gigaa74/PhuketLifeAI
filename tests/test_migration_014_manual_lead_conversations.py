import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from database import MIGRATIONS, get_connection, init_db


class Migration014ManualLeadConversationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp.name) / "migration-014.db"

    def tearDown(self):
        self.temp.cleanup()

    def test_existing_manual_lead_is_preserved_and_backfilled(self):
        connection = sqlite3.connect(self.db_path)
        try:
            connection.execute(
                "CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY, applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
            )
            for version, migration in MIGRATIONS:
                if version > 13:
                    break
                with connection:
                    migration(connection)
                    connection.execute(
                        "INSERT INTO schema_migrations(version) VALUES (?)", (version,)
                    )
            metadata = json.dumps({
                "source_user_id": 123456,
                "source_username": "known_client",
                "source_name": "Известный клиент",
            }, ensure_ascii=False)
            with connection:
                connection.execute(
                    """INSERT INTO manual_leads
                       (owner_telegram_id, source_metadata, original_text,
                        normalized_content_hash, classification, categories,
                        extracted_data, status, updated_at)
                       VALUES (90001, ?, 'Ищу жильё', 'fingerprint', 'client',
                               '[\"housing\"]', '{}', 'in_progress',
                               '2026-08-25T10:00:00+00:00')""",
                    (metadata,),
                )
        finally:
            connection.close()

        init_db(self.db_path)
        connection = get_connection(self.db_path)
        try:
            versions = [row[0] for row in connection.execute(
                "SELECT version FROM schema_migrations ORDER BY version"
            )]
            self.assertEqual(versions, list(range(1, 15)))
            lead = connection.execute(
                "SELECT contact_key, contact_username, profile_url, original_text FROM manual_leads"
            ).fetchone()
            self.assertEqual(
                lead,
                ("user:123456", "known_client", "https://t.me/known_client", "Ищу жильё"),
            )
            message = connection.execute(
                "SELECT lead_id, original_text FROM manual_lead_messages"
            ).fetchone()
            self.assertEqual(message, (1, "Ищу жильё"))
            self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])
        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main()
