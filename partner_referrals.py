import json
import sqlite3
from datetime import datetime, timezone

from database import get_connection


REFERRAL_STATUSES = {
    "needs_owner_review",
    "in_progress",
    "needs_partner_info",
    "resolved",
    "closed",
}

STATUS_LABELS_RU = {
    "needs_owner_review": "Требует проверки владельца",
    "in_progress": "В работе",
    "needs_partner_info": "Нужны дополнительные данные",
    "resolved": "Решено",
    "closed": "Закрыто",
}


class PartnerReferralError(RuntimeError):
    pass


def _now():
    return datetime.now(timezone.utc).isoformat()


def _decode(row):
    if not row:
        return None
    result = dict(row)
    try:
        result["attachment_metadata"] = json.loads(
            result.get("attachment_metadata") or "{}"
        )
    except (TypeError, json.JSONDecodeError):
        result["attachment_metadata"] = {}
    return result


def get_partner_referral(request_id, db_path=None):
    connection = get_connection(db_path)
    connection.row_factory = sqlite3.Row
    try:
        return _decode(connection.execute(
            "SELECT * FROM partner_referral_requests WHERE id=?",
            (int(request_id),),
        ).fetchone())
    finally:
        connection.close()


def create_partner_referral(
    partner_id,
    source_chat_id,
    source_message_id,
    partner_telegram_user_id,
    telegram_username_snapshot,
    original_text,
    message_type,
    telegram_file_id=None,
    attachment_metadata=None,
    db_path=None,
):
    connection = get_connection(db_path)
    connection.row_factory = sqlite3.Row
    try:
        with connection:
            cursor = connection.execute(
                """INSERT OR IGNORE INTO partner_referral_requests
                   (partner_id, source_chat_id, source_message_id,
                    partner_telegram_user_id, telegram_username_snapshot,
                    original_text, message_type, telegram_file_id,
                    attachment_metadata, status, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?,
                           'needs_owner_review', ?)""",
                (
                    int(partner_id), int(source_chat_id), int(source_message_id),
                    int(partner_telegram_user_id),
                    str(telegram_username_snapshot or "").lstrip("@") or None,
                    str(original_text or ""), str(message_type),
                    telegram_file_id,
                    json.dumps(attachment_metadata or {}, ensure_ascii=False),
                    _now(),
                ),
            )
            row = connection.execute(
                """SELECT * FROM partner_referral_requests
                   WHERE source_chat_id=? AND source_message_id=?""",
                (int(source_chat_id), int(source_message_id)),
            ).fetchone()
        return _decode(row), bool(cursor.rowcount)
    finally:
        connection.close()


def mark_owner_notification(request_id, delivered, error=None, db_path=None):
    connection = get_connection(db_path)
    try:
        with connection:
            cursor = connection.execute(
                """UPDATE partner_referral_requests
                   SET owner_notified_at=?, owner_notification_error=?, updated_at=?
                   WHERE id=?""",
                (
                    _now() if delivered else None,
                    None if delivered else str(error or "notification_failed"),
                    _now(), int(request_id),
                ),
            )
        if not cursor.rowcount:
            raise PartnerReferralError("Партнёрский запрос не найден")
    finally:
        connection.close()
    return get_partner_referral(request_id, db_path)


def set_partner_referral_status(request_id, status, db_path=None):
    if status not in REFERRAL_STATUSES:
        raise PartnerReferralError("Недопустимый статус партнёрского запроса")
    connection = get_connection(db_path)
    try:
        with connection:
            cursor = connection.execute(
                """UPDATE partner_referral_requests SET status=?, updated_at=?
                   WHERE id=?""",
                (status, _now(), int(request_id)),
            )
        if not cursor.rowcount:
            raise PartnerReferralError("Партнёрский запрос не найден")
    finally:
        connection.close()
    return get_partner_referral(request_id, db_path)


def status_label_ru(status):
    return STATUS_LABELS_RU.get(status, "Неизвестный статус")
