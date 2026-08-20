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


if __name__ == "__main__":
    unittest.main()
