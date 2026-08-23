import argparse
import json
import sqlite3

from database import DB_NAME, get_connection


CANONICAL_PARTNER_ID = 3
DUPLICATE_PARTNER_ID = 6
APPLICATION_ID = 1
OLD_USER_ID = 1905717582
NEW_USER_ID = 8502972477
OLD_USERNAME = "lerikaDi"
NEW_USERNAME = "Hereld"


class LeraRepairConflict(RuntimeError):
    pass


DEPENDENT_TABLES = (
    "partner_requests", "partner_offers", "partner_approved_terms",
    "partner_term_proposals", "partner_commercial_audit",
    "partner_applications", "partner_referral_requests",
    "partner_identity_relink_requests",
)


def _schema_partner_dependencies(connection):
    result = {}
    table_names = [
        row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
    ]
    for table in table_names:
        columns = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
        if "partner_id" in columns:
            result[table] = "partner_id"
        if "selected_partner_id" in columns:
            result[table] = "selected_partner_id"
    return result


def _row(connection, partner_id):
    connection.row_factory = sqlite3.Row
    row = connection.execute(
        "SELECT * FROM partners WHERE id=?", (partner_id,)
    ).fetchone()
    return dict(row) if row else None


def _validate(connection):
    canonical = _row(connection, CANONICAL_PARTNER_ID)
    duplicate = _row(connection, DUPLICATE_PARTNER_ID)
    if not canonical or canonical["name"] != "Лера":
        raise LeraRepairConflict("Каноническая запись Леры не совпадает с ожиданиями")
    if (canonical["telegram_user_id"] != OLD_USER_ID or
            str(canonical["telegram_username"] or "").casefold() != OLD_USERNAME.casefold()):
        raise LeraRepairConflict("Прежняя identity канонической Леры неожиданна")
    if not duplicate or duplicate["name"] != "Валерия":
        raise LeraRepairConflict("Дублирующая запись Валерии не найдена")
    if (duplicate["telegram_user_id"] != NEW_USER_ID or
            duplicate["telegram_username"] is not None):
        raise LeraRepairConflict("Новая numeric identity неожиданна")
    application = connection.execute(
        """SELECT status, partner_id, telegram_user_id, contact_text
           FROM partner_applications WHERE id=?""", (APPLICATION_ID,)
    ).fetchone()
    if not application or tuple(application[:3]) != (
        "approved", DUPLICATE_PARTNER_ID, NEW_USER_ID
    ):
        raise LeraRepairConflict("Заявка Леры не совпадает с ожиданиями")
    if str(application[3] or "").strip().lstrip("@").casefold() != NEW_USERNAME.casefold():
        raise LeraRepairConflict("Контакт заявки Леры неожиданен")
    counts = {
        "canonical_terms": connection.execute(
            "SELECT COUNT(*) FROM partner_approved_terms WHERE partner_id=3"
        ).fetchone()[0],
        "canonical_audit": connection.execute(
            "SELECT COUNT(*) FROM partner_commercial_audit WHERE partner_id=3"
        ).fetchone()[0],
        "duplicate_terms": connection.execute(
            "SELECT COUNT(*) FROM partner_approved_terms WHERE partner_id=6"
        ).fetchone()[0],
        "duplicate_referrals": connection.execute(
            "SELECT COUNT(*) FROM partner_referral_requests WHERE partner_id=6"
        ).fetchone()[0],
    }
    if (canonical.get("partner_type") != "hybrid" or
            counts != {"canonical_terms": 7, "canonical_audit": 2,
                       "duplicate_terms": 0, "duplicate_referrals": 0}):
        raise LeraRepairConflict("Production-состояние Леры неожиданно")
    conflict = connection.execute(
        """SELECT id FROM partners WHERE id NOT IN (?, ?) AND
           (telegram_user_id=? OR lower(ltrim(COALESCE(telegram_username,''),'@'))=lower(?))""",
        (CANONICAL_PARTNER_ID, DUPLICATE_PARTNER_ID, NEW_USER_ID, NEW_USERNAME),
    ).fetchone()
    if conflict:
        raise LeraRepairConflict("Новая identity конфликтует с другим партнёром")
    dependencies = _schema_partner_dependencies(connection)
    expected = {
        table: ("selected_partner_id" if table == "partner_identity_relink_requests"
                else "partner_id")
        for table in DEPENDENT_TABLES
    }
    if dependencies != expected:
        raise LeraRepairConflict("Обнаружена неожиданная таблица зависимости партнёра")
    return canonical, duplicate


