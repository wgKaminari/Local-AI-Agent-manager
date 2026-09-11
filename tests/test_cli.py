import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from norway_job_agent.cli import collect_sources, main
from norway_job_agent.storage import JobStore


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.folder = Path(self.temporary.name)
        self.data = self.folder / "personal"
        self.vacancy = {"title": "Python Developer", "company": "Fixture AS", "source_url": "https://example.org/jobs/abc", "description": "Python services in Oslo"}

    def run_cli(self, *args):
        output, errors = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            code = main(["--data-dir", str(self.data), *map(str, args)])
        return code, output.getvalue(), errors.getvalue()

    def test_import_review_refresh_and_export_end_to_end(self):
        self.assertEqual(self.run_cli("init")[0], 0)
        profile = self.data / "profile.json"
        profile.write_text(json.dumps({"skills": ["Python"], "evidence": ["Built an internal Python reporting tool."]}), encoding="utf-8")
        source = self.folder / "vacancy.json"
        source.write_text(json.dumps(self.vacancy), encoding="utf-8")
        self.assertEqual(self.run_cli("import", source)[0], 0)
        self.assertEqual(self.run_cli("status", "1", "saved")[0], 0)
        self.assertEqual(self.run_cli("notes", "1", "Review tomorrow")[0], 0)
        self.assertEqual(json.loads(self.run_cli("import", source)[1]), {"created": 0, "updated": 1})
        rows = json.loads(self.run_cli("list", "--match")[1])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "saved")
        self.assertEqual(rows[0]["match"]["matched_skills"], ["Python"])
        output = self.folder / "brief.md"
        self.assertEqual(self.run_cli("brief", "1", "--output", output)[0], 0)
        self.assertIn("Built an internal Python reporting tool", output.read_text(encoding="utf-8"))
        output.write_text("My reviewed edits", encoding="utf-8")
        self.assertEqual(self.run_cli("brief", "1", "--output", output)[0], 1)
        self.assertEqual(output.read_text(encoding="utf-8"), "My reviewed edits")
        self.assertEqual(json.loads(self.run_cli("show", "1")[1])["notes"], "Review tomorrow")

    def test_invalid_batch_does_not_partially_import(self):
        self.run_cli("init")
        source = self.folder / "bad.json"
        source.write_text(json.dumps([self.vacancy, {"title": "Missing source"}]), encoding="utf-8")
        self.assertEqual(self.run_cli("import", source)[0], 1)
        self.assertEqual(json.loads(self.run_cli("list")[1]), [])

    def test_init_preserves_existing_profile(self):
        self.run_cli("init")
        profile = self.data / "profile.json"
        profile.write_text('{"name": "My name"}', encoding="utf-8")
        self.run_cli("init")
        self.assertEqual(json.loads(profile.read_text(encoding="utf-8")), {"name": "My name"})

    def test_collection_reports_failed_sources_and_keeps_successful_results(self):
        with JobStore(":memory:") as store:
            with patch("norway_job_agent.sources.fetch_greenhouse", return_value=[self.vacancy]):
                result = collect_sources({"sources": [{"type": "greenhouse", "board": "fixture"}, {"type": "unsupported"}]}, store)
            self.assertEqual(result["created"], 1)
            self.assertTrue(result["sources"][0]["ok"])
            self.assertFalse(result["sources"][1]["ok"])
            self.assertEqual(len(store.list_jobs()), 1)


if __name__ == "__main__":
    unittest.main()
