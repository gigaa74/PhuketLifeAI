import argparse
import sqlite3
from datetime import datetime
from pathlib import Path


def backup_database(source, destination_dir, *, timestamp=None):
    source = Path(source).resolve()
    destination_dir = Path(destination_dir).resolve()
    if not source.is_file():
        raise FileNotFoundError(f"База не найдена: {source}")
    destination_dir.mkdir(parents=True, exist_ok=True)
    stamp = timestamp or datetime.now().strftime("%Y%m%d-%H%M%S")
    destination = destination_dir / f"{source.stem}.{stamp}.db"
    if destination.exists():
        raise FileExistsError(f"Backup уже существует: {destination}")

    source_connection = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    destination_connection = sqlite3.connect(destination)
    try:
        source_integrity = source_connection.execute(
            "PRAGMA integrity_check"
        ).fetchone()[0]
        if source_integrity != "ok":
            raise RuntimeError("Исходная база не прошла integrity_check")
        source_connection.backup(destination_connection)
        destination_integrity = destination_connection.execute(
            "PRAGMA integrity_check"
        ).fetchone()[0]
        if destination_integrity != "ok":
            raise RuntimeError("Backup не прошёл integrity_check")
    except Exception:
        destination_connection.close()
        source_connection.close()
        destination.unlink(missing_ok=True)
        raise
    else:
        destination_connection.close()
        source_connection.close()
    return destination


def main():
    parser = argparse.ArgumentParser(
        description="Создать проверенный SQLite backup через штатный backup API."
    )
    parser.add_argument("--source", default="phuketlife.db")
    parser.add_argument("--destination-dir", default="backups")
    arguments = parser.parse_args()
    path = backup_database(arguments.source, arguments.destination_dir)
    print(f"Backup создан: {path}")


if __name__ == "__main__":
    main()
