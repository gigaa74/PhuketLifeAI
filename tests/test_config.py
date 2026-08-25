import tempfile
import unittest
from pathlib import Path

from config import ConfigurationError, load_settings


class ConfigTests(unittest.TestCase):
    def test_missing_variables_are_reported_by_name(self):
        with self.assertRaises(ConfigurationError) as error:
            load_settings({})
        message = str(error.exception)
        self.assertIn("TELEGRAM_BOT_TOKEN", message)
        self.assertIn("YANDEX_FOLDER_ID", message)

    def test_optional_ca_bundle_is_used_for_tls_verification(self):
        with tempfile.TemporaryDirectory() as directory:
            bundle = Path(directory) / "ca.pem"
            bundle.touch()
            settings = load_settings(
                {
                    "TELEGRAM_BOT_TOKEN": "telegram-secret",
                    "GIGACHAT_API_KEY": "gigachat-secret",
                    "YANDEX_SEARCH_API_KEY": "yandex-secret",
                    "YANDEX_FOLDER_ID": "folder-id",
                    "GIGACHAT_CA_BUNDLE": str(bundle),
                }
            )
        self.assertEqual(settings.gigachat_tls_verify, str(bundle))

    def test_optional_admin_user_id_is_parsed(self):
        settings = load_settings(
            {
                "TELEGRAM_BOT_TOKEN": "telegram-secret",
                "GIGACHAT_API_KEY": "gigachat-secret",
                "YANDEX_SEARCH_API_KEY": "yandex-secret",
                "YANDEX_FOLDER_ID": "folder-id",
                "TELEGRAM_ADMIN_USER_ID": "123456",
            }
        )
        self.assertEqual(settings.telegram_admin_user_id, 123456)

    def test_partner_handoff_defaults_to_review_and_accepts_hybrid(self):
        base = {
            "TELEGRAM_BOT_TOKEN": "telegram-secret",
            "GIGACHAT_API_KEY": "gigachat-secret",
            "YANDEX_SEARCH_API_KEY": "yandex-secret",
            "YANDEX_FOLDER_ID": "folder-id",
        }
        self.assertEqual(load_settings(base).partner_handoff_mode, "review")
        self.assertEqual(
            load_settings({**base, "PARTNER_HANDOFF_MODE": "hybrid"}).partner_handoff_mode,
            "hybrid",
        )

    def test_gigachat_timeout_is_configurable_and_positive(self):
        base = {
            "TELEGRAM_BOT_TOKEN": "telegram-secret",
            "GIGACHAT_API_KEY": "gigachat-secret",
            "YANDEX_SEARCH_API_KEY": "yandex-secret",
            "YANDEX_FOLDER_ID": "folder-id",
        }
        self.assertEqual(load_settings(base).gigachat_timeout_seconds, 30.0)
        configured = load_settings({**base, "GIGACHAT_TIMEOUT_SECONDS": "12.5"})
        self.assertEqual(configured.gigachat_timeout_seconds, 12.5)
        with self.assertRaises(ConfigurationError):
            load_settings({**base, "GIGACHAT_TIMEOUT_SECONDS": "0"})

    def test_reliability_controls_have_safe_defaults_and_validate(self):
        base = {
            "TELEGRAM_BOT_TOKEN": "telegram-secret",
            "GIGACHAT_API_KEY": "gigachat-secret",
            "YANDEX_SEARCH_API_KEY": "yandex-secret",
            "YANDEX_FOLDER_ID": "folder-id",
        }
        settings = load_settings(base)
        self.assertEqual(settings.client_rate_limit_requests, 10)
        self.assertEqual(settings.client_rate_limit_window_seconds, 60.0)
        self.assertEqual(settings.external_retry_attempts, 3)
        self.assertEqual(settings.external_retry_base_delay_seconds, 0.5)

        configured = load_settings({
            **base,
            "CLIENT_RATE_LIMIT_REQUESTS": "4",
            "CLIENT_RATE_LIMIT_WINDOW_SECONDS": "15",
            "EXTERNAL_RETRY_ATTEMPTS": "2",
            "EXTERNAL_RETRY_BASE_DELAY_SECONDS": "0.1",
        })
        self.assertEqual(configured.client_rate_limit_requests, 4)
        self.assertEqual(configured.external_retry_attempts, 2)
        with self.assertRaises(ConfigurationError):
            load_settings({**base, "EXTERNAL_RETRY_ATTEMPTS": "0"})
        with self.assertRaises(ConfigurationError):
            load_settings({**base, "EXTERNAL_RETRY_ATTEMPTS": "6"})


if __name__ == "__main__":
    unittest.main()
