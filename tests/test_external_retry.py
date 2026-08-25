import unittest
from unittest.mock import Mock, patch

import requests

import bot
import client_ai
from yandex_provider import YandexSearchProvider


class ExternalRetryTests(unittest.TestCase):
    @patch("reliability.time.sleep", return_value=None)
    @patch("yandex_provider.requests.post")
    def test_yandex_transient_failure_is_retried(self, post, sleep):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"rawData": None}
        post.side_effect = [requests.Timeout("private"), response]
        provider = YandexSearchProvider.__new__(YandexSearchProvider)
        provider.api_key = "secret"
        provider.folder_id = "folder"
        provider.url = "https://example.invalid"
        provider.retry_attempts = 2
        provider.retry_base_delay_seconds = 0.1
        provider._build_queries = Mock(return_value=["safe query"])

        self.assertEqual(provider.search({"result_limit": 1}), [])
        self.assertEqual(post.call_count, 2)
        sleep.assert_called_once_with(0.1)

    @patch("reliability.time.sleep", return_value=None)
    @patch("client_ai.requests.post")
    def test_oauth_transient_failure_is_retried(self, post, sleep):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "access_token": "new-token",
            "expires_at": 123456,
        }
        post.side_effect = [requests.ConnectionError("private"), response]

        token, expires_at = bot._fetch_access_token()

        self.assertEqual(token, "new-token")
        self.assertEqual(expires_at, 123456)
        self.assertEqual(post.call_count, 2)
        sleep.assert_called_once_with(
            bot.SETTINGS.external_retry_base_delay_seconds
        )


if __name__ == "__main__":
    unittest.main()
