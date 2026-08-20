
import json
import hashlib
from database import get_connection


CASE_TRANSITIONS = {
    "new": {"active", "ready_for_search", "cancelled"},
    "active": {"ready_for_search", "cancelled", "completed"},
    "ready_for_search": {"active", "searching", "cancelled", "completed"},
    "searching": {"ready_for_search", "results_presented", "cancelled"},
    "results_presented": {
        "active",
        "ready_for_search",
        "searching",
        "cancelled",
        "completed",
    },
}

TERMINAL_CASE_STATUSES = ("completed", "closed", "cancelled")


def get_or_create_case(client_id, category, title):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT id
        FROM cases
        WHERE client_id = ?
        AND status NOT IN ('completed', 'closed', 'cancelled')
        ORDER BY id DESC
        LIMIT 1
        """,
        (client_id,)
    )

    case = cursor.fetchone()

    if case:
        case_id = case[0]
    else:
        cursor.execute(
            """
            INSERT INTO cases
            (client_id, title, category, description, status, priority)
            VALUES (?, ?, ?, ?, 'new', 'normal')
            """,
            (
                client_id,
                title,
                category,
                ""
            )
        )

        case_id = cursor.lastrowid

    conn.commit()
    conn.close()

    return case_id


def update_case(case_id, data, missing_data=None, status=None):
    conn = get_connection()
    cursor = conn.cursor()

    data_json = json.dumps(
        data,
        ensure_ascii=False
    )

    missing_json = json.dumps(
        missing_data or [],
        ensure_ascii=False
    )

    if status is None:
        cursor.execute(
            """
            UPDATE cases
            SET data = ?,
                missing_data = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                data_json,
                missing_json,
                case_id
            )
        )
    else:
        current = cursor.execute(
            "SELECT status FROM cases WHERE id = ?",
            (case_id,),
        ).fetchone()
        if current and not can_transition_case(current[0], status):
            conn.close()
            raise ValueError(
                f"Недопустимый переход кейса: {current[0]} -> {status}"
            )
        cursor.execute(
            """
            UPDATE cases
            SET data = ?,
                missing_data = ?,
                status = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                data_json,
                missing_json,
                status,
                case_id
            )
        )

    conn.commit()
    conn.close()


def merge_case_data(existing_data, new_data):
    """Merge non-empty extracted values into persisted case data."""
    merged = dict(existing_data) if isinstance(existing_data, dict) else {}
    if not isinstance(new_data, dict):
        return merged
    for key, value in new_data.items():
        if value not in (None, "", [], {}):
            merged[key] = value
    return merged


def get_housing_missing_fields(case_data):
    required_fields = (
        "arrival_date",
        "departure_date",
        "people",
        "budget",
    )
    data = case_data if isinstance(case_data, dict) else {}
    return [
        field
        for field in required_fields
        if data.get(field) in (None, "", [], {})
    ]


def get_case_status(missing_data):
    return "active" if missing_data else "ready_for_search"


def build_search_fingerprint(case_data):
    data = case_data if isinstance(case_data, dict) else {}
    search_fields = {
        key: data.get(key)
        for key in (
            "arrival_date",
            "departure_date",
            "people",
            "budget",
            "location",
            "pet",
            "housing_type",
        )
    }
    payload = json.dumps(search_fields, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def get_repeat_search_options(case_data, repeat_search=False):
    data = case_data if isinstance(case_data, dict) else {}
    state = data.get("_search_state", {})
    if not isinstance(state, dict):
        state = {}

    fingerprint = build_search_fingerprint(data)
    same_search = state.get("fingerprint") == fingerprint
    return {
        "fingerprint": fingerprint,
        "excluded_urls": (
            list(state.get("shown_urls", [])) if same_search else []
        ),
        "page": (
            int(state.get("next_page", 0))
            if repeat_search and same_search
            else 0
        ),
    }


def record_search_results(case_data, shown_results, page, fingerprint):
    data = dict(case_data) if isinstance(case_data, dict) else {}
    previous_state = data.get("_search_state", {})
    if not isinstance(previous_state, dict):
        previous_state = {}

    previous_urls = (
        previous_state.get("shown_urls", [])
        if previous_state.get("fingerprint") == fingerprint
        else []
    )
    shown_urls = list(previous_urls)
    seen = set(shown_urls)
    for result in shown_results:
        url = str(result.get("url", "")).strip()
        if url and url not in seen:
            shown_urls.append(url)
            seen.add(url)

    data["_search_state"] = {
        "fingerprint": fingerprint,
        "shown_urls": shown_urls[-200:],
        "next_page": int(page) + 1,
    }
    return data


def can_transition_case(current_status, new_status):
    if current_status == new_status:
        return True
    return new_status in CASE_TRANSITIONS.get(current_status, set())


def set_case_status(case_id, new_status):
    case = get_case(case_id)
    if not case:
        raise ValueError("Кейс не найден")
    if not can_transition_case(case["status"], new_status):
        raise ValueError(
            f"Недопустимый переход кейса: {case['status']} -> {new_status}"
        )
    update_case(
        case_id,
        case["data"],
        case["missing_data"],
        new_status,
    )


def get_case(case_id):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT
            id,
            client_id,
            title,
            category,
            data,
            missing_data,
            status,
            priority,
            created_at,
            updated_at
        FROM cases
        WHERE id = ?
        """,
        (case_id,)
    )

    case = cursor.fetchone()

    conn.close()

    if not case:
        return None

    return {
        "id": case[0],
        "client_id": case[1],
        "title": case[2],
        "category": case[3],
        "data": json.loads(case[4]) if case[4] else {},
        "missing_data": json.loads(case[5]) if case[5] else [],
        "status": case[6],
        "priority": case[7],
        "created_at": case[8],
        "updated_at": case[9]
    }


def get_client_active_case(client_id):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT id
        FROM cases
        WHERE client_id = ?
        AND status NOT IN ('completed', 'closed', 'cancelled')
        ORDER BY id DESC
        LIMIT 1
        """,
        (client_id,)
    )

    result = cursor.fetchone()

    conn.close()

    if not result:
        return None

    return get_case(result[0])


def format_case_for_ai(case):
    if not case:
        return "Активного кейса нет."

    public_data = {
        key: value
        for key, value in case["data"].items()
        if not str(key).startswith("_")
    }

    return f"""
АКТИВНЫЙ КЕЙС КЛИЕНТА

ID: {case['id']}
Категория: {case['category']}
Название: {case['title']}
Статус: {case['status']}
Приоритет: {case['priority']}

Уже известно:
{json.dumps(public_data, ensure_ascii=False, indent=2)}

Необходимо уточнить:
{json.dumps(case['missing_data'], ensure_ascii=False, indent=2)}
"""

def close_active_case(client_id):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        UPDATE cases
        SET status = 'cancelled',
            updated_at = CURRENT_TIMESTAMP
        WHERE client_id = ?
        AND status NOT IN ('completed', 'closed', 'cancelled')
        """,
        (client_id,)
    )

    conn.commit()
    conn.close()
