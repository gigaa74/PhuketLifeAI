import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from database import MIGRATIONS, get_connection, init_db


class PartnerApplicationsMigrationTests(unittest.TestCase):
    def test_v8_production_like_data_is_preserved_by_v9(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "production-like-v8.db"
            connection = sqlite3.connect(path)
            connection.execute(
                """CREATE TABLE schema_migrations (
                   version INTEGER PRIMARY KEY,
                   applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"""
            )
            for version, migration in MIGRATIONS:
                if version > 8:
                    continue
                with connection:
                    migration(connection)
                    connection.execute(
                        "INSERT INTO schema_migrations(version) VALUES (?)", (version,)
                    )
            with connection:
                client_id = connection.execute(
                    "INSERT INTO clients(telegram_id, username) VALUES(101, 'client')"
                ).lastrowid
                case_id = connection.execute(
                    """INSERT INTO cases(client_id, title, status, category, data)
                       VALUES (?, 'Existing case', 'in_progress', 'housing', '{}')""",
                    (client_id,),
                ).lastrowid
                partner_id = connection.execute(
                    """INSERT INTO partners(name, status, services, telegram_user_id,
                       telegram_username, partner_type, allowed_actions)
                       VALUES('Existing Partner', 'active', '["housing"]', 202,
                              'partner', 'hybrid', '[]')"""
                ).lastrowid
                connection.execute(
                    """INSERT INTO partner_approved_terms
                       (partner_id, term_key, term_value, approved_by)
                       VALUES (?, 'commission', '10%', 1)""",
                    (partner_id,),
                )
                candidate_id = connection.execute(
                    """INSERT INTO scout_candidates
                       (scout_type, source_chat_id, source_message_id, original_text,
                        detected_category, detected_categories, confidence,
                        detection_reasons)
                       VALUES ('partner', -1, 5, 'Existing candidate', 'housing',
                               '["housing"]', 0.8, '[]')"""
                ).lastrowid
            connection.close()

            init_db(path)

            connection = get_connection(path)
            try:
                partner = connection.execute(
                    """SELECT name, status, telegram_user_id, telegram_username
                       FROM partners WHERE id=?""", (partner_id,)
                ).fetchone()
                term = connection.execute(
                    """SELECT term_key, term_value FROM partner_approved_terms
                       WHERE partner_id=?""", (partner_id,)
                ).fetchone()
                case = connection.execute(
                    "SELECT title, status, category, data FROM cases WHERE id=?",
                    (case_id,),
                ).fetchone()
                candidate = connection.execute(
                    """SELECT original_text, detected_category, detected_categories,
                       confidence FROM scout_candidates WHERE id=?""",
                    (candidate_id,),
                ).fetchone()
                applications_table = connection.execute(
                    """SELECT name FROM sqlite_master
                       WHERE type='table' AND name='partner_applications'"""
                ).fetchone()
                versions = [row[0] for row in connection.execute(
                    "SELECT version FROM schema_migrations ORDER BY version"
                )]
            finally:
                connection.close()

            self.assertEqual(partner, (
                "Existing Partner", "active", 202, "partner"
            ))
            self.assertEqual(term, ("commission", "10%"))
            self.assertEqual(case, (
                "Existing case", "in_progress", "housing", "{}"
            ))
            self.assertEqual(candidate[:2], (
                "Existing candidate", "housing"
            ))
            self.assertEqual(json.loads(candidate[2]), ["housing"])
            self.assertEqual(candidate[3], 0.8)
            self.assertIsNotNone(applications_table)
            self.assertEqual(versions, list(range(1, 11)))


if __name__ == "__main__":
    unittest.main()
