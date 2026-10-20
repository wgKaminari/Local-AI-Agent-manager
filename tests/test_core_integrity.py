"""Atomic updates and country switching with synthetic, offline data."""

import copy
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from norway_job_agent import service
from norway_job_agent.storage import JobStore
from support import nav_fetch, offline_collection


def setUpModule():
    offline_collection()


class CoreFixture(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="core-integrity-")
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name)
        service.initialize(self.folder)

    def job(self, identifier, **changes):
        return {"source": "fixture", "source_id": identifier,
                "source_url": f"https://example.org/jobs/{identifier}",
                "title": f"Data Scientist {identifier}", "company": "Fixture Company",
                "description": "Python tools", "location": "Berlin, Germany", **changes}


class CoreIntegrityTests(CoreFixture):
    def test_nav_identity_conflict_skips_only_that_advert_and_keeps_notes(self):
        first = service.import_vacancy(self.folder, self.job("first", source="nav"))[0]
        service.import_vacancy(self.folder, self.job("second", source="nav"))
        service.update_workflow(self.folder, first, "saved", "Reviewed notes")
        jobs = [self.job("first", source="nav", title="Refreshed title"),
                self.job("new", source="nav", title="Machine Learning Engineer"),
                self.job("first", source="nav", source_url=self.job("second")["source_url"])]
        settings = {"sources": [{"type": "nav"}, {"type": "greenhouse", "board": "fixture"}]}
        with patch("norway_job_agent.service._fetch_nav", return_value=nav_fetch(jobs)), \
                patch("norway_job_agent.sources.fetch_greenhouse", return_value=[self.job("successful", title="Statistician")]):
            report = service.collect(self.folder, settings)
        nav = report["sources"][0]
        self.assertTrue(nav["ok"])
        self.assertEqual((nav["created"], nav["updated"]), (1, 1))
        self.assertIn("1 adverts conflicted", nav["coverage"])
        self.assertEqual((report["created"], report["updated"], report["withdrawn"]), (2, 1, 0))
        self.assertEqual({row["source_id"] for row in service.vacancies(self.folder)}, {"first", "second", "new", "successful"})
        self.assertEqual(service.get_vacancy(self.folder, first)["title"], "Refreshed title")
        self.assertEqual(service.get_vacancy(self.folder, first)["notes"], "Reviewed notes")

    def test_nav_withdrawal_failure_rolls_back_new_jobs_and_prior_withdrawals(self):
        first = service.import_vacancy(self.folder, self.job("first", source="nav"))[0]
        service.import_vacancy(self.folder, self.job("second", source="nav"))
        original_withdraw = JobStore.withdraw_source_job

        def withdraw(store, source, identifier):
            if identifier == "second":
                raise ValueError("Fixture withdrawal failed")
            return original_withdraw(store, source, identifier)

        fetched = nav_fetch([self.job("discard", source="nav")], ["first", "second"], marks=[("v1", "[]", "discard")])
        with patch("norway_job_agent.service._fetch_nav", return_value=fetched), \
                patch.object(JobStore, "withdraw_source_job", withdraw), \
                patch("norway_job_agent.nav_index.mark_hydrated") as marked, \
                patch("norway_job_agent.nav_index.mark_withdrawals_applied") as applied:
            report = service.collect(self.folder)
        self.assertFalse(report["sources"][0]["ok"])
        self.assertEqual((report["created"], report["updated"], report["withdrawn"]), (0, 0, 0))
        # Nothing is recorded as checked, so the next refresh retries it all.
        marked.assert_not_called()
        applied.assert_not_called()
        self.assertEqual({row["source_id"] for row in service.vacancies(self.folder)}, {"first", "second"})
        self.assertTrue(service.get_vacancy(self.folder, first)["is_active"])
        self.assertEqual(service.get_vacancy(self.folder, first)["description"], "Python tools")

    def test_conflicting_source_rolls_back_its_batch_and_continues_other_sources(self):
        from norway_job_agent.cli import collect_sources
        with JobStore(self.folder / "vacancies.db") as store:
            first, _ = store.upsert_job(self.job("first"))
            store.upsert_job(self.job("second"))
            store.update_notes(first, "Keep me")
            jobs = [self.job("first", title="Uncommitted refresh"), self.job("uncommitted-new"),
                    self.job("first", source_url=self.job("second")["source_url"])]
            with patch("norway_job_agent.sources.fetch_greenhouse", side_effect=[jobs, [self.job("successful")]]):
                report = collect_sources({"sources": [
                    {"type": "greenhouse", "board": "conflicting"},
                    {"type": "greenhouse", "board": "successful"},
                ]}, store)
            self.assertEqual((report["created"], report["updated"]), (1, 0))
            self.assertFalse(report["sources"][0]["ok"])
            self.assertEqual((report["sources"][0]["created"], report["sources"][0]["updated"]), (0, 0))
            self.assertTrue(report["sources"][1]["ok"])
            self.assertEqual({row["source_id"] for row in store.list_jobs()}, {"first", "second", "successful"})
            self.assertEqual(store.get_job(first)["title"], "Data Scientist first")
            self.assertEqual(store.get_job(first)["notes"], "Keep me")

    def test_conflicting_email_batch_rolls_back_new_rows_and_prior_refreshes(self):
        first = service.import_vacancy(self.folder, self.job("first", location=""))[0]
        second = service.import_vacancy(self.folder, self.job("second"))[0]
        service.update_workflow(self.folder, first, "ready", "Reviewed notes")
        service.save_edited_letter(self.folder, first, "Reviewed letter")
        with JobStore(self.folder / "vacancies.db") as store:
            before = {row["id"]: row for row in store.list_jobs()}
        email = {"email": {"message_id": "fixture-message"}}
        candidates = [self.job("first", raw_json=email), self.job("new", raw_json=email),
                      self.job("first", source_url=self.job("second")["source_url"], raw_json=email)]
        with self.assertRaisesRegex(ValueError, "multiple saved jobs"):
            service.import_email_candidates(self.folder, candidates)
        with JobStore(self.folder / "vacancies.db") as store:
            self.assertEqual({row["id"]: row for row in store.list_jobs()}, before)
            self.assertEqual(store.provenance(first), [])
            self.assertEqual(store.provenance(second), [])
            self.assertEqual(store.list_drafts(first)[0]["content"], "Reviewed letter")
            self.assertNotIn("new", store.source_ids("fixture"))

    def test_provenance_write_failure_rolls_back_entire_import(self):
        with patch.object(JobStore, "record_provenance", side_effect=sqlite3.OperationalError("fixture disk error")):
            with self.assertRaises(sqlite3.OperationalError):
                service.import_email_candidates(self.folder, [self.job("new", raw_json={"email": {"message_id": "1"}})])
        self.assertEqual(service.vacancies(self.folder), [])

    def test_workflow_update_does_not_leave_status_changed_when_notes_fail(self):
        job_id = service.import_vacancy(self.folder, self.job("first"))[0]
        service.update_workflow(self.folder, job_id, "saved", "Original notes")
        with patch.object(JobStore, "update_notes", side_effect=sqlite3.OperationalError("fixture disk error")):
            with self.assertRaises(sqlite3.OperationalError):
                service.update_workflow(self.folder, job_id, "applied", "New notes")
        job = service.get_vacancy(self.folder, job_id)
        self.assertEqual((job["status"], job["notes"]), ("saved", "Original notes"))

    def test_invalid_notes_leave_workflow_intact(self):
        job_id = service.import_vacancy(self.folder, self.job("first"))[0]
        with self.assertRaisesRegex(ValueError, "notes must be text"):
            service.update_workflow(self.folder, job_id, "applied", None)
        self.assertEqual(service.get_vacancy(self.folder, job_id)["status"], "new")

    def test_nested_transaction_can_recover_then_commit_other_work(self):
        with JobStore(self.folder / "vacancies.db") as store:
            with store.transaction():
                first, _ = store.upsert_job(self.job("first"))
                try:
                    with store.transaction():
                        store.upsert_job(self.job("discard"))
                        raise ValueError("Abandon this group")
                except ValueError:
                    pass
                store.update_notes(first, "Kept")
                store.upsert_job(self.job("last"))
        self.assertEqual({job["source_id"] for job in service.vacancies(self.folder)}, {"first", "last"})
        self.assertEqual(service.get_vacancy(self.folder, first)["notes"], "Kept")

    def test_outer_transaction_rolls_back_workflow_letters_and_preparations(self):
        with JobStore(self.folder / "vacancies.db") as store:
            first, _ = store.upsert_job(self.job("first"))
            with self.assertRaisesRegex(RuntimeError, "cancel group"):
                with store.transaction():
                    store.update_status(first, "ready")
                    store.save_draft(first, "Not committed")
                    store.save_preparation(first, {"answers": []})
                    raise RuntimeError("cancel group")
            self.assertEqual(store.get_job(first)["status"], "new")
            self.assertEqual(store.list_drafts(first), [])
            self.assertEqual(store.preparations(first), [])

    def test_withdrawn_application_history_is_accessible_without_reopening_search(self):
        first = service.import_vacancy(self.folder, self.job("first"))[0]
        service.update_workflow(self.folder, first, "applied", "Interview follow-up")
        service.save_edited_letter(self.folder, first, "Sent cover letter")
        with JobStore(self.folder / "vacancies.db") as store:
            store.withdraw_source_job("fixture", "first")
        self.assertEqual(service.vacancies(self.folder), [])
        history = service.vacancies(self.folder, status="applied", country="DE", include_inactive=True)
        self.assertEqual([row["id"] for row in history], [first])
        self.assertEqual(history[0]["is_active"], 0)
        self.assertEqual(history[0]["description"], "")
        self.assertEqual(history[0]["notes"], "Interview follow-up")
        self.assertEqual(service.draft_history(self.folder, first)[0]["content"], "Sent cover letter")

    def test_expired_nav_history_stays_available_in_application_view(self):
        first = service.import_vacancy(self.folder, self.job("first", source="nav", deadline="2000-01-01"))[0]
        service.update_workflow(self.folder, first, "applied", "Historical application")
        self.assertEqual(service.vacancies(self.folder), [])
        self.assertEqual([row["id"] for row in service.vacancies(self.folder, include_inactive=True)], [first])


