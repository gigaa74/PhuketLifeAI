import json
import sqlite3

DB_NAME = "phuketlife.db"


def get_connection(db_path=None):
    connection = sqlite3.connect(db_path or DB_NAME)
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 5000")
    return connection


def _column_names(connection, table):
    return {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}


def _migration_001_initial_schema(connection):
    cursor = connection.cursor()

    # Клиенты
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS clients (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id INTEGER UNIQUE NOT NULL,
            username TEXT,
            first_name TEXT,
            last_name TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Диалоги
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_id INTEGER NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (client_id) REFERENCES clients(id)
        )
    """)

    # Кейсы
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS cases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_id INTEGER NOT NULL,
            title TEXT,
            description TEXT,
            status TEXT DEFAULT 'new',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (client_id) REFERENCES clients(id)
        )
    """)

    # Партнёры
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS partners (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            location TEXT,
            services TEXT,
            contacts TEXT,
            notes TEXT,
            status TEXT DEFAULT 'active',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

def _migration_002_case_fields(connection):
    columns = _column_names(connection, "cases")
    additions = (
        ("category", "TEXT"),
        ("data", "TEXT"),
        ("missing_data", "TEXT"),
        ("priority", "TEXT DEFAULT 'normal'"),
    )
    for name, definition in additions:
        if name not in columns:
            connection.execute(f"ALTER TABLE cases ADD COLUMN {name} {definition}")


def _migration_003_indexes(connection):
    connection.executescript(
        """
        CREATE INDEX IF NOT EXISTS idx_messages_client_id_id
            ON messages(client_id, id);
        CREATE INDEX IF NOT EXISTS idx_cases_client_status_id
            ON cases(client_id, status, id);
        """
    )


def _migration_004_partner_network(connection):
    partner_columns = _column_names(connection, "partners")
    additions = (
        ("location", "TEXT"),
        ("services", "TEXT"),
        ("contacts", "TEXT"),
        ("notes", "TEXT"),
        ("status", "TEXT DEFAULT 'candidate'"),
        ("created_at", "TIMESTAMP"),
        ("telegram_user_id", "INTEGER"),
        ("telegram_username", "TEXT"),
        ("areas", "TEXT"),
        ("commission_notes", "TEXT"),
        ("updated_at", "TIMESTAMP"),
        ("invite_token_hash", "TEXT"),
    )
    for name, definition in additions:
        if name not in partner_columns:
            connection.execute(
                f"ALTER TABLE partners ADD COLUMN {name} {definition}"
            )
    connection.execute(
        "UPDATE partners SET created_at = COALESCE(created_at, CURRENT_TIMESTAMP)"
    )
    connection.execute(
        "UPDATE partners SET updated_at = COALESCE(updated_at, created_at, CURRENT_TIMESTAMP)"
    )
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS partner_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            case_id INTEGER NOT NULL,
            partner_id INTEGER NOT NULL,
            service_category TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'created',
            request_payload TEXT NOT NULL,
            partner_response TEXT,
            telegram_message_id INTEGER,
            error_code TEXT,
            error_message TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            sent_at TIMESTAMP,
            responded_at TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (case_id) REFERENCES cases(id),
            FOREIGN KEY (partner_id) REFERENCES partners(id)
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_partners_telegram_user_id
            ON partners(telegram_user_id)
            WHERE telegram_user_id IS NOT NULL;
        CREATE UNIQUE INDEX IF NOT EXISTS idx_partners_invite_token_hash
            ON partners(invite_token_hash)
            WHERE invite_token_hash IS NOT NULL;
        CREATE INDEX IF NOT EXISTS idx_partners_status
            ON partners(status, id);
        CREATE INDEX IF NOT EXISTS idx_partner_requests_case_id
            ON partner_requests(case_id, id);
        CREATE INDEX IF NOT EXISTS idx_partner_requests_partner_id
            ON partner_requests(partner_id, id);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_partner_requests_active_unique
            ON partner_requests(case_id, partner_id, service_category)
            WHERE status IN ('created', 'sent');
        """
    )


def _migration_005_partner_handoff(connection):
    partner_columns = _column_names(connection, "partners")
    if "auto_handoff_enabled" not in partner_columns:
        connection.execute(
            "ALTER TABLE partners ADD COLUMN auto_handoff_enabled INTEGER DEFAULT 0"
        )
    request_columns = _column_names(connection, "partner_requests")
    if "partner_response_metadata" not in request_columns:
        connection.execute(
            "ALTER TABLE partner_requests ADD COLUMN partner_response_metadata TEXT"
        )
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS partner_offers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            partner_request_id INTEGER NOT NULL UNIQUE,
            case_id INTEGER NOT NULL,
            partner_id INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'needs_review',
            handoff_decision TEXT NOT NULL DEFAULT 'review_required',
            raw_partner_response TEXT NOT NULL,
            offer_title TEXT,
            offer_description TEXT,
            price_text TEXT,
            currency TEXT,
            url TEXT,
            conditions TEXT,
            validation_reasons TEXT NOT NULL DEFAULT '[]',
            validation_score REAL NOT NULL DEFAULT 0,
            telegram_metadata TEXT,
            client_telegram_message_id INTEGER,
            error_code TEXT,
            error_message TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            validated_at TIMESTAMP,
            sent_at TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (partner_request_id) REFERENCES partner_requests(id),
            FOREIGN KEY (case_id) REFERENCES cases(id),
            FOREIGN KEY (partner_id) REFERENCES partners(id)
        );
        CREATE INDEX IF NOT EXISTS idx_partner_offers_case_id
            ON partner_offers(case_id, id);
        CREATE INDEX IF NOT EXISTS idx_partner_offers_status
            ON partner_offers(status, id);
        CREATE INDEX IF NOT EXISTS idx_partner_offers_partner_id
            ON partner_offers(partner_id, id);
        """
    )


