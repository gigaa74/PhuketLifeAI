import threading
import time


class AccessTokenCache:
    """Thread-safe in-memory cache for an expiring access token."""

    def __init__(self, fetch_token, refresh_margin_seconds=60, clock=None):
        self._fetch_token = fetch_token
        self._refresh_margin_seconds = refresh_margin_seconds
        self._clock = clock or time.time
        self._token = None
        self._expires_at = 0
        self._lock = threading.Lock()

    def get(self):
        with self._lock:
            now = self._clock()
            if (
                self._token
                and now < self._expires_at - self._refresh_margin_seconds
            ):
                return self._token

            token, expires_at = self._fetch_token()
            if not token:
                raise ValueError("GigaChat OAuth вернул пустой access token")

            expires_at = float(expires_at)
            if expires_at > 100_000_000_000:
                expires_at /= 1000

            self._token = token
            self._expires_at = expires_at
            return token
