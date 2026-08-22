import hashlib
import json
import re
import sqlite3
from datetime import datetime, timezone

from database import get_connection


ALLOW = "ALLOW"
REQUIRE_OWNER_APPROVAL = "REQUIRE_OWNER_APPROVAL"
DENY = "DENY"

OPERATIONAL_ACTIONS = {
    "ask_availability", "ask_schedule", "ask_service_details", "ask_location",
    "ask_capacity", "ask_operational_requirements", "send_approved_terms",
    "send_request", "receive_offer", "update_operational_status",
}
OWNER_ONLY_ACTIONS = {
    "change_commission", "approve_commission", "change_discount",
    "approve_discount", "change_payment_method", "approve_payment_method",
    "accept_exclusivity", "accept_liability", "accept_compensation",
    "accept_contractual_obligation", "resolve_commercial_dispute",
}

TERM_PATTERNS = (
    ("commission", re.compile(r"(?:комисси\w*|вам|для вас)\D{0,35}(\d+(?:[.,]\d+)?\s*%)", re.I)),
    ("discount", re.compile(r"(?:скидк\w*)\D{0,20}(\d+(?:[.,]\d+)?\s*%)", re.I)),
    ("payment_method", re.compile(r"(?:предоплат\w*|оплат\w*[^.\n]{0,25}(?:USDT|крипт\w*|наличн\w*|перевод\w*))", re.I)),
    ("exclusivity", re.compile(r"(?:эксклюзив\w*|только с вами|только через вас)", re.I)),
    ("liability", re.compile(r"(?:ответственност\w*|компенсац\w*|возмещен\w*)", re.I)),
    ("contractual_obligation", re.compile(r"(?:обязательств\w*|минимум\s+\d+\s+клиент\w*)", re.I)),
)

AGREEMENT_PATTERNS = re.compile(
    r"\b(?:согласны|согласен|подходит|договорились|принимаем|утверждаем)\b", re.I
)


def authority_for(action, actor_type="ai"):
    if action in OPERATIONAL_ACTIONS:
        return ALLOW
    if action in OWNER_ONLY_ACTIONS:
        return ALLOW if actor_type == "owner" else REQUIRE_OWNER_APPROVAL
    return DENY


def detect_commercial_changes(message):
    text = str(message or "").strip()
    changes = {}
    for key, pattern in TERM_PATTERNS:
        match = pattern.search(text)
        if match:
            changes[key] = match.group(1).strip() if match.lastindex else match.group(0).strip()
    return changes


def guard_partner_response(response, has_unapproved_terms=False):
    text = str(response or "").strip()
    if has_unapproved_terms and AGREEMENT_PATTERNS.search(text):
        return (
            "Спасибо, предложение зафиксировали. Мы не можем самостоятельно "
            "согласовывать или изменять коммерческие условия. Передали вопрос "
            "ответственному лицу Phuket Life и вернёмся к Вам после решения."
        )
    return text


