import inspect
import io
import json
import unittest
import asyncio
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import bot
import client_ai
import client_handler
from truthfulness import GENERATION_DELAY_MESSAGE
from gigachat_provider import (
    GigaChatGenerationError,
    generate_text,
    normalize_response_content,
)


class GigaChatProviderTests(unittest.TestCase):
    @staticmethod
    def sdk_response(text):
        return SimpleNamespace(
            messages=[SimpleNamespace(content=text)]
        )

    @patch("gigachat_provider.GigaChat")
    def test_sdk_generation_success_returns_text(self, sdk_class):
        client = sdk_class.return_value
        client.chat.create.return_value = self.sdk_response("тест")

        result = generate_text(
            [{"role": "user", "content": "test"}],
            access_token="secret-token",
            model="GigaChat-2-Max",
            timeout=30,
            ca_bundle="C:/certs/root.pem",
        )

        self.assertEqual(result, "тест")
        sdk_class.assert_called_once_with(
            access_token="secret-token",
            model="GigaChat-2-Max",
            timeout=30,
            ca_bundle_file="C:/certs/root.pem",
        )
        client.close.assert_called_once_with()

    @patch("gigachat_provider.GigaChat")
    def test_sdk_exception_is_wrapped_and_logged_without_secret(self, sdk_class):
        client = sdk_class.return_value
        client.chat.create.side_effect = RuntimeError("secret-token")
        output = io.StringIO()

        with redirect_stdout(output), self.assertRaises(GigaChatGenerationError):
            generate_text(
                [{"role": "user", "content": "test"}],
                access_token="secret-token",
                model="GigaChat-2-Max",
                timeout=30,
            )

        self.assertIn("RuntimeError", output.getvalue())
        self.assertNotIn("secret-token", output.getvalue())
        client.close.assert_called_once_with()

    @patch("gigachat_provider.GigaChat")
    def test_failure_log_has_safe_generation_metadata(self, sdk_class):
        client_text = "private client request"
        token = "secret-token"
        client = sdk_class.return_value
        transport_error = RuntimeError("private provider response")
        transport_error.__cause__ = TimeoutError("transport detail")
        client.chat.create.side_effect = transport_error
        output = io.StringIO()

        with redirect_stdout(output), self.assertRaises(GigaChatGenerationError):
            generate_text(
                [{"role": "user", "content": client_text}],
                access_token=token,
                model="GigaChat-2-Max",
                timeout=30,
                stage="analyze_case",
                correlation_id=12345,
            )

        log = output.getvalue()
        record = json.loads(log)
        self.assertEqual(record["correlation_id"], 12345)
        self.assertEqual(record["stage"], "analyze_case")
        self.assertIn("latency_ms", record)
        self.assertEqual(record["prompt_chars"], len(client_text))
        self.assertEqual(record["prompt_bytes"], len(client_text.encode("utf-8")))
        self.assertEqual(record["messages_count"], 1)
        self.assertEqual(record["exception_type"], "RuntimeError")
        self.assertEqual(record["cause_type"], "TimeoutError")
        for secret in (client_text, token, "private provider response", "transport detail"):
            self.assertNotIn(secret, log)

    @patch("reliability.time.sleep", return_value=None)
    @patch("gigachat_provider.GigaChat")
    def test_transient_sdk_failure_is_retried(self, sdk_class, sleep):
        client = sdk_class.return_value
        client.chat.create.side_effect = [
            TimeoutError("temporary private detail"),
            self.sdk_response("готово"),
        ]

        result = generate_text(
            [{"role": "user", "content": "test"}],
            access_token="secret-token",
            model="GigaChat-2-Max",
            timeout=30,
            retry_attempts=2,
            retry_base_delay_seconds=0.1,
        )

        self.assertEqual(result, "готово")
        self.assertEqual(client.chat.create.call_count, 2)
        sleep.assert_called_once_with(0.1)

    def test_client_rate_limit_stops_before_database_and_generation(self):
        update = SimpleNamespace(
            effective_user=SimpleNamespace(id=555),
            message=SimpleNamespace(
                text="Ищу жильё",
                reply_text=AsyncMock(),
            ),
        )
        with (
            patch("client_handler.CLIENT_RATE_LIMITER.allow", return_value=False),
            patch("client_handler.get_or_create_client") as get_client,
            patch("client_handler.ask_gigachat") as generation,
        ):
            asyncio.run(bot.handle_message(update, SimpleNamespace()))

        get_client.assert_not_called()
        generation.assert_not_called()
        update.message.reply_text.assert_awaited_once_with(bot.RATE_LIMIT_MESSAGE)

    def test_analyze_transport_failure_stops_second_generation(self):
        update = SimpleNamespace(
            update_id=987,
            message=SimpleNamespace(
                text="Ищу жильё на Пхукете",
                reply_text=AsyncMock(),
            ),
        )
        response_plan = SimpleNamespace(
            trusted_facts={},
            case_continuity="not_applicable",
            mode="action",
            standard_id="STD-001",
            standard_version="1.0",
            next_action="create_case",
        )
        routing = {"intent": "new_case", "category": "housing"}

        with (
            patch("client_handler.get_or_create_client", return_value=1),
            patch("client_handler.save_message"),
            patch("client_handler.pure_greeting_response", return_value=None),
            patch("client_handler.get_history", return_value=[]),
            patch("client_handler.get_client_active_case", return_value=None),
            patch("client_handler.plan_response", return_value=response_plan),
            patch("client_handler.select_answer_source", return_value="model"),
            patch("client_handler.route_with_conversation_policy", return_value=routing),
            patch("client_handler.apply_case_continuity", return_value=routing),
            patch("client_handler.should_use_conversation_flow", return_value=False),
            patch(
                "client_handler.run_blocking",
                new=AsyncMock(side_effect=GigaChatGenerationError),
            ) as run_blocking,
            patch("client_handler.persist_case_analysis") as persist_case,
            patch("client_handler.ask_gigachat") as ask_gigachat,
        ):
            asyncio.run(bot.handle_message(update, SimpleNamespace()))

        self.assertEqual(run_blocking.await_count, 1)
        ask_gigachat.assert_not_called()
        persist_case.assert_not_called()
        update.message.reply_text.assert_awaited_once_with(GENERATION_DELAY_MESSAGE)
        safe_message = update.message.reply_text.await_args.args[0]
        for internal_name in ("ReadTimeout", "GigaChat", "SDK", "httpx"):
            self.assertNotIn(internal_name.casefold(), safe_message.casefold())

    def test_application_analysis_error_is_not_classified_as_transport(self):
        update = SimpleNamespace(
            update_id=988,
            message=SimpleNamespace(
                text="Ищу жильё на Пхукете",
                reply_text=AsyncMock(),
            ),
        )
        response_plan = SimpleNamespace(
            trusted_facts={},
            case_continuity="not_applicable",
            mode="action",
            standard_id="STD-001",
            standard_version="1.0",
            next_action="create_case",
        )
        routing = {"intent": "new_case", "category": "housing"}
        blocking = AsyncMock(side_effect=[ValueError("invalid JSON"), "safe answer"])

        with (
            patch("client_handler.get_or_create_client", return_value=1),
            patch("client_handler.save_message"),
            patch("client_handler.pure_greeting_response", return_value=None),
            patch("client_handler.get_history", return_value=[]),
            patch("client_handler.get_client_active_case", return_value=None),
            patch("client_handler.plan_response", return_value=response_plan),
            patch("client_handler.select_answer_source", return_value="model"),
            patch("client_handler.route_with_conversation_policy", return_value=routing),
            patch("client_handler.apply_case_continuity", return_value=routing),
            patch("client_handler.should_use_conversation_flow", return_value=False),
            patch("client_handler.run_blocking", new=blocking),
            patch("client_handler.guard_policy_answer", side_effect=lambda text, plan: text),
            patch("client_handler.guard_client_voice", side_effect=lambda text, message: text),
        ):
            asyncio.run(bot.handle_message(update, SimpleNamespace()))

        self.assertEqual(blocking.await_count, 2)
        update.message.reply_text.assert_awaited_once_with("safe answer")
        self.assertNotEqual("safe answer", GENERATION_DELAY_MESSAGE)

    def test_real_capability_regression_does_not_execute_old_housing_case(self):
        message = "Привет, чем Phuket Life может помочь?"
        active_case = {
            "id": 77,
            "category": "housing",
            "title": "Поиск жилья на Пхукете",
            "status": "ready_for_search",
            "data": {
                "people": "3",
                "arrival_date": "15.09",
                "departure_date": "15.10",
                "budget": "150000",
            },
            "missing_data": [],
        }
        update = SimpleNamespace(
            update_id=1001,
            message=SimpleNamespace(text=message, reply_text=AsyncMock()),
        )

        with (
            patch("client_handler.get_or_create_client", return_value=1),
            patch("client_handler.save_message"),
            patch(
                "client_handler.get_history",
                return_value=[{"role": "user", "content": message}],
            ),
            patch("client_handler.get_client_active_case", return_value=active_case),
            patch("client_handler.format_case_for_ai", return_value="existing housing case"),
            patch("client_handler.persist_case_analysis") as persist_case,
            patch("client_handler.execute_housing_search", new=AsyncMock()) as search,
            patch("client_handler.ask_gigachat") as generation,
        ):
            asyncio.run(bot.handle_message(update, SimpleNamespace()))

        persist_case.assert_not_called()
        search.assert_not_awaited()
        generation.assert_not_called()
        update.message.reply_text.assert_awaited_once()
        answer = update.message.reply_text.await_args.args[0]
        self.assertIn("concierge-компаньон", answer)

    @patch("client_ai.get_access_token", return_value="token")
    @patch("client_ai.generate_text")
    def test_successful_analyze_case_flow_is_unchanged(self, generation, _token):
        generation.return_value = (
            '{"category":"housing","title":"Поиск жилья","data":{},'
            '"missing_data":["budget"]}'
        )

        result = bot.analyze_case(
            [{"role": "user", "content": "Ищу жильё"}],
            correlation_id=321,
        )

        self.assertEqual(result["category"], "housing")
        self.assertEqual(result["missing_data"], ["budget"])
        self.assertEqual(generation.call_args.kwargs["stage"], "analyze_case")
        self.assertEqual(generation.call_args.kwargs["correlation_id"], 321)

    @patch("client_ai.get_access_token", return_value="token")
    @patch("client_ai.generate_text", return_value="Нормальный ответ")
    def test_normal_conversation_generation_is_unchanged(self, generation, _token):
        result = bot.ask_gigachat(
            [{"role": "user", "content": "Добрый день"}],
            correlation_id=654,
        )

        self.assertEqual(result, "Нормальный ответ")
        self.assertEqual(generation.call_args.kwargs["stage"], "conversation")
        self.assertEqual(generation.call_args.kwargs["correlation_id"], 654)

    def test_telegram_handler_offloads_blocking_generation(self):
        source = inspect.getsource(bot.handle_message)
        self.assertIn("await run_blocking(\n                ask_gigachat", source)
        self.assertIn("await run_blocking(\n                analyze_case", source)

    def test_plain_string_content_remains_plain_string(self):
        self.assertEqual(normalize_response_content("готово"), "готово")

    def test_one_structured_content_part_returns_its_text(self):
        part = SimpleNamespace(text="готово", files=None, function_call=None)
        self.assertEqual(normalize_response_content([part]), "готово")

    def test_multiple_text_parts_are_concatenated(self):
        parts = [SimpleNamespace(text="первая "), {"text": "часть"}]
        self.assertEqual(normalize_response_content(parts), "первая часть")

    def test_empty_or_unexpected_content_raises_controlled_error(self):
        for content in (None, [], "", [SimpleNamespace(files=[])]):
            with self.subTest(content=type(content).__name__):
                with self.assertRaises(GigaChatGenerationError):
                    normalize_response_content(content)

    def test_structured_object_repr_never_becomes_client_text(self):
        part = SimpleNamespace(
            text="ответ", function_call={"name": "internal"}, files=["secret"]
        )
        result = normalize_response_content([part])
        self.assertEqual(result, "ответ")
        self.assertNotIn("namespace", result.casefold())
        self.assertNotIn("function_call", result)


if __name__ == "__main__":
    unittest.main()
