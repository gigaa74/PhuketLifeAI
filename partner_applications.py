import json
import sqlite3
from datetime import datetime, timezone

from database import get_connection
from partner_network import (
    PartnerUnavailableError,
    create_partner,
    create_partner_invite,
    onboard_partner,
    resolve_partner_telegram_identity,
    set_partner_status,
)


APPLICATION_STEPS = (
    "name", "services", "areas", "delivery_model", "live_source",
    "availability_confirmation", "request_requirements",
    "commercial_model", "contact", "links", "licenses",
)
REQUIRED_APPLICATION_STEPS = {"name", "services", "contact"}


class PartnerApplicationError(RuntimeError):
    pass


def _now():
    return datetime.now(timezone.utc).isoformat()


def _normalize_username(username):
    value = str(username or "").strip().lstrip("@").strip()
    return value or None


def get_application(application_id, db_path=None):
    connection = get_connection(db_path)
    connection.row_factory = sqlite3.Row
    try:
        row = connection.execute(
            "SELECT * FROM partner_applications WHERE id=?",
            (int(application_id),),
        ).fetchone()
        return dict(row) if row else None
    finally:
        connection.close()


def get_open_application(telegram_user_id, db_path=None):
    connection = get_connection(db_path)
    connection.row_factory = sqlite3.Row
    try:
        row = connection.execute(
            """SELECT * FROM partner_applications
               WHERE telegram_user_id=? AND status IN ('collecting', 'needs_review')
               ORDER BY id DESC LIMIT 1""",
            (int(telegram_user_id),),
        ).fetchone()
        return dict(row) if row else None
    finally:
        connection.close()


def start_application(telegram_user_id, telegram_username=None, db_path=None):
    user_id = int(telegram_user_id)
    existing = get_open_application(user_id, db_path)
    if existing:
        username = _normalize_username(telegram_username)
        if existing["telegram_username"] != username:
            connection = get_connection(db_path)
            try:
                with connection:
                    connection.execute(
                        """UPDATE partner_applications
                           SET telegram_username=?, updated_at=? WHERE id=?""",
                        (username, _now(), existing["id"]),
                    )
            finally:
                connection.close()
            existing = get_application(existing["id"], db_path)
        return existing, False
    connection = get_connection(db_path)
    try:
        with connection:
            cursor = connection.execute(
                """INSERT INTO partner_applications
                   (telegram_user_id, telegram_username, status, current_step,
                    updated_at) VALUES (?, ?, 'collecting', 'name', ?)""",
                (user_id, _normalize_username(telegram_username), _now()),
            )
        return get_application(cursor.lastrowid, db_path), True
    except sqlite3.IntegrityError:
        existing = get_open_application(user_id, db_path)
        if existing:
            return existing, False
        raise
    finally:
        connection.close()


def record_application_answer(application_id, value, db_path=None):
    application = get_application(application_id, db_path)
    if not application or application["status"] != "collecting":
        raise PartnerApplicationError("Заявка не находится в заполнении")
    text = str(value or "").strip()
    if not text:
        raise PartnerApplicationError("Ответ не должен быть пустым")
    step = application["current_step"]
    if step not in APPLICATION_STEPS:
        raise PartnerApplicationError("Неизвестный шаг заявки")
    column = {
        "name": "applicant_name", "services": "services_text",
        "areas": "areas_text", "delivery_model": "delivery_model_text",
        "live_source": "live_source_text",
        "availability_confirmation": "availability_confirmation_text",
        "request_requirements": "request_requirements_text",
        "commercial_model": "commercial_model_text",
        "contact": "contact_text", "links": "links_text",
        "licenses": "licenses_text",
    }[step]
    index = APPLICATION_STEPS.index(step)
    next_step = (
        APPLICATION_STEPS[index + 1]
        if index + 1 < len(APPLICATION_STEPS) else None
    )
    connection = get_connection(db_path)
    try:
        with connection:
            if next_step:
                connection.execute(
                    f"""UPDATE partner_applications SET {column}=?, current_step=?,
                        updated_at=? WHERE id=? AND status='collecting'""",
                    (text, next_step, _now(), application["id"]),
                )
            else:
                connection.execute(
                    f"""UPDATE partner_applications SET {column}=?,
                        status='needs_review', current_step='complete',
                        submitted_at=?, updated_at=?
                        WHERE id=? AND status='collecting'""",
                    (text, _now(), _now(), application["id"]),
                )
    finally:
        connection.close()
    return get_application(application["id"], db_path)