def repair_lera_identity(db_path=DB_NAME, dry_run=True):
    connection = get_connection(db_path)
    connection.row_factory = sqlite3.Row
    try:
        canonical = _row(connection, CANONICAL_PARTNER_ID)
        duplicate = _row(connection, DUPLICATE_PARTNER_ID)
        if (canonical and not duplicate and
                canonical["telegram_user_id"] == NEW_USER_ID and
                str(canonical["telegram_username"] or "").casefold() == NEW_USERNAME.casefold()):
            return {"changed": False, "dry_run": dry_run, "partner_id": CANONICAL_PARTNER_ID}
        canonical, duplicate = _validate(connection)
        if dry_run:
            return {"changed": True, "dry_run": True, "partner_id": CANONICAL_PARTNER_ID}
        try:
            with connection:
                try:
                    contacts = json.loads(canonical.get("contacts") or "{}")
                    if not isinstance(contacts, dict):
                        contacts = {"legacy_contacts": canonical.get("contacts")}
                except (TypeError, json.JSONDecodeError):
                    contacts = {"legacy_contacts": canonical.get("contacts")}
                contacts.setdefault("telegram_identity_history", []).append({
                    "telegram_user_id": OLD_USER_ID,
                    "telegram_username": OLD_USERNAME,
                    "access": "revoked",
                })
                connection.execute(
                    "UPDATE partners SET telegram_user_id=NULL, telegram_username=NULL WHERE id=?",
                    (DUPLICATE_PARTNER_ID,),
                )
                connection.execute(
                    """UPDATE partners SET telegram_user_id=?, telegram_username=?,
                       contacts=?, updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                    (NEW_USER_ID, NEW_USERNAME, json.dumps(contacts, ensure_ascii=False),
                     CANONICAL_PARTNER_ID),
                )
                dependencies = _schema_partner_dependencies(connection)
                for table, column in dependencies.items():
                    connection.execute(
                        f"UPDATE {table} SET {column}=? WHERE {column}=?",
                        (CANONICAL_PARTNER_ID, DUPLICATE_PARTNER_ID),
                    )
                remaining = sum(
                    connection.execute(
                        f"SELECT COUNT(*) FROM {table} WHERE "
                        f"{'selected_partner_id' if table == 'partner_identity_relink_requests' else 'partner_id'}=?",
                        (DUPLICATE_PARTNER_ID,),
                    ).fetchone()[0]
                    for table, column in dependencies.items()
                )
                if remaining:
                    raise LeraRepairConflict("Остались ссылки на дублирующую запись")
                connection.execute("DELETE FROM partners WHERE id=?", (DUPLICATE_PARTNER_ID,))
                connection.execute(
                    """INSERT INTO partner_commercial_audit
                       (partner_id, action, actor_type, details)
                       VALUES (?, 'telegram_identity_duplicate_merged', 'repair', ?)""",
                    (CANONICAL_PARTNER_ID, json.dumps({
                        "duplicate_partner_id": DUPLICATE_PARTNER_ID,
                        "application_id": APPLICATION_ID,
                    }, ensure_ascii=False)),
                )
        except sqlite3.IntegrityError as error:
            raise LeraRepairConflict("Зависимые записи конфликтуют при переносе") from error
    finally:
        connection.close()
    return {"changed": True, "dry_run": False, "partner_id": CANONICAL_PARTNER_ID}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    result = repair_lera_identity(dry_run=not args.apply)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
