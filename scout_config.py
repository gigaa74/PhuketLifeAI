import os
from dataclasses import dataclass, field

from config import ConfigurationError


SCOUT_TYPES = {"partner", "client"}


@dataclass(frozen=True)
class ScoutSettings:
    scout_type: str
    bot_token: str = field(repr=False)
    allowed_chat_ids: frozenset[int]
    owner_user_id: int | None
    outreach_enabled: bool = False


def _parse_chat_ids(raw, variable_name):
    values = set()
    for item in str(raw or "").split(","):
        item = item.strip()
        if not item:
            continue
        try:
            values.add(int(item))
        except ValueError as error:
            raise ConfigurationError(
                f"{variable_name} должен содержать numeric Telegram chat IDs"
            ) from error
    return frozenset(values)


def _parse_optional_int(raw, variable_name):
    value = str(raw or "").strip()
    if not value:
        return None
    try:
        return int(value)
    except ValueError as error:
        raise ConfigurationError(f"{variable_name} должен быть целым числом") from error


def load_scout_settings(scout_type, environ=None):
    if scout_type not in SCOUT_TYPES:
        raise ConfigurationError("scout_type должен быть partner или client")
    values = os.environ if environ is None else environ
    prefix = scout_type.upper() + "_SCOUT"
    token_name = prefix + "_BOT_TOKEN"
    allowlist_name = prefix + "_ALLOWED_CHAT_IDS"
    token = str(values.get(token_name, "")).strip()
    if not token:
        raise ConfigurationError(f"Отсутствует обязательная переменная {token_name}")
    outreach_enabled = str(
        values.get("SCOUT_OUTREACH_ENABLED", "false")
    ).strip().casefold() in {"1", "true", "yes", "on"}
    return ScoutSettings(
        scout_type=scout_type,
        bot_token=token,
        allowed_chat_ids=_parse_chat_ids(values.get(allowlist_name), allowlist_name),
        owner_user_id=_parse_optional_int(
            values.get("TELEGRAM_ADMIN_USER_ID"), "TELEGRAM_ADMIN_USER_ID"
        ),
        outreach_enabled=outreach_enabled,
    )
