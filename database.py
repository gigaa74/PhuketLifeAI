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


MIGRATIONS = (
    (1, _migration_001_initial_schema),
    (2, _migration_002_case_fields),
    (3, _migration_003_indexes),
    (4, _migration_004_partner_network),
    (5, _migration_005_partner_handoff),
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


if __name__ == "__main__":
    init_db()
    print("База Phuket Life успешно создана или обновлена!")
