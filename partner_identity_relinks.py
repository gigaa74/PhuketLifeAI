import json
import sqlite3
from datetime import datetime, timezone

from database import get_connection
from partner_network import get_partner


class PartnerIdentityRelinkError(RuntimeError):
    pass


def _now():
    return datetime.now(timezone.utc).isoformat()


def _username(value):
    return str(value or "").strip().lstrip("@") or None


def get_relink(request_id, db_path=None):
    connection = get_connection(db_path)
    connection.row_factory = sqlite3.Row
    try:
        row = connection.execute(
            "SELECT * FROM partner_identity_relink_requests WHERE id=?",
            (int(request_id),),
        ).fetchone()
        return dict(row) if row else None
    finally:
        connection.close()


def get_open_relink(telegram_user_id, db_path=None):
    connection = get_connection(db_path)
    connection.row_factory = sqlite3.Row
    try:
        row = connection.execute(
            """SELECT * FROM partner_identity_relink_requests
               WHERE telegram_user_id=? AND status IN ('collecting','needs_review')
               ORDER BY id DESC LIMIT 1""",
            (int(telegram_user_id),),
        ).fetchone()
        return dict(row) if row else None
    finally:
        connection.close()


def start_relink(telegram_user_id, telegram_username=None, db_path=None):
    existing = get_open_relink(telegram_user_id, db_path)
    if existing:
        return existing, False
    connection = get_connection(db_path)
    try:
        with connection:
            cursor = connection.execute(
                """INSERT INTO partner_identity_relink_requests
                   (telegram_user_id, telegram_username, updated_at)
                   VALUES (?, ?, ?)""",
                (int(telegram_user_id), _username(telegram_username), _now()),
            )
        return get_relink(cursor.lastrowid, db_path), True
    finally:
        connection.close()


def record_relink_answer(request_id, value, db_path=None):
    request = get_relink(request_id, db_path)
    if not request or request["status"] != "collecting":
        raise PartnerIdentityRelinkError("Запрос не находится в заполнении")
    text = str(value or "").strip()
    if not text:
        raise PartnerIdentityRelinkError("Ответ не должен быть пустым")
    if request["current_step"] == "partner_name":
        column, next_step, status = "partner_name_text", "previous_contact", "collecting"
    elif request["current_step"] == "previous_contact":
        column, next_step, status = "previous_contact_text", "complete", "needs_review"
    else:
        raise PartnerIdentityRelinkError("Неизвестный шаг запроса")
    connection = get_connection(db_path)
    try:
        with connection:
            connection.execute(
                f"""UPDATE partner_identity_relink_requests SET {column}=?,
                    current_step=?, status=?, submitted_at=CASE WHEN ?='needs_review'
                    THEN ? ELSE submitted_at END, updated_at=? WHERE id=?""",
                (text, next_step, status, status, _now(), _now(), request["id"]),
            )
    finally:
        connection.close()
    return get_relink(request["id"], db_path)


def cancel_relink(telegram_user_id, db_path=None):
    request = get_open_relink(telegram_user_id, db_path)
    if not request or request["status"] != "collecting":
        return request
    connection = get_connection(db_path)
    try:
        with connection:
            connection.execute(
                """UPDATE partner_identity_relink_requests
                   SET status='cancelled', updated_at=? WHERE id=?""",
                (_now(), request["id"]),
            )
    finally:
        connection.close()
    return get_relink(request["id"], db_path)


def decide_relink(request_id, partner_id, approved, owner_id, db_path=None):
    request = get_relink(request_id, db_path)
    if not request:
        raise PartnerIdentityRelinkError("Запрос на смену аккаунта не найден")
    if request["status"] in ("approved", "rejected"):
        return request
    if request["status"] != "needs_review":
        raise PartnerIdentityRelinkError("Запрос ещё не готов к решению")
    partner = get_partner(partner_id, db_path) if approved else None
    if approved and (not partner or partner.get("status") != "active"):
        raise PartnerIdentityRelinkError("Активный партнёр не найден")
    connection = get_connection(db_path)
    connection.row_factory = sqlite3.Row
    try:
        if approved:
            if request["telegram_username"]:
                conflict = connection.execute(
                    """SELECT id FROM partners WHERE id<>? AND
                       (telegram_user_id=? OR
                        lower(ltrim(COALESCE(telegram_username,''),'@'))=lower(?))""",
                    (partner["id"], request["telegram_user_id"],
                     request["telegram_username"]),
                ).fetchone()
            else:
                conflict = connection.execute(
                    "SELECT id FROM partners WHERE id<>? AND telegram_user_id=?",
                    (partner["id"], request["telegram_user_id"]),
                ).fetchone()
            if conflict:
                raise PartnerIdentityRelinkError("Новая Telegram identity конфликтует")
            try:
                contacts = json.loads(partner.get("contacts") or "{}")
                if not isinstance(contacts, dict):
                    contacts = {"legacy_contacts": partner.get("contacts")}
            except (TypeError, json.JSONDecodeError):
                contacts = {"legacy_contacts": partner.get("contacts")}
            history = contacts.setdefault("telegram_identity_history", [])
            history.append({
                "telegram_user_id": partner.get("telegram_user_id"),
                "telegram_username": partner.get("telegram_username"),
                "replaced_at": _now(),
            })
            with connection:
                connection.execute(
                    """UPDATE partners SET telegram_user_id=?, telegram_username=?,
                       contacts=?, updated_at=? WHERE id=?""",
                    (request["telegram_user_id"], request["telegram_username"],
                     json.dumps(contacts, ensure_ascii=False), _now(), partner["id"]),
                )
                connection.execute(
                    """INSERT INTO partner_commercial_audit
                       (partner_id, action, actor_type, actor_id, details)
                       VALUES (?, 'telegram_identity_relinked', 'owner', ?, ?)""",
                    (partner["id"], int(owner_id), json.dumps({
                        "relink_request_id": request["id"],
                        "old_telegram_user_id": partner.get("telegram_user_id"),
                        "old_telegram_username": partner.get("telegram_username"),
                    }, ensure_ascii=False)),
                )
                connection.execute(
                    """UPDATE partner_identity_relink_requests SET status='approved',
                       selected_partner_id=?, decided_at=?, decided_by=?, updated_at=?
                       WHERE id=?""",
                    (partner["id"], _now(), int(owner_id), _now(), request["id"]),
                )
        else:
            with connection:
                connection.execute(
                    """UPDATE partner_identity_relink_requests SET status='rejected',
                       selected_partner_id=NULL, decided_at=?, decided_by=?, updated_at=?
                       WHERE id=?""",
                    (_now(), int(owner_id), _now(), request["id"]),
                )
    except sqlite3.IntegrityError as error:
        raise PartnerIdentityRelinkError("Новая Telegram identity конфликтует") from error
    finally:
        connection.close()
    return get_relink(request["id"], db_path)
