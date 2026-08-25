import sqlite3
import tempfile
import unittest
from pathlib import Path

from scripts.backup_database import backup_database


class DatabaseBackupTests(unittest.TestCase):
    def test_backup_is_readable_and_preserves_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "production.db"
            connection = sqlite3.connect(source)
            connection.execute("CREATE TABLE sample(id INTEGER, value TEXT)")
            connection.execute("INSERT INTO sample VALUES(1, 'preserved')")
            connection.commit()
            connection.close()

            destination = backup_database(
                source, root / "backups", timestamp="20260825-120000"
            )

            backup = sqlite3.connect(destination)
            try:
                self.assertEqual(
                    backup.execute("SELECT * FROM sample").fetchall(),
                    [(1, "preserved")],
                )
                self.assertEqual(
                    backup.execute("PRAGMA integrity_check").fetchone()[0], "ok"
                )
            finally:
                backup.close()

    def test_existing_destination_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "production.db"
            sqlite3.connect(source).close()
            first = backup_database(source, root / "backups", timestamp="same")
            self.assertTrue(first.exists())
            with self.assertRaises(FileExistsError):
                backup_database(source, root / "backups", timestamp="same")


if __name__ == "__main__":
    unittest.main()