def _migration_006_partner_operating_system(connection):
    partner_columns = _column_names(connection, "partners")
    additions = (
        ("partner_type", "TEXT NOT NULL DEFAULT 'service_provider'"),
        ("allowed_actions", "TEXT NOT NULL DEFAULT '[]'"),
        ("operational_notes", "TEXT"),
    )
    for name, definition in additions:
        if name not in partner_columns:
            connection.execute(f"ALTER TABLE partners ADD COLUMN {name} {definition}")
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS partner_approved_terms (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            partner_id INTEGER NOT NULL,
            term_key TEXT NOT NULL,
            term_value TEXT NOT NULL,
            approved_by INTEGER,
            approved_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(partner_id, term_key),
            FOREIGN KEY (partner_id) REFERENCES partners(id)
        );
        CREATE TABLE IF NOT EXISTS partner_term_proposals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            partner_id INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending_owner_approval',
            proposed_changes TEXT NOT NULL,
            source TEXT NOT NULL,
            source_message TEXT NOT NULL,
            source_message_id INTEGER,
            fingerprint TEXT NOT NULL,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            decided_at TIMESTAMP,
            decided_by INTEGER,
            decision_note TEXT,
            UNIQUE(partner_id, fingerprint, status),
            FOREIGN KEY (partner_id) REFERENCES partners(id)
        );
        CREATE TABLE IF NOT EXISTS partner_commercial_audit (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            partner_id INTEGER NOT NULL,
            proposal_id INTEGER,
            action TEXT NOT NULL,
            actor_type TEXT NOT NULL,
            actor_id INTEGER,
            details TEXT NOT NULL DEFAULT '{}',
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (partner_id) REFERENCES partners(id),
            FOREIGN KEY (proposal_id) REFERENCES partner_term_proposals(id)
        );
        CREATE INDEX IF NOT EXISTS idx_partner_terms_partner
            ON partner_approved_terms(partner_id, term_key);
        CREATE INDEX IF NOT EXISTS idx_partner_proposals_status
            ON partner_term_proposals(status, partner_id, id);
        CREATE INDEX IF NOT EXISTS idx_partner_audit_partner
            ON partner_commercial_audit(partner_id, id);
        """
    )


def _migration_007_scout_candidates(connection):
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS scout_candidates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scout_type TEXT NOT NULL CHECK(scout_type IN ('partner', 'client')),
            source_chat_id INTEGER NOT NULL,
            source_chat_title TEXT,
            source_message_id INTEGER NOT NULL,
            source_user_id INTEGER,
            source_username TEXT,
            original_text TEXT NOT NULL,
            detected_category TEXT NOT NULL,
            confidence REAL NOT NULL CHECK(confidence >= 0 AND confidence <= 1),
            detection_reasons TEXT NOT NULL DEFAULT '[]',
            status TEXT NOT NULL DEFAULT 'needs_review',
            owner_decision TEXT NOT NULL DEFAULT 'pending',
            owner_decided_at TIMESTAMP,
            owner_decided_by INTEGER,
            outreach_status TEXT NOT NULL DEFAULT 'not_contacted',
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(scout_type, source_chat_id, source_message_id)
        );
        CREATE INDEX IF NOT EXISTS idx_scout_candidates_review
            ON scout_candidates(scout_type, status, id);
        CREATE INDEX IF NOT EXISTS idx_scout_candidates_identity
            ON scout_candidates(scout_type, source_user_id, id)
            WHERE source_user_id IS NOT NULL;
        """
    )


