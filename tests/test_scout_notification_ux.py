import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

from database import MIGRATIONS, get_connection, init_db
from scout_bot import _format_owner_notification, process_scout_observation
from scout_candidates import list_scout_candidates
from scout_detector import CATEGORY_PATTERNS, classify_scout_message
from scout_labels import CATEGORY_LABELS_RU


class ScoutNotificationUxTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp.name) / "notification.db"
        init_db(self.db_path)

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def observation(text, message_id=1):
        return {
            "source_chat_id": -100500,
            "source_chat_title": "Phuket Services",
            "source_message_id": message_id,
            "source_user_id": 101,
            "source_username": "provider",
            "original_text": text,
        }

    async def test_single_category_is_russian_without_numeric_confidence(self):
        notifier = AsyncMock()
        result = await process_scout_observation(
            "partner", {-100500},
            self.observation("Сдаю апартаменты в Кароне от собственника"),
            77, notifier, self.db_path,
        )
        text = notifier.await_args.kwargs["text"]
        self.assertEqual(result["candidate"]["detected_categories"], ["housing"])
        self.assertIn("Тип: партнёр", text)
        self.assertIn("Основная категория: жильё", text)
        self.assertIn("Найденные категории: жильё", text)
        self.assertIn("Сила сигнала: высокая", text)
        self.assertNotIn("Уверенность:", text)

    async def test_multiple_categories_are_saved_without_weaker_signal(self):
        notifier = AsyncMock()
        result = await process_scout_observation(
            "partner", {-100500}, self.observation(
                "Предлагаем аренду автомобилей и трансфер из аэропорта"
            ), 77, notifier, self.db_path,
        )
        candidate = result["candidate"]
        self.assertEqual(
            candidate["detected_categories"], ["car_rental", "transfer"]
        )
        self.assertEqual(candidate["confidence"], 0.9)
        text = notifier.await_args.kwargs["text"]
        self.assertIn(
            "Найденные категории: аренда автомобилей, трансфер", text
        )
        self.assertIn("Сила сигнала: высокая", text)
        self.assertNotIn("Уверенность", text)

    def test_legacy_notification_falls_back_to_primary_category(self):
        text = _format_owner_notification({
            "scout_type": "client", "source_chat_id": -1,
            "source_chat_title": "Old Source", "source_user_id": 202,
            "source_username": None, "original_text": "Нужен трансфер",
            "detected_category": "transfer", "confidence": 0.8,
            "detection_reasons": ["Старое объяснение"],
        })
        self.assertIn("Найденные категории: трансфер", text)
        self.assertNotIn("0.8", text)

    def test_internal_keys_stay_stable(self):
        expected = {
            "housing", "car_rental", "bike_rental", "transfer",
            "excursions", "boats", "fishing", "food", "wellness",
            "medical", "legal_visa", "relocation",
        }
        self.assertEqual(set(CATEGORY_PATTERNS), expected)
        self.assertEqual(set(CATEGORY_LABELS_RU), expected)

    async def test_observe_only_contract_is_unchanged(self):
        notifier = AsyncMock()
        result = await process_scout_observation(
            "client", {-100500},
            self.observation("Нужна аренда автомобиля на неделю"),
            77, notifier, self.db_path,
        )
        self.assertFalse(result["outreach_performed"])
        self.assertEqual(result["candidate"]["outreach_status"], "not_contacted")
        notifier.assert_awaited_once()


class ScoutDetectedCategoriesMigrationTests(unittest.TestCase):
    def test_v8_backfills_and_v9_preserves_existing_candidate(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "v7.db"
            connection = sqlite3.connect(path)
            connection.execute(
                """CREATE TABLE schema_migrations (
                   version INTEGER PRIMARY KEY,
                   applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"""
            )
            for version, migration in MIGRATIONS:
                if version > 7:
                    continue
                with connection:
                    migration(connection)
                    connection.execute(
                        "INSERT INTO schema_migrations(version) VALUES (?)", (version,)
                    )
            with connection:
                connection.execute(
                    """INSERT INTO scout_candidates
                       (scout_type, source_chat_id, source_message_id, original_text,
                        detected_category, confidence, detection_reasons)
                       VALUES ('partner', -10, 55, 'Сдаю виллу',
                               'housing', 0.8, '["legacy reason"]')"""
                )
            connection.close()

            init_db(path)
            candidate = list_scout_candidates(db_path=path)[0]
            self.assertEqual(candidate["original_text"], "Сдаю виллу")
            self.assertEqual(candidate["confidence"], 0.8)
            self.assertEqual(candidate["detected_categories"], ["housing"])
            connection = get_connection(path)
            try:
                columns = {
                    row[1] for row in connection.execute(
                        "PRAGMA table_info(partner_applications)"
                    )
                }
                raw = connection.execute(
                    "SELECT detected_categories FROM scout_candidates WHERE id=1"
                ).fetchone()[0]
                versions = [row[0] for row in connection.execute(
                    "SELECT version FROM schema_migrations ORDER BY version"
                )]
            finally:
                connection.close()
            self.assertIn("telegram_user_id", columns)
            self.assertEqual(json.loads(raw), ["housing"])
            self.assertEqual(versions, list(range(1, 11)))


if __name__ == "__main__":
    unittest.main()
