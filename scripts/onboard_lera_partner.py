import json
import sqlite3

from database import DB_NAME, get_connection, init_db
from partner_network import create_partner, get_partner


PARTNER_NAME = "Лера"
PARTNER_USERNAME = "lerikaDi"

APPROVED_TERMS = {
    "base_commission_percent": "10",
    "commission_ladder_5_successful_deals_percent": "11",
    "commission_ladder_10_successful_deals_percent": "12",
    "commission_ladder_30_successful_deals_percent": "14",
    "client_referral_before_booking": (
        "Phuket Life передаёт имя/контакт клиента и сообщает, что клиент "
        "обратился от Phuket Life"
    ),
    "housing_invoice_issuer": "Лера или её руководитель",
    "partner_reward_invoice_process": (
        "После успешной сделки Phuket Life направляет отдельный счёт "
        "на партнёрское вознаграждение"
    ),
}

OPERATIONAL_DATA = {
    "direction": "жильё / апартаменты",
    "areas_context": ["Karon", "Bang Tao / Bellevue"],
    "inventory_context": {
        "complex_1_approx_apartments": 13,
        "complex_2_approx_apartments": 6,
        "warning": "Количество может меняться; это не live availability.",
    },
    "live_availability_source": "https://media.zdravkov.net/byroom/broker.html",
    "availability_policy": (
        "Наличие и цены подтверждать через live source или ответ партнёра."
    ),
    "known_capabilities": [
        "Партнёр в принципе работает с криптовалютой; схема взаиморасчётов "
        "Phuket Life с партнёром не утверждена."
    ],
    "open_questions": [
        "способы оплаты бронирования клиентом",
        "комиссия при отмене бронирования",
        "комиссия при переносе бронирования",
        "конкретная crypto payment scheme",
        "валюты и networks для crypto",
        "дополнительные коммерческие условия",
    ],
}


class PartnerOnboardingConflict(RuntimeError):
    pass


def _normalize_username(value):
    return str(value or "").strip().lstrip("@").casefold()


def _find_existing(connection):
    connection.row_factory = sqlite3.Row
    by_name = connection.execute(
        "SELECT * FROM partners WHERE lower(name) = lower(?) ORDER BY id",
        (PARTNER_NAME,),
    ).fetchall()
    username_rows = connection.execute(
        """SELECT * FROM partners
           WHERE lower(ltrim(COALESCE(telegram_username, ''), '@')) = lower(?)
           ORDER BY id""",
        (PARTNER_USERNAME,),
    ).fetchall()
    if len(by_name) > 1:
        raise PartnerOnboardingConflict("Найдено несколько партнёров с именем Лера")
    existing = by_name[0] if by_name else None
    conflicts = [row for row in username_rows if not existing or row["id"] != existing["id"]]
    if conflicts:
        raise PartnerOnboardingConflict(
            "Telegram username lerikaDi уже связан с другим партнёром"
        )
    return existing


def onboard_lera(db_path=DB_NAME):
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
            areas=["Karon", "Bang Tao"],
            status="active",
            telegram_username=PARTNER_USERNAME,
            partner_type="hybrid",
            operational_notes=json.dumps(OPERATIONAL_DATA, ensure_ascii=False),
            db_path=db_path,
        )
    else:
        partner = get_partner(existing["id"], db_path)
        if partner.get("telegram_user_id") is None and _normalize_username(
            partner.get("telegram_username")
        ) != _normalize_username(PARTNER_USERNAME):
            raise PartnerOnboardingConflict(
                "Существующая Лера имеет другой pre-link Telegram username"
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
    partner, created = onboard_lera()
    print("Лера создана." if created else "Лера уже существует; запись обновлена идемпотентно.")
    print(json.dumps({
        key: partner.get(key) for key in (
            "id", "name", "partner_type", "status", "telegram_username",
            "telegram_user_id", "services", "areas", "approved_terms",
            "pending_terms", "operational_notes",
        )
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
