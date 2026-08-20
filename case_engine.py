
import sqlite3
import json
from datetime import datetime


DB_PATH = "phuketlife.db"


def get_connection():
    return sqlite3.connect(DB_PATH)


def get_or_create_case(client_id, category, title):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT id
        FROM cases
        WHERE client_id = ?
        AND status NOT IN ('completed', 'closed')
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
        AND status NOT IN ('completed', 'closed')
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

    return f"""
АКТИВНЫЙ КЕЙС КЛИЕНТА

ID: {case['id']}
Категория: {case['category']}
Название: {case['title']}
Статус: {case['status']}
Приоритет: {case['priority']}

Уже известно:
{json.dumps(case['data'], ensure_ascii=False, indent=2)}

Необходимо уточнить:
{json.dumps(case['missing_data'], ensure_ascii=False, indent=2)}
"""

def close_active_case(client_id):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        UPDATE cases
        SET status = 'closed',
            updated_at = CURRENT_TIMESTAMP
        WHERE client_id = ?
        AND status NOT IN ('completed', 'closed')
        """,
        (client_id,)
    )

    conn.commit()
    conn.close()
