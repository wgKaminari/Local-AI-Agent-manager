import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from norway_job_agent import service
from support import nav_fetch, offline_collection



def setUpModule():
    offline_collection()
    # Drafting starts an installed Ollama; tests use mocked generation and
    # must never launch the real local AI server.
    patcher = patch("norway_job_agent.runtime_setup.ensure_ollama")
    patcher.start()
    unittest.addModuleCleanup(patcher.stop)

class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.folder = Path(self.temporary.name)
        service.initialize(self.folder)
        self.job = {"source": "nav", "source_id": "12345678-1234-4234-8234-123456789012", "title": "Data Scientist", "company": "Fixture AS", "source_url": "https://example.com/jobs/12345", "description": "Python and statistics", "location": "Oslo, Norway"}

    def test_nav_withdrawal_hides_ad_preserves_personal_workflow(self):
        with patch("norway_job_agent.service._fetch_nav", return_value=nav_fetch(jobs=[self.job])):
            self.assertEqual(service.collect(self.folder)["created"], 1)
        service.update_workflow(self.folder, 1, "saved", "My own note")
        service.save_edited_letter(self.folder, 1, "My reviewed letter")
        with patch("norway_job_agent.service._fetch_nav", return_value=nav_fetch(withdrawn=[self.job["source_id"]])):
            self.assertEqual(service.collect(self.folder)["withdrawn"], 1)
        self.assertEqual(service.vacancies(self.folder), [])
        archived = service.get_vacancy(self.folder, 1)
        self.assertEqual(archived["description"], "")
        self.assertEqual(archived["notes"], "My own note")
        self.assertEqual(archived["status"], "saved")
        self.assertEqual(service.draft_history(self.folder, 1)[0]["content"], "My reviewed letter")
        with self.assertRaisesRegex(ValueError, "withdrawn"):
            service.write_letter(self.folder, 1)

    def test_failed_collection_preserves_feed_position(self):
        from norway_job_agent import nav_index
        from norway_job_agent.sources import SourceError
        from support import REAL_SYNC
        previous = {"url": "https://pam-stilling-feed.nav.no/api/v1/feed/previous", "etag": "", "last_modified": "",
                    "initial_since": "Mon, 01 Jun 2026 00:00:00 GMT"}
        with nav_index.NavIndex(self.folder) as index:
            index.save_page([], previous)
        updates = []
        with patch("norway_job_agent.nav_index.sync", REAL_SYNC), \
                patch("norway_job_agent.nav._get_token", return_value=("fixture-token", True)), \
                patch("norway_job_agent.nav._fetch_nav", side_effect=SourceError("Temporary failure")):
            report = service.collect(self.folder, progress=updates.append)
        self.assertFalse(report["sources"][0]["ok"])
        self.assertIn("Temporary failure", report["sources"][0]["error"])
        self.assertIn("Checking 1 source, including 1 whole-market job site", updates[0])
        self.assertTrue(any("NAV (1 of 1)" in update for update in updates), updates)
        with nav_index.NavIndex(self.folder) as index:
            self.assertEqual(index.cursor(), previous)

    def test_target_and_horizon_are_explained_separately(self):
        service.import_vacancy(self.folder, self.job)
        service.import_vacancy(self.folder, {**self.job, "source_id": "other", "source_url": "https://example.com/jobs/other", "title": "Quantitative Analyst"})
        jobs = service.vacancies(self.folder)
        self.assertEqual([job["match"]["track"] for job in jobs], ["target", "horizon"])
        self.assertIn("Related to your target fields", jobs[1]["match"]["reasons"][0])
        self.assertTrue(any("A1-A2" in warning and "B1" in warning for warning in jobs[0]["match"]["warnings"]))

    def test_generated_letter_saved_without_application_status_change(self):
        service.import_vacancy(self.folder, self.job)
        result = {"cover_letter": "A factual draft", "review_notes": [], "used_evidence": ["Example"]}
        with patch("norway_job_agent.local_ai.generate_cover_letter", return_value=result):
            saved = service.write_letter(self.folder, 1)
        self.assertGreater(saved["id"], 0)
        self.assertEqual(service.get_vacancy(self.folder, 1)["status"], "new")
        self.assertEqual(service.draft_history(self.folder, 1)[0]["content"], "A factual draft")

    def test_cv_document_import_and_profile_suggestion_do_not_overwrite_user(self):
        docx = self.folder / "cv.docx"
        with zipfile.ZipFile(docx, "w") as archive:
            archive.writestr("word/document.xml", '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Candidate</w:t></w:r></w:p><w:p><w:r><w:t>Python projects</w:t></w:r></w:p></w:body></w:document>')
        self.assertEqual(service.import_cv_text(docx), "Candidate\nPython projects")
        before = service.read_profile(self.folder)
        with patch("norway_job_agent.local_ai.extract_profile", return_value={"name": "Suggested name"}):
            service.suggest_profile(self.folder, "Candidate")
        self.assertEqual(service.read_profile(self.folder), before)

    def test_settings_reject_paid_remote_cloud_model(self):
        with self.assertRaises(ValueError):
            service.save_settings(self.folder, {"sources": [], "model": "any-model:cloud"})


if __name__ == "__main__":
    unittest.main()
