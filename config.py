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

    return Settings(
        telegram_bot_token=values["TELEGRAM_BOT_TOKEN"].strip(),
        gigachat_api_key=values["GIGACHAT_API_KEY"].strip(),
        yandex_search_api_key=values["YANDEX_SEARCH_API_KEY"].strip(),
        yandex_folder_id=values["YANDEX_FOLDER_ID"].strip(),
        gigachat_ca_bundle=ca_bundle,
    )