def _now():
    return datetime.now(timezone.utc).isoformat()


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def create_pending_proposal(partner_id, message, source="telegram_partner_reply",
                            source_message_id=None, db_path=None):
    changes = detect_commercial_changes(message)
    if not changes:
        return None
    normalized = " ".join(str(message).casefold().split())
    fingerprint = hashlib.sha256(_json(changes).encode() + normalized.encode()).hexdigest()
    connection = get_connection(db_path)
    connection.row_factory = sqlite3.Row
    try:
        with connection:
            connection.execute(
                """INSERT OR IGNORE INTO partner_term_proposals
                   (partner_id, proposed_changes, source, source_message,
                    source_message_id, fingerprint, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (partner_id, _json(changes), source, str(message), source_message_id,
                 fingerprint, _now()),
            )
            row = connection.execute(
                """SELECT * FROM partner_term_proposals
                   WHERE partner_id=? AND fingerprint=?
                     AND status='pending_owner_approval' ORDER BY id DESC LIMIT 1""",
                (partner_id, fingerprint),
            ).fetchone()
            connection.execute(
                """INSERT INTO partner_commercial_audit
                   (partner_id, proposal_id, action, actor_type, details)
                   SELECT ?, ?, 'proposal_recorded', 'partner', ?
                   WHERE NOT EXISTS (
                     SELECT 1 FROM partner_commercial_audit
                     WHERE proposal_id=? AND action='proposal_recorded')""",
                (partner_id, row["id"], _json({"source": source}), row["id"]),
            )
        return _proposal(row)
    finally:
        connection.close()


def _proposal(row):
    if not row:
        return None
    value = dict(row)
    value["proposed_changes"] = json.loads(value["proposed_changes"])
    return value


def list_pending_proposals(partner_id=None, db_path=None):
    connection = get_connection(db_path)
    connection.row_factory = sqlite3.Row
    try:
        sql = "SELECT * FROM partner_term_proposals WHERE status='pending_owner_approval'"
        params = ()
        if partner_id is not None:
            sql += " AND partner_id=?"
            params = (partner_id,)
        return [_proposal(r) for r in connection.execute(sql + " ORDER BY id", params)]
    finally:
        connection.close()


def decide_proposal(proposal_id, approve, owner_id=None, note=None, db_path=None):
    if owner_id is None:
        raise PermissionError("Коммерческое решение может принять только владелец")
    connection = get_connection(db_path)
    connection.row_factory = sqlite3.Row
    try:
        with connection:
            row = connection.execute(
                "SELECT * FROM partner_term_proposals WHERE id=?", (proposal_id,)
            ).fetchone()
            if not row or row["status"] != "pending_owner_approval":
                raise ValueError("Коммерческое предложение не найдено или уже обработано")
            changes = json.loads(row["proposed_changes"])
            if approve:
                for key, value in changes.items():
                    connection.execute(
                        """INSERT INTO partner_approved_terms
                           (partner_id, term_key, term_value, approved_by, approved_at, updated_at)
                           VALUES (?, ?, ?, ?, ?, ?)
                           ON CONFLICT(partner_id, term_key) DO UPDATE SET
                             term_value=excluded.term_value, approved_by=excluded.approved_by,
                             approved_at=excluded.approved_at, updated_at=excluded.updated_at""",
                        (row["partner_id"], key, str(value), owner_id, _now(), _now()),
                    )
            status = "approved" if approve else "rejected"
            connection.execute(
                """UPDATE partner_term_proposals SET status=?, decided_at=?,
                   decided_by=?, decision_note=? WHERE id=?""",
                (status, _now(), owner_id, note, proposal_id),
            )
            connection.execute(
                """INSERT INTO partner_commercial_audit
                   (partner_id, proposal_id, action, actor_type, actor_id, details)
                   VALUES (?, ?, ?, 'owner', ?, ?)""",
                (row["partner_id"], proposal_id,
                 "proposal_approved" if approve else "proposal_rejected",
                 owner_id, _json({"changes": changes, "note": note})),
            )
        return get_proposal(proposal_id, db_path)
    finally:
        connection.close()


def get_proposal(proposal_id, db_path=None):
    connection = get_connection(db_path)
    connection.row_factory = sqlite3.Row
    try:
        return _proposal(connection.execute(
            "SELECT * FROM partner_term_proposals WHERE id=?", (proposal_id,)
        ).fetchone())
    finally:
        connection.close()


def get_approved_terms(partner_id, db_path=None):
    connection = get_connection(db_path)
    try:
        return dict(connection.execute(
            "SELECT term_key, term_value FROM partner_approved_terms WHERE partner_id=?",
            (partner_id,),
        ).fetchall())
    finally:
        connection.close()


def record_proposal_delivery(proposal_id, delivered, error=None, db_path=None):
    proposal = get_proposal(proposal_id, db_path)
    if not proposal:
        raise ValueError("Коммерческое предложение не найдено")
    connection = get_connection(db_path)
    try:
        with connection:
            connection.execute(
                """INSERT INTO partner_commercial_audit
                   (partner_id, proposal_id, action, actor_type, details)
                   VALUES (?, ?, ?, 'application', ?)""",
                (
                    proposal["partner_id"], proposal_id,
                    "owner_decision_delivered" if delivered
                    else "owner_decision_delivery_failed",
                    _json({"error": error}),
                ),
            )
    finally:
        connection.close()
