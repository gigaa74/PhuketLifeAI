import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import bot
from admin_ui import partner_action_buttons
from database import init_db
from partner_authority import (
    create_pending_proposal,
    decide_proposal,
    get_approved_terms,
    get_proposal,
    list_pending_proposals,
    record_proposal_delivery,
)
from partner_network import create_partner, get_partner, sync_partner_telegram_identity


def _callbacks(markup):
    return [
        (button.text, button.callback_data)
        for row in markup.inline_keyboard for button in row
    ]


class PendingCommercialUiTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp.name) / "pending-ui.db"
        init_db(self.db_path)
        partner = create_partner(
            "Pending Partner", ["housing"], status="active",
            telegram_username="pending_partner", db_path=self.db_path,
        )
        self.partner = sync_partner_telegram_identity(
            70007, "pending_partner", self.db_path
        )

    def tearDown(self):
        self.temp.cleanup()

    def proposal(self, text):
        return create_pending_proposal(
            self.partner["id"], text, source="telegram_partner_message",
            source_message_id=99, db_path=self.db_path,
        )

    def test_partner_card_without_pending_has_no_pending_button(self):
        labels = [label for row in partner_action_buttons(
            self.partner["id"], False, pending_count=0
        ) for label, _ in row]
        self.assertFalse(any("Ожидают решения" in label for label in labels))

    def test_partner_card_with_pending_shows_count(self):
        labels = [label for row in partner_action_buttons(
            self.partner["id"], False, pending_count=1
        ) for label, _ in row]
        self.assertIn("⚠️ Ожидают решения (1)", labels)

    async def test_click_opens_proposal_with_decision_and_back_buttons(self):
        proposal = self.proposal("Комиссия 15%")
        query = SimpleNamespace(edit_message_text=AsyncMock())
        with patch.object(bot, "get_proposal",
                          side_effect=lambda pid: get_proposal(pid, self.db_path)), \
             patch.object(bot, "get_partner",
                          side_effect=lambda pid: get_partner(pid, self.db_path)):
            await bot._show_pending_term(query, proposal["id"])
        text = query.edit_message_text.await_args.args[0]
        markup = query.edit_message_text.await_args.kwargs["reply_markup"]
        callbacks = _callbacks(markup)
        self.assertIn("Pending Partner", text)
        self.assertIn("Комиссия: 15%", text)
        self.assertIn(("✅ Утвердить", f"terms:approve:{proposal['id']}"), callbacks)
        self.assertIn(("❌ Отклонить", f"terms:reject:{proposal['id']}"), callbacks)
        self.assertIn(("⬅️ Назад к партнёру", f"partner:view:{self.partner['id']}"), callbacks)

    async def test_multiple_pending_proposals_are_contextual_and_navigable(self):
        first = self.proposal("Комиссия 15%")
        second = self.proposal("Работаем только по предоплате в USDT")
        query = SimpleNamespace(edit_message_text=AsyncMock())
        with patch.object(bot, "get_partner",
                          side_effect=lambda pid: get_partner(pid, self.db_path)), \
             patch.object(bot, "list_pending_proposals",
                          side_effect=lambda pid: list_pending_proposals(pid, self.db_path)):
            await bot._show_pending_terms(query, self.partner["id"])
        text = query.edit_message_text.await_args.args[0]
        callbacks = _callbacks(
            query.edit_message_text.await_args.kwargs["reply_markup"]
        )
        self.assertIn("Комиссия: 15%", text)
        self.assertIn("Способ оплаты", text)
        self.assertIn(f"terms:view:{first['id']}", [data for _, data in callbacks])
        self.assertIn(f"terms:view:{second['id']}", [data for _, data in callbacks])

    async def _decision_callback(self, proposal, approve):
        action = "approve" if approve else "reject"
        query = SimpleNamespace(
            data=f"terms:{action}:{proposal['id']}",
            answer=AsyncMock(), edit_message_text=AsyncMock(),
        )
        update = SimpleNamespace(
            callback_query=query, effective_user=SimpleNamespace(id=1)
        )
        sender = AsyncMock()
        context = SimpleNamespace(bot=SimpleNamespace(send_message=sender))
        with patch.object(bot, "can_access_admin_panel", return_value=True), \
             patch.object(bot, "execute_commercial_decision",
                          side_effect=lambda pid, approved, owner_id=None: decide_proposal(
                              pid, approved, owner_id, db_path=self.db_path)), \
             patch.object(bot, "get_partner",
                          side_effect=lambda pid: get_partner(pid, self.db_path)), \
             patch.object(bot, "get_approved_terms",
                          side_effect=lambda pid: get_approved_terms(pid, self.db_path)), \
             patch.object(bot, "record_proposal_delivery",
                          side_effect=lambda pid, delivered, error=None: record_proposal_delivery(
                              pid, delivered, error, self.db_path)):
            await bot.admin_callback_handler(update, context)
        return query, sender

    async def test_approve_from_ui_decreases_count_and_notifies_partner(self):
        proposal = self.proposal("Комиссия 15%")
        query, sender = await self._decision_callback(proposal, True)
        self.assertEqual(list_pending_proposals(self.partner["id"], self.db_path), [])
        self.assertEqual(get_approved_terms(self.partner["id"], self.db_path)["commission"], "15%")
        sender.assert_awaited_once()
        self.assertEqual(sender.await_args.kwargs["chat_id"], 70007)
        self.assertIn("Партнёр уведомлён", query.edit_message_text.await_args.args[0])

    async def test_reject_from_ui_decreases_count_and_keeps_approved_terms(self):
        proposal = self.proposal("Комиссия 18%")
        query, sender = await self._decision_callback(proposal, False)
        self.assertEqual(list_pending_proposals(self.partner["id"], self.db_path), [])
        self.assertEqual(get_approved_terms(self.partner["id"], self.db_path), {})
        sender.assert_awaited_once()
        self.assertIn("Партнёр уведомлён", query.edit_message_text.await_args.args[0])


if __name__ == "__main__":
    unittest.main()
