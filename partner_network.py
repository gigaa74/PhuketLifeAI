import hashlib
import json
import secrets
import sqlite3
from datetime import datetime, timezone

from database import get_connection
from partner_authority import (
    OPERATIONAL_ACTIONS,
    create_pending_proposal,
    get_approved_terms,
    list_pending_proposals,
)


PARTNER_STATUSES = {"candidate", "active", "paused", "blocked"}
PARTNER_TYPES = {"service_provider", "b2b_channel", "hybrid"}
SERVICE_CATEGORIES = {
    "housing", "transfer", "car_rental", "bike_rental", "excursions",
    "boats", "fishing", "visa", "sim", "activities", "medical",
    "beauty", "food", "delivery", "guide", "photo_video", "other",
}
REQUEST_STATUSES = {
    "created", "sent", "responded", "declined", "failed", "cancelled"
}
DECLINE_PHRASES = (
    "нет вариантов", "вариантов нет", "не могу помочь", "не смогу помочь",
    "ничего нет", "нет предложений",
)
CATEGORY_LABELS = {
    "housing": "жильё",
    "transfer": "трансфер",
    "car_rental": "аренда автомобиля",
    "bike_rental": "аренда байка",
    "excursions": "экскурсии",
}


class PartnerNetworkError(RuntimeError):
    code = "partner_network_error"


class InvalidCaseError(PartnerNetworkError):
    code = "invalid_case"


class PartnerUnavailableError(PartnerNetworkError):
    code = "partner_unavailable"


class DuplicatePartnerRequestError(PartnerNetworkError):
    code = "duplicate_request"


class PartnerTelegramError(PartnerNetworkError):
    code = "telegram_failure"

    def __init__(self, request_id):
        super().__init__("Telegram не подтвердил отправку партнёру")
        self.request_id = request_id


def _now():
    return datetime.now(timezone.utc).isoformat()


def _encode_list(values):
    if isinstance(values, str):
        values = [item.strip() for item in values.split(",") if item.strip()]
    return json.dumps(list(values or []), ensure_ascii=False)


def _decode_list(value):
    if not value:
        return []
    try:
        decoded = json.loads(value)
        if isinstance(decoded, list):
            return [str(item).strip() for item in decoded if str(item).strip()]
    except (TypeError, json.JSONDecodeError):
        pass
    return [item.strip() for item in str(value).split(",") if item.strip()]


def _validate_services(services):
    values = _decode_list(_encode_list(services))
    invalid = sorted(set(values) - SERVICE_CATEGORIES)
    if invalid:
        raise ValueError("Неизвестные категории услуг: " + ", ".join(invalid))
    return values


def _normalize_telegram_username(username):
    value = str(username or "").strip().lstrip("@").strip()
    return value or None


def _partner_from_row(row):
    if not row:
        return None
    result = dict(row)
    result["services"] = _decode_list(result.get("services"))
    result["areas"] = _decode_list(result.get("areas"))
    result["allowed_actions"] = _decode_list(result.get("allowed_actions"))
    result.pop("invite_token_hash", None)
    return result


