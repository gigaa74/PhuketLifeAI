import json
import tempfile
import unittest
from pathlib import Path

from database import MIGRATIONS, get_connection, init_db
from partner_identity_relinks import (
    PartnerIdentityRelinkError,
    decide_relink,
    get_relink,
    record_relink_answer,
    start_relink,
)
from partner_network import (
    create_partner,
    get_partner,
    resolve_partner_telegram_identity,
    sync_partner_telegram_identity,
)
from scripts.onboard_lera_partner import onboard_lera
from scripts.repair_lera_identity_merge import (
    LeraRepairConflict,
    repair_lera_identity,
)


class PartnerIdentityRelinkTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp.name) / "relink.db"
        init_db(self.db_path)
        self.lera, _ = onboard_lera(self.db_path)
        sync_partner_telegram_identity(1905717582, "lerikaDi", self.db_path)

    def tearDown(self):
        self.temp.cleanup()

    def _completed(self, user_id=8502972477, username="Hereld"):
        request, created = start_relink(user_id, username, self.db_path)
        self.assertTrue(created)
        request = record_relink_answer(request["id"], "Лера", self.db_path)
        return record_relink_answer(request["id"], "@lerikaDi", self.db_path)

    def test_relink_approve_preserves_partner_terms_and_revokes_old_identity(self):
        before = get_partner(self.lera["id"], self.db_path)
        request = self._completed()
        decided = decide_relink(
            request["id"], self.lera["id"], True, 900001, self.db_path
        )
        after = get_partner(self.lera["id"], self.db_path)
        self.assertEqual(decided["status"], "approved")
        self.assertEqual(after["telegram_user_id"], 8502972477)
        self.assertEqual(after["telegram_username"], "Hereld")
        self.assertEqual(after["approved_terms"], before["approved_terms"])
        self.assertEqual(after["services"], before["services"])
        self.assertEqual(after["operational_notes"], before["operational_notes"])
        self.assertEqual(
            resolve_partner_telegram_identity(1905717582, "lerikaDi", self.db_path)["status"],
            "not_found",
        )
        self.assertEqual(
            resolve_partner_telegram_identity(8502972477, "Hereld", self.db_path)["partner"]["id"],
            self.lera["id"],
        )
        connection = get_connection(self.db_path)
        try:
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM partners").fetchone()[0],
                1,
            )
        finally:
            connection.close()

    def test_relink_reject_and_identity_conflict_grant_no_access(self):
        request = self._completed()
        rejected = decide_relink(request["id"], 0, False, 900001, self.db_path)
        self.assertEqual(rejected["status"], "rejected")
        self.assertEqual(get_partner(self.lera["id"], self.db_path)["telegram_user_id"], 1905717582)

        request, _ = start_relink(777001, "occupied", self.db_path)
        request = record_relink_answer(request["id"], "Лера", self.db_path)
        request = record_relink_answer(request["id"], "@lerikaDi", self.db_path)
        create_partner(
            "Other", ["housing"], status="active", telegram_username="occupied",
            db_path=self.db_path,
        )
        with self.assertRaises(PartnerIdentityRelinkError):
            decide_relink(request["id"], self.lera["id"], True, 900001, self.db_path)

    def test_one_numeric_identity_has_one_open_relink(self):
        first, created = start_relink(8502972477, "Hereld", self.db_path)
        second, created_again = start_relink(8502972477, "changed", self.db_path)
        self.assertTrue(created)
        self.assertFalse(created_again)
        self.assertEqual(first["id"], second["id"])


