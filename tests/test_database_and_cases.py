import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import case_engine
from database import get_connection, get_or_create_client, init_db


class DatabaseAndCaseTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test.db"
        init_db(self.db_path)
        self.connection_patch = patch(
            "case_engine.get_connection",
            side_effect=lambda: get_connection(self.db_path),
        )
        self.connection_patch.start()

    def tearDown(self):
        self.connection_patch.stop()
        self.temp_dir.cleanup()

    def test_new_database_has_current_versioned_schema(self):
        connection = get_connection(self.db_path)
        try:
            columns = {
                row[1]
                for row in connection.execute("PRAGMA table_info(cases)")
            }
            versions = [
                row[0]
                for row in connection.execute(
                    "SELECT version FROM schema_migrations ORDER BY version"
                )
            ]
        finally:
            connection.close()

        self.assertTrue(
            {"category", "data", "missing_data", "priority"}.issubset(columns)
        )
        self.assertEqual(versions, [1, 2, 3])

    def test_client_case_creation_update_and_ready_transition(self):
        client_id = get_or_create_client(
            123456,
            username="test_user",
            first_name="Test",
            db_path=self.db_path,
        )
        case_id = case_engine.get_or_create_case(
            client_id,
            "housing",
            "Поиск жилья на Пхукете",
        )
        partial_data = {
            "arrival_date": "1 сентября",
            "people": "2",
        }
        missing = case_engine.get_housing_missing_fields(partial_data)
        case_engine.update_case(
            case_id,
            partial_data,
            missing,
            case_engine.get_case_status(missing),
        )
        partial_case = case_engine.get_case(case_id)
        self.assertEqual(partial_case["status"], "active")
        self.assertEqual(
            partial_case["missing_data"],
            ["departure_date", "budget"],
        )

        complete_data = case_engine.merge_case_data(
            partial_case["data"],
            {"departure_date": "30 сентября", "budget": "100 000 рублей"},
        )
        missing = case_engine.get_housing_missing_fields(complete_data)
        case_engine.update_case(
            case_id,
            complete_data,
            missing,
            case_engine.get_case_status(missing),
        )
        ready_case = case_engine.get_case(case_id)

        self.assertEqual(ready_case["status"], "ready_for_search")
        self.assertEqual(ready_case["missing_data"], [])
        self.assertEqual(ready_case["data"], complete_data)

    def test_migration_preserves_existing_legacy_data(self):
        legacy_path = Path(self.temp_dir.name) / "legacy.db"
        connection = sqlite3.connect(legacy_path)
        connection.executescript(
            """
            CREATE TABLE clients (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER UNIQUE NOT NULL
            );
            CREATE TABLE messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                client_id INTEGER NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL
            );
            CREATE TABLE cases (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                client_id INTEGER NOT NULL,
                title TEXT,
                description TEXT,
                status TEXT DEFAULT 'new',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE partners (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL
            );
            INSERT INTO clients(telegram_id) VALUES (777);
            INSERT INTO cases(client_id, title) VALUES (1, 'Существующий кейс');
            """
        )
        connection.commit()
        connection.close()

        init_db(legacy_path)
        connection = get_connection(legacy_path)
        try:
            title = connection.execute("SELECT title FROM cases").fetchone()[0]
            columns = {
                row[1]
                for row in connection.execute("PRAGMA table_info(cases)")
            }
        finally:
            connection.close()

        self.assertEqual(title, "Существующий кейс")
        self.assertIn("missing_data", columns)


class CaseDataTests(unittest.TestCase):
    def test_merge_keeps_old_values_when_new_values_are_empty(self):
        result = case_engine.merge_case_data(
            {"budget": "80 000 рублей", "people": "2"},
            {"budget": "", "people": "3", "location": None},
        )
        self.assertEqual(result, {"budget": "80 000 рублей", "people": "3"})

    def test_housing_missing_fields_and_status(self):
        missing = case_engine.get_housing_missing_fields(
            {"arrival_date": "1 сентября", "departure_date": "10 сентября"}
        )
        self.assertEqual(missing, ["people", "budget"])
        self.assertEqual(case_engine.get_case_status(missing), "active")
        self.assertEqual(case_engine.get_case_status([]), "ready_for_search")


if __name__ == "__main__":
    unittest.main()
