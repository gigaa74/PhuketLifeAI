import argparse
import json
import sqlite3

from database import DB_NAME, get_connection


CANONICAL_PARTNER_ID = 6
DONOR_PARTNER_ID = 3
APPLICATION_ID = 1
OLD_USER_ID = 1905717582
NEW_USER_ID = 8502972477
OLD_USERNAME = "lerikaDi"
NEW_USERNAME = "Hereld"

EXPECTED_PARTNER_DEPENDENCIES = {
    "partner_requests": "partner_id",
    "partner_offers": "partner_id",
    "partner_approved_terms": "partner_id",
    "partner_term_proposals": "partner_id",
    "partner_commercial_audit": "partner_id",
    "partner_applications": "partner_id",
    "partner_referral_requests": "partner_id",
    "partner_identity_relink_requests": "selected_partner_id",
}

EXPECTED_FOREIGN_KEYS = {
    ("partner_requests", "case_id", "cases", "id"),
    ("partner_requests", "partner_id", "partners", "id"),
    ("partner_offers", "partner_request_id", "partner_requests", "id"),
    ("partner_offers", "case_id", "cases", "id"),
    ("partner_offers", "partner_id", "partners", "id"),
    ("partner_approved_terms", "partner_id", "partners", "id"),
    ("partner_term_proposals", "partner_id", "partners", "id"),
    ("partner_commercial_audit", "partner_id", "partners", "id"),
    ("partner_commercial_audit", "proposal_id", "partner_term_proposals", "id"),
    ("partner_applications", "partner_id", "partners", "id"),
    ("partner_referral_requests", "partner_id", "partners", "id"),
    ("partner_identity_relink_requests", "selected_partner_id", "partners", "id"),
}


class LeraRepairConflict(RuntimeError):
    pass


def _row(connection, partner_id):
    connection.row_factory = sqlite3.Row
    row = connection.execute(
        "SELECT * FROM partners WHERE id=?", (int(partner_id),)
    ).fetchone()
    return dict(row) if row else None


def _schema_partner_dependencies(connection):
    result = {}
    for (table,) in connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ):
        columns = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
        if "partner_id" in columns:
            result[table] = "partner_id"
        if "selected_partner_id" in columns:
            result[table] = "selected_partner_id"
    return result


def _schema_foreign_keys(connection):
    result = set()
    for (table,) in connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ):
        for row in connection.execute(f"PRAGMA foreign_key_list({table})"):
            result.add((table, row[3], row[2], row[4]))
    return result


def _count(connection, table, partner_id, column="partner_id"):
    return connection.execute(
        f"SELECT COUNT(*) FROM {table} WHERE {column}=?", (partner_id,)
    ).fetchone()[0]


def _validate_schema(connection):
    if _schema_partner_dependencies(connection) != EXPECTED_PARTNER_DEPENDENCIES:
        raise LeraRepairConflict("Обнаружена неожиданная таблица зависимости партнёра")
    relevant = {
        item for item in _schema_foreign_keys(connection)
        if item[2] in {
            "partners", "partner_requests", "partner_offers",
            "partner_term_proposals", "cases",
        }
    }
    if relevant != EXPECTED_FOREIGN_KEYS:
        raise LeraRepairConflict("Схема foreign key отличается от проверенной")


def _validate_lera(connection):
    donor = _row(connection, DONOR_PARTNER_ID)
    canonical = _row(connection, CANONICAL_PARTNER_ID)
    if not donor or donor["name"] != "Лера":
        raise LeraRepairConflict("Donor partner_id=3 не совпадает с Лерой")
    if (donor["telegram_user_id"] != OLD_USER_ID or
            str(donor["telegram_username"] or "").casefold() != OLD_USERNAME.casefold() or
            donor.get("partner_type") != "hybrid"):
        raise LeraRepairConflict("Fingerprint donor identity неожиданен")
    if not canonical or canonical["name"] != "Валерия":
        raise LeraRepairConflict("Canonical partner_id=6 не совпадает с Валерией")
    if canonical["telegram_user_id"] != NEW_USER_ID or canonical["telegram_username"] is not None:
        raise LeraRepairConflict("Fingerprint рабочей identity неожиданен")
    application = connection.execute(
        """SELECT status,partner_id,telegram_user_id,contact_text
           FROM partner_applications WHERE id=?""", (APPLICATION_ID,)
    ).fetchone()
    if (not application or tuple(application[:3]) !=
            ("approved", CANONICAL_PARTNER_ID, NEW_USER_ID) or
            str(application[3] or "").strip().lstrip("@").casefold() != NEW_USERNAME.casefold()):
        raise LeraRepairConflict("Application id=1 неожиданна")
    counts = {
        "donor_terms": _count(connection, "partner_approved_terms", 3),
        "donor_audit": _count(connection, "partner_commercial_audit", 3),
        "canonical_terms": _count(connection, "partner_approved_terms", 6),
        "canonical_audit": _count(connection, "partner_commercial_audit", 6),
        "canonical_referrals": _count(connection, "partner_referral_requests", 6),
    }
    if counts != {
        "donor_terms": 7, "donor_audit": 2, "canonical_terms": 0,
        "canonical_audit": 0, "canonical_referrals": 0,
    }:
        raise LeraRepairConflict("Counts данных Леры неожиданны")
    conflict = connection.execute(
        """SELECT id FROM partners WHERE id NOT IN (3,6) AND
           (telegram_user_id=? OR lower(ltrim(COALESCE(telegram_username,''),'@'))=lower(?))""",
        (NEW_USER_ID, NEW_USERNAME),
    ).fetchone()
    if conflict:
        raise LeraRepairConflict("Рабочая identity конфликтует с другим партнёром")
    return donor, canonical


