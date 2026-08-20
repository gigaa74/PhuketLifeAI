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


MIGRATIONS = (
    (1, _migration_001_initial_schema),
    (2, _migration_002_case_fields),
    (3, _migration_003_indexes),
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
