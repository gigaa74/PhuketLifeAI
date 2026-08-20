import unittest

from token_cache import AccessTokenCache


class TokenCacheTests(unittest.TestCase):
    def test_valid_token_is_reused(self):
        now = [1000.0]
        calls = []

        def fetch():
            calls.append(True)
            return "token-one", 2000

        cache = AccessTokenCache(fetch, clock=lambda: now[0])
        self.assertEqual(cache.get(), "token-one")
        self.assertEqual(cache.get(), "token-one")
        self.assertEqual(len(calls), 1)

    def test_expired_token_is_refreshed(self):
        now = [1000.0]
        tokens = iter((("token-one", 1100), ("token-two", 2200)))
        cache = AccessTokenCache(lambda: next(tokens), clock=lambda: now[0])

        self.assertEqual(cache.get(), "token-one")
        now[0] = 1200
        self.assertEqual(cache.get(), "token-two")


if __name__ == "__main__":
    unittest.main()
