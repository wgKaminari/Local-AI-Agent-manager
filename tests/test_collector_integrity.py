"""Offline regressions for coverage, geography and complete ATS snapshots."""

import io
import json
import unittest
from unittest.mock import Mock, patch

from norway_job_agent import company_sources, sources
from norway_job_agent.cli import collect_sources
from norway_job_agent.countries import COUNTRY_TERMS, job_countries
from norway_job_agent.storage import JobStore


def posting(identifier="1", source="greenhouse", board="fixture", **changes):
    return {"title": "Data Scientist", "source": source,
            "source_id": f"{board}:{identifier}", "company": "Fixture",
            "source_url": f"https://example.org/jobs/{board}/{identifier}",
            "location": "Boston, MA", "description": "Python analytics", **changes}


def ashby_job(**changes):
    return {"title": "Data Scientist", "jobUrl": "https://jobs.ashbyhq.com/fixture/123",
            "applyUrl": "https://jobs.ashbyhq.com/fixture/123/application",
            "location": "Boston", "address": {"postalAddress": {"addressCountry": "US"}},
            "descriptionHtml": "<p>Build Python tools.</p>", **changes}


class CompleteFeedTests(unittest.TestCase):
    def test_ashby_supports_secondary_countries_and_omits_unlisted_roles(self):
        visible = ashby_job(isRemote=True, secondaryLocations=[{
            "location": "Berlin", "address": {"postalAddress": {"addressCountry": "DE"}}}])
        with patch.object(sources, "_json", return_value={"jobs": [visible, ashby_job(isListed=False)]}) as fetch:
            jobs = sources.fetch_ashby("fixture", name="Fixture Company")
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["company"], "Fixture Company")
        self.assertEqual(jobs[0]["description"], "Build Python tools.")
        self.assertEqual(jobs[0]["source_id"], "fixture:123")
        self.assertEqual(job_countries(jobs[0]), ["US", "DE"])
        self.assertTrue(jobs[0]["location"].startswith("Remote"))
        self.assertEqual(fetch.call_args.kwargs["max_response_bytes"], sources.MAX_ATS_RESPONSE_BYTES)

    def test_ashby_malformed_public_posting_fails_the_whole_feed(self):
        for invalid in (None, ashby_job(title=""), ashby_job(jobUrl="javascript:bad"),
                        ashby_job(address=[]), ashby_job(secondaryLocations="Berlin"),
                        ashby_job(secondaryLocations=[{"address": "bad"}])):
            if isinstance(invalid, dict) and invalid["jobUrl"].startswith("https:"):
                invalid["jobUrl"] = "https://jobs.ashbyhq.com/fixture/invalid"
            with self.subTest(invalid=invalid), patch.object(sources, "_json", return_value={"jobs": [ashby_job(), invalid]}):
                with self.assertRaises(sources.SourceError):
                    sources.fetch_ashby("fixture")

    def test_greenhouse_rejects_incomplete_snapshot_and_duplicate_identifiers(self):
        job = {"id": 1, "title": "Data Scientist"}
        for payload in ({"jobs": [job], "meta": {"total": 2}},
                        {"jobs": [job, {**job, "id": "1"}]}):
            with self.subTest(payload=payload), patch.object(sources, "_json", side_effect=[{"name": "Fixture"}, payload]):
                with self.assertRaises(sources.SourceError):
                    sources.fetch_greenhouse("fixture")

    def test_larger_ats_budget_accepts_large_feed_but_remains_bounded(self):
        large = b"x" * (sources.MAX_RESPONSE_BYTES + 100)
        response = Mock(status=200)
        response.getheaders.return_value = [("Content-Length", str(len(large)))]
        response.read1.side_effect = io.BytesIO(large).read
        connection = Mock()
        connection.getresponse.return_value = response
        with patch.object(sources, "_public_addresses", return_value=["93.184.216.34"]), \
                patch.object(sources.http.client, "HTTPSConnection", return_value=connection):
            result = sources._request_once("https://example.org/feed", max_response_bytes=sources.MAX_ATS_RESPONSE_BYTES)
            self.assertEqual(result[2], large)
            response.getheaders.return_value = [("Content-Length", str(sources.MAX_ATS_RESPONSE_BYTES + 1))]
            with self.assertRaisesRegex(sources.SourceError, "48 MB"):
                sources._request_once("https://example.org/feed", max_response_bytes=sources.MAX_ATS_RESPONSE_BYTES)
        with patch.object(sources, "_public_addresses") as resolve:
            for budget in (0, True, sources.MAX_ATS_RESPONSE_BYTES + 1):
                with self.assertRaises(sources.SourceError):
                    sources._request_once("https://example.org/feed", max_response_bytes=budget)
            resolve.assert_not_called()


