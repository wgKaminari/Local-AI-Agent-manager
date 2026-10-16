"""Recommendation regressions based on the reported 2.5-year search failure."""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from norway_job_agent import service
from norway_job_agent.web_api import create_app, close_resources


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.data = Path(self.temporary.name)
        self.app = create_app(self.data, "fixture-session")
        self.app.config.update(TESTING=True, LOCAL_ORIGIN="http://127.0.0.1:7654")
        self.client = self.app.test_client()
        self.profile = service.read_profile(self.data)
        self.profile.update(years_of_experience=2.5, target_roles=["Data Scientist", "Machine Learning Engineer"],
                            related_roles=["Data Engineer"], skills=["Python", "SQL"], preferred_locations=[])
        service.save_profile(self.data, self.profile)
        service.save_settings(self.data, {"sources": [], "model": "qwen3:4b", "active_country": "US"})

    def tearDown(self):
        close_resources(self.app)
        self.app.extensions["local_state"].worker.shutdown(wait=True)
        self.temporary.cleanup()

    def get(self, url):
        return self.client.get(url, base_url="http://127.0.0.1:7654", headers={"X-Session-Token": "fixture-session"})

    def job(self, title, description, slug):
        return service.import_vacancy(self.data, {"title": title, "company": "Fixture " + slug,
            "location": "New York, NY", "source": "manual", "source_url": "https://jobs.example.test/" + slug,
            "description": description})[0]

    def test_for_you_excludes_experience_conflicts_and_explains_counts(self):
        for title, description, slug in [
            ("Senior Machine Learning Engineer II, Ads Response Prediction", "Build Python and SQL models.", "senior"),
            ("Staff Data Scientist, Causal Inference & Experimentation", "Python SQL.", "staff"),
            ("Data Scientist", "Minimum 5 years of professional experience required. Python SQL.", "five-years"),
        ]:
            self.job(title, description, slug)
        suitable = self.job("Data Scientist", "At least 2 years of professional experience required. Python.", "two-years")
        unknown = self.job("Machine Learning Engineer", "Develop Python products. Check the original requirements.", "unspecified")
        data = self.get("/api/jobs?country=US&view=for-you").get_json()
        self.assertEqual({row["id"] for row in data["jobs"]}, {suitable, unknown})
        self.assertEqual(data["summary"]["role_matches"], 5)
        self.assertEqual(data["summary"]["experience_mismatches"], 3)
        self.assertEqual(data["summary"]["shown_employers"], 2)
        self.assertNotIn("description", data["jobs"][0])
        self.assertIn("description", self.get(f"/api/jobs/{suitable}").get_json()["job"])

    def test_explicit_all_levels_and_mismatch_filters_remain_available(self):
        senior = self.job("Senior Data Scientist", "Python SQL", "senior")
        self.job("Data Scientist", "2 years of experience required.", "two-years")
        self.assertEqual(len(self.get("/api/jobs?view=for-you&experience=all").get_json()["jobs"]), 2)
        rows = self.get("/api/jobs?view=for-you&experience=mismatch").get_json()["jobs"]
        self.assertEqual([row["id"] for row in rows], [senior])
        self.assertEqual(self.get("/api/jobs?experience=invalid").status_code, 400)

    def test_high_keyword_overlap_does_not_rank_senior_above_suitable_role(self):
        self.job("Staff Data Scientist", "Python SQL", "staff")
        suitable = self.job("Data Scientist", "Python. At least 2 years of experience.", "suitable")
        self.assertEqual(service.vacancies(self.data, country="US")[0]["id"], suitable)

    def test_saved_senior_role_remains_accessible_with_its_notes(self):
        senior = self.job("Senior Data Scientist", "Python SQL", "saved-senior")
        service.update_workflow(self.data, senior, "saved", "Keep this referral for review")
        rows = self.get("/api/jobs?view=saved").get_json()["jobs"]
        self.assertEqual(rows[0]["id"], senior)
        self.assertEqual(rows[0]["notes"], "Keep this referral for review")

    def test_coverage_distinguishes_directory_configured_and_checked_sources(self):
        service.save_settings(self.data, {"model": "qwen3:4b", "sources": [
            {"type": "nav"}, {"type": "ashby", "board": "fixture", "countries": ["US"]},
            {"type": "greenhouse", "board": "german-fixture", "countries": ["DE"]}]})
        service._write_json(self.data / "last-collection.json", {"country": "US", "started_at": "2026-10-09T10:00:00+00:00", "sources": [
            {"ok": False}, {"ok": True, "has_more": True}]})
        us = self.get("/api/discovery/coverage?country=US").get_json()
        self.assertEqual(us["configured_collectors"], 1)
        self.assertEqual((us["checked"], us["failed"], us["limited"]), (2, 1, 1))
        self.assertGreater(us["directory_employers"], us["available_collectors"])
        de = self.get("/api/discovery/coverage?country=DE").get_json()
        self.assertIsNone(de["last_run"])
        self.assertEqual(de["checked"], 0)

    def test_old_reports_are_not_presented_as_a_fresh_country_scan(self):
        service._write_json(self.data / "last-collection.json", {"created": 490, "sources": [{"ok": True}]})
        result = service.discovery_coverage(self.data, "US")
        self.assertTrue(result["legacy_report"])
        self.assertEqual(result["checked"], 0)
        self.assertIsNone(result["last_run"])

    def test_recommendations_offer_employer_variety_without_dropping_jobs(self):
        from norway_job_agent.web_api import _diverse_recommendations
        jobs = [{"id": number, "company": company, "match": {"track": track, "experience": {"status": "review"}}}
                for number, company, track in [(1, "A", "target"), (2, "A", "target"),
                    (3, "A", "target"), (4, "B", "target"), (5, "C", "target"), (6, "D", "horizon")]]
        result = _diverse_recommendations(jobs)
        self.assertEqual([row["id"] for row in result], [1, 2, 4, 5, 3, 6])

    def test_early_role_gate_preserves_full_search_results_counts_and_order(self):
        self.profile["excluded_keywords"] = ["unpaid"]
        self.profile["country_preferences"] = {"DE": {"target_roles": ["Research Engineer"]}}
        service.save_profile(self.data, self.profile)
        cases = [
            ("Data Scientist", "2 years of Python experience required.", "US", "manual", ""),
            ("Senior Data Scientist", "5 years of Python experience required.", "US", "manual", ""),
            ("Account Executive", "Python SQL", "US", "manual", ""),
            ("Data Engineer", "Python SQL", "US", "manual", ""),
            ("PhD Candidate", "Research in mathematics and Python", "US", "manual", ""),
            ("Data Scientist", "unpaid Python internship", "US", "manual", ""),
            ("Data Scientist", "Python", "US", "manual", "2000-01-01"),
            ("Account Executive", "SQL", "NO", "nav", "2000-01-01"),
            ("Data Scientist", "Python", "NO", "nav", "2000-01-01"),
            ("Account Executive", "SQL", "NO", "nav", "not-a-date"),
            ("Research Engineer", "Python", "DE", "manual", ""),
            ("Data Scientist", "Python", "DE", "manual", ""),
        ]
        for number, (title, description, country, source, deadline) in enumerate(cases):
            service.import_vacancy(self.data, {"title": title, "description": description,
                "countries": [country], "company": "Fixture " + str(number % 3), "source": source,
                "deadline": deadline, "source_url": f"https://jobs.example.test/parity/{number}"})
        full_search = service.vacancies

        def without_gate(*args, **kwargs):
            kwargs["recommendations_only"] = False
            return full_search(*args, **kwargs)

        for parameters in ("", "&country=US", "&country=DE", "&country=NO", "&query=Python",
                           "&experience=all", "&experience=mismatch", "&sort=company", "&sort=newest"):
            with self.subTest(parameters=parameters):
                url = "/api/jobs?view=for-you" + parameters
                with patch.object(service, "vacancies", side_effect=without_gate):
                    expected = self.get(url).get_json()
                actual = self.get(url).get_json()
                self.assertEqual(actual, expected)
        self.assertEqual(self.get("/api/jobs?view=for-you").get_json()["summary"]["in_scope"], 10)


if __name__ == "__main__":
    unittest.main()
