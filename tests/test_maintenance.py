"""Log file, automatic backups and recovery from damaged settings files."""
import logging
import sqlite3
import sys
import tempfile
import unittest
import zipfile
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from norway_job_agent import maintenance, service
from norway_job_agent.web_api import close_resources, create_app


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.data = Path(self.temporary.name)
        service.initialize(self.data)
        self.job = service.import_vacancy(self.data, {"title": "Data Scientist", "company": "Fixture", "source": "manual",
                                                      "source_url": "https://jobs.example.test/1"})[0]

    def test_backup_holds_a_consistent_database_and_settings_but_no_credentials(self):
        (self.data / "gmail-token.json").write_text("{}", encoding="utf-8")
        path = maintenance.automatic_backup(self.data, now=datetime(2026, 10, 16, 9, 0, 0))
        self.assertEqual(path.name, "auto-20261016-090000.zip")
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
            self.assertIn("vacancies.db", names)
            self.assertTrue({"profile.json", "sources.json"} <= names)
            self.assertNotIn("gmail-token.json", names)
            archive.extract("vacancies.db", self.data / "check")
        with closing(sqlite3.connect(self.data / "check" / "vacancies.db")) as copy:
            self.assertEqual(copy.execute("SELECT title FROM jobs").fetchall(), [("Data Scientist",)])
        self.assertEqual(list((self.data / maintenance.BACKUP_FOLDER).glob("*.partial")), [])

    def test_backups_run_once_per_interval_and_keep_the_newest_ten(self):
        start = datetime(2026, 1, 1)
        self.assertIsNotNone(maintenance.automatic_backup(self.data, now=start))
        self.assertIsNone(maintenance.automatic_backup(self.data, now=start + timedelta(hours=2)))
        for day in range(1, 14):
            maintenance.automatic_backup(self.data, now=start + timedelta(days=day))
        backups = sorted(p.name for p in (self.data / maintenance.BACKUP_FOLDER).glob("auto-*.zip"))
        self.assertEqual(len(backups), maintenance.BACKUP_KEEP)
        self.assertEqual(backups[-1], "auto-20260114-000000.zip")

    def test_restore_uses_newest_valid_backup_and_keeps_the_damaged_file(self):
        profile = service.read_profile(self.data)
        profile["name"] = "Backed Up Name"
        service.save_profile(self.data, profile)
        maintenance.automatic_backup(self.data, now=datetime(2026, 10, 1))
        (self.data / "profile.json").write_text("{broken", encoding="utf-8")
        with self.assertRaises(service.SettingsFileError) as caught:
            service.read_profile(self.data)
        self.assertEqual(caught.exception.file, "profile.json")
        message = maintenance.restore_settings_file(self.data, "profile.json")
        self.assertIn("01 October 2026", message)
        self.assertEqual(service.read_profile(self.data)["name"], "Backed Up Name")
        damaged = list(self.data.glob("profile.json.damaged-*"))
        self.assertEqual([p.read_text(encoding="utf-8") for p in damaged], ["{broken"])

    def test_restore_without_backup_starts_from_defaults(self):
        (self.data / "sources.json").write_text('{"sources": "x"}', encoding="utf-8")
        self.assertIn("defaults", maintenance.restore_settings_file(self.data, "sources.json"))
        self.assertEqual(service.read_settings(self.data)["sources"], [{"type": "nav"}])
        with self.assertRaises(ValueError):
            maintenance.restore_settings_file(self.data, "vacancies.db")


class RecoveryApiTests(unittest.TestCase):
    def test_damaged_profile_offers_recovery_instead_of_a_dead_end(self):
        with tempfile.TemporaryDirectory() as folder:
            data = Path(folder)
            service.initialize(data)
            (data / "profile.json").write_text("{broken", encoding="utf-8")
            app = create_app(data, "fixture-session")
            app.config.update(TESTING=True, LOCAL_ORIGIN="http://127.0.0.1:7654")
            client, headers = app.test_client(), {"X-Session-Token": "fixture-session"}
            try:
                response = client.get("/api/bootstrap", base_url="http://127.0.0.1:7654", headers=headers)
                self.assertEqual(response.status_code, 409)
                self.assertEqual(response.get_json()["damaged_file"], "profile.json")
                self.assertIn(str(data / "profile.json"), response.get_json()["error"])
                response = client.post("/api/recover", json={"file": "profile.json"}, base_url="http://127.0.0.1:7654", headers=headers)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(client.get("/api/bootstrap", base_url="http://127.0.0.1:7654", headers=headers).status_code, 200)
                refused = client.post("/api/recover", json={"file": "../secrets"}, base_url="http://127.0.0.1:7654", headers=headers)
                self.assertEqual(refused.status_code, 400)
            finally:
                close_resources(app)
                app.extensions["local_state"].worker.shutdown(wait=True)


class LoggingTests(unittest.TestCase):
    def test_errors_go_to_a_rotating_file_once(self):
        root = logging.getLogger()
        before = list(root.handlers)
        with tempfile.TemporaryDirectory() as folder:
            try:
                path = maintenance.configure_logging(Path(folder))
                maintenance.configure_logging(Path(folder))
                added = [h for h in root.handlers if h not in before]
                self.assertEqual(len(added), 1)
                logging.getLogger("norway_job_agent.test").error("Fixture failure")
                added[0].flush()
                self.assertIn("Fixture failure", path.read_text(encoding="utf-8"))
            finally:
                for handler in root.handlers[:]:
                    if handler not in before:
                        root.removeHandler(handler)
                        handler.close()


if __name__ == "__main__":
    unittest.main()