def _validate_test_partners(connection):
    partner_ids = {
        row[0] for row in connection.execute("SELECT id FROM partners")
    }
    if partner_ids != {1, 2, 3, 4, 5, 6}:
        raise LeraRepairConflict("Набор production partners неожиданен")
    real_names = connection.execute(
        "SELECT id,name FROM partners WHERE id IN (4,5) ORDER BY id"
    ).fetchall()
    if [tuple(row) for row in real_names] != [(4, "Инна"), (5, "Сергей")]:
        raise LeraRepairConflict("Карточки Инны или Сергея неожиданны")
    first, second = _row(connection, 1), _row(connection, 2)
    if not first or (
        first["name"] != "Test Partner" or first["status"] != "blocked" or
        first["telegram_user_id"] != 6233935382 or
        str(first["telegram_username"] or "").casefold() != "gigaa74"
    ):
        raise LeraRepairConflict("Fingerprint Test Partner неожиданен")
    if not second or (
        second["name"] != "Test Partner 2" or
        second["telegram_user_id"] != 8733594703 or
        str(second["telegram_username"] or "").casefold() != "gigaaa74"
    ):
        raise LeraRepairConflict("Fingerprint Test Partner 2 неожиданен")
    for table, column in EXPECTED_PARTNER_DEPENDENCIES.items():
        if _count(connection, table, 1, column):
            raise LeraRepairConflict("У Test Partner обнаружены связанные данные")
    expected = {
        "partner_requests": 3, "partner_offers": 1,
        "partner_approved_terms": 1, "partner_term_proposals": 2,
        "partner_commercial_audit": 7, "partner_applications": 0,
        "partner_referral_requests": 0, "partner_identity_relink_requests": 0,
    }
    actual = {
        table: _count(connection, table, 2, column)
        for table, column in EXPECTED_PARTNER_DEPENDENCIES.items()
    }
    if actual != expected:
        raise LeraRepairConflict("Counts Test Partner 2 неожиданны")
    term = connection.execute(
        "SELECT term_key,term_value FROM partner_approved_terms WHERE partner_id=2"
    ).fetchone()
    if not term or tuple(term) != ("commission", "15%"):
        raise LeraRepairConflict("Test commission 15% не подтверждена")
    if connection.execute(
        """SELECT id FROM partner_offers WHERE partner_id=2 AND
           partner_request_id NOT IN (SELECT id FROM partner_requests WHERE partner_id=2)"""
    ).fetchone():
        raise LeraRepairConflict("Связи test offers/requests неожиданны")
    if connection.execute(
        """SELECT id FROM partner_offers WHERE partner_request_id IN
           (SELECT id FROM partner_requests WHERE partner_id=2)
           AND partner_id<>2"""
    ).fetchone():
        raise LeraRepairConflict("Чужой offer ссылается на test request")
    if connection.execute(
        """SELECT id FROM partner_commercial_audit WHERE partner_id=2 AND
           proposal_id IS NOT NULL AND proposal_id NOT IN
           (SELECT id FROM partner_term_proposals WHERE partner_id=2)"""
    ).fetchone():
        raise LeraRepairConflict("Связи test audit/proposals неожиданны")
    if connection.execute(
        """SELECT id FROM partner_commercial_audit WHERE proposal_id IN
           (SELECT id FROM partner_term_proposals WHERE partner_id=2)
           AND partner_id<>2"""
    ).fetchone():
        raise LeraRepairConflict("Чужой audit ссылается на test proposal")


