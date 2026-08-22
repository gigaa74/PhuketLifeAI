import sqlite3
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone
from io import StringIO
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import scout_bot
from telegram import Chat, Message, Update, User
from config import ConfigurationError
from database import get_connection, init_db
from scout_bot import process_scout_observation
from scout_candidates import list_scout_candidates
from scout_config import load_scout_settings
from scout_detector import classify_scout_message


class ScoutConfigTests(unittest.TestCase):
    def test_two_tokens_and_allowlists_are_loaded_separately(self):
        environ = {
            "PARTNER_SCOUT_BOT_TOKEN": "partner-secret-token",
            "CLIENT_SCOUT_BOT_TOKEN": "client-secret-token",
            "PARTNER_SCOUT_ALLOWED_CHAT_IDS": "-1001,-1002",
            "CLIENT_SCOUT_ALLOWED_CHAT_IDS": "-2001",
            "TELEGRAM_ADMIN_USER_ID": "77",
        }
        partner = load_scout_settings("partner", environ)
        client = load_scout_settings("client", environ)
        self.assertEqual(partner.bot_token, "partner-secret-token")
        self.assertEqual(client.bot_token, "client-secret-token")
        self.assertEqual(partner.allowed_chat_ids, {-1001, -1002})
        self.assertEqual(client.allowed_chat_ids, {-2001})

    def test_tokens_are_not_exposed_in_repr_or_errors(self):
        token = "never-print-this-token"
        settings = load_scout_settings("partner", {
            "PARTNER_SCOUT_BOT_TOKEN": token,
        })
        self.assertNotIn(token, repr(settings))
        with self.assertRaises(ConfigurationError) as caught:
            load_scout_settings("client", {
                "PARTNER_SCOUT_BOT_TOKEN": token,
            })
        self.assertNotIn(token, str(caught.exception))

    def test_empty_allowlist_is_valid_and_outreach_defaults_false(self):
        settings = load_scout_settings("client", {
            "CLIENT_SCOUT_BOT_TOKEN": "secret",
        })
        self.assertEqual(settings.allowed_chat_ids, frozenset())
        self.assertFalse(settings.outreach_enabled)

    def test_empty_allowlist_startup_warning_is_safe_and_no_network_is_used(self):
        settings = load_scout_settings("partner", {
            "PARTNER_SCOUT_BOT_TOKEN": "secret-never-log",
        })
        application = SimpleNamespace(run_polling=MagicMock())
        output = StringIO()
        with patch.object(scout_bot, "load_scout_settings", return_value=settings), \
             patch.object(scout_bot, "build_scout_application", return_value=application), \
             redirect_stdout(output):
            scout_bot.run_scout_bot("partner")
        self.assertIn("Allowlist пуст", output.getvalue())
        self.assertNotIn("secret-never-log", output.getvalue())
        application.run_polling.assert_called_once_with()

    def test_common_startup_loads_both_scout_configs_from_temp_dotenv(self):
        with tempfile.TemporaryDirectory() as directory:
            env_path = Path(directory) / "scout.env"
            partner_token = "partner-dotenv-placeholder"
            client_token = "client-dotenv-placeholder"
            env_path.write_text(
                "".join((
                    "PARTNER_SCOUT_BOT_" + "TOKEN=" + partner_token + "\n",
                    "CLIENT_SCOUT_BOT_" + "TOKEN=" + client_token + "\n",
                    "PARTNER_SCOUT_ALLOWED_CHAT_IDS=-1001\n",
                    "CLIENT_SCOUT_ALLOWED_CHAT_IDS=-2001\n",
                    "TELEGRAM_ADMIN_USER_ID=77\n",
                )),
                encoding="utf-8",
            )
            application = SimpleNamespace(run_polling=MagicMock())
            captured = []

            def fake_build(settings):
                captured.append(settings)
                return application

            output = StringIO()
            with patch.dict(os.environ, {}, clear=True), \
                 patch.object(scout_bot, "build_scout_application", side_effect=fake_build), \
                 redirect_stdout(output):
                scout_bot.run_scout_bot("partner", dotenv_path=env_path)
                scout_bot.run_scout_bot("client", dotenv_path=env_path)

            self.assertEqual(captured[0].bot_token, partner_token)
            self.assertEqual(captured[1].bot_token, client_token)
            self.assertEqual(captured[0].allowed_chat_ids, {-1001})
            self.assertEqual(captured[1].allowed_chat_ids, {-2001})
            self.assertEqual(captured[0].owner_user_id, 77)
            self.assertNotIn(partner_token, output.getvalue())
            self.assertNotIn(client_token, output.getvalue())

    def test_caption_filter_and_observation_are_supported(self):
        message = Message(
            message_id=5,
            date=datetime.now(timezone.utc),
            chat=Chat(id=-100500, type="supergroup", title="Allowed"),
            from_user=User(
                id=101, first_name="Source", is_bot=False,
                username="caption_user",
            ),
            caption="Предлагаем трансфер из аэропорта",
        )
        update = Update(update_id=9, message=message)
        self.assertTrue(scout_bot.SCOUT_MESSAGE_FILTER.check_update(update))
        observation = scout_bot._observation_from_update(update)
        self.assertEqual(
            observation["original_text"], "Предлагаем трансфер из аэропорта"
        )


class ScoutFoundationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp.name) / "scout.db"
        init_db(self.db_path)

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def observation(message_id=1, user_id=101, username="source_user",
                    text="Сдаю апартаменты в Кароне напрямую от собственника"):
        return {
            "source_chat_id": -100500,
            "source_chat_title": "Phuket Community",
            "source_message_id": message_id,
            "source_user_id": user_id,
            "source_username": username,
            "original_text": text,
        }

    async def test_empty_and_forbidden_allowlist_do_not_save_or_notify(self):
        notifier = AsyncMock()
        for allowlist in (set(), {-999}):
            result = await process_scout_observation(
                "partner", allowlist, self.observation(), 77, notifier,
                self.db_path,
            )
            self.assertFalse(result["processed"])
        self.assertEqual(list_scout_candidates(db_path=self.db_path), [])
        notifier.assert_not_awaited()

    async def test_allowed_partner_candidate_is_saved_and_owner_notified(self):
        notifier = AsyncMock()
        result = await process_scout_observation(
            "partner", {-100500}, self.observation(), 77, notifier,
            self.db_path,
        )
        self.assertTrue(result["processed"])
        self.assertEqual(result["candidate"]["detected_category"], "housing")
        self.assertEqual(result["candidate"]["status"], "needs_review")
        self.assertEqual(result["candidate"]["owner_decision"], "pending")
        self.assertEqual(result["candidate"]["outreach_status"], "not_contacted")
        self.assertFalse(result["outreach_performed"])
        notifier.assert_awaited_once()
        self.assertEqual(notifier.await_args.kwargs["chat_id"], 77)

    async def test_allowed_client_candidate_is_saved(self):
        result = await process_scout_observation(
            "client", {-100500}, self.observation(
                text="Ищу квартиру на Пхукете, нужна аренда на два месяца"
            ), db_path=self.db_path,
        )
        self.assertTrue(result["processed"])
        self.assertEqual(result["candidate"]["scout_type"], "client")
        self.assertEqual(result["candidate"]["detected_category"], "housing")

    def test_classification_requires_category_and_strong_intent(self):
        self.assertIsNotNone(classify_scout_message(
            "partner", "Предлагаем трансфер из аэропорта на минивэне"
        ))
        self.assertIsNotNone(classify_scout_message(
            "client", "Нужен трансфер из аэропорта до Карона"
        ))
        self.assertIsNone(classify_scout_message("client", "Машина"))
        self.assertIsNone(classify_scout_message(
            "partner", "Сегодня на Пхукете хорошая погода"
        ))

    async def test_duplicate_message_is_not_duplicated_or_renotified(self):
        notifier = AsyncMock()
        first = await process_scout_observation(
            "partner", {-100500}, self.observation(), 77, notifier,
            self.db_path,
        )
        second = await process_scout_observation(
            "partner", {-100500}, self.observation(), 77, notifier,
            self.db_path,
        )
        self.assertTrue(first["created"])
        self.assertFalse(second["created"])
        self.assertEqual(len(list_scout_candidates(db_path=self.db_path)), 1)
        self.assertEqual(notifier.await_count, 1)

    async def test_same_username_with_different_numeric_ids_stays_distinct(self):
        await process_scout_observation(
            "partner", {-100500}, self.observation(1, 101, "shared"),
            db_path=self.db_path,
        )
        await process_scout_observation(
            "partner", {-100500}, self.observation(2, 202, "shared"),
            db_path=self.db_path,
        )
        candidates = list_scout_candidates(db_path=self.db_path)
        self.assertEqual([item["source_user_id"] for item in candidates], [101, 202])

    async def test_username_change_updates_same_numeric_identity_observation(self):
        await process_scout_observation(
            "partner", {-100500}, self.observation(1, 101, "old_name"),
            db_path=self.db_path,
        )
        result = await process_scout_observation(
            "partner", {-100500}, self.observation(1, 101, "new_name"),
            db_path=self.db_path,
        )
        self.assertFalse(result["created"])
        self.assertEqual(result["candidate"]["source_username"], "new_name")
        self.assertEqual(result["candidate"]["source_user_id"], 101)

    async def test_no_automatic_outreach_or_group_reply_exists(self):
        owner_notifier = AsyncMock()
        result = await process_scout_observation(
            "client", {-100500}, self.observation(
                text="Нужен автомобиль в аренду на неделю"
            ), 77, owner_notifier, self.db_path,
        )
        self.assertFalse(result["outreach_performed"])
        self.assertEqual(result["candidate"]["outreach_status"], "not_contacted")
        owner_notifier.assert_awaited_once()
        self.assertNotEqual(owner_notifier.await_args.kwargs["chat_id"], 101)
        self.assertNotEqual(owner_notifier.await_args.kwargs["chat_id"], -100500)

    async def test_owner_notification_failure_keeps_candidate_without_secret_log(self):
        result = await process_scout_observation(
            "partner", {-100500}, self.observation(), 77,
            AsyncMock(side_effect=RuntimeError("secret transport detail")),
            self.db_path,
        )
        self.assertTrue(result["processed"])
        self.assertTrue(result["owner_notification_failed"])
        self.assertFalse(result["owner_notified"])
        self.assertEqual(len(list_scout_candidates(db_path=self.db_path)), 1)

    def test_migration_preserves_existing_partner_and_case(self):
        connection = get_connection(self.db_path)
        try:
            with connection:
                partner_id = connection.execute(
                    "INSERT INTO partners(name) VALUES('Existing Partner')"
                ).lastrowid
                client_id = connection.execute(
                    "INSERT INTO clients(telegram_id) VALUES(90001)"
                ).lastrowid
                case_id = connection.execute(
                    "INSERT INTO cases(client_id,title) VALUES(?, 'Existing Case')",
                    (client_id,),
                ).lastrowid
        finally:
            connection.close()
        init_db(self.db_path)
        connection = get_connection(self.db_path)
        try:
            self.assertEqual(connection.execute(
                "SELECT name FROM partners WHERE id=?", (partner_id,)
            ).fetchone()[0], "Existing Partner")
            self.assertEqual(connection.execute(
                "SELECT title FROM cases WHERE id=?", (case_id,)
            ).fetchone()[0], "Existing Case")
        finally:
            connection.close()


class ScoutMigrationTests(unittest.TestCase):
    def test_v7_migration_is_non_destructive(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "legacy.db"
            connection = sqlite3.connect(path)
            connection.execute("CREATE TABLE partners(id INTEGER PRIMARY KEY, name TEXT)")
            connection.execute("INSERT INTO partners VALUES(1, 'Legacy')")
            connection.commit()
            connection.close()
            init_db(path)
            connection = sqlite3.connect(path)
            try:
                self.assertEqual(
                    connection.execute("SELECT name FROM partners WHERE id=1").fetchone()[0],
                    "Legacy",
                )
                self.assertIsNotNone(connection.execute(
                    "SELECT name FROM sqlite_master WHERE name='scout_candidates'"
                ).fetchone())
            finally:
                connection.close()


if __name__ == "__main__":
    unittest.main()
