"""List filters with several choices, new sort orders, quick marks."""

import json
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from norway_job_agent import service
from norway_job_agent.storage import JobStore
from norway_job_agent.web_api import create_app
from support import offline_collection


def setUpModule():
    offline_collection()


def days_ago(n):
    return (date.today() - timedelta(days=n)).isoformat()


class Fixture(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.data = Path(temporary.name)
        service.initialize(self.data)
        self.ids = {}
        rows = [  # key, title, location, posted days ago, deadline in days, stage
            ("a", "Python Developer A", "Oslo, Norway", 1, 20, "applied"),
            ("b", "Python Developer B", "Remote (Europe)", 5, 3, "rejected"),
            ("c", "Python Developer C", "Berlin, Germany", 20, None, "interview"),
            ("d", "Python Developer D", "Kyiv, Ukraine", 60, 40, "new"),
        ]
        for key, title, location, posted, deadline, stage in rows:
            identifier = service.import_vacancy(self.data, {
                "title": title, "company": f"Fixture {key}", "location": location, "countries": ["NO"],
                "description": "Python", "source": "manual", "source_url": f"https://jobs.example.test/{key}",
                "published_at": days_ago(posted), **({"deadline": (date.today() + timedelta(days=deadline)).isoformat()} if deadline else {})})[0]
            self.ids[key] = identifier
            if stage != "new":
                service.update_workflow(self.data, identifier, stage, "")
        service.list_page(self.data)  # stores matches
        # Fixed scores and experience results, as matching would store them.
        with JobStore(self.data / "vacancies.db") as store:
            for key, score, mismatch, experience in (("a", 90, 0, "no_conflict"), ("b", 60, 0, "stretch"),
                                                      ("c", 75, 1, "mismatch"), ("d", 40, 0, "review")):
                match = json.loads(store._connection.execute("SELECT match_json FROM job_matches WHERE job_id = ?", (self.ids[key],)).fetchone()[0])
                match["experience"] = {**(match.get("experience") or {}), "status": experience}
                store._connection.execute("UPDATE job_matches SET score = ?, mismatch = ?, match_json = ? WHERE job_id = ?",
                                          (score, mismatch, json.dumps(match), self.ids[key]))
            store._connection.commit()

    def keys(self, **options):
        page = service.list_page(self.data, **options)
        names = {identifier: key for key, identifier in self.ids.items()}
        return [names[job["id"]] for job in page["jobs"]]


class FilterTests(Fixture):
    def test_several_stages_at_once(self):
        self.assertEqual(sorted(self.keys(status="applied,rejected")), ["a", "b"])
        self.assertEqual(sorted(self.keys(status="interview")), ["c"])
        with self.assertRaises(ValueError):
            service.list_page(self.data, status="applied,unknown")

    def test_experience_levels_fits_stretch_and_above(self):
        self.assertEqual(sorted(self.keys(experience="fits")), ["a", "d"])
        self.assertEqual(sorted(self.keys(experience="stretch,above")), ["b", "c"])
        self.assertEqual(sorted(self.keys(experience="fits,stretch")), ["a", "b", "d"])
        self.assertEqual(sorted(self.keys(experience="fits,stretch,above")), ["a", "b", "c", "d"])
        self.assertEqual(sorted(self.keys(experience="relevant")), ["a", "b", "d"])  # older interface value
        with self.assertRaises(ValueError):
            service.list_page(self.data, experience="senior")

    def test_posted_within_minimum_match_and_remote(self):
        self.assertEqual(sorted(self.keys(posted=7)), ["a", "b"])
        self.assertEqual(sorted(self.keys(posted=30)), ["a", "b", "c"])
        self.assertEqual(sorted(self.keys(min_score=70)), ["a", "c"])
        self.assertEqual(self.keys(remote=True), ["b"])
        self.assertEqual(self.keys(posted=7, min_score=70), ["a"])
        with self.assertRaises(ValueError):
            service.list_page(self.data, min_score=101)

    def test_sort_orders(self):
        self.assertEqual(self.keys(sort="score"), ["a", "c", "b", "d"])
        self.assertEqual(self.keys(sort="newest"), ["a", "b", "c", "d"])
        self.assertEqual(self.keys(sort="deadline"), ["b", "a", "d", "c"])  # no deadline last
        self.assertEqual(self.keys(sort="company"), ["a", "b", "c", "d"])
        self.assertEqual(len(self.keys(sort="found")), 4)
        with self.assertRaises(ValueError):
            service.list_page(self.data, sort="random")


class QuickMarkApiTests(Fixture):
    def setUp(self):
        super().setUp()
        self.app = create_app(self.data, token="t")
        self.app.config["LOCAL_ORIGIN"] = "http://127.0.0.1:5000"
        self.client = self.app.test_client()

    def request(self, method, path, **options):
        return self.client.open(path, method=method, base_url="http://127.0.0.1:5000", headers={"X-Session-Token": "t"}, **options)

    def test_a_quick_mark_changes_only_the_stage(self):
        service.update_workflow(self.data, self.ids["d"], "new", "Call the recruiter on Monday")
        response = self.request("POST", f"/api/jobs/{self.ids['d']}/stage", json={"status": "applied"})
        self.assertEqual(response.get_json(), {"ok": True, "status": "applied"})
        job = service.get_vacancy(self.data, self.ids["d"])
        self.assertEqual((job["status"], job["notes"]), ("applied", "Call the recruiter on Monday"))
        self.assertEqual(self.request("POST", f"/api/jobs/{self.ids['d']}/stage", json={"status": "hired"}).status_code, 400)

    def test_quick_marks_are_chosen_in_settings(self):
        self.assertEqual(self.request("GET", "/api/appearance").get_json()["quick_marks"], ["applied", "rejected"])
        saved = self.request("PUT", "/api/appearance", json={"quick_marks": ["interview", "applied"]}).get_json()
        self.assertEqual(saved["quick_marks"], ["applied", "interview"])  # stage order
        self.assertEqual(saved["theme"], "light")  # the theme is untouched
        for wrong in (["applied", "rejected", "saved", "ready", "interview"], ["new"], "applied"):
            self.assertEqual(self.request("PUT", "/api/appearance", json={"quick_marks": wrong}).status_code, 400)
        self.assertEqual(self.request("PUT", "/api/appearance", json={"quick_marks": []}).get_json()["quick_marks"], [])
        filtered = self.request("GET", "/api/jobs?view=discover&status=applied,rejected&experience=all&posted=7&min_score=50&remote=1")
        self.assertEqual([job["id"] for job in filtered.get_json()["jobs"]], [self.ids["b"]])


if __name__ == "__main__":
    unittest.main()
