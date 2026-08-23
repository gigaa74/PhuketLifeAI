import json
import sqlite3

from database import DB_NAME, get_connection, init_db
from partner_network import create_partner, get_partner


PARTNER_NAME = "Сергей"
PARTNER_USERNAME = "chudo_ostrov_phuket"
ADDITIONAL_REFERENCE_USERNAME = "malevich_phuket"

APPROVED_SERVICES = [
    "excursions",
    "vehicle_rental",
    "housing_rental",
    "local_delivery",
    "local_assistance",
]

SYSTEM_SERVICE_CATEGORIES = [
    "excursions",
    "car_rental",
    "bike_rental",
    "housing",
    "delivery",
    "guide",
]

OPERATIONAL_DATA = {
    "approved_service_scope": APPROVED_SERVICES,
    "service_category_mapping": {
        "vehicle_rental": ["car_rental", "bike_rental"],
        "housing_rental": "housing",
        "local_delivery": "delivery",
        "local_assistance": "guide",
    },
    "service_area": "Phuket / весь Пхукет",
    "delivery_model": {
        "direct": [
            "помощь на месте",
            "доставка",
            "часть экскурсий",
        ],
        "through_partners": [
            "аренда жилья",
            "аренда транспорта",
            "остальные экскурсии",
        ],
        "direct_client_contact_by_subcontractors": True,
    },
    "business_context": [
        "Большая локальная партнёрская сеть.",
        "Может передавать контакты исполнителей.",
        "Симиланские острова доступны с октября.",
        "По словам Сергея, по Симиланам у него очень низкие цены.",
        "Цены, программа, даты, наличие мест и исполнитель всегда требуют актуального подтверждения.",
        "Конкретный каталог или единая база предложений отсутствуют.",
        "Предложения уточняются через Сергея и его партнёров.",
    ],
    "commercial_context": (
        "Сергей получает процент с каждой успешной сделки; размер, формула "
        "и условия выплаты Phuket Life не утверждены."
    ),
    "open_questions": [
        "размер или формула вознаграждения Phuket Life",
        "от какой суммы рассчитывается комиссия",
        "кто выплачивает комиссию",
        "сроки и способ выплаты",
        "отмены и возвраты",
        "повторные обращения клиента",
        "распределение ответственности при прямом общении клиента с исполнителем",
    ],
    "compliance_exclusions": [
        "обмен рублей или валют",
        "водительские удостоверения",
        "неофициальное решение вопросов со штрафами",
        "вопросы с полицией",
        "иммиграционные вопросы",
        "уголовные или юридические вопросы",
        "международная доставка в РФ",
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
            "Найдено несколько партнёров с именем Сергей"
        )
    existing = by_name[0] if by_name else None
    conflicts = [
        row for row in by_username
        if not existing or row["id"] != existing["id"]
    ]
    if conflicts:
        raise PartnerOnboardingConflict(
            "Telegram username chudo_ostrov_phuket уже связан с другим партнёром"
        )
    return existing


def onboard_sergey(db_path=DB_NAME):
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
            SYSTEM_SERVICE_CATEGORIES,
            areas=["Phuket"],
            status="active",
            telegram_username=PARTNER_USERNAME,
            partner_type="hybrid",
            operational_notes=json.dumps(
                OPERATIONAL_DATA, ensure_ascii=False
            ),
            db_path=db_path,
        )
        connection = get_connection(db_path)
        try:
            with connection:
                connection.execute(
                    "UPDATE partners SET contacts=? WHERE id=?",
                    (
                        json.dumps({
                            "telegram_reference_username": (
                                ADDITIONAL_REFERENCE_USERNAME
                            ),
                            "identity_role": "reference_only",
                        }, ensure_ascii=False),
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
                "Существующий Сергей имеет другой pre-link Telegram username"
            )

    connection = get_connection(db_path)
    try:
        with connection:
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
    partner, created = onboard_sergey()
    print(
        "Сергей создан."
        if created else
        "Сергей уже существует; запись оставлена без перезаписи."
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