class CountryPresetTests(CoreFixture):
    def entry(self, identifier="shared", countries=None, **source_changes):
        return {"id": identifier, "name": identifier, "countries": countries or ["NO", "DE", "US"],
                "source": {"type": "greenhouse", "board": identifier, **source_changes}}

    def settings(self, sources):
        service.save_settings(self.folder, {"sources": sources, "model": "qwen3:4b", "active_country": "NO"})

    def test_country_activation_merges_board_scopes_without_touching_profile(self):
        before = (self.folder / "profile.json").read_bytes()
        with patch("norway_job_agent.company_catalog.company_catalog", return_value=[self.entry()]):
            norwegian = service.select_country(self.folder, "NO")
            german = service.select_country(self.folder, "DE")
        boards = [source for source in german["sources"] if source["type"] == "greenhouse"]
        self.assertEqual(len(boards), 1)
        self.assertEqual(boards[0]["countries"], ["NO", "DE"])
        self.assertNotIn("locations", boards[0])
        self.assertEqual(german["active_country"], "DE")
        self.assertEqual(norwegian["country_presets_added"], ["NO"])
        self.assertEqual(german["country_presets_added"], ["NO", "DE"])
        self.assertEqual((self.folder / "profile.json").read_bytes(), before)

    def test_country_switch_does_not_readd_an_intentionally_removed_preset(self):
        with patch("norway_job_agent.company_catalog.company_catalog", return_value=[self.entry()]):
            settings = service.select_country(self.folder, "DE")
            settings["sources"] = []
            service.save_settings(self.folder, settings)
            service.select_country(self.folder, "")
            selected = service.select_country(self.folder, "DE")
        self.assertEqual(selected["sources"], [])

    def test_country_limit_failure_preserves_entire_settings_file(self):
        self.settings([{"type": "greenhouse", "board": f"board-{number}"} for number in range(250)])
        before = (self.folder / "sources.json").read_bytes()
        with patch("norway_job_agent.company_catalog.company_catalog", return_value=[self.entry()]):
            with self.assertRaisesRegex(ValueError, "limit is 250"):
                service.select_country(self.folder, "DE")
        self.assertEqual((self.folder / "sources.json").read_bytes(), before)

    def test_existing_unrestricted_board_does_not_become_country_limited(self):
        original = {"type": "greenhouse", "board": "shared", "name": "My global board"}
        self.settings([original])
        with patch("norway_job_agent.company_catalog.company_catalog", return_value=[self.entry()]):
            selected = service.select_country(self.folder, "DE")
        self.assertEqual(selected["sources"], [original])

    def test_unscoped_user_location_filter_is_not_widened_or_mistaken_for_global(self):
        original = {"type": "greenhouse", "board": "shared", "locations": ["Oslo"]}
        self.settings([original])
        with patch("norway_job_agent.company_catalog.company_catalog", return_value=[self.entry()]):
            selected = service.select_country(self.folder, "DE")
            repeated = service.add_catalog_sources(self.folder, ["shared"], "DE")
        self.assertEqual(selected["sources"][0], original)
        self.assertEqual(selected["sources"][1]["countries"], ["DE"])
        self.assertEqual(repeated["sources"], selected["sources"])

    def test_same_board_with_distinct_collection_settings_remains_independent(self):
        original = {"type": "greenhouse", "board": "shared", "max_jobs": 2, "countries": ["NO"]}
        self.settings([original])
        with patch("norway_job_agent.company_catalog.company_catalog", return_value=[self.entry(max_jobs=60)]):
            selected = service.select_country(self.folder, "DE")
        self.assertEqual(selected["sources"][0], original)
        self.assertEqual(len(selected["sources"]), 2)

    def test_explicit_add_deduplicates_ids_and_does_not_change_active_country(self):
        self.settings([])
        with patch("norway_job_agent.company_catalog.company_catalog", return_value=[self.entry()]):
            selected = service.add_catalog_sources(self.folder, ["shared", "shared"], "DE")
        self.assertEqual(selected["active_country"], "NO")
        self.assertEqual(len(selected["sources"]), 1)
        self.assertEqual(selected["sources"][0]["countries"], ["DE"])
        self.assertEqual(selected["country_presets_added"], [])

    def test_invalid_selection_never_changes_saved_settings(self):
        before = (self.folder / "sources.json").read_bytes()
        with patch("norway_job_agent.company_catalog.company_catalog", return_value=[self.entry()]):
            for country in ("FR", None, ["DE"]):
                with self.subTest(country=country), self.assertRaises(ValueError):
                    service.select_country(self.folder, country)
            for ids in (["missing"], "shared", [None]):
                with self.subTest(ids=ids), self.assertRaises(ValueError):
                    service.add_catalog_sources(self.folder, ids, "DE")
        self.assertEqual((self.folder / "sources.json").read_bytes(), before)

    def test_all_four_real_catalog_presets_fit_and_remain_unique(self):
        profile_before = (self.folder / "profile.json").read_bytes()
        for country in ("NO", "US", "DE", "UA"):
            result = service.select_country(self.folder, country)
        self.assertLessEqual(len(result["sources"]), 250)
        self.assertEqual(set(result["country_presets_added"]), {"NO", "US", "DE", "UA"})
        self.assertEqual(len([source for source in result["sources"] if source["type"] == "nav"]), 1)
        before = copy.deepcopy(result)
        self.assertEqual(service.select_country(self.folder, "UA"), before)
        self.assertEqual((self.folder / "profile.json").read_bytes(), profile_before)


if __name__ == "__main__":
    unittest.main()