def skip_application_step(application_id, db_path=None):
    application = get_application(application_id, db_path)
    if not application or application["status"] != "collecting":
        raise PartnerApplicationError("Заявка не находится в заполнении")
    if application["current_step"] in REQUIRED_APPLICATION_STEPS:
        raise PartnerApplicationError("Этот вопрос нельзя пропустить")
    return record_application_answer(application_id, "не указано", db_path)


def move_application_back(application_id, db_path=None):
    application = get_application(application_id, db_path)
    if not application or application["status"] != "collecting":
        raise PartnerApplicationError("Заявка не находится в заполнении")
    step = application["current_step"]
    if step not in APPLICATION_STEPS:
        raise PartnerApplicationError("Неизвестный шаг заявки")
    index = APPLICATION_STEPS.index(step)
    if index == 0:
        return application
    connection = get_connection(db_path)
    try:
        with connection:
            connection.execute(
                """UPDATE partner_applications SET current_step=?, updated_at=?
                   WHERE id=? AND status='collecting'""",
                (APPLICATION_STEPS[index - 1], _now(), application["id"]),
            )
    finally:
        connection.close()
    return get_application(application["id"], db_path)


def cancel_application(telegram_user_id, db_path=None):
    application = get_open_application(telegram_user_id, db_path)
    if not application or application["status"] != "collecting":
        return application
    connection = get_connection(db_path)
    try:
        with connection:
            connection.execute(
                """UPDATE partner_applications SET status='cancelled',
                   updated_at=? WHERE id=?""",
                (_now(), application["id"]),
            )
    finally:
        connection.close()
    return get_application(application["id"], db_path)


def list_applications(status="needs_review", db_path=None):
    connection = get_connection(db_path)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            "SELECT * FROM partner_applications WHERE status=? ORDER BY id",
            (status,),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        connection.close()


def decide_application(application_id, approved, owner_id, note=None,
                       db_path=None):
    application = get_application(application_id, db_path)
    if not application:
        raise PartnerApplicationError("Заявка не найдена")
    if application["status"] in ("approved", "rejected"):
        return application
    if application["status"] != "needs_review":
        raise PartnerApplicationError("Заявка ещё не готова к решению")
    partner_id = None
    if approved:
        resolution = resolve_partner_telegram_identity(
            application["telegram_user_id"],
            application["telegram_username"],
            db_path,
        )
        if resolution["status"] == "partner":
            partner_id = resolution["partner"]["id"]
        elif resolution["status"] == "conflict":
            raise PartnerApplicationError(
                "Telegram identity конфликтует с существующим партнёром"
            )
        else:
            partner = create_partner(
                application["applicant_name"], ["other"],
                areas=application["areas_text"], status="candidate",
                telegram_username=application["telegram_username"],
                operational_notes=json.dumps({
                    "application_answers": {
                        key: application.get(key) for key in (
                            "services_text", "areas_text",
                            "delivery_model_text", "live_source_text",
                            "availability_confirmation_text",
                            "request_requirements_text",
                            "commercial_model_text", "contact_text",
                            "links_text", "licenses_text",
                        )
                    },
                    "approved_terms": {},
                    "open_questions": [
                        "Коммерческие условия требуют явного подтверждения владельца"
                    ],
                }, ensure_ascii=False),
                db_path=db_path,
            )
            try:
                token = create_partner_invite(partner["id"], db_path)
                linked = onboard_partner(
                    token, application["telegram_user_id"],
                    application["telegram_username"], db_path,
                )
                partner_id = set_partner_status(
                    linked["id"], "active", db_path
                )["id"]
            except PartnerUnavailableError as error:
                raise PartnerApplicationError(str(error)) from error
    connection = get_connection(db_path)
    try:
        with connection:
            connection.execute(
                """UPDATE partner_applications SET status=?, partner_id=?,
                   decided_at=?, decided_by=?, decision_note=?, updated_at=?
                   WHERE id=? AND status='needs_review'""",
                (
                    "approved" if approved else "rejected", partner_id,
                    _now(), int(owner_id), note, _now(), application["id"],
                ),
            )
    finally:
        connection.close()
    return get_application(application["id"], db_path)