def create_partner(
    name, services, areas=None, status="candidate", telegram_username=None,
    commission_notes=None, notes=None, db_path=None,
    partner_type="service_provider", allowed_actions=None, operational_notes=None,
):
    if status not in PARTNER_STATUSES:
        raise ValueError("Недопустимый статус партнёра")
    if partner_type not in PARTNER_TYPES:
        raise ValueError("Недопустимый тип партнёра")
    services = _validate_services(services)
    connection = get_connection(db_path)
    try:
        with connection:
            cursor = connection.execute(
                """
                INSERT INTO partners
                    (name, telegram_username, status, services, areas,
                     commission_notes, notes, updated_at, partner_type,
                     allowed_actions, operational_notes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    name.strip(), _normalize_telegram_username(telegram_username), status,
                    _encode_list(services), _encode_list(areas),
                    commission_notes, notes, _now(), partner_type,
                    _encode_list(allowed_actions or sorted(OPERATIONAL_ACTIONS)),
                    operational_notes,
                ),
            )
        return get_partner(cursor.lastrowid, db_path)
    finally:
        connection.close()


def get_partner(partner_id, db_path=None):
    connection = get_connection(db_path)
    connection.row_factory = sqlite3.Row
    try:
        row = connection.execute(
            "SELECT * FROM partners WHERE id = ?", (partner_id,)
        ).fetchone()
        partner = _partner_from_row(row)
        if partner:
            partner["approved_terms"] = get_approved_terms(partner_id, db_path)
            partner["pending_terms"] = list_pending_proposals(partner_id, db_path)
            partner["requires_owner_approval"] = True
        return partner
    finally:
        connection.close()


def list_partners(db_path=None):
    connection = get_connection(db_path)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute("SELECT * FROM partners ORDER BY id").fetchall()
        partners = []
        for row in rows:
            partner = _partner_from_row(row)
            partner["approved_terms"] = get_approved_terms(partner["id"], db_path)
            partner["pending_terms"] = list_pending_proposals(partner["id"], db_path)
            partner["requires_owner_approval"] = True
            partners.append(partner)
        return partners
    finally:
        connection.close()


def update_partner(partner_id, services=None, areas=None, notes=None, db_path=None):
    updates = []
    values = []
    if services is not None:
        updates.append("services = ?")
        values.append(_encode_list(_validate_services(services)))
    if areas is not None:
        updates.append("areas = ?")
        values.append(_encode_list(areas))
    if notes is not None:
        updates.append("notes = ?")
        values.append(notes)
    if not updates:
        return get_partner(partner_id, db_path)
    updates.append("updated_at = ?")
    values.extend((_now(), partner_id))
    connection = get_connection(db_path)
    try:
        with connection:
            cursor = connection.execute(
                f"UPDATE partners SET {', '.join(updates)} WHERE id = ?", values
            )
        if not cursor.rowcount:
            raise PartnerUnavailableError("Партнёр не найден")
    finally:
        connection.close()
    return get_partner(partner_id, db_path)


def set_partner_status(partner_id, status, db_path=None):
    if status not in PARTNER_STATUSES:
        raise ValueError("Недопустимый статус партнёра")
    connection = get_connection(db_path)
    try:
        with connection:
            cursor = connection.execute(
                "UPDATE partners SET status = ?, updated_at = ? WHERE id = ?",
                (status, _now(), partner_id),
            )
        if not cursor.rowcount:
            raise PartnerUnavailableError("Партнёр не найден")
    finally:
        connection.close()
    return get_partner(partner_id, db_path)


def set_partner_auto_handoff(partner_id, enabled, db_path=None):
    connection = get_connection(db_path)
    try:
        with connection:
            cursor = connection.execute(
                """
                UPDATE partners SET auto_handoff_enabled = ?, updated_at = ?
                WHERE id = ?
                """,
                (int(bool(enabled)), _now(), partner_id),
            )
        if not cursor.rowcount:
            raise PartnerUnavailableError("Партнёр не найден")
    finally:
        connection.close()
    return get_partner(partner_id, db_path)


def create_partner_invite(partner_id, db_path=None):
    token = secrets.token_urlsafe(24)
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    connection = get_connection(db_path)
    try:
        with connection:
            cursor = connection.execute(
                "UPDATE partners SET invite_token_hash = ?, updated_at = ? WHERE id = ?",
                (token_hash, _now(), partner_id),
            )
        if not cursor.rowcount:
            raise PartnerUnavailableError("Партнёр не найден")
    finally:
        connection.close()
    return token


def onboard_partner(token, telegram_user_id, telegram_username=None, db_path=None):
    token_hash = hashlib.sha256(str(token).encode("utf-8")).hexdigest()
    connection = get_connection(db_path)
    connection.row_factory = sqlite3.Row
    try:
        row = connection.execute(
            "SELECT id FROM partners WHERE invite_token_hash = ?", (token_hash,)
        ).fetchone()
        if not row:
            raise PartnerUnavailableError("Приглашение недействительно")
        try:
            with connection:
                connection.execute(
                    """
                    UPDATE partners
                    SET telegram_user_id = ?, telegram_username = ?,
                        invite_token_hash = NULL, updated_at = ?
                    WHERE id = ?
                    """,
                    (telegram_user_id, _normalize_telegram_username(telegram_username),
                     _now(), row["id"]),
                )
        except sqlite3.IntegrityError as error:
            raise PartnerUnavailableError(
                "Telegram уже связан с другим партнёром"
            ) from error
        return get_partner(row["id"], db_path)
    finally:
        connection.close()


def sync_partner_telegram_identity(telegram_user_id, telegram_username=None,
                                   db_path=None):
    """Resolve by immutable Telegram ID, or link one unbound username match."""
    user_id = int(telegram_user_id)
    username = _normalize_telegram_username(telegram_username)
    connection = get_connection(db_path)
    connection.row_factory = sqlite3.Row
    try:
        row = connection.execute(
            "SELECT * FROM partners WHERE telegram_user_id = ?", (user_id,)
        ).fetchone()
        linked = False
        if not row and username:
            candidates = connection.execute(
                """SELECT * FROM partners
                   WHERE telegram_user_id IS NULL
                     AND lower(ltrim(COALESCE(telegram_username, ''), '@')) = lower(?)
                   ORDER BY id""",
                (username,),
            ).fetchall()
            if len(candidates) == 1:
                row = candidates[0]
                linked = True
        if not row:
            return None

        old_username = _normalize_telegram_username(row["telegram_username"])
        username_changed = old_username != username
        if linked or username_changed:
            action = (
                "telegram_identity_linked" if linked else
                "telegram_username_removed" if username is None else
                "telegram_username_updated"
            )
            try:
                with connection:
                    connection.execute(
                        """UPDATE partners
                           SET telegram_user_id = ?, telegram_username = ?, updated_at = ?
                           WHERE id = ?""",
                        (user_id, username, _now(), row["id"]),
                    )
                    connection.execute(
                        """INSERT INTO partner_commercial_audit
                           (partner_id, action, actor_type, actor_id, details)
                           VALUES (?, ?, 'telegram_user', ?, ?)""",
                        (row["id"], action, user_id, json.dumps({
                            "old_username": old_username,
                            "new_username": username,
                        }, ensure_ascii=False)),
                    )
            except sqlite3.IntegrityError as error:
                raise PartnerUnavailableError(
                    "Telegram уже связан с другим партнёром"
                ) from error
        return get_partner(row["id"], db_path)
    finally:
        connection.close()


def resolve_partner_telegram_identity(telegram_user_id, telegram_username=None,
                                      db_path=None):
    """Resolve a partner safely and distinguish no match from identity conflict."""
    try:
        partner = sync_partner_telegram_identity(
            telegram_user_id, telegram_username, db_path
        )
    except PartnerUnavailableError:
        return {"status": "conflict", "partner": None}
    if partner:
        return {"status": "partner", "partner": partner}
    username = _normalize_telegram_username(telegram_username)
    if username:
        connection = get_connection(db_path)
        try:
            matches = connection.execute(
                """SELECT COUNT(*) FROM partners
                   WHERE lower(ltrim(COALESCE(telegram_username, ''), '@')) = lower(?)""",
                (username,),
            ).fetchone()[0]
        finally:
            connection.close()
        if matches:
            return {"status": "conflict", "partner": None}
    return {"status": "not_found", "partner": None}


def _case_areas(case):
    data = case.get("data") or {}
    candidates = (
        data.get("location"), data.get("area"), data.get("pickup_location"),
        data.get("dropoff_location"), data.get("from"), data.get("to"),
    )
    return {str(value).casefold().strip() for value in candidates if value}


def find_partners_for_case(case, db_path=None):
    if not case or not case.get("category"):
        raise InvalidCaseError("Кейс не найден или не имеет категории")
    category = case["category"]
    case_areas = _case_areas(case)
    matches = []
    for partner in list_partners(db_path):
        if partner["status"] != "active" or category not in partner["services"]:
            continue
        partner_areas = {area.casefold() for area in partner["areas"]}
        if partner_areas and case_areas and not partner_areas.intersection(case_areas):
            continue
        matches.append(partner)
    return matches


def format_partner_request(case):
    data = case.get("data") or {}
    category = case.get("category") or "other"
    lines = [
        "📩 Новый запрос Phuket Life",
        f"Запрос #{case['id']}",
        "",
        f"Услуга: {CATEGORY_LABELS.get(category, category)}",
    ]
    safe_fields = (
        ("Район", "location"),
        ("Даты", "dates"),
        ("Дата заезда", "arrival_date"),
        ("Дата выезда", "departure_date"),
        ("Гостей", "people"),
        ("Бюджет", "budget"),
        ("Откуда", "pickup_location"),
        ("Куда", "dropoff_location"),
        ("Тип жилья", "housing_type"),
    )
    for label, key in safe_fields:
        value = data.get(key)
        if value:
            lines.append(f"{label}: {value}")
    preferences = data.get("preferences") or data.get("notes")
    if preferences:
        lines.extend(("", "Пожелания:", str(preferences)))
    lines.extend(("", "Если можете помочь — ответьте на это сообщение."))
    return "\n".join(lines)


def get_case_for_partner(case_id, db_path=None):
    connection = get_connection(db_path)
    connection.row_factory = sqlite3.Row
    try:
        row = connection.execute(
            "SELECT id, category, data, status FROM cases WHERE id = ?", (case_id,)
        ).fetchone()
        if not row:
            raise InvalidCaseError("Кейс не найден")
        result = dict(row)
        try:
            result["data"] = json.loads(result.get("data") or "{}")
        except json.JSONDecodeError:
            result["data"] = {}
        return result
    finally:
        connection.close()


def get_partner_request(request_id, db_path=None):
    connection = get_connection(db_path)
    connection.row_factory = sqlite3.Row
    try:
        row = connection.execute(
            "SELECT * FROM partner_requests WHERE id = ?", (request_id,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        connection.close()


def list_partner_requests(limit=20, db_path=None):
    connection = get_connection(db_path)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            """
            SELECT pr.*, p.name AS partner_name
            FROM partner_requests pr
            JOIN partners p ON p.id = pr.partner_id
            ORDER BY pr.id DESC LIMIT ?
            """,
            (max(1, min(int(limit), 100)),),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        connection.close()


async def send_case_to_partner(case_id, partner_id, telegram_sender, db_path=None):
    case = get_case_for_partner(case_id, db_path)
    partner = get_partner(partner_id, db_path)
    if not partner or partner["status"] != "active":
        raise PartnerUnavailableError("Партнёр недоступен")
    if not partner.get("telegram_user_id"):
        raise PartnerUnavailableError("Партнёр ещё не подключил Telegram")
    if case["category"] not in partner["services"]:
        raise PartnerUnavailableError("Партнёр не оказывает услугу этого кейса")

    payload = format_partner_request(case)
    connection = get_connection(db_path)
    try:
        try:
            with connection:
                cursor = connection.execute(
                    """
                    INSERT INTO partner_requests
                        (case_id, partner_id, service_category, status,
                         request_payload, updated_at)
                    VALUES (?, ?, ?, 'created', ?, ?)
                    """,
                    (case_id, partner_id, case["category"], payload, _now()),
                )
            request_id = cursor.lastrowid
        except sqlite3.IntegrityError as error:
            raise DuplicatePartnerRequestError(
                "Активный запрос этому партнёру уже существует"
            ) from error
    finally:
        connection.close()

    try:
        message = await telegram_sender(
            chat_id=partner["telegram_user_id"], text=payload
        )
        message_id = (
            message.get("message_id") if isinstance(message, dict)
            else getattr(message, "message_id")
        )
        if message_id is None:
            raise RuntimeError("Telegram response has no message_id")
    except Exception as error:
        connection = get_connection(db_path)
        try:
            with connection:
                connection.execute(
                    """
                    UPDATE partner_requests
                    SET status = 'failed', error_code = 'telegram_failure',
                        error_message = ?, updated_at = ? WHERE id = ?
                    """,
                    (type(error).__name__, _now(), request_id),
                )
        finally:
            connection.close()
        raise PartnerTelegramError(request_id) from error

    connection = get_connection(db_path)
    try:
        with connection:
            connection.execute(
                """
                UPDATE partner_requests
                SET status = 'sent', telegram_message_id = ?, sent_at = ?,
                    updated_at = ?, error_code = NULL, error_message = NULL
                WHERE id = ?
                """,
                (message_id, _now(), _now(), request_id),
            )
    finally:
        connection.close()
    return get_partner_request(request_id, db_path)


def record_partner_reply(
    telegram_user_id, reply_to_message_id, response_text, db_path=None,
    response_metadata=None, telegram_username=None,
):
    sync_partner_telegram_identity(
        telegram_user_id, telegram_username, db_path=db_path
    )
    connection = get_connection(db_path)
    connection.row_factory = sqlite3.Row
    try:
        row = connection.execute(
            """
            SELECT pr.id
            FROM partner_requests pr
            JOIN partners p ON p.id = pr.partner_id
            WHERE p.telegram_user_id = ?
              AND pr.telegram_message_id = ?
              AND pr.status = 'sent'
            ORDER BY pr.id DESC LIMIT 1
            """,
            (telegram_user_id, reply_to_message_id),
        ).fetchone()
        if not row:
            return None
        normalized = " ".join(str(response_text or "").casefold().split())
        status = (
            "declined"
            if any(phrase in normalized for phrase in DECLINE_PHRASES)
            else "responded"
        )
        with connection:
            connection.execute(
                """
                UPDATE partner_requests
                SET status = ?, partner_response = ?, partner_response_metadata = ?,
                    responded_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    status,
                    str(response_text or ""),
                    json.dumps(response_metadata, ensure_ascii=False)
                    if response_metadata else None,
                    _now(), _now(), row["id"],
                ),
            )
        request = get_partner_request(row["id"], db_path)
        request["commercial_proposal"] = create_pending_proposal(
            request["partner_id"], response_text,
            source="telegram_partner_reply",
            source_message_id=reply_to_message_id,
            db_path=db_path,
        )
        return request
    finally:
        connection.close()


def is_admin(admin_user_id, user_id):
    return admin_user_id is not None and int(user_id) == int(admin_user_id)
