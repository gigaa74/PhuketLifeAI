import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import bot
from database import get_connection, init_db
from partner_authority import (
    create_pending_proposal,
    decide_proposal,
    get_approved_terms,
    list_pending_proposals,
    record_proposal_delivery,
)
from partner_network import (
    create_partner,
    get_partner,
    sync_partner_telegram_identity,
)
from telegram.ext import ApplicationHandlerStop


class PartnerRoutingSmokeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp.name) / "routing.db"
        init_db(self.db_path)
        self.partner = create_partner(
            "Smoke Partner", ["other"], status="active",
            telegram_username="gigaaa74", db_path=self.db_path,
        )

    def tearDown(self):
        self.temp.cleanup()

    def _patch_routing_services(self):
        return (
            patch.object(bot, "sync_partner_telegram_identity",
                         side_effect=lambda uid, username: sync_partner_telegram_identity(
                             uid, username, self.db_path)),
            patch.object(bot, "create_pending_proposal",
                         side_effect=lambda pid, text, **kwargs: create_pending_proposal(
                             pid, text, db_path=self.db_path, **kwargs)),
            patch.object(bot, "get_partner",
                         side_effect=lambda pid: get_partner(pid, self.db_path)),
        )

    async def test_known_partner_commercial_update_bypasses_client_flow(self):
        message = SimpleNamespace(
            text="Теперь работаем с комиссией 15%. Подтвердите, пожалуйста.",
            caption=None, message_id=901, reply_to_message=None,
            reply_text=AsyncMock(),
        )
        update = SimpleNamespace(
            effective_user=SimpleNamespace(id=8733594703, username="gigaaa74"),
            effective_message=message,
        )
        context = SimpleNamespace(bot=SimpleNamespace(send_message=AsyncMock()))
        routing_patches = self._patch_routing_services()
        with routing_patches[0], routing_patches[1], routing_patches[2], \
             patch.object(bot, "SETTINGS", SimpleNamespace(telegram_admin_user_id=123)), \
             patch.object(bot, "analyze_case") as analyze, \
             patch.object(bot, "ask_gigachat") as llm:
            with self.assertRaises(ApplicationHandlerStop):
                await bot.partner_identity_sync_handler(update, context)

        linked = get_partner(self.partner["id"], self.db_path)
        self.assertEqual(linked["telegram_user_id"], 8733594703)
        self.assertEqual(len(list_pending_proposals(linked["id"], self.db_path)), 1)
        self.assertEqual(get_approved_terms(linked["id"], self.db_path), {})
        reply = message.reply_text.await_args.args[0].casefold()
        self.assertIn("не можем самостоятельно", reply)
        self.assertIn("ответственному лицу", reply)
        context.bot.send_message.assert_awaited_once()
        analyze.assert_not_called()
        llm.assert_not_called()
        connection = get_connection(self.db_path)
        try:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM cases").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM clients").fetchone()[0], 0)
        finally:
            connection.close()

    async def test_owner_approve_updates_terms_and_notifies_numeric_id(self):
        linked = sync_partner_telegram_identity(
            8733594703, "gigaaa74", self.db_path
        )
        proposal = create_pending_proposal(
            linked["id"], "Комиссия 15%", db_path=self.db_path
        )
        approved = decide_proposal(
            proposal["id"], True, owner_id=1, db_path=self.db_path
        )
        sender = AsyncMock()
        with patch.object(bot, "record_proposal_delivery",
                          side_effect=lambda pid, delivered, error=None: record_proposal_delivery(
                              pid, delivered, error, self.db_path)):
            delivered, error = await bot._notify_partner_owner_decision(
                approved, linked, True, sender
            )
        self.assertTrue(delivered)
        self.assertIsNone(error)
        self.assertEqual(get_approved_terms(linked["id"], self.db_path)["commission"], "15%")
        self.assertEqual(sender.await_args.kwargs["chat_id"], 8733594703)
        self.assertIn("комиссия 15%", sender.await_args.kwargs["text"].casefold())

    async def test_owner_reject_keeps_terms_and_notifies(self):
        linked = sync_partner_telegram_identity(
            8733594703, "gigaaa74", self.db_path
        )
        proposal = create_pending_proposal(
            linked["id"], "Комиссия 15%", db_path=self.db_path
        )
        rejected = decide_proposal(
            proposal["id"], False, owner_id=1, db_path=self.db_path
        )
        sender = AsyncMock()
        with patch.object(bot, "record_proposal_delivery",
                          side_effect=lambda pid, delivered, error=None: record_proposal_delivery(
                              pid, delivered, error, self.db_path)), \
             patch.object(bot, "get_approved_terms",
                          side_effect=lambda pid: get_approved_terms(pid, self.db_path)):
            delivered, _ = await bot._notify_partner_owner_decision(
                rejected, linked, False, sender
            )
        self.assertTrue(delivered)
        self.assertEqual(get_approved_terms(linked["id"], self.db_path), {})
        self.assertIn("не согласованы", sender.await_args.kwargs["text"].casefold())

    async def test_failed_send_is_not_reported_as_delivered(self):
        linked = sync_partner_telegram_identity(
            8733594703, "gigaaa74", self.db_path
        )
        proposal = create_pending_proposal(
            linked["id"], "Комиссия 15%", db_path=self.db_path
        )
        approved = decide_proposal(
            proposal["id"], True, owner_id=1, db_path=self.db_path
        )
        with patch.object(bot, "record_proposal_delivery",
                          side_effect=lambda pid, delivered, error=None: record_proposal_delivery(
                              pid, delivered, error, self.db_path)):
            delivered, error = await bot._notify_partner_owner_decision(
                approved, linked, True, AsyncMock(side_effect=RuntimeError("down"))
            )
        self.assertFalse(delivered)
        self.assertIn("не подтвердил", error)
        connection = get_connection(self.db_path)
        try:
            action = connection.execute(
                """SELECT action FROM partner_commercial_audit
                   WHERE proposal_id=? ORDER BY id DESC LIMIT 1""",
                (proposal["id"],),
            ).fetchone()[0]
        finally:
            connection.close()
        self.assertEqual(action, "owner_decision_delivery_failed")

    async def test_missing_numeric_id_is_not_delivered_by_username(self):
        proposal = create_pending_proposal(
            self.partner["id"], "Комиссия 15%", db_path=self.db_path
        )
        rejected = decide_proposal(
            proposal["id"], False, owner_id=1, db_path=self.db_path
        )
        sender = AsyncMock()
        with patch.object(bot, "record_proposal_delivery",
                          side_effect=lambda pid, delivered, error=None: record_proposal_delivery(
                              pid, delivered, error, self.db_path)):
            delivered, error = await bot._notify_partner_owner_decision(
                rejected, self.partner, False, sender
            )
        self.assertFalse(delivered)
        self.assertIn("user ID", error)
        sender.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