class CountryEvidenceTests(unittest.TestCase):
    def test_foreign_country_metadata_prevents_city_collision(self):
        with patch.object(sources, "_json", return_value={"jobs": [ashby_job(
                location="Berlin", address={"postalAddress": {"addressCountry": "Canada"}})]}):
            job = sources.fetch_ashby("fixture")[0]
        self.assertEqual(job_countries(job), [])
        with JobStore(":memory:") as store:
            identifier, _ = store.upsert_job(job)
            self.assertEqual(job_countries(store.get_job(identifier)), [])

    def test_mixed_structured_and_unstructured_locations_are_preserved(self):
        with patch.object(sources, "_json", return_value={"jobs": [ashby_job(
                location="Berlin", address={"postalAddress": {"addressCountry": "Canada"}},
                secondaryLocations=[{"location": "Munich, Germany"}, {"location": "Boston, MA"}])]}):
            job = sources.fetch_ashby("fixture")[0]
        self.assertEqual(job_countries(job), ["DE", "US"])

    def test_unknown_secondary_location_does_not_reinterpret_foreign_city(self):
        with patch.object(sources, "_json", return_value={"jobs": [ashby_job(
                location="Berlin", address={"postalAddress": {"addressCountry": "Canada"}},
                secondaryLocations=[{"location": "Remote"}])]}):
            job = sources.fetch_ashby("fixture")[0]
        self.assertEqual(job_countries(job), [])
        with JobStore(":memory:") as store:
            identifier, _ = store.upsert_job(job)
            self.assertEqual(job_countries(store.get_job(identifier)), [])

    def test_jsonld_retains_foreign_country_and_remote_applicant_scope(self):
        cases = [({"jobLocation": {"address": {"addressLocality": "Berlin", "addressCountry": "Canada"}}}, []),
                 ({"jobLocationType": "TELECOMMUTE", "applicantLocationRequirements": {"@type": "Country", "name": "USA"}}, ["US"])]
        for changes, expected in cases:
            payload = {"@type": "JobPosting", "title": "Data Scientist", **changes}
            page = ('<script type="application/ld+json">' + json.dumps(payload) + '</script>').encode()
            with self.subTest(changes=changes), patch.object(sources, "_fetch", return_value=(200, {}, page, "https://example.org/job/1")):
                self.assertEqual(job_countries(sources.fetch_job_url("https://example.org/job/1")[0]), expected)

    def test_refresh_does_not_reuse_previous_structured_country(self):
        with JobStore(":memory:") as store:
            old = sources._set_location_countries(posting(location="Boston"), [("Boston", "US")])
            identifier, _ = store.upsert_job(old)
            new = sources._set_location_countries(posting(location="Remote"), [("Remote", None)])
            store.upsert_job(new)
            self.assertEqual(job_countries(store.get_job(identifier)), [])

    def test_legacy_generated_city_filters_are_retired_but_custom_filters_remain(self):
        source = {"countries": ["US"], "locations": COUNTRY_TERMS["US"]}
        self.assertEqual(sources.source_location_terms(source), [])
        # A city missing from the former short allowlist must reach collection.
        self.assertIsNone(sources.geographic_rejection({"location": "Raleigh, NC"}, source))
        source["locations"] = ["Boston"]
        self.assertEqual(sources.geographic_rejection({"location": "Raleigh, NC"}, source), "filtered_location")

    def test_country_collisions_and_unknown_locations_remain_conservative(self):
        cases = {"Berlin, NH - Remote": ["US"], "Berlin, Canada": [],
                 "Berlin, Germany; Boston, MA": ["DE", "US"], "Remote": [],
                 "Kyiv, Ukraine": ["UA"], "Oslo, Norway": ["NO"]}
        for location, expected in cases.items():
            with self.subTest(location=location):
                self.assertEqual(job_countries({"location": location}), expected)


class SnapshotReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.store = JobStore(":memory:")
        self.addCleanup(self.store.close)

    def collect(self, jobs, **source_changes):
        with patch.object(sources, "fetch_greenhouse", return_value=jobs):
            return collect_sources({"sources": [{"type": "greenhouse", "board": "fixture", **source_changes}]}, self.store)

    def test_complete_snapshot_retires_missing_only_and_preserves_personal_work(self):
        missing, _ = self.store.upsert_job(posting("missing"))
        present, _ = self.store.upsert_job(posting("present"))
        other, _ = self.store.upsert_job(posting("other", board="other"))
        self.store.update_status(missing, "applied")
        self.store.update_notes(missing, "Follow up tomorrow")
        self.store.save_draft(missing, "Reviewed cover letter")
        self.store.save_preparation(missing, {"answers": ["Reviewed answer"]})
        report = self.collect([posting("present")])
        self.assertEqual(report["withdrawn"], 1)
        saved = self.store.get_job(missing)
        self.assertEqual((saved["is_active"], saved["status"], saved["notes"]), (0, "applied", "Follow up tomorrow"))
        self.assertEqual(saved["description"], "Python analytics")
        self.assertEqual(self.store.list_drafts(missing)[0]["content"], "Reviewed cover letter")
        self.assertEqual(self.store.preparations(missing)[0]["payload"]["answers"], ["Reviewed answer"])
        self.assertTrue(self.store.get_job(present)["is_active"])
        self.assertTrue(self.store.get_job(other)["is_active"])

    def test_full_identity_set_precedes_country_filter_and_reopens_same_record(self):
        german, _ = self.store.upsert_job(posting("de", location="Berlin, Germany"))
        report = self.collect([posting("us"), posting("de", location="Berlin, Germany"), posting("unknown", location="Remote")], countries=["US"])
        self.assertEqual(report["withdrawn"], 0)
        self.assertTrue(self.store.get_job(german)["is_active"])
        row = report["sources"][0]
        self.assertEqual((row["fetched"], row["matched"], row["filtered_country"], row["unknown_country"]), (3, 1, 1, 1))
        self.collect([])
        report = self.collect([posting("de", location="Berlin, Germany")])
        self.assertEqual((report["created"], report["updated"]), (0, 1))
        self.assertTrue(self.store.get_job(german)["is_active"])

    def test_failed_or_malformed_feed_never_retires_previous_results(self):
        identifier, _ = self.store.upsert_job(posting())
        with patch.object(sources, "fetch_greenhouse", side_effect=sources.SourceError("incomplete")):
            report = collect_sources({"sources": [{"type": "greenhouse", "board": "fixture"}]}, self.store)
        self.assertFalse(report["sources"][0]["ok"])
        self.assertTrue(self.store.get_job(identifier)["is_active"])
        report = self.collect([posting("new"), posting("bad", title="")])
        self.assertFalse(report["sources"][0]["ok"])
        self.assertEqual(report["created"], 0)
        self.assertTrue(self.store.get_job(identifier)["is_active"])
        self.assertEqual(len(self.store.list_jobs()), 1)

    def test_partial_company_scan_never_retires_old_jobs(self):
        identifier, _ = self.store.upsert_job(posting(source="workday"))
        with patch.object(company_sources, "fetch_company_source", return_value={"jobs": [], "has_more": True, "coverage": "Partial"}):
            report = collect_sources({"sources": [{"type": "workday"}]}, self.store)
        self.assertTrue(report["sources"][0]["ok"])
        self.assertEqual(report["withdrawn"], 0)
        self.assertTrue(self.store.get_job(identifier)["is_active"])

    def test_snapshot_does_not_retire_historical_alias_owned_by_another_provider(self):
        identifier, _ = self.store.upsert_job(posting())
        other = posting(source="lever", board="global:other")
        other["source_url"] = posting()["source_url"]
        updated, _ = self.store.upsert_job(other)
        self.assertEqual(updated, identifier)
        self.assertEqual(self.collect([])["withdrawn"], 0)
        self.assertTrue(self.store.get_job(identifier)["is_active"])

    def test_snapshot_validation_rolls_back_batch_before_any_retirement(self):
        identifier, _ = self.store.upsert_job(posting())
        report = self.collect([posting("bad", board="different")])
        self.assertFalse(report["sources"][0]["ok"])
        self.assertEqual(report["created"], 0)
        self.assertEqual(len(self.store.list_jobs()), 1)
        self.assertTrue(self.store.get_job(identifier)["is_active"])

    def test_reconciliation_failure_rolls_back_updates_and_withdrawals_together(self):
        identifier, _ = self.store.upsert_job(posting("old"))
        original = JobStore.reconcile_source_snapshot

        def fail_after_withdrawal(store, *args):
            original(store, *args)
            raise ValueError("Fixture failure after reconciliation")

        with patch.object(JobStore, "reconcile_source_snapshot", fail_after_withdrawal):
            report = self.collect([posting("new")])
        self.assertFalse(report["sources"][0]["ok"])
        self.assertEqual((report["created"], report["updated"], report["withdrawn"]), (0, 0, 0))
        self.assertTrue(self.store.get_job(identifier)["is_active"])
        self.assertEqual(len(self.store.list_jobs()), 1)

    def test_complete_ashby_and_lever_boards_reconcile_only_their_scope(self):
        for source, board, method, extra in (("ashby", "fixture", "fetch_ashby", {}),
                                             ("lever", "eu:fixture", "fetch_lever", {"region": "eu"})):
            with self.subTest(source=source):
                identifier, _ = self.store.upsert_job(posting(source, source=source, board=board))
                other, _ = self.store.upsert_job(posting(source, source=source, board="other"))
                with patch.object(sources, method, return_value=[]):
                    report = collect_sources({"sources": [{"type": source, "board": "fixture", **extra}]}, self.store)
                self.assertEqual(report["withdrawn"], 1)
                self.assertFalse(self.store.get_job(identifier)["is_active"])
                self.assertTrue(self.store.get_job(other)["is_active"])


