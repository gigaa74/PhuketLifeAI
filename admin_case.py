import json
import sqlite3

from admin_i18n import (
    format_case_status_ru,
    format_handoff_decision_ru,
    format_offer_status_ru,
    format_service_category_ru,
    format_validation_reason_ru,
)
from database import get_connection


COMMON_FIELDS = (
    ("Район", "location"),
    ("Дата", "date"),
    ("Откуда", "pickup_location"),
    ("Куда", "dropoff_location"),
    ("Гостей", "people"),
    ("Бюджет", "budget"),
)

CATEGORY_FIELDS = {
    "housing": (
        ("Район", "location"),
        ("Дата заезда", "arrival_date"),
        ("Дата выезда", "departure_date"),
        ("Гостей", "people"),
        ("Бюджет", "budget"),
        ("Тип жилья", "housing_type"),
    ),
    "transfer": (
        ("Дата", "date"),
        ("Откуда", "pickup_location"),
        ("Куда", "dropoff_location"),
        ("Пассажиров", "people"),
        ("Бюджет", "budget"),
    ),
}


class AdminCaseNotFoundError(LookupError):
    pass


def get_admin_case(case_id, db_path=None):
    connection = get_connection(db_path)
    connection.row_factory = sqlite3.Row
    try:
        row = connection.execute(
            """
            SELECT c.id, c.category, c.status, c.title, c.data,
                   cl.username, cl.first_name, cl.last_name
            FROM cases c
            JOIN clients cl ON cl.id = c.client_id
            WHERE c.id = ?
            """,
            (case_id,),
        ).fetchone()
        if not row:
            raise AdminCaseNotFoundError("Кейс не найден")
        result = dict(row)
        try:
            result["data"] = json.loads(result.get("data") or "{}")
        except json.JSONDecodeError:
            result["data"] = {}
        return result
    finally:
        connection.close()


def list_admin_cases(limit=20, db_path=None):
    connection = get_connection(db_path)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            """
            SELECT c.id, c.category, c.status, c.title, c.data,
                   cl.username, cl.first_name, cl.last_name
            FROM cases c
            JOIN clients cl ON cl.id = c.client_id
            ORDER BY c.id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        cases = []
        for row in rows:
            item = dict(row)
            try:
                item["data"] = json.loads(item.get("data") or "{}")
            except json.JSONDecodeError:
                item["data"] = {}
            cases.append(item)
        return cases
    finally:
        connection.close()


def _client_display(case):
    display_name = " ".join(
        part.strip()
        for part in (case.get("first_name"), case.get("last_name"))
        if part and part.strip()
    )
    username = str(case.get("username") or "").strip().lstrip("@")
    if display_name and username:
        return f"{display_name} (@{username})"
    if display_name:
        return display_name
    if username:
        return f"@{username}"
    return "не указан"


def format_admin_case_snapshot(case):
    category = case.get("category") or "other"
    category_label = format_service_category_ru(category)
    lines = [
        f"Кейс №{case['id']} — {category_label}",
        f"Статус: {format_case_status_ru(case.get('status'))}",
        f"Клиент: {_client_display(case)}",
    ]
    data = case.get("data") or {}
    fields = CATEGORY_FIELDS.get(category, COMMON_FIELDS)
    for label, key in fields:
        value = data.get(key)
        if value not in (None, "", [], {}):
            lines.append(f"{label}: {value}")
    return "\n".join(lines)


def get_admin_case_snapshot(case_id, db_path=None):
    return format_admin_case_snapshot(get_admin_case(case_id, db_path))


def format_offer_review_card(
    offer, partner_name=None, case_snapshot=None, client_message=None
):
    status = offer.get("status") or "needs_review"
    titles = {
        "needs_review": "⚠️ Предложение требует проверки",
        "ready_to_send": "🟢 Предложение готово к отправке",
        "sent_to_client": "✅ Предложение отправлено клиенту",
        "rejected": "❌ Предложение отклонено",
    }
    title = titles.get(status, "ℹ️ Предложение партнёра")
    reasons = "\n".join(
        f"- {format_validation_reason_ru(reason)}"
        for reason in offer.get("validation_reasons", [])
    )
    case_summary = case_snapshot or f"Кейс №{offer['case_id']}"
    sections = [
        (
        f"{title}\n\n"
        f"{case_summary}\n"
        f"Партнёр: {partner_name or offer.get('partner_name', offer['partner_id'])}\n"
        f"Статус предложения: {format_offer_status_ru(status)}\n"
        "Способ обработки: "
        f"{format_handoff_decision_ru(offer.get('handoff_decision'))}"
        ),
        f"Исходный ответ партнёра:\n{offer['raw_partner_response']}",
    ]
    if reasons:
        reason_title = (
            "Причины проверки:"
            if status == "needs_review"
            else "Почему потребовалась ручная проверка:"
        )
        sections.append(f"{reason_title}\n{reasons}")
    if client_message:
        sections.append(f"Отправлено клиенту:\n{client_message}")
    sections.append(f"Предложение №{offer['id']}")
    return "\n\n".join(sections)
