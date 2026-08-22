import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import bot
from admin_ui import (
    format_partner_allowed_actions,
    format_partner_card,
    format_partner_commercial_terms,
    format_partner_open_questions,
    format_partner_operations,
    partner_action_buttons,
)
from database import get_connection, init_db
from partner_network import get_partner
from scripts.onboard_lera_partner import onboard_lera


def _callbacks(markup):
    return [
        (button.text, button.callback_data)
        for row in markup.inline_keyboard for button in row
    ]


class PartnerCardRussianUxTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp.name) / "partner-card.db"
        init_db(self.db_path)
        self.partner, _ = onboard_lera(self.db_path)

    def tearDown(self):
        self.temp.cleanup()

    def _database_snapshot(self):
        connection = get_connection(self.db_path)
        try:
            partner = connection.execute(
                "SELECT * FROM partners WHERE id=?", (self.partner["id"],)
            ).fetchone()
            terms = connection.execute(
                """SELECT * FROM partner_approved_terms WHERE partner_id=?
                   ORDER BY id""", (self.partner["id"],)
            ).fetchall()
            return partner, terms
        finally:
            connection.close()

    def test_main_card_is_short_and_contains_no_raw_internal_data(self):
        text = format_partner_card(self.partner)
        self.assertEqual(text.splitlines()[0], "🤝 Лера")
        self.assertIn("Статус: Активен", text)
        self.assertIn("Формат: Гибридный партнёр", text)
        self.assertIn("Направление: Жильё", text)
        self.assertIn("Районы: Карон, Банг Тао", text)
        self.assertIn("Ожидают решения: 0", text)
        self.assertIn("Автоотправка: Выключена", text)
        self.assertLessEqual(len(text.splitlines()), 8)
        self.assertNotIn("{", text)
        self.assertNotIn("operational_notes", text)
        self.assertNotIn("base_commission_percent", text)
        self.assertNotIn("ask_availability", text)

    def test_commercial_terms_are_human_readable_with_commission_ladder(self):
        text = format_partner_commercial_terms(self.partner)
        self.assertIn("Базовая комиссия: 10%", text)
        self.assertIn("— после 5 успешных сделок: 11%", text)
        self.assertIn("— после 10 успешных сделок: 12%", text)
        self.assertIn("— после 30 успешных сделок: 14%", text)
        self.assertIn("Перед бронированием:", text)
        self.assertIn("Счёт за проживание:", text)
        self.assertIn("Партнёрское вознаграждение:", text)
        for key in self.partner["approved_terms"]:
            self.assertNotIn(key, text)

    def test_operational_notes_are_sectioned_and_link_is_clickable(self):
        text = format_partner_operations(self.partner)
        self.assertIn("🏠 Объекты и операционная информация", text)
        self.assertIn("— Карон", text)
        self.assertIn("— Банг Тао / Bellevue", text)
        self.assertIn("— комплекс 1: около 13 апартаментов", text)
        self.assertIn("— комплекс 2: около 6 апартаментов", text)
        self.assertIn("⚠️ Количество объектов может меняться", text)
        self.assertIn("https://media.zdravkov.net/byroom/broker.html", text)
        self.assertNotIn("inventory_context", text)
        self.assertNotIn("{", text)
        self.assertNotIn("[", text)

    def test_open_questions_are_a_readable_list_and_empty_state_is_safe(self):
        text = format_partner_open_questions(self.partner)
        self.assertIn("— способы оплаты бронирования клиентом;", text)
        self.assertIn("— условия комиссии при отмене;", text)
        self.assertIn("— схема оплаты криптовалютой;", text)
        empty = dict(self.partner)
        data = json.loads(empty["operational_notes"])
        data["open_questions"] = []
        empty["operational_notes"] = json.dumps(data, ensure_ascii=False)
        self.assertIn(
            "Все необходимые вопросы согласованы ✅",
            format_partner_open_questions(empty),
        )

    def test_allowed_actions_are_translated_and_unknown_action_does_not_fail(self):
        partner = dict(self.partner)
        partner["allowed_actions"] = [
            "ask_availability", "send_request", "unknown_custom_action"
        ]
        text = format_partner_allowed_actions(partner)
        self.assertIn("Запрашивать наличие", text)
        self.assertIn("Передавать запрос", text)
        self.assertIn("Другое действие: Unknown custom action", text)
        self.assertNotIn("ask_availability", text)
        self.assertNotIn("unknown_custom_action", text)

    def test_main_buttons_open_sections_and_return_to_partner_list(self):
        rows = partner_action_buttons(self.partner["id"], False)
        callbacks = [item for row in rows for item in row]
        self.assertIn(("💼 Коммерческие условия",
                       f"partner:commercial:{self.partner['id']}"), callbacks)
        self.assertIn(("🏠 Объекты и работа",
                       f"partner:operations:{self.partner['id']}"), callbacks)
        self.assertIn(("❓ Открытые вопросы",
                       f"partner:questions:{self.partner['id']}"), callbacks)
        self.assertIn(("⚙️ Разрешённые действия",
                       f"partner:actions:{self.partner['id']}"), callbacks)
        self.assertIn(("⬅️ К списку партнёров", "admin:partners"), callbacks)

    async def test_section_has_back_button(self):
        query = SimpleNamespace(edit_message_text=AsyncMock())
        with patch.object(
            bot, "get_partner", side_effect=lambda _: get_partner(
                self.partner["id"], self.db_path
            )
        ):
            await bot._show_partner_section(
                query, self.partner["id"], format_partner_operations
            )
        callbacks = _callbacks(
            query.edit_message_text.await_args.kwargs["reply_markup"]
        )
        self.assertIn(("⬅️ Назад к партнёру",
                       f"partner:view:{self.partner['id']}"), callbacks)

    async def test_auto_handoff_requires_confirmation_and_does_not_mutate(self):
        before = self._database_snapshot()
        query = SimpleNamespace(edit_message_text=AsyncMock())
        with patch.object(
            bot, "get_partner", side_effect=lambda _: get_partner(
                self.partner["id"], self.db_path
            )
        ):
            await bot._show_partner_auto_confirmation(query, self.partner["id"])
        text = query.edit_message_text.await_args.args[0]
        callbacks = _callbacks(
            query.edit_message_text.await_args.kwargs["reply_markup"]
        )
        self.assertIn("Включить автоматическую отправку", text)
        self.assertIn(("✅ Подтвердить",
                       f"partner:auto:{self.partner['id']}:on"), callbacks)
        self.assertIn(("❌ Отмена",
                       f"partner:view:{self.partner['id']}"), callbacks)
        self.assertEqual(before, self._database_snapshot())
        self.assertFalse(get_partner(
            self.partner["id"], self.db_path
        )["auto_handoff_enabled"])

    async def test_non_admin_cannot_open_partner_card(self):
        query = SimpleNamespace(
            data=f"partner:view:{self.partner['id']}", answer=AsyncMock(),
            edit_message_text=AsyncMock(),
        )
        update = SimpleNamespace(
            callback_query=query, effective_user=SimpleNamespace(id=999)
        )
        context = SimpleNamespace(bot=SimpleNamespace(send_message=AsyncMock()))
        settings = SimpleNamespace(
            telegram_admin_user_id=1,
            partner_handoff_mode="review",
        )
        with patch.object(bot, "SETTINGS", settings):
            await bot.admin_callback_handler(update, context)
        query.answer.assert_awaited_once_with(
            "Недостаточно прав.", show_alert=True
        )
        query.edit_message_text.assert_not_awaited()

    def test_all_formatters_leave_database_unchanged(self):
        before = self._database_snapshot()
        format_partner_card(self.partner)
        format_partner_commercial_terms(self.partner)
        format_partner_operations(self.partner)
        format_partner_open_questions(self.partner)
        format_partner_allowed_actions(self.partner)
        self.assertEqual(before, self._database_snapshot())


if __name__ == "__main__":
    unittest.main()