def _decode_dict(value):
    try:
        decoded = json.loads(value or "{}")
        return decoded if isinstance(decoded, dict) else {"legacy_value": value}
    except (TypeError, json.JSONDecodeError):
        return {"legacy_value": value} if value else {}


def _merged_contacts(donor, canonical):
    contacts = _decode_dict(canonical.get("contacts"))
    donor_contacts = _decode_dict(donor.get("contacts"))
    if donor_contacts:
        contacts["donor_contacts"] = donor_contacts
    contacts.setdefault("telegram_identity_history", []).append({
        "telegram_user_id": OLD_USER_ID,
        "telegram_username": OLD_USERNAME,
        "access": "revoked",
    })
    return json.dumps(contacts, ensure_ascii=False)


def _merged_operational_notes(donor, canonical):
    notes = _decode_dict(donor.get("operational_notes"))
    notes["merged_canonical_context"] = {
        "notes": canonical.get("notes"),
        "operational_notes": canonical.get("operational_notes"),
        "services": canonical.get("services"),
        "areas": canonical.get("areas"),
        "application_id": APPLICATION_ID,
    }
    return json.dumps(notes, ensure_ascii=False)


def _already_complete(connection):
    canonical = _row(connection, 6)
    if any(_row(connection, value) for value in (1, 2, 3)) or not canonical:
        return False
    application = connection.execute(
        "SELECT partner_id FROM partner_applications WHERE id=1"
    ).fetchone()
    return (
        canonical["name"] == "Валерия" and
        canonical["telegram_user_id"] == NEW_USER_ID and
        str(canonical["telegram_username"] or "").casefold() == NEW_USERNAME.casefold() and
        _count(connection, "partner_approved_terms", 6) == 7 and
        _count(connection, "partner_commercial_audit", 6) == 2 and
        application and application[0] == 6
    )


def repair_lera_identity(db_path=DB_NAME, dry_run=True):
    connection = get_connection(db_path)
    connection.row_factory = sqlite3.Row
    try:
        if _already_complete(connection):
            return {"changed": False, "dry_run": dry_run, "partner_id": 6}
        _validate_schema(connection)
        donor, canonical = _validate_lera(connection)
        _validate_test_partners(connection)
        if dry_run:
            return {
                "changed": True, "dry_run": True, "partner_id": 6,
                "delete_partner_ids": [1, 2, 3],
            }
        try:
            with connection:
                connection.execute(
                    "UPDATE partners SET telegram_user_id=NULL,telegram_username=NULL WHERE id=3"
                )
                connection.execute(
                    """UPDATE partners SET name='Валерия',partner_type='hybrid',
                       status='active',telegram_user_id=?,telegram_username=?,
                       services=?,areas=?,allowed_actions=?,operational_notes=?,
                       auto_handoff_enabled=?,contacts=?,updated_at=CURRENT_TIMESTAMP
                       WHERE id=6""",
                    (
                        NEW_USER_ID, NEW_USERNAME, donor["services"], donor["areas"],
                        donor["allowed_actions"], _merged_operational_notes(donor, canonical),
                        donor["auto_handoff_enabled"], _merged_contacts(donor, canonical),
                    ),
                )
                dependencies = _schema_partner_dependencies(connection)
                for table, column in dependencies.items():
                    connection.execute(f"UPDATE {table} SET {column}=6 WHERE {column}=3")
                for table, column in dependencies.items():
                    if _count(connection, table, 3, column):
                        raise LeraRepairConflict("Остались ссылки на donor partner_id=3")
                connection.execute("DELETE FROM partners WHERE id=3")

                connection.execute("DELETE FROM partner_commercial_audit WHERE partner_id=2")
                connection.execute("DELETE FROM partner_offers WHERE partner_id=2")
                connection.execute("DELETE FROM partner_term_proposals WHERE partner_id=2")
                connection.execute("DELETE FROM partner_requests WHERE partner_id=2")
                connection.execute("DELETE FROM partner_approved_terms WHERE partner_id=2")
                for table, column in dependencies.items():
                    if _count(connection, table, 1, column) or _count(connection, table, 2, column):
                        raise LeraRepairConflict("Остались ссылки на test partners")
                connection.execute("DELETE FROM partners WHERE id IN (1,2)")
                if connection.execute("PRAGMA foreign_key_check").fetchone():
                    raise LeraRepairConflict("foreign_key_check обнаружил нарушение")
        except sqlite3.IntegrityError as error:
            raise LeraRepairConflict("Maintenance конфликтует с зависимыми данными") from error
    finally:
        connection.close()
    return {"changed": True, "dry_run": False, "partner_id": 6}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    print(json.dumps(repair_lera_identity(dry_run=not args.apply), ensure_ascii=False))


if __name__ == "__main__":
    main()
