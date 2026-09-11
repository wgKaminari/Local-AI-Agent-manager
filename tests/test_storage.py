"""Regressions for deduplication and preservation of user-managed state."""

import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from norway_job_agent.storage import JobStore, canonical_url


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "nested" / "jobs.sqlite3"
        self.store = JobStore(self.path)
        self.addCleanup(self.temporary.cleanup)
        self.addCleanup(self.store.close)

    def posting(self, **overrides):
        return {
            "source": "company", "source_id": "123", "source_url": "https://example.org/jobs/123",
            "apply_url": "https://ats.example.org/jobs/123", "title": "Python Developer",
            "company": "Example AS", "location": "Oslo", "description": "Build useful services.",
            **overrides,
        }

    def test_tracking_urls_deduplicate_and_job_query_ids_remain(self):
        first, created = self.store.upsert_job(self.posting(source_id="", source_url="https://EXAMPLE.org:443/jobs/123/?utm_source=mail&jobId=123#apply"))
        second, created_again = self.store.upsert_job(self.posting(source_id="", source_url="https://example.org/jobs/123?fbclid=tracking&jobId=123"))
        self.assertTrue(created)
        self.assertFalse(created_again)
        self.assertEqual(first, second)
        self.assertEqual(canonical_url("https://EXAMPLE.org:443/jobs/?z=2&utm_campaign=x&a=1"), "https://example.org/jobs?a=1&z=2")
        self.assertNotEqual(canonical_url("https://example.org/jobs?jobId=1"), canonical_url("https://example.org/jobs?jobId=2"))

    def test_refresh_preserves_workflow_notes_drafts_and_nonempty_metadata(self):
        job_id, _ = self.store.upsert_job(self.posting())
        first = self.store.get_job(job_id)
        self.store.update_status(job_id, "ready")
        self.store.update_notes(job_id, "Speak to Anna before applying.")
        draft_id = self.store.save_draft(job_id, "Dear hiring team,", "test-model", "profile-digest")
        _, created = self.store.upsert_job(self.posting(title="Senior Python Developer", company="", description="", status="new", notes=""))
        updated = self.store.get_job(job_id)
        self.assertFalse(created)
        self.assertEqual(updated["status"], "ready")
        self.assertEqual(updated["notes"], "Speak to Anna before applying.")
        self.assertEqual(updated["title"], "Senior Python Developer")
        self.assertEqual(updated["company"], "Example AS")
        self.assertEqual(updated["description"], "Build useful services.")
        self.assertEqual(updated["discovered_at"], first["discovered_at"])
        self.assertGreaterEqual(updated["last_seen_at"], first["last_seen_at"])
        self.assertEqual(self.store.list_drafts(job_id)[0]["id"], draft_id)

    def test_generic_careers_application_url_does_not_collapse_jobs(self):
        for number, url in enumerate(("https://company.example/careers", "https://company.example/en/jobs?country=Norway", "https://company.example/careers/apply")):
            with self.subTest(url=url):
                first, _ = self.store.upsert_job(self.posting(source_id=f"a{number}", source_url=f"https://board.example/jobs/a{number}", apply_url=url))
                second, created = self.store.upsert_job(self.posting(source_id=f"b{number}", source_url=f"https://board.example/jobs/b{number}", apply_url=url))
                self.assertTrue(created)
                self.assertNotEqual(first, second)
        self.assertEqual(len(self.store.list_jobs()), 6)

    def test_cross_source_merge_retains_old_identity_aliases(self):
        first, _ = self.store.upsert_job(self.posting())
        second, created = self.store.upsert_job(self.posting(source="board", source_id="board-987", source_url="https://board.example/vacancies/987"))
        self.assertFalse(created)
        self.assertEqual(first, second)
        # Old source's stable ID continues to work when its URL changes and it
        # temporarily omits the application link.
        third, created = self.store.upsert_job(self.posting(source_url="https://example.org/new/jobs/123", apply_url=""))
        self.assertFalse(created)
        self.assertEqual(first, third)
        self.assertEqual(len(self.store.list_jobs()), 1)

    def test_same_title_company_are_not_deduplication_keys(self):
        first, _ = self.store.upsert_job(self.posting(apply_url=""))
        second, created = self.store.upsert_job(self.posting(source_id="456", source_url="https://example.org/jobs/456", apply_url=""))
        self.assertTrue(created)
        self.assertNotEqual(first, second)

    def test_url_only_manual_refresh_preserves_provider_identity_pair(self):
        first, _ = self.store.upsert_job(self.posting(source="board", apply_url=""))
        refreshed, created = self.store.upsert_job(self.posting(source="manual", source_id="", apply_url="", title="Updated title"))
        self.assertFalse(created)
        self.assertEqual(first, refreshed)
        row = self.store.get_job(first)
        self.assertEqual((row["source"], row["source_id"]), ("board", "123"))
        self.assertEqual(row["title"], "Updated title")
        second, created = self.store.upsert_job(self.posting(source="manual", source_id="123", source_url="https://example.org/jobs/456", apply_url=""))
        self.assertTrue(created)
        self.assertNotEqual(first, second)
        # The original provider identity remains usable even when its URL changes.
        refreshed, created = self.store.upsert_job(self.posting(source="board", source_url="https://example.org/replacement/123", apply_url=""))
        self.assertFalse(created)
        self.assertEqual(first, refreshed)
        self.assertEqual(len(self.store.list_jobs()), 2)

    def test_id_without_source_does_not_inherit_existing_provider(self):
        first, _ = self.store.upsert_job(self.posting(apply_url=""))
        refreshed, created = self.store.upsert_job(self.posting(source="", source_id="456", apply_url=""))
        self.assertFalse(created)
        self.assertEqual(first, refreshed)
        row = self.store.get_job(first)
        self.assertEqual((row["source"], row["source_id"]), ("company", "123"))
        second, created = self.store.upsert_job(self.posting(source_id="456", source_url="https://example.org/jobs/456", apply_url=""))
        self.assertTrue(created)
        self.assertNotEqual(first, second)

    def test_application_link_matches_employer_detail_link(self):
        first, _ = self.store.upsert_job(self.posting())
        second, created = self.store.upsert_job(self.posting(source="ats", source_id="ats-123", source_url="https://ats.example.org/jobs/123", apply_url=""))
        self.assertFalse(created)
        self.assertEqual(first, second)

    def test_invalid_status_and_missing_job(self):
        job_id, _ = self.store.upsert_job(self.posting())
        with self.assertRaises(ValueError):
            self.store.update_status(job_id, "auto-applied")
        with self.assertRaises(ValueError):
            self.store.list_jobs(status="unknown")
        with self.assertRaises(KeyError):
            self.store.update_status(9000, "saved")
        with self.assertRaises(KeyError):
            self.store.save_draft(9000, "Draft")
        self.assertIsNone(self.store.get_job(9000))
        self.assertEqual(self.store.get_job(job_id)["status"], "new")

    def test_filtered_search_is_literal_and_parameterized(self):
        job_id, _ = self.store.upsert_job(self.posting(title="100% Python Developer"))
        self.store.update_status(job_id, "saved")
        self.assertEqual(len(self.store.list_jobs(status="saved", query="100%")), 1)
        self.assertEqual(self.store.list_jobs(status="new"), [])
        self.assertEqual(self.store.list_jobs(query="' OR 1=1 --"), [])
        self.assertEqual(self.store.list_jobs(query="_"), [])

    def test_persistence_and_initial_schema_version(self):
        job_id, _ = self.store.upsert_job(self.posting(raw_json={"remote": True}))
        with JobStore(self.path) as reopened:
            self.assertEqual(reopened.get_job(job_id)["raw_json"], '{"remote": true}')
        with closing(sqlite3.connect(self.path)) as connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 2)

    def test_conflicting_identity_update_is_atomic(self):
        first, _ = self.store.upsert_job(self.posting(apply_url=""))
        second, _ = self.store.upsert_job(self.posting(source_id="456", source_url="https://example.org/jobs/456", apply_url=""))
        with self.assertRaises(ValueError):
            self.store.upsert_job(self.posting(source_id="123", source_url="https://example.org/jobs/456", apply_url="", title="Conflicting title"))
        self.assertEqual(len(self.store.list_jobs()), 2)
        self.assertEqual(self.store.get_job(first)["title"], "Python Developer")
        self.assertEqual(self.store.get_job(second)["source_id"], "456")


if __name__ == "__main__":
    unittest.main()
