import tempfile
import unittest
from contextlib import ExitStack, contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import bot
from database import (
    get_client_by_telegram_id,
    get_connection,
    get_or_create_client,
    init_db,
)
from partner_applications import (
    cancel_application,
    decide_application,
    get_application,
    get_open_application,
    list_applications,
    move_application_back,
    record_application_answer,
    skip_application_step,
    start_application,
)
from partner_identity_relinks import (
    cancel_relink, decide_relink, get_open_relink, get_relink,
    record_relink_answer, start_relink,
)
from partner_network import (
    get_partner,
    resolve_partner_telegram_identity,
    sync_partner_telegram_identity,
)
from scripts.onboard_lera_partner import onboard_lera


class RoleAwareStartTests(unittest.IsolatedAsyncioTestCase):
    APPLICATION_ANSWERS = (
        "Компания", "Трансферы", "Пхукет", "Самостоятельно", "Каталог",
        "По запросу", "Даты и бюджет", "Процент", "@applicant",
        "https://example.com", "не требуются",
    )
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp.name) / "role-aware-start.db"
        init_db(self.db_path)
        self.lera, _ = onboard_lera(self.db_path)
        self.admin_id = 900001

    def tearDown(self):
        self.temp.cleanup()

    def _update(self, user_id, username, first_name="Пользователь"):
        message = SimpleNamespace(
            text="/start",
            caption=None,
            reply_text=AsyncMock(),
        )
        user = SimpleNamespace(
            id=user_id,
            username=username,
            first_name=first_name,
            last_name=None,
        )
        return SimpleNamespace(
            effective_user=user,
            effective_message=message,
            message=message,
        )

    def _context(self):
        return SimpleNamespace(
            args=[],
            user_data={},
            bot=SimpleNamespace(send_message=AsyncMock()),
        )

    def _callback_update(self, user_id, username, data):
        query = SimpleNamespace(
            data=data,
            answer=AsyncMock(),
            edit_message_text=AsyncMock(),
        )
        user = SimpleNamespace(
            id=user_id, username=username, first_name="Пользователь",
            last_name=None,
        )
        return SimpleNamespace(
            effective_user=user,
            effective_message=SimpleNamespace(),
            callback_query=query,
        )

    @contextmanager
    def _bot_patches(self):
        def resolve(user_id, username):
            return resolve_partner_telegram_identity(
                user_id, username, self.db_path
            )

        def create_client(incoming_update):
            user = incoming_update.effective_user
            return get_or_create_client(
                user.id,
                user.username,
                user.first_name,
                user.last_name,
                self.db_path,
            )

        settings = SimpleNamespace(telegram_admin_user_id=self.admin_id)
        with ExitStack() as stack:
            replacements = {
                "resolve_partner_telegram_identity": resolve,
                "get_or_create_client": create_client,
                "get_client_by_telegram_id": lambda user_id: get_client_by_telegram_id(
                    user_id, self.db_path
                ),
                "get_open_application": lambda user_id: get_open_application(
                    user_id, self.db_path
                ),
                "start_application": lambda user_id, username: start_application(
                    user_id, username, self.db_path
                ),
                "cancel_application": lambda user_id: cancel_application(
                    user_id, self.db_path
                ),
                "record_application_answer": lambda app_id, value: record_application_answer(
                    app_id, value, self.db_path
                ),
                "move_application_back": lambda app_id: move_application_back(
                    app_id, self.db_path
                ),
                "skip_application_step": lambda app_id: skip_application_step(
                    app_id, self.db_path
                ),
                "get_open_relink": lambda user_id: get_open_relink(
                    user_id, self.db_path
                ),
                "get_relink": lambda request_id: get_relink(
                    request_id, self.db_path
                ),
                "start_relink": lambda user_id, username: start_relink(
                    user_id, username, self.db_path
                ),
                "record_relink_answer": lambda request_id, value: record_relink_answer(
                    request_id, value, self.db_path
                ),
                "cancel_relink": lambda user_id: cancel_relink(
                    user_id, self.db_path
                ),
                "decide_relink": lambda request_id, partner_id, approved, owner_id: decide_relink(
                    request_id, partner_id, approved, owner_id, self.db_path
                ),
                "get_application": lambda app_id: get_application(
                    app_id, self.db_path
                ),
                "list_applications": lambda: list_applications(
                    "needs_review", self.db_path
                ),
                "decide_application": lambda app_id, approved, owner_id: decide_application(
                    app_id, approved, owner_id, db_path=self.db_path
                ),
                "SETTINGS": settings,
            }
            for name, replacement in replacements.items():
                stack.enter_context(patch.object(bot, name, replacement))
            yield

    async def _start(self, update, context=None):
        context = context or self._context()
        with self._bot_patches():
            await bot.start(update, context)
        return context

    def _count(self, table, where="1=1", params=()):
        connection = get_connection(self.db_path)
        try:
            return connection.execute(
                f"SELECT COUNT(*) FROM {table} WHERE {where}", params
            ).fetchone()[0]
        finally:
            connection.close()

    async def test_lera_start_binds_real_id_and_returns_partner_welcome_only(self):
        update = self._update(731245678, "lerikaDi", "Лера")
        await self._start(update)

        current = get_partner(self.lera["id"], self.db_path)
        self.assertEqual(current["telegram_user_id"], 731245678)
        self.assertEqual(current["telegram_username"], "lerikaDi")
        self.assertEqual(self._count("partners", "name = ?", ("Лера",)), 1)
        self.assertEqual(self._count("clients"), 0)
        self.assertEqual(self._count("cases"), 0)
        welcome = update.message.reply_text.await_args.args[0]
        self.assertIn("Лера, здравствуйте", welcome)
        self.assertIn("рабочий Telegram подключён", welcome)

    async def test_repeated_partner_start_is_idempotent_and_preserves_terms(self):
        update = self._update(731245678, "lerikaDi", "Лера")
        connection = get_connection(self.db_path)
        try:
            terms_before = connection.execute(
                """SELECT term_key, term_value FROM partner_approved_terms
                   WHERE partner_id=? ORDER BY term_key""",
                (self.lera["id"],),
            ).fetchall()
        finally:
            connection.close()

        await self._start(update)
        await self._start(update)

        connection = get_connection(self.db_path)
        try:
            terms_after = connection.execute(
                """SELECT term_key, term_value FROM partner_approved_terms
                   WHERE partner_id=? ORDER BY term_key""",
                (self.lera["id"],),
            ).fetchall()
        finally:
            connection.close()
        self.assertEqual(terms_after, terms_before)
        self.assertEqual(self._count("partners", "name = ?", ("Лера",)), 1)
        self.assertEqual(self._count("clients"), 0)
        self.assertEqual(update.message.reply_text.await_count, 2)

    async def test_changed_username_syncs_by_numeric_identity(self):
        await self._start(self._update(731245678, "lerikaDi", "Лера"))
        update = self._update(731245678, "leraCurrent", "Лера")
        await self._start(update)
        current = get_partner(self.lera["id"], self.db_path)
        self.assertEqual(current["telegram_username"], "leraCurrent")
        self.assertEqual(self._count("partners", "name = ?", ("Лера",)), 1)

    async def test_unknown_user_gets_role_buttons_without_assigned_role(self):
        update = self._update(812345678, "new_client", "Иван")
        await self._start(update)
        welcome = update.message.reply_text.await_args.args[0]
        self.assertEqual(welcome, bot.ROLE_CHOICE_WELCOME)
        markup = update.message.reply_text.await_args.kwargs["reply_markup"]
        labels = [button.text for row in markup.inline_keyboard for button in row]
        self.assertIn("🧳 Нужна услуга", labels)
        self.assertIn("🤝 Хочу стать партнёром", labels)
        self.assertNotIn("комис", welcome.casefold())
        self.assertEqual(self._count("clients", "telegram_id=?", (812345678,)), 0)
        self.assertEqual(self._count("cases"), 0)

    async def test_client_choice_assigns_client_without_empty_case(self):
        update = self._callback_update(812345678, "new_client", "role:client")
        with self._bot_patches():
            await bot.role_choice_callback_handler(update, self._context())
        self.assertEqual(
            update.callback_query.edit_message_text.await_args.args[0],
            bot.CLIENT_START_WELCOME,
        )
        self.assertEqual(self._count("clients", "telegram_id=?", (812345678,)), 1)
        self.assertEqual(self._count("cases"), 0)

    async def test_partner_choice_is_deduplicated_and_grants_no_access(self):
        context = self._context()
        first = self._callback_update(822345678, "applicant", "role:partner")
        second = self._callback_update(822345678, "applicant", "role:partner_new")
        with self._bot_patches():
            await bot.role_choice_callback_handler(first, context)
            await bot.role_choice_callback_handler(second, context)
        application = get_open_application(822345678, self.db_path)
        self.assertEqual(application["status"], "collecting")
        self.assertEqual(self._count("partner_applications"), 1)
        self.assertEqual(self._count("partners", "telegram_user_id=?", (822345678,)), 0)
        self.assertEqual(self._count("clients", "telegram_id=?", (822345678,)), 0)

    async def test_partner_second_choice_appears_only_after_partner_choice(self):
        initial = self._update(822345678, "applicant")
        await self._start(initial)
        initial_markup = initial.message.reply_text.await_args.kwargs["reply_markup"]
        initial_labels = [b.text for row in initial_markup.inline_keyboard for b in row]
        self.assertNotIn("🆕 Я новый партнёр", initial_labels)
        self.assertNotIn("🔄 Я уже сотрудничаю, но сменил аккаунт", initial_labels)

        choice = self._callback_update(822345678, "applicant", "role:partner")
        with self._bot_patches():
            await bot.role_choice_callback_handler(choice, self._context())
        markup = choice.callback_query.edit_message_text.await_args.kwargs["reply_markup"]
        labels = [b.text for row in markup.inline_keyboard for b in row]
        self.assertEqual(labels, [
            "🆕 Я новый партнёр",
            "🔄 Я уже сотрудничаю, но сменил аккаунт",
        ])
        self.assertEqual(self._count("partner_applications"), 0)
        self.assertEqual(self._count("partner_identity_relink_requests"), 0)

    def test_onboarding_callback_data_stays_within_telegram_limit(self):
        maximum = 9223372036854775807
        markup = bot._admin_keyboard([[
            ("confirm", f"relink:confirm:{maximum}:{maximum}"),
            ("application", f"application:approve:{maximum}"),
        ]])
        callbacks = [
            button.callback_data
            for row in markup.inline_keyboard for button in row
        ]
        callbacks.extend([
            button.callback_data
            for keyboard in (bot._role_choice_keyboard(), bot._partner_path_keyboard())
            for row in keyboard.inline_keyboard for button in row
        ])
        self.assertTrue(all(len(value.encode("utf-8")) <= 64 for value in callbacks))

    async def test_application_completion_notifies_only_owner(self):
        application, _ = start_application(822345678, "applicant", self.db_path)
        context = self._context()
        answers = self.APPLICATION_ANSWERS
        with self._bot_patches():
            for answer in answers:
                update = self._update(822345678, "applicant")
                update.message.text = answer
                try:
                    await bot.partner_application_message_handler(update, context)
                except bot.ApplicationHandlerStop:
                    pass
        completed = get_application(application["id"], self.db_path)
        self.assertEqual(completed["status"], "needs_review")
        self.assertEqual(self._count("partners", "telegram_user_id=?", (822345678,)), 0)
        self.assertEqual(context.bot.send_message.await_count, 1)
        self.assertEqual(
            context.bot.send_message.await_args.kwargs["chat_id"], self.admin_id
        )

    async def test_last_optional_question_can_be_skipped(self):
        application, _ = start_application(822345678, "applicant", self.db_path)
        for answer in self.APPLICATION_ANSWERS[:-1]:
            application = record_application_answer(
                application["id"], answer, self.db_path
            )
        self.assertEqual(application["current_step"], "licenses")
        update = self._callback_update(822345678, "applicant", "role:app_skip")
        context = self._context()
        with self._bot_patches():
            await bot.role_choice_callback_handler(update, context)
        completed = get_application(application["id"], self.db_path)
        self.assertEqual(completed["status"], "needs_review")
        context.bot.send_message.assert_awaited_once()
        self.assertIn(
            "передана владельцу",
            update.callback_query.edit_message_text.await_args.args[0],
        )

    async def test_application_can_be_cancelled_and_restarted(self):
        first, _ = start_application(822345678, "applicant", self.db_path)
        cancel_application(822345678, self.db_path)
        second, created = start_application(822345678, "applicant", self.db_path)
        self.assertTrue(created)
        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual(get_application(first["id"], self.db_path)["status"],
                         "cancelled")

    async def test_owner_approve_creates_and_links_active_partner(self):
        application, _ = start_application(822345678, "applicant", self.db_path)
        for answer in self.APPLICATION_ANSWERS:
            application = record_application_answer(
                application["id"], answer, self.db_path
            )
        decided = decide_application(
            application["id"], True, self.admin_id, db_path=self.db_path
        )
        partner = get_partner(decided["partner_id"], self.db_path)
        self.assertEqual(decided["status"], "approved")
        self.assertEqual(partner["status"], "active")
        self.assertEqual(partner["telegram_user_id"], 822345678)
        self.assertEqual(self._count("partners", "telegram_user_id=?", (822345678,)), 1)

    async def test_owner_reject_grants_no_partner_access(self):
        application, _ = start_application(822345678, "applicant", self.db_path)
        for answer in self.APPLICATION_ANSWERS:
            application = record_application_answer(
                application["id"], answer, self.db_path
            )
        decided = decide_application(
            application["id"], False, self.admin_id, db_path=self.db_path
        )
        self.assertEqual(decided["status"], "rejected")
        self.assertIsNone(decided["partner_id"])
        self.assertEqual(self._count("partners", "telegram_user_id=?", (822345678,)), 0)

    async def test_owner_can_approve_from_admin_callback(self):
        application, _ = start_application(822345678, "applicant", self.db_path)
        for answer in self.APPLICATION_ANSWERS:
            application = record_application_answer(
                application["id"], answer, self.db_path
            )
        update = self._callback_update(
            self.admin_id, "owner", f"application:approve:{application['id']}"
        )
        context = self._context()
        with self._bot_patches():
            await bot.admin_callback_handler(update, context)
        decided = get_application(application["id"], self.db_path)
        self.assertEqual(decided["status"], "approved")
        self.assertIsNotNone(decided["partner_id"])
        self.assertIn(
            "Заявка утверждена",
            update.callback_query.edit_message_text.await_args.args[0],
        )
        partner = get_partner(decided["partner_id"], self.db_path)
        self.assertEqual(partner["approved_terms"], {})
        welcome = context.bot.send_message.await_args.kwargs["text"]
        self.assertIn("Как мы работаем", welcome)
        self.assertIn("запрос клиента", welcome)

    async def test_owner_relink_approval_sends_full_welcome(self):
        sync_partner_telegram_identity(731245678, "lerikaDi", self.db_path)
        request, _ = start_relink(8502972477, "Hereld", self.db_path)
        request = record_relink_answer(request["id"], "Лера", self.db_path)
        request = record_relink_answer(request["id"], "@lerikaDi", self.db_path)
        update = self._callback_update(
            self.admin_id, "owner",
            f"relink:confirm:{request['id']}:{self.lera['id']}",
        )
        context = self._context()
        with self._bot_patches():
            await bot.admin_callback_handler(update, context)
        welcome = context.bot.send_message.await_args.kwargs["text"]
        self.assertIn("Как мы работаем", welcome)
        self.assertIn("Коммерческие условия не меняются автоматически", welcome)
        self.assertEqual(
            get_partner(self.lera["id"], self.db_path)["telegram_user_id"],
            8502972477,
        )

    async def test_existing_client_case_is_not_reset(self):
        client_id = get_or_create_client(
            812345678, "known_client", "Иван", None, self.db_path
        )
        connection = get_connection(self.db_path)
        try:
            with connection:
                cursor = connection.execute(
                    """INSERT INTO cases(client_id, title, status, category, data)
                       VALUES (?, 'Аренда', 'in_progress', 'car_rental', '{"days": 5}')""",
                    (client_id,),
                )
                case_id = cursor.lastrowid
        finally:
            connection.close()

        await self._start(self._update(812345678, "known_client", "Иван"))

        connection = get_connection(self.db_path)
        try:
            row = connection.execute(
                "SELECT status, category, data FROM cases WHERE id=?", (case_id,)
            ).fetchone()
        finally:
            connection.close()
        self.assertEqual(row, ("in_progress", "car_rental", '{"days": 5}'))
        self.assertEqual(self._count("cases", "client_id=?", (client_id,)), 1)

    async def test_identity_conflict_does_not_bind_or_create_client(self):
        sync_partner_telegram_identity(731245678, "lerikaDi", self.db_path)
        update = self._update(999888777, "lerikaDi", "Другой пользователь")
        context = await self._start(update)

        current = get_partner(self.lera["id"], self.db_path)
        self.assertEqual(current["telegram_user_id"], 731245678)
        self.assertEqual(self._count("clients", "telegram_id=?", (999888777,)), 0)
        self.assertEqual(update.message.reply_text.await_args.args[0],
                         bot.IDENTITY_CONFLICT_WELCOME)
        context.bot.send_message.assert_awaited_once()
        self.assertEqual(
            context.bot.send_message.await_args.kwargs["chat_id"], self.admin_id
        )

    async def test_admin_has_priority_and_is_not_created_as_client(self):
        update = self._update(self.admin_id, "owner", "Владелец")
        await self._start(update)
        self.assertEqual(update.message.reply_text.await_args.args[0],
                         bot.ADMIN_START_WELCOME)
        self.assertEqual(self._count("clients", "telegram_id=?", (self.admin_id,)), 0)

    async def test_role_welcomes_are_russian_and_use_respectful_you(self):
        for text in (bot.PARTNER_START_WELCOME, bot.CLIENT_START_WELCOME):
            self.assertRegex(text, r"\b(?:Вы|Вас|Вам|Ваш)\b")
            self.assertRegex(text, "[А-Яа-яЁё]")
        self.assertNotIn("commission", bot.CLIENT_START_WELCOME.casefold())

    async def test_early_identity_handler_does_not_process_start_command(self):
        update = self._update(731245678, "lerikaDi", "Лера")
        update.effective_message.message_id = 42
        with patch.object(
            bot, "sync_partner_telegram_identity"
        ) as sync_identity:
            await bot.partner_identity_sync_handler(update, self._context())
        sync_identity.assert_not_called()
        self.assertIsNone(get_partner(self.lera["id"], self.db_path)["telegram_user_id"])


if __name__ == "__main__":
    unittest.main()
