import os
from dataclasses import dataclass
from pathlib import Path


class ConfigurationError(RuntimeError):
    """Raised when required application configuration is missing or invalid."""


@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str
    gigachat_api_key: str
    yandex_search_api_key: str
    yandex_folder_id: str
    gigachat_ca_bundle: str | None = None
    telegram_admin_user_id: int | None = None
    partner_handoff_mode: str = "review"
    gigachat_timeout_seconds: float = 30.0
    client_rate_limit_requests: int = 10
    client_rate_limit_window_seconds: float = 60.0
    external_retry_attempts: int = 3
    external_retry_base_delay_seconds: float = 0.5

    @property
    def gigachat_tls_verify(self):
        return self.gigachat_ca_bundle or True


def load_settings(environ=None):
    """Load and validate settings without exposing secret values."""
    values = os.environ if environ is None else environ
    required = (
        "TELEGRAM_BOT_TOKEN",
        "GIGACHAT_API_KEY",
        "YANDEX_SEARCH_API_KEY",
        "YANDEX_FOLDER_ID",
    )
    missing = [name for name in required if not values.get(name, "").strip()]
    if missing:
        raise ConfigurationError(
            "Отсутствуют обязательные переменные окружения: "
            + ", ".join(missing)
        )

    ca_bundle = values.get("GIGACHAT_CA_BUNDLE", "").strip() or None
    if ca_bundle and not Path(ca_bundle).is_file():
        raise ConfigurationError(
            "Файл сертификатов из GIGACHAT_CA_BUNDLE не найден: "
            + ca_bundle
        )

    admin_user_id_text = values.get("TELEGRAM_ADMIN_USER_ID", "").strip()
    try:
        admin_user_id = int(admin_user_id_text) if admin_user_id_text else None
    except ValueError as error:
        raise ConfigurationError(
            "TELEGRAM_ADMIN_USER_ID должен быть целым числом"
        ) from error

    handoff_mode = values.get("PARTNER_HANDOFF_MODE", "review").strip().lower()
    if handoff_mode not in ("review", "hybrid"):
        raise ConfigurationError(
            "PARTNER_HANDOFF_MODE должен быть review или hybrid"
        )

    timeout_text = values.get("GIGACHAT_TIMEOUT_SECONDS", "30").strip()
    try:
        gigachat_timeout_seconds = float(timeout_text)
    except ValueError as error:
        raise ConfigurationError(
            "GIGACHAT_TIMEOUT_SECONDS должен быть положительным числом"
        ) from error
    if gigachat_timeout_seconds <= 0:
        raise ConfigurationError(
            "GIGACHAT_TIMEOUT_SECONDS должен быть положительным числом"
        )

    def positive_number(name, default, cast, maximum=None):
        raw_value = values.get(name, str(default)).strip()
        try:
            parsed = cast(raw_value)
        except ValueError as error:
            raise ConfigurationError(
                f"{name} должен быть положительным числом"
            ) from error
        if parsed <= 0:
            raise ConfigurationError(
                f"{name} должен быть положительным числом"
            )
        if maximum is not None and parsed > maximum:
            raise ConfigurationError(
                f"{name} не должен превышать {maximum}"
            )
        return parsed

    client_rate_limit_requests = positive_number(
        "CLIENT_RATE_LIMIT_REQUESTS", 10, int, 1000
    )
    client_rate_limit_window_seconds = positive_number(
        "CLIENT_RATE_LIMIT_WINDOW_SECONDS", 60, float, 86400
    )
    external_retry_attempts = positive_number(
        "EXTERNAL_RETRY_ATTEMPTS", 3, int, 5
    )
    external_retry_base_delay_seconds = positive_number(
        "EXTERNAL_RETRY_BASE_DELAY_SECONDS", 0.5, float, 60
    )

    return Settings(
        telegram_bot_token=values["TELEGRAM_BOT_TOKEN"].strip(),
        gigachat_api_key=values["GIGACHAT_API_KEY"].strip(),
        yandex_search_api_key=values["YANDEX_SEARCH_API_KEY"].strip(),
        yandex_folder_id=values["YANDEX_FOLDER_ID"].strip(),
        gigachat_ca_bundle=ca_bundle,
        telegram_admin_user_id=admin_user_id,
        partner_handoff_mode=handoff_mode,
        gigachat_timeout_seconds=gigachat_timeout_seconds,
        client_rate_limit_requests=client_rate_limit_requests,
        client_rate_limit_window_seconds=client_rate_limit_window_seconds,
        external_retry_attempts=external_retry_attempts,
        external_retry_base_delay_seconds=external_retry_base_delay_seconds,
    )
