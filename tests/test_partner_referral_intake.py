import tempfile
import unittest
from contextlib import ExitStack, contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import bot
from database import get_connection, init_db
from partner_authority import create_pending_proposal
from partner_network import (
    create_partner,
    get_partner,
    sync_partner_telegram_identity,
)
from partner_referrals import (
    create_partner_referral,
    get_partner_referral,
    mark_owner_notification,
    set_partner_referral_status,
)


class PartnerReferralIntakeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp.name) / "referrals.db"
        init_db(self.db_path)
        partner = create_partner(
            "Active Partner", ["housing"], status="active",
            telegram_username="active_partner", db_path=self.db_path,
        )
        self.partner = sync_partner_telegram_identity(
            70001, "active_partner", self.db_path
        )
        self.admin_id = 90001

    def tearDown(self):
        self.temp.cleanup()

    def _message(self, message_id=10, text="Клиент ищет виллу на месяц", **values):
        defaults = {
            "message_id": message_id,
            "chat_id": 70001,
            "text": text,
            "caption": None,
            "photo": None,
            "document": None,
            "voice": None,
            "location": None,
            "contact": None,
            "reply_to_message": None,
            "reply_text": AsyncMock(),
        }
        defaults.update(values)
        return SimpleNamespace(**defaults)

    def _update(self, message, user_id=70001, username="active_partner"):
        return SimpleNamespace(
            effective_user=SimpleNamespace(id=user_id, username=username),
            effective_message=message,
            effective_chat=SimpleNamespace(id=message.chat_id),
        )

    def _context(self, notification_error=None):
        sender = (
            AsyncMock(side_effect=notification_error)
            if notification_error else AsyncMock()
        )
        return SimpleNamespace(bot=SimpleNamespace(
            send_message=sender,
            copy_message=AsyncMock(),
        ))

    @contextmanager
    def _bot_patches(self):
        settings = SimpleNamespace(
            telegram_admin_user_id=self.admin_id,
            partner_handoff_mode="review",
        )
        replacements = {
            "SETTINGS": settings,
            "sync_partner_telegram_identity": lambda uid, username: (
                sync_partner_telegram_identity(uid, username, self.db_path)
            ),
            "create_pending_proposal": lambda partner_id, text, **kwargs: (
                create_pending_proposal(
                    partner_id, text, db_path=self.db_path, **kwargs
                )
            ),
            "create_partner_referral": lambda *args, **kwargs: (
                create_partner_referral(*args, db_path=self.db_path, **kwargs)
            ),
            "mark_owner_notification": lambda request_id, delivered, error=None: (
                mark_owner_notification(
                    request_id, delivered, error, self.db_path
                )
            ),
            "set_partner_referral_status": lambda request_id, status: (
                set_partner_referral_status(request_id, status, self.db_path)
            ),
            "get_partner": lambda partner_id: get_partner(
                partner_id, self.db_path
            ),
        }
        with ExitStack() as stack:
            for name, value in replacements.items():
                stack.enter_context(patch.object(bot, name, value))
            yield

    async def _handle(self, update, context):
        with self._bot_patches():
            with self.assertRaises(bot.ApplicationHandlerStop):
                await bot.partner_identity_sync_handler(update, context)

    def _count(self, table):
        connection = get_connection(self.db_path)
        try:
            return connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        finally:
            connection.close()

    def test_migration_010_preserves_existing_data(self):
        connection = get_connection(self.db_path)
        try:
            with connection:
                connection.execute("DROP TABLE partner_referral_requests")
                connection.execute(
                    "DELETE FROM schema_migrations WHERE version=10"
                )
                connection.execute(
                    """INSERT INTO clients(telegram_id, username)
                       VALUES (81001, 'existing_client')"""
                )
                client_id = connection.execute(
                    "SELECT id FROM clients WHERE telegram_id=81001"
                ).fetchone()[0]
                connection.execute(
                    """INSERT INTO cases(client_id, category, status)
                       VALUES (?, 'housing', 'new')""",
                    (client_id,),
                )
                connection.execute(
                    """INSERT INTO scout_candidates
                       (scout_type, source_chat_id, source_message_id,
                        original_text, detected_category,
                        detected_categories, confidence, status)
                       VALUES ('client', 1, 2, 'Нужна квартира', 'housing',
                               '["housing"]', 0.9, 'needs_review')"""
                )
            snapshots = {
                table: connection.execute(
                    f"SELECT * FROM {table} ORDER BY id"
                ).fetchall()
                for table in (
                    "partners", "partner_approved_terms", "clients",
                    "cases", "scout_candidates",
                )
            }
        finally:
            connection.close()

        init_db(self.db_path)

        connection = get_connection(self.db_path)
        try:
            self.assertEqual(
                connection.execute(
                    "SELECT MAX(version) FROM schema_migrations"
                ).fetchone()[0],
                13,
            )
            self.assertIsNotNone(connection.execute(
                """SELECT name FROM sqlite_master
                   WHERE type='table' AND name='partner_referral_requests'"""
            ).fetchone())
            for table, before in snapshots.items():
                after = connection.execute(
                    f"SELECT * FROM {table} ORDER BY id"
                ).fetchall()
                self.assertEqual(after, before, table)
        finally:
            connection.close()

    async def test_active_partner_text_is_saved_acknowledged_and_notified(self):
        message = self._message()
        context = self._context()
        await self._handle(self._update(message), context)

        request = get_partner_referral(1, self.db_path)
        self.assertEqual(request["partner_id"], self.partner["id"])
        self.assertEqual(request["original_text"], message.text)
        self.assertEqual(request["message_type"], "text")
        self.assertEqual(request["status"], "needs_owner_review")
        acknowledgement = message.reply_text.await_args.args[0]
        self.assertIn("Запрос №1 принят", acknowledgement)
        self.assertNotIn("needs_owner_review", acknowledgement)
        context.bot.send_message.assert_awaited_once()
        owner_text = context.bot.send_message.await_args.kwargs["text"]
        self.assertIn("Active Partner", owner_text)
        self.assertIn(message.text, owner_text)
        self.assertIn("Требует проверки владельца", owner_text)

    async def test_duplicate_update_is_not_saved_or_notified_twice(self):
        message = self._message(message_id=44)
        update = self._update(message)
        context = self._context()
        await self._handle(update, context)
        with self._bot_patches():
            with self.assertRaises(bot.ApplicationHandlerStop):
                await bot.partner_identity_sync_handler(update, context)
        self.assertEqual(self._count("partner_referral_requests"), 1)
        self.assertEqual(message.reply_text.await_count, 1)
        self.assertEqual(context.bot.send_message.await_count, 1)

    async def test_notification_failure_does_not_lose_request_or_log_exception(self):
        message = self._message()
        context = self._context(RuntimeError("secret exception body"))
        with patch("builtins.print") as printer:
            await self._handle(self._update(message), context)
        request = get_partner_referral(1, self.db_path)
        self.assertIsNotNone(request)
        self.assertEqual(request["owner_notification_error"], "RuntimeError")
        logged = " ".join(str(arg) for call in printer.call_args_list for arg in call.args)
        self.assertIn("RuntimeError", logged)
        self.assertNotIn("secret exception body", logged)

    async def test_active_partner_creates_neither_client_nor_case_and_keeps_terms(self):
        connection = get_connection(self.db_path)
        try:
            with connection:
                connection.execute(
                    """INSERT INTO partner_approved_terms
                       (partner_id, term_key, term_value)
                       VALUES (?, 'commission', '10%')""",
                    (self.partner["id"],),
                )
            partner_before = connection.execute(
                "SELECT * FROM partners WHERE id=?", (self.partner["id"],)
            ).fetchone()
            terms_before = connection.execute(
                "SELECT * FROM partner_approved_terms WHERE partner_id=?",
                (self.partner["id"],),
            ).fetchall()
        finally:
            connection.close()
        await self._handle(self._update(self._message()), self._context())
        connection = get_connection(self.db_path)
        try:
            partner_after = connection.execute(
                "SELECT * FROM partners WHERE id=?", (self.partner["id"],)
            ).fetchone()
            terms_after = connection.execute(
                "SELECT * FROM partner_approved_terms WHERE partner_id=?",
                (self.partner["id"],),
            ).fetchall()
        finally:
            connection.close()
        self.assertEqual(self._count("clients"), 0)
        self.assertEqual(self._count("cases"), 0)
        self.assertEqual(partner_after, partner_before)
        self.assertEqual(terms_after, terms_before)

    async def test_unknown_inactive_and_identity_conflict_have_no_partner_access(self):
        unknown = self._update(self._message(message_id=20), 80001, "unknown")
        with self._bot_patches():
            await bot.partner_identity_sync_handler(unknown, self._context())

        for offset, status in enumerate(("candidate", "paused", "blocked"), 1):
            username = f"{status}_partner"
            inactive_partner = create_partner(
                status.title(), ["housing"], status=status,
                telegram_username=username, db_path=self.db_path,
            )
            self.assertIsNotNone(inactive_partner)
            inactive = self._update(
                self._message(message_id=20 + offset),
                80001 + offset,
                username,
            )
            with self._bot_patches():
                await bot.partner_identity_sync_handler(
                    inactive, self._context()
                )

        conflict = self._update(
            self._message(message_id=30), 99999, "active_partner"
        )
        with self._bot_patches():
            await bot.partner_identity_sync_handler(conflict, self._context())
        self.assertEqual(self._count("partner_referral_requests"), 0)
        current = get_partner(self.partner["id"], self.db_path)
        self.assertEqual(current["telegram_user_id"], 70001)

    async def test_admin_and_callback_updates_are_not_partner_referrals(self):
        admin_partner = create_partner(
            "Admin Partner", ["housing"], status="active",
            telegram_username="admin_partner", db_path=self.db_path,
        )
        sync_partner_telegram_identity(
            self.admin_id, "admin_partner", self.db_path
        )
        admin_message = self._message(message_id=40)
        admin_update = self._update(
            admin_message, self.admin_id, "admin_partner"
        )
        callback_update = SimpleNamespace(
            callback_query=SimpleNamespace(data="admin:partners"),
            effective_user=SimpleNamespace(
                id=self.partner["telegram_user_id"],
                username="active_partner",
            ),
            effective_message=self._message(message_id=41),
        )
        with self._bot_patches():
            await bot.partner_identity_sync_handler(
                admin_update, self._context()
            )
            await bot.partner_identity_sync_handler(
                callback_update, self._context()
            )
        self.assertIsNotNone(admin_partner)
        self.assertEqual(self._count("partner_referral_requests"), 0)

    async def test_supported_attachment_types_are_saved_and_copied_to_owner(self):
        photo = SimpleNamespace(
            file_id="photo-id", width=100, height=80, file_size=123
        )
        document = SimpleNamespace(
            file_id="doc-id", file_name="brief.pdf",
            mime_type="application/pdf", file_size=456,
        )
        voice = SimpleNamespace(
            file_id="voice-id", duration=12, mime_type="audio/ogg", file_size=789
        )
        location = SimpleNamespace(latitude=7.88, longitude=98.39)
        contact = SimpleNamespace(
            phone_number="+660000", first_name="Клиент",
            last_name=None, user_id=None,
        )
        examples = [
            ("photo", {"photo": [photo], "caption": "Фото объекта"}, "photo-id"),
            ("document", {"document": document, "caption": "Требования"}, "doc-id"),
            ("voice", {"voice": voice}, "voice-id"),
            ("location", {"location": location}, None),
            ("contact", {"contact": contact}, None),
        ]
        for index, (expected_type, values, expected_file_id) in enumerate(examples, 1):
            message = self._message(message_id=100 + index, text=None, **values)
            context = self._context()
            await self._handle(self._update(message), context)
            request = get_partner_referral(index, self.db_path)
            self.assertEqual(request["message_type"], expected_type)
            self.assertEqual(request["telegram_file_id"], expected_file_id)
            context.bot.copy_message.assert_awaited_once_with(
                chat_id=self.admin_id,
                from_chat_id=70001,
                message_id=100 + index,
            )
        self.assertEqual(self._count("partner_referral_requests"), 5)

    async def test_admin_status_buttons_are_idempotent_and_non_admin_is_denied(self):
        keyboard = bot._partner_referral_buttons(9223372036854775807)
        self.assertTrue(all(
            len(button.callback_data.encode("utf-8")) <= 64
            for row in keyboard.inline_keyboard for button in row
        ))
        request, _ = create_partner_referral(
            self.partner["id"], 70001, 50, 70001, "active_partner",
            "Нужна квартира", "text", db_path=self.db_path,
        )
        query = SimpleNamespace(
            data=f"referral:status:{request['id']}:in_progress",
            answer=AsyncMock(), edit_message_text=AsyncMock(),
        )
        admin_update = SimpleNamespace(
            callback_query=query,
            effective_user=SimpleNamespace(id=self.admin_id),
        )
        context = self._context()
        with self._bot_patches():
            await bot.admin_callback_handler(admin_update, context)
            await bot.admin_callback_handler(admin_update, context)
        self.assertEqual(
            get_partner_referral(request["id"], self.db_path)["status"],
            "in_progress",
        )
        self.assertEqual(self._count("partner_referral_requests"), 1)

        denied_query = SimpleNamespace(
            data=f"referral:status:{request['id']}:resolved",
            answer=AsyncMock(), edit_message_text=AsyncMock(),
        )
        denied = SimpleNamespace(
            callback_query=denied_query,
            effective_user=SimpleNamespace(id=12345),
        )
        with self._bot_patches():
            await bot.admin_callback_handler(denied, context)
        denied_query.answer.assert_awaited_once_with(
            "Недостаточно прав.", show_alert=True
        )
        self.assertEqual(
            get_partner_referral(request["id"], self.db_path)["status"],
            "in_progress",
        )


if __name__ == "__main__":
    unittest.main()