def _migration_008_scout_detected_categories(connection):
    columns = _column_names(connection, "scout_candidates")
    if "detected_categories" not in columns:
        connection.execute(
            "ALTER TABLE scout_candidates ADD COLUMN detected_categories TEXT"
        )
    rows = connection.execute(
        """SELECT id, detected_category FROM scout_candidates
           WHERE detected_categories IS NULL OR trim(detected_categories) = ''"""
    ).fetchall()
    for candidate_id, category in rows:
        connection.execute(
            "UPDATE scout_candidates SET detected_categories=? WHERE id=?",
            (json.dumps([category], ensure_ascii=False), candidate_id),
        )


def _migration_009_partner_applications(connection):
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS partner_applications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_user_id INTEGER NOT NULL,
            telegram_username TEXT,
            applicant_name TEXT,
            services_text TEXT,
            areas_text TEXT,
            contact_text TEXT,
            status TEXT NOT NULL DEFAULT 'collecting'
                CHECK(status IN ('collecting', 'needs_review', 'approved',
                                 'rejected', 'cancelled')),
            current_step TEXT NOT NULL DEFAULT 'name',
            partner_id INTEGER,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            submitted_at TIMESTAMP,
            decided_at TIMESTAMP,
            decided_by INTEGER,
            decision_note TEXT,
            FOREIGN KEY (partner_id) REFERENCES partners(id)
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_partner_applications_open_identity
            ON partner_applications(telegram_user_id)
            WHERE status IN ('collecting', 'needs_review');
        CREATE INDEX IF NOT EXISTS idx_partner_applications_review
            ON partner_applications(status, id);
        """
    )


MIGRATIONS = (
    (1, _migration_001_initial_schema),
    (2, _migration_002_case_fields),
    (3, _migration_003_indexes),
    (4, _migration_004_partner_network),
    (5, _migration_005_partner_handoff),
    (6, _migration_006_partner_operating_system),
    (7, _migration_007_scout_candidates),
    (8, _migration_008_scout_detected_categories),
    (9, _migration_009_partner_applications),
)


def init_db(db_path=None):
    """Create or safely migrate an SQLite database to the current schema."""
    connection = get_connection(db_path)
    try:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY,
                applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        applied = {
            row[0]
            for row in connection.execute("SELECT version FROM schema_migrations")
        }
        for version, migration in MIGRATIONS:
            if version in applied:
                continue
            with connection:
                migration(connection)
                connection.execute(
                    "INSERT INTO schema_migrations(version) VALUES (?)",
                    (version,),
                )
    finally:
        connection.close()


def get_or_create_client(
    telegram_id,
    username=None,
    first_name=None,
    last_name=None,
    db_path=None,
):
    connection = get_connection(db_path)
    try:
        row = connection.execute(
            "SELECT id FROM clients WHERE telegram_id = ?",
            (telegram_id,),
        ).fetchone()
        if row:
            return row[0]
        with connection:
            cursor = connection.execute(
                """
                INSERT INTO clients
                    (telegram_id, username, first_name, last_name)
                VALUES (?, ?, ?, ?)
                """,
                (telegram_id, username, first_name, last_name),
            )
        return cursor.lastrowid
    finally:
        connection.close()


def get_client_by_telegram_id(telegram_id, db_path=None):
    connection = get_connection(db_path)
    connection.row_factory = sqlite3.Row
    try:
        row = connection.execute(
            "SELECT * FROM clients WHERE telegram_id = ?", (int(telegram_id),)
        ).fetchone()
        return dict(row) if row else None
    finally:
        connection.close()


if __name__ == "__main__":
    init_db()
    print("База Phuket Life успешно создана или обновлена!")
