"""Regressions for faster listing/collection and tolerance of damaged local files."""
import sqlite3
import sys
from contextlib import closing
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from norway_job_agent import service
from norway_job_agent.experience_matching import experience_requirements
from norway_job_agent.storage import JobStore
from norway_job_agent.web_api import close_resources, create_app


class ListingTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.data = Path(self.temporary.name)
        self.app = create_app(self.data, "fixture-session")
        self.app.config.update(TESTING=True, LOCAL_ORIGIN="http://127.0.0.1:7654")
        self.client = self.app.test_client()
        profile = service.read_profile(self.data)
        profile.update(years_of_experience=2.5, target_roles=["Data Scientist"], skills=["Python"])
        service.save_profile(self.data, profile)

    def tearDown(self):
        close_resources(self.app)
        self.app.extensions["local_state"].worker.shutdown(wait=True)
        self.temporary.cleanup()

    def get(self, url):
        return self.client.get(url, base_url="http://127.0.0.1:7654", headers={"X-Session-Token": "fixture-session"})

    def job(self, slug, status="new", **values):
        identifier = service.import_vacancy(self.data, {
            "title": "Data Scientist", "company": "Fixture " + slug, "location": "Oslo, Norway",
            "source": "manual", "source_url": "https://jobs.example.test/" + slug,
            "description": "At least 2 years of experience required. Python.", **values})[0]
        if status != "new":
            service.update_workflow(self.data, identifier, status, "")
        return identifier

    def test_list_rows_carry_only_card_match_fields(self):
        identifier = self.job("one")
        row = self.get("/api/jobs").get_json()["jobs"][0]
        self.assertEqual(set(row["match"]), {"score", "track", "expired", "excluded_terms", "experience"})
        self.assertEqual(row["match"]["experience"], {"status": "no_conflict", "candidate_years": 2.5, "minimum_years": 2.0})
        detail = self.get(f"/api/jobs/{identifier}").get_json()["job"]["match"]
        self.assertIn("warnings", detail)
        self.assertTrue(detail["experience"]["requirements"])

    def test_stage_views_match_only_their_stages_and_keep_scope_counts(self):
        saved = self.job("saved", "saved")
        applied = self.job("applied", "applied")
        self.job("plain-one")
        self.job("plain-two")
        from norway_job_agent import profile as profile_module
        with patch.object(profile_module, "match_job", wraps=profile_module.match_job) as matcher, \
                patch("norway_job_agent.service.match_job", new=matcher):
            data = self.get("/api/jobs?view=saved").get_json()
        self.assertEqual([row["id"] for row in data["jobs"]], [saved])
        self.assertEqual(matcher.call_count, 1)
        self.assertEqual(data["summary"]["in_scope"], 4)
        self.assertEqual(data["summary"]["employers"], 4)
        data = self.get("/api/jobs?view=applications").get_json()
        self.assertEqual([row["id"] for row in data["jobs"]], [applied])
        self.assertEqual(data["summary"]["in_scope"], 4)

    def test_damaged_collection_report_does_not_block_opening(self):
        (self.data / "last-collection.json").write_text("{not json", encoding="utf-8")
        response = self.get("/api/bootstrap")
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.get_json()["collection"])
        self.assertIsNone(self.get("/api/discovery/coverage").get_json()["last_run"])


class RequirementCacheTests(unittest.TestCase):
    def test_cached_results_are_independent_copies(self):
        text = "Minimum 3 years of professional experience required."
        first = experience_requirements(text)
        first[0]["years"] = 99
        first.append({"years": 1})
        second = experience_requirements(text)
        self.assertEqual(second, [{"years": 3.0, "evidence": text.rstrip("."), "kind": "required"}])
        self.assertEqual(experience_requirements(text + " Extra."), second)
        self.assertEqual(experience_requirements(""), [])


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "vacancies.db"

    def test_new_databases_use_wal_and_reopen_without_writing(self):
        with JobStore(self.path):
            pass
        with closing(sqlite3.connect(self.path, isolation_level=None)) as connection:
            self.assertEqual(connection.execute("PRAGMA journal_mode").fetchone()[0], "wal")
        # A reopen with a complete schema must not run migration DDL.
        statements = []
        original = sqlite3.connect

        def tracing_connect(*args, **kwargs):
            connection = original(*args, **kwargs)
            connection.set_trace_callback(statements.append)
            return connection

        with patch("norway_job_agent.storage.sqlite3.connect", side_effect=tracing_connect):
            with JobStore(self.path):
                pass
        self.assertTrue(statements)
        self.assertFalse([sql for sql in statements if sql.lstrip().upper().startswith(("CREATE", "ALTER", "PRAGMA USER_VERSION ="))])

    def test_version_three_database_missing_a_table_is_still_migrated(self):
        with JobStore(self.path):
            pass
        with closing(sqlite3.connect(self.path, isolation_level=None)) as connection:
            connection.execute("DROP TABLE application_deliveries")
        with JobStore(self.path) as store:
            store.upsert_job({"title": "Data Scientist", "company": "Fixture", "source": "manual",
                              "source_url": "https://jobs.example.test/one"})
            self.assertEqual(store.deliveries(1), [])

    def test_rollback_journal_database_is_converted_once(self):
        with closing(sqlite3.connect(self.path, isolation_level=None)) as connection:
            connection.execute("PRAGMA journal_mode = DELETE")
        with JobStore(self.path):
            pass
        with JobStore(self.path):
            pass
        with closing(sqlite3.connect(self.path, isolation_level=None)) as connection:
            self.assertEqual(connection.execute("PRAGMA journal_mode").fetchone()[0], "wal")


class ParallelCollectionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.folder = Path(self.temporary.name)
        service.initialize(self.folder)

    def test_downloads_overlap_but_writes_follow_configured_order(self):
        boards = [f"board-{index}" for index in range(6)]
        delays = {board: 0.05 * (6 - index) for index, board in enumerate(boards)}
        active, peak, lock = [0], [0], threading.Lock()
        writes = []

        def fetch(board):
            with lock:
                active[0] += 1
                peak[0] = max(peak[0], active[0])
            time.sleep(delays[board])
            with lock:
                active[0] -= 1
            return [{"title": "Data Scientist", "company": board, "source": "greenhouse",
                     "source_id": f"{board}:1", "source_url": f"https://jobs.example.test/{board}/1"}]

        from norway_job_agent import collection
        original_store = collection.store_fetched_source

        def recording_store(batch, store, report):
            writes.append(batch["result"]["source"]["board"])
            return original_store(batch, store, report)

        settings = {"sources": [{"type": "greenhouse", "board": board} for board in boards], "model": "qwen3:4b"}
        with patch("norway_job_agent.sources.fetch_greenhouse", side_effect=fetch), \
                patch.object(collection, "store_fetched_source", side_effect=recording_store):
            started = time.monotonic()
            report = service.collect(self.folder, settings)
            elapsed = time.monotonic() - started
        self.assertEqual(writes, boards)
        self.assertEqual([row["source"]["board"] for row in report["sources"]], boards)
        self.assertEqual(report["created"], 6)
        self.assertGreater(peak[0], 1)
        self.assertLess(elapsed, sum(delays.values()))

    def test_one_failing_download_does_not_stop_other_sources(self):
        from norway_job_agent.sources import SourceError

        def fetch(board):
            if board == "broken":
                raise SourceError("The source returned HTTP 500; check the URL or board ID.")
            return [{"title": "Data Scientist", "company": board, "source": "greenhouse",
                     "source_id": f"{board}:1", "source_url": f"https://jobs.example.test/{board}/1"}]

        settings = {"sources": [{"type": "greenhouse", "board": "broken"}, {"type": "greenhouse", "board": "fine"}]}
        with patch("norway_job_agent.sources.fetch_greenhouse", side_effect=fetch):
            report = service.collect(self.folder, settings)
        self.assertEqual([row["ok"] for row in report["sources"]], [False, True])
        self.assertIn("HTTP 500", report["sources"][0]["error"])
        self.assertEqual(report["created"], 1)

    def test_lever_jobs_use_the_configured_employer_name(self):
        from norway_job_agent import sources
        posting = [{"id": "abc", "text": "Data Scientist", "hostedUrl": "https://jobs.lever.co/dnb/abc",
                    "categories": {"location": "Oslo"}}]
        with patch.object(sources, "_json", return_value=posting):
            self.assertEqual(sources.fetch_lever("dnb", name="Dun & Bradstreet")[0]["company"], "Dun & Bradstreet")
            self.assertEqual(sources.fetch_lever("dnb")[0]["company"], "dnb")


class TransientRetryTests(unittest.TestCase):
    def setUp(self):
        from norway_job_agent import sources
        self.sources = sources
        patcher = patch.object(sources, "RETRY_PAUSE_SECONDS", 0)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_get_retries_one_dropped_connection(self):
        dropped = self.sources._TransientSourceError("Could not download from example.com; check connectivity or try again later.")
        with patch.object(self.sources, "_request_once", side_effect=[dropped, (200, {}, b"page")]) as request:
            self.assertEqual(self.sources._fetch("https://example.com/job")[2], b"page")
        self.assertEqual(request.call_count, 2)

    def test_repeated_failures_status_codes_and_posts_are_not_retried(self):
        dropped = self.sources._TransientSourceError("Could not download from example.com; check connectivity or try again later.")
        with patch.object(self.sources, "_request_once", side_effect=[dropped, dropped, (200, {}, b"")]) as request,                 self.assertRaisesRegex(self.sources.SourceError, "Could not download"):
            self.sources._fetch("https://example.com/job")
        self.assertEqual(request.call_count, 2)
        with patch.object(self.sources, "_request_once", return_value=(503, {}, b"")) as request,                 self.assertRaisesRegex(self.sources.SourceError, "HTTP 503"):
            self.sources._fetch("https://example.com/job")
        self.assertEqual(request.call_count, 1)
        with patch.object(self.sources, "_request_once", side_effect=[dropped, (200, {}, b"")]) as request,                 self.assertRaises(self.sources.SourceError):
            self.sources._fetch("https://example.com/search", method="POST", body=b"{}")
        self.assertEqual(request.call_count, 1)


if __name__ == "__main__":
    unittest.main()
