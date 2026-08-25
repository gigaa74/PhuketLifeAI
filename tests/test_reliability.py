import asyncio
import io
import json
import unittest
from contextlib import redirect_stdout
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

from reliability import (
    SlidingWindowRateLimiter,
    retry_call,
    safe_log,
    telegram_error_handler,
)


class ReliabilityTests(unittest.TestCase):
    def test_rate_limiter_is_per_key_and_recovers_after_window(self):
        now = [100.0]
        limiter = SlidingWindowRateLimiter(2, 10, clock=lambda: now[0])

        self.assertTrue(limiter.allow(1))
        self.assertTrue(limiter.allow(1))
        self.assertFalse(limiter.allow(1))
        self.assertTrue(limiter.allow(2))
        now[0] = 110.1
        self.assertTrue(limiter.allow(1))

    def test_retry_uses_bounded_exponential_backoff(self):
        calls = []
        delays = []

        def operation():
            calls.append(True)
            if len(calls) < 3:
                raise TimeoutError("private provider details")
            return "ok"

        result = retry_call(
            operation, attempts=3, base_delay_seconds=0.25,
            sleep=delays.append,
        )

        self.assertEqual(result, "ok")
        self.assertEqual(len(calls), 3)
        self.assertEqual(delays, [0.25, 0.5])

    def test_retry_does_not_repeat_permanent_error(self):
        calls = []

        def operation():
            calls.append(True)
            raise ValueError("invalid request")

        with self.assertRaises(ValueError):
            retry_call(operation, attempts=3, sleep=lambda _: None)
        self.assertEqual(len(calls), 1)

    def test_safe_log_drops_sensitive_fields_and_exception_messages(self):
        output = io.StringIO()
        with redirect_stdout(output):
            safe_log(
                "provider_failed",
                level="error",
                error=TimeoutError("passport 123 secret"),
                provider="yandex",
                prompt_text="private request",
                telegram_username="private_user",
                result_count=2,
            )

        record = json.loads(output.getvalue())
        self.assertEqual(record["event"], "provider_failed")
        self.assertEqual(record["provider"], "yandex")
        self.assertEqual(record["result_count"], 2)
        self.assertEqual(record["exception_type"], "TimeoutError")
        self.assertNotIn("prompt_text", record)
        self.assertNotIn("telegram_username", record)
        self.assertNotIn("passport", output.getvalue())

    def test_telegram_error_handler_logs_retry_after_without_message(self):
        error = RuntimeError("private telegram payload")
        error.retry_after = timedelta(seconds=7)
        with patch("reliability.safe_log") as log:
            asyncio.run(
                telegram_error_handler(None, SimpleNamespace(error=error))
            )
        log.assert_called_once_with(
            "telegram_application_error",
            level="warning",
            error=error,
            retry_after_seconds=7.0,
        )


if __name__ == "__main__":
    unittest.main()
