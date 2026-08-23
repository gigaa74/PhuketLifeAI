import json
import sqlite3

from database import DB_NAME, get_connection, init_db
from partner_network import create_partner, get_partner


PARTNER_NAME = "Инна"
PARTNER_DISPLAY_NAME = "Tranquillo"
PARTNER_USERNAME = "WGoggins"
PARTNER_PHONE = "+381 62 853 0214"

APPROVED_TERMS = {
    "commission_basis": "фактически полученная партнёром комиссия со сделки",
    "partner_commission_share_percent": "70",
    "phuket_life_commission_share_percent": "30",
    "partner_client_responsibility": (
        "Инна полностью ведёт подготовленного клиента и закрывает сделку"
    ),
}

OPERATIONAL_DATA = {
    "display_name": PARTNER_DISPLAY_NAME,
    "approved_direction": "аренда жилья",
    "known_capabilities": [
        {
            "capability": "продажа недвижимости",
            "approval_status": "не утверждено как направление сотрудничества",
        }
    ],
    "request_requirements": {
        "required": ["даты", "бюджет", "район", "наличие транспорта"],
        "additional_if_known": [
            "количество гостей",
            "срок проживания",
            "тип жилья",
            "питомцы",
            "пожелания по удалённости от моря",
        ],
    },
    "business_context": [
        "Опыт работы на Пхукете — более 13 лет.",
        "Большая партнёрская сеть и сотни объектов.",
        "Наличие и цены динамические и зависят от сезона.",
        "Существующий канал или база содержит не все объекты.",
        "Основной трафик сейчас приходит с других площадок, а не из Telegram.",
        "У Инны есть собственный Telegram-бот, но сейчас он практически не используется.",
        "Ориентир комиссии со сделки — обычно около 200–300 USD, иногда около 100 USD при простой сделке.",
        "Небольшие сделки Инна может не брать, но точный минимальный порог не утверждён.",
    ],
    "public_profile_url": "https://t.me/thainvest/8204",
    "open_questions": [
        "районы работы",
        "краткосрочная и/или долгосрочная аренда",
        "минимальный бюджет клиента",
        "готовность принимать запросы на покупку недвижимости",
        "сроки и способ выплаты комиссии Phuket Life",
        "отмены, продления и повторные сделки",
    ],
}


class PartnerOnboardingConflict(RuntimeError):
    pass


def _normalize_username(value):
    return str(value or "").strip().lstrip("@").casefold()


def _find_existing(connection):
    connection.row_factory = sqlite3.Row
    by_name = connection.execute(
        "SELECT * FROM partners WHERE lower(name)=lower(?) ORDER BY id",
        (PARTNER_NAME,),
    ).fetchall()
    by_username = connection.execute(
        """SELECT * FROM partners
           WHERE lower(ltrim(COALESCE(telegram_username, ''), '@'))=lower(?)
           ORDER BY id""",
        (PARTNER_USERNAME,),
    ).fetchall()
    if len(by_name) > 1:
        raise PartnerOnboardingConflict(
            "Найдено несколько партнёров с именем Инна"
        )
    existing = by_name[0] if by_name else None
    conflicts = [
        row for row in by_username
        if not existing or row["id"] != existing["id"]
    ]
    if conflicts:
        raise PartnerOnboardingConflict(
            "Telegram username WGoggins уже связан с другим партнёром"
        )
    return existing


def onboard_inna(db_path=DB_NAME):
    init_db(db_path)
    connection = get_connection(db_path)
    try:
        existing = _find_existing(connection)
    finally:
        connection.close()

    created = existing is None
    if created:
        partner = create_partner(
            PARTNER_NAME,
            ["housing"],
            status="active",
            telegram_username=PARTNER_USERNAME,
            partner_type="hybrid",
            operational_notes=json.dumps(OPERATIONAL_DATA, ensure_ascii=False),
            db_path=db_path,
        )
        connection = get_connection(db_path)
        try:
            with connection:
                connection.execute(
                    "UPDATE partners SET contacts=? WHERE id=?",
                    (
                        json.dumps({"phone": PARTNER_PHONE}, ensure_ascii=False),
                        partner["id"],
                    ),
                )
        finally:
            connection.close()
    else:
        partner = get_partner(existing["id"], db_path)
        if partner.get("telegram_user_id") is None and _normalize_username(
            partner.get("telegram_username")
        ) != _normalize_username(PARTNER_USERNAME):
            raise PartnerOnboardingConflict(
                "Существующая Инна имеет другой pre-link Telegram username"
            )

    connection = get_connection(db_path)
    try:
        with connection:
            for key, value in APPROVED_TERMS.items():
                connection.execute(
                    """INSERT INTO partner_approved_terms
                       (partner_id, term_key, term_value, approved_at, updated_at)
                       VALUES (?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                       ON CONFLICT(partner_id, term_key) DO NOTHING""",
                    (partner["id"], key, value),
                )
            connection.execute(
                """INSERT INTO partner_commercial_audit
                   (partner_id, action, actor_type, details)
                   SELECT ?, 'real_partner_onboarded', 'application', ?
                   WHERE NOT EXISTS (
                       SELECT 1 FROM partner_commercial_audit
                       WHERE partner_id=? AND action='real_partner_onboarded'
                   )""",
                (
                    partner["id"],
                    json.dumps({"created": created}, ensure_ascii=False),
                    partner["id"],
                ),
            )
    finally:
        connection.close()
    return get_partner(partner["id"], db_path), created


def main():
    partner, created = onboard_inna()
    print(
        "Инна создана."
        if created else
        "Инна уже существует; запись оставлена без перезаписи."
    )
    print(json.dumps({
        key: partner.get(key) for key in (
            "id", "name", "partner_type", "status", "telegram_username",
            "telegram_user_id", "services", "areas", "approved_terms",
            "operational_notes",
        )
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