class GeographicBudgetTests(unittest.TestCase):
    def setUp(self):
        delay = patch.object(company_sources.time, "sleep")
        delay.start()
        self.addCleanup(delay.stop)

    def test_workday_scans_past_wrong_country_before_result_cap(self):
        source = {"type": "workday", "url": "https://fixture.wd3.myworkdayjobs.com/External",
                  "countries": ["US"], "max_jobs": 1}
        payloads = [{"jobPostings": [{"externalPath": "/job/1"}, {"externalPath": "/job/2"}], "total": 2},
                    {"jobPostingInfo": {"title": "Data Scientist", "location": "Berlin", "country": {"descriptor": "Germany"}}},
                    {"jobPostingInfo": {"title": "Data Scientist", "location": "Raleigh", "country": {"descriptor": "United States"}}}]
        with patch.object(company_sources, "_json", side_effect=payloads):
            report = company_sources.fetch_company_source(source)
        self.assertEqual(len(report["jobs"]), 1)
        self.assertEqual(job_countries(report["jobs"][0]), ["US"])
        self.assertEqual((report["scanned"], report["filtered_country"]), (2, 1))
        self.assertFalse(report["has_more"])

    def test_workday_scan_budget_advertises_unexamined_results(self):
        source = {"type": "workday", "url": "https://fixture.wd3.myworkdayjobs.com/External",
                  "countries": ["US"], "max_jobs": 1, "max_scan_jobs": 1}
        with patch.object(company_sources, "_json", side_effect=[
                {"jobPostings": [{"externalPath": "/job/1"}], "total": 10},
                {"jobPostingInfo": {"title": "Data Scientist", "location": "Berlin, Germany"}}]):
            report = company_sources.fetch_company_source(source)
        self.assertEqual(report["jobs"], [])
        self.assertEqual((report["scanned"], report["available"]), (1, 10))
        self.assertTrue(report["has_more"])
        self.assertIn("partial", report["coverage"])

    def test_smartrecruiters_retains_later_custom_location_match(self):
        source = {"type": "smartrecruiters", "board": "Fixture", "country": "us", "max_jobs": 1, "locations": ["Raleigh"]}
        detail = {"name": "Data Scientist", "location": {"city": "Boston", "country": "us"}}
        with patch.object(company_sources, "_json", side_effect=[
                {"content": [{"id": "1"}, {"id": "2"}], "totalFound": 2}, detail,
                {**detail, "location": {"city": "Raleigh", "country": "us"}}]):
            report = company_sources.fetch_company_source(source)
        self.assertEqual(report["jobs"][0]["source_id"], "Fixture:2")
        self.assertEqual((report["scanned"], report["filtered_location"]), (2, 1))
        self.assertFalse(report["has_more"])

    def test_career_page_filters_before_result_cap_and_reports_failed_pages(self):
        source = {"type": "career_page", "url": "https://example.org/", "job_path": "/jobs/", "countries": ["US"], "max_jobs": 1}
        page = b'<a href="/jobs/1">1</a><a href="/jobs/2">2</a><a href="/jobs/3">3</a>'
        with patch.object(company_sources, "_fetch", return_value=(200, {}, page, source["url"])), \
                patch.object(company_sources, "fetch_job_url", side_effect=[
                    [posting("de", location="Berlin, Germany")], sources.SourceError("HTTP 410"), [posting("us")]]):
            report = company_sources.fetch_company_source(source)
        self.assertEqual(report["jobs"][0]["source_id"], "fixture:us")
        self.assertEqual((report["scanned"], report["filtered_country"]), (3, 1))
        self.assertTrue(report["has_more"])
        self.assertIn("1 pages could not be read", report["coverage"])


if __name__ == "__main__":
    unittest.main()