class TwoStageMigrationTests(unittest.TestCase):
    def test_migration_011_preserves_v10_data_and_adds_structures(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "v10.db"
            connection = get_connection(db_path)
            try:
                connection.execute(
                    """CREATE TABLE schema_migrations(
                       version INTEGER PRIMARY KEY, applied_at TIMESTAMP)"""
                )
                for version, migration in MIGRATIONS:
                    if version > 10:
                        break
                    with connection:
                        migration(connection)
                        connection.execute(
                            "INSERT INTO schema_migrations(version) VALUES(?)",
                            (version,),
                        )
                with connection:
                    connection.execute(
                        "INSERT INTO partners(name,status) VALUES('Existing','active')"
                    )
                    connection.execute(
                        """INSERT INTO partner_applications
                           (telegram_user_id,status,current_step,applicant_name)
                           VALUES(123,'collecting','contact','Existing applicant')"""
                    )
                partner_columns = [
                    row[1] for row in connection.execute(
                        "PRAGMA table_info(partners)"
                    )
                ]
                selected_partner_columns = ", ".join(partner_columns)
                before = connection.execute(
                    f"SELECT {selected_partner_columns} FROM partners"
                ).fetchall()
            finally:
                connection.close()
            init_db(db_path)
            connection = get_connection(db_path)
            try:
                columns = {
                    row[1] for row in connection.execute(
                        "PRAGMA table_info(partner_applications)"
                    )
                }
                self.assertTrue({
                    "delivery_model_text", "live_source_text",
                    "availability_confirmation_text",
                    "request_requirements_text", "commercial_model_text",
                    "links_text", "licenses_text",
                }.issubset(columns))
                self.assertIsNotNone(connection.execute(
                    """SELECT name FROM sqlite_master WHERE type='table'
                       AND name='partner_identity_relink_requests'"""
                ).fetchone())
                self.assertEqual(
                    connection.execute(
                        f"SELECT {selected_partner_columns} FROM partners"
                    ).fetchall(),
                    before,
                )
                self.assertIn("invite_expires_at", {
                    row[1] for row in connection.execute(
                        "PRAGMA table_info(partners)"
                    )
                })
                self.assertEqual(
                    [row[0] for row in connection.execute(
                        "SELECT version FROM schema_migrations ORDER BY version"
                    )], list(range(1, 15)),
                )
            finally:
                connection.close()


class LeraIdentityRepairTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp.name) / "repair.db"
        init_db(self.db_path)
        connection = get_connection(self.db_path)
        try:
            with connection:
                connection.execute(
                    """INSERT INTO partners(id,name,status,telegram_user_id,telegram_username)
                       VALUES(1,'Test Partner','blocked',6233935382,'GIGAA74')"""
                )
                connection.execute(
                    """INSERT INTO partners(id,name,status,telegram_user_id,telegram_username)
                       VALUES(2,'Test Partner 2','active',8733594703,'gigaaa74')"""
                )
                connection.execute(
                    """INSERT INTO partners
                       (id,name,status,partner_type,telegram_user_id,telegram_username,
                        services,areas,allowed_actions,operational_notes,auto_handoff_enabled)
                       VALUES(3,'Лера','active','hybrid',1905717582,'lerikaDi',
                              '["housing"]','["Karon"]','["receive_requests"]',
                              '{"direction":"жильё"}',1)"""
                )
                for partner_id, name in ((4, "Инна"), (5, "Сергей")):
                    connection.execute(
                        "INSERT INTO partners(id,name,status) VALUES(?,?,'active')",
                        (partner_id, name),
                    )
                connection.execute(
                    """INSERT INTO partners
                       (id,name,status,telegram_user_id,telegram_username,contacts,
                        notes,operational_notes,services,areas)
                       VALUES(6,'Валерия','active',8502972477,NULL,'@Hereld',
                              'обслуживание квартир','рабочие районы',
                              '["other"]','["Phuket"]')"""
                )
                for index in range(7):
                    connection.execute(
                        """INSERT INTO partner_approved_terms
                           (partner_id,term_key,term_value) VALUES(3,?,?)""",
                        (f"term_{index}", str(index)),
                    )
                for _ in range(2):
                    connection.execute(
                        """INSERT INTO partner_commercial_audit
                           (partner_id,action,actor_type) VALUES(3,'existing','owner')"""
                    )
                connection.execute(
                    """INSERT INTO partner_applications
                       (id,telegram_user_id,telegram_username,contact_text,status,
                        current_step,partner_id,services_text,areas_text)
                       VALUES(1,8502972477,'Hereld','@Hereld','approved','complete',6,
                              'обслуживание квартир','Пхукет')"""
                )
                connection.execute(
                    """INSERT INTO partner_approved_terms
                       (partner_id,term_key,term_value)
                       VALUES(2,'commission','15%')"""
                )
                connection.execute("INSERT INTO clients(telegram_id) VALUES(700001)")
                client_id = connection.execute(
                    "SELECT id FROM clients WHERE telegram_id=700001"
                ).fetchone()[0]
                connection.execute(
                    "INSERT INTO cases(client_id,status) VALUES(?,'new')", (client_id,)
                )
                case_id = connection.execute(
                    "SELECT id FROM cases WHERE client_id=?", (client_id,)
                ).fetchone()[0]
                request_ids = []
                for index in range(3):
                    cursor = connection.execute(
                        """INSERT INTO partner_requests
                           (case_id,partner_id,service_category,status,request_payload)
                           VALUES(?,2,?,'responded','{}')""",
                        (case_id, f"test_{index}"),
                    )
                    request_ids.append(cursor.lastrowid)
                connection.execute(
                    """INSERT INTO partner_offers
                       (partner_request_id,case_id,partner_id,raw_partner_response)
                       VALUES(?,?,2,'test offer')""",
                    (request_ids[0], case_id),
                )
                proposal_ids = []
                for index in range(2):
                    cursor = connection.execute(
                        """INSERT INTO partner_term_proposals
                           (partner_id,status,proposed_changes,source,source_message,fingerprint)
                           VALUES(2,'pending_owner_approval','{}','test','test',?)""",
                        (f"fingerprint-{index}",),
                    )
                    proposal_ids.append(cursor.lastrowid)
                for index in range(7):
                    connection.execute(
                        """INSERT INTO partner_commercial_audit
                           (partner_id,proposal_id,action,actor_type)
                           VALUES(2,?,'test','owner')""",
                        (proposal_ids[index % 2],),
                    )
        finally:
            connection.close()

    def tearDown(self):
        self.temp.cleanup()

    def test_repair_dry_run_apply_and_idempotency(self):
        connection = get_connection(self.db_path)
        try:
            others_before = connection.execute(
                "SELECT * FROM partners WHERE id IN (4,5) ORDER BY id"
            ).fetchall()
            clients_before = connection.execute("SELECT * FROM clients ORDER BY id").fetchall()
            cases_before = connection.execute("SELECT * FROM cases ORDER BY id").fetchall()
            terms_before = connection.execute(
                "SELECT COUNT(*) FROM partner_approved_terms"
            ).fetchone()[0]
        finally:
            connection.close()
        dry = repair_lera_identity(self.db_path, dry_run=True)
        self.assertTrue(dry["changed"])
        self.assertEqual(dry["partner_id"], 6)
        self.assertEqual(dry["delete_partner_ids"], [1, 2, 3])
        applied = repair_lera_identity(self.db_path, dry_run=False)
        repeated = repair_lera_identity(self.db_path, dry_run=False)
        self.assertTrue(applied["changed"])
        self.assertFalse(repeated["changed"])
        lera = get_partner(6, self.db_path)
        self.assertEqual(lera["telegram_user_id"], 8502972477)
        self.assertEqual(lera["telegram_username"], "Hereld")
        self.assertEqual(lera["name"], "Валерия")
        self.assertIsNone(get_partner(3, self.db_path))
        self.assertIsNone(get_partner(1, self.db_path))
        self.assertIsNone(get_partner(2, self.db_path))
        self.assertEqual(len(lera["approved_terms"]), 7)
        connection = get_connection(self.db_path)
        try:
            self.assertEqual(connection.execute(
                "SELECT partner_id FROM partner_applications WHERE id=1"
            ).fetchone()[0], 6)
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM partner_commercial_audit WHERE partner_id=6"
            ).fetchone()[0], 2)
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM partners"
            ).fetchone()[0], 3)
            self.assertIsNone(connection.execute(
                "SELECT 1 FROM partner_requests WHERE partner_id IN (1,2,3)"
            ).fetchone())
            self.assertIsNone(connection.execute("PRAGMA foreign_key_check").fetchone())
            self.assertEqual(connection.execute(
                "SELECT * FROM partners WHERE id IN (4,5) ORDER BY id"
            ).fetchall(), others_before)
            self.assertEqual(
                connection.execute("SELECT * FROM clients ORDER BY id").fetchall(),
                clients_before,
            )
            self.assertEqual(
                connection.execute("SELECT * FROM cases ORDER BY id").fetchall(),
                cases_before,
            )
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM partner_approved_terms"
            ).fetchone()[0], terms_before - 1)
        finally:
            connection.close()

    def test_repair_refuses_unexpected_state(self):
        connection = get_connection(self.db_path)
        try:
            with connection:
                connection.execute(
                    "UPDATE partners SET telegram_username='unexpected' WHERE id=3"
                )
        finally:
            connection.close()
        with self.assertRaises(LeraRepairConflict):
            repair_lera_identity(self.db_path, dry_run=False)

    def test_repair_refuses_unknown_partner_dependency_without_deleting(self):
        connection = get_connection(self.db_path)
        try:
            with connection:
                connection.execute(
                    """CREATE TABLE unexpected_partner_link(
                       id INTEGER PRIMARY KEY, partner_id INTEGER,
                       FOREIGN KEY(partner_id) REFERENCES partners(id))"""
                )
                connection.execute(
                    "INSERT INTO unexpected_partner_link(partner_id) VALUES(3)"
                )
        finally:
            connection.close()
        with self.assertRaises(LeraRepairConflict):
            repair_lera_identity(self.db_path, dry_run=False)
        self.assertIsNotNone(get_partner(3, self.db_path))
        self.assertEqual(get_partner(3, self.db_path)["telegram_user_id"], 1905717582)

    def test_repair_rolls_back_everything_on_dependency_collision(self):
        connection = get_connection(self.db_path)
        try:
            with connection:
                case_id = connection.execute("SELECT id FROM cases LIMIT 1").fetchone()[0]
                connection.execute(
                    """INSERT INTO partner_requests
                       (case_id,partner_id,service_category,status,request_payload)
                       VALUES(?,6,'collision','created','{}')""", (case_id,)
                )
                connection.execute(
                    """INSERT INTO partner_requests
                       (case_id,partner_id,service_category,status,request_payload)
                       VALUES(?,3,'collision','created','{}')""", (case_id,)
                )
        finally:
            connection.close()
        with self.assertRaises(LeraRepairConflict):
            repair_lera_identity(self.db_path, dry_run=False)
        donor = get_partner(3, self.db_path)
        canonical = get_partner(6, self.db_path)
        self.assertEqual(donor["telegram_user_id"], 1905717582)
        self.assertEqual(canonical["telegram_user_id"], 8502972477)
        self.assertIsNone(canonical["telegram_username"])


if __name__ == "__main__":
    unittest.main()
