import json
import sqlite3
from datetime import datetime, timezone

from database import get_connection


def _now():
    return datetime.now(timezone.utc).isoformat()


def _normalize_username(value):
    username = str(value or "").strip().lstrip("@").strip()
    return username or None


def _candidate(row):
    if not row:
        return None
    result = dict(row)
    result["detection_reasons"] = json.loads(result["detection_reasons"] or "[]")
    return result


def save_scout_candidate(scout_type, observation, detection, db_path=None):
    connection = get_connection(db_path)
    connection.row_factory = sqlite3.Row
    try:
        existing = connection.execute(
            """SELECT * FROM scout_candidates
               WHERE scout_type=? AND source_chat_id=? AND source_message_id=?""",
            (scout_type, observation["source_chat_id"], observation["source_message_id"]),
        ).fetchone()
        username = _normalize_username(observation.get("source_username"))
        user_id = observation.get("source_user_id")
        if existing:
            same_identity = (
                existing["source_user_id"] is None
                or user_id is None
                or int(existing["source_user_id"]) == int(user_id)
            )
            if same_identity:
                with connection:
                    connection.execute(
                        """UPDATE scout_candidates SET
                           source_chat_title=?, source_user_id=COALESCE(source_user_id, ?),
                           source_username=?, updated_at=? WHERE id=?""",
                        (observation.get("source_chat_title"), user_id, username,
                         _now(), existing["id"]),
                    )
                existing = connection.execute(
                    "SELECT * FROM scout_candidates WHERE id=?", (existing["id"],)
                ).fetchone()
            return _candidate(existing), False
        with connection:
            cursor = connection.execute(
                """INSERT INTO scout_candidates
                   (scout_type, source_chat_id, source_chat_title, source_message_id,
                    source_user_id, source_username, original_text,
                    detected_category, confidence, detection_reasons, status,
                    updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    scout_type, observation["source_chat_id"],
                    observation.get("source_chat_title"),
                    observation["source_message_id"], user_id, username,
                    observation["original_text"], detection["detected_category"],
                    detection["confidence"],
                    json.dumps(detection["detection_reasons"], ensure_ascii=False),
                    detection.get("status", "needs_review"), _now(),
                ),
            )
        row = connection.execute(
            "SELECT * FROM scout_candidates WHERE id=?", (cursor.lastrowid,)
        ).fetchone()
        return _candidate(row), True
    finally:
        connection.close()


def list_scout_candidates(scout_type=None, db_path=None):
    connection = get_connection(db_path)
    connection.row_factory = sqlite3.Row
    try:
        if scout_type:
            rows = connection.execute(
                "SELECT * FROM scout_candidates WHERE scout_type=? ORDER BY id",
                (scout_type,),
            ).fetchall()
        else:
            rows = connection.execute("SELECT * FROM scout_candidates ORDER BY id").fetchall()
        return [_candidate(row) for row in rows]
    finally:
        connection.close()
