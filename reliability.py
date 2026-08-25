"""Small reliability primitives shared by production entry points."""

from collections import defaultdict, deque
import json
import threading
import time


_SENSITIVE_FRAGMENTS = (
    "address", "authorization", "contact", "email", "message", "payload",
    "phone", "prompt", "query", "response", "secret", "text", "token",
    "url", "username",
)
_SAFE_AGGREGATES = {
    "messages_count", "prompt_bytes", "prompt_chars", "result_count",
}


def safe_log(event, *, level="info", error=None, **fields):
    """Write one JSON log line containing metadata only, never raw payloads."""
    record = {"event": str(event), "level": str(level)}
    for key, value in fields.items():
        normalized_key = str(key).casefold()
        if (
            normalized_key not in _SAFE_AGGREGATES
            and any(fragment in normalized_key for fragment in _SENSITIVE_FRAGMENTS)
        ):
            continue
        if value is None or isinstance(value, (str, int, float, bool)):
            record[str(key)] = value
    if error is not None:
        record["exception_type"] = type(error).__name__
        cause = error.__cause__ or error.__context__
        if cause is not None:
            record["cause_type"] = type(cause).__name__
    print(json.dumps(record, ensure_ascii=False, sort_keys=True))


def is_transient_error(error):
    """Return True only for failures that can reasonably succeed on retry."""
    current = error
    seen = set()
    transient_names = (
        "connectionerror", "connecterror", "networkerror", "readtimeout",
        "servererror", "temporarilyunavailable", "timeout", "timeouterror",
    )
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, (ConnectionError, TimeoutError)):
            return True
        if any(name in type(current).__name__.casefold() for name in transient_names):
            return True
        response = getattr(current, "response", None)
        status_code = (
            getattr(current, "status_code", None)
            or getattr(response, "status_code", None)
        )
        if status_code == 429 or (isinstance(status_code, int) and status_code >= 500):
            return True
        current = current.__cause__ or current.__context__
    return False


def retry_call(
    operation,
    *,
    attempts=3,
    base_delay_seconds=0.5,
    retry_if=is_transient_error,
    sleep=None,
):
    """Run a synchronous operation with bounded exponential backoff."""
    attempts = max(1, int(attempts))
    delay = max(0.0, float(base_delay_seconds))
    sleep = sleep or time.sleep
    for attempt in range(1, attempts + 1):
        try:
            return operation()
        except Exception as error:
            if attempt >= attempts or not retry_if(error):
                raise
            sleep(delay)
            delay *= 2


class SlidingWindowRateLimiter:
    """Thread-safe per-key request limiter with no persistent user data."""

    def __init__(self, limit, window_seconds, *, clock=time.monotonic):
        self.limit = max(1, int(limit))
        self.window_seconds = max(0.001, float(window_seconds))
        self._clock = clock
        self._events = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key):
        now = self._clock()
        cutoff = now - self.window_seconds
        with self._lock:
            events = self._events[key]
            while events and events[0] <= cutoff:
                events.popleft()
            if len(events) >= self.limit:
                return False
            events.append(now)
            return True


async def telegram_error_handler(_update, context):
    """Consume Telegram application errors without exposing payloads or tokens."""
    error = getattr(context, "error", None)
    retry_after = getattr(error, "retry_after", None)
    if hasattr(retry_after, "total_seconds"):
        retry_after = retry_after.total_seconds()
    fields = {}
    if isinstance(retry_after, (int, float)):
        fields["retry_after_seconds"] = max(0.0, float(retry_after))
    safe_log(
        "telegram_application_error",
        level="warning" if retry_after is not None else "error",
        error=error,
        **fields,
    )
