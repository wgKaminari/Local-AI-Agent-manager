"""Djinni's whole-board category crawl and the Adzuna job site, offline."""

import json
import sys
import tempfile
import time
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from norway_job_agent import adzuna, market, service
from norway_job_agent.sources import SourceError
from support import offline_collection


def setUpModule():
    offline_collection()


def djinni_job(identifier, title="Python Developer"):
    return {"title": title, "company": market.DJINNI_COMPANY, "location": "Ukraine / remote", "description": "Python",
            "source": "djinni", "source_id": str(identifier), "source_url": f"https://djinni.co/jobs/{identifier}-x/",
            "apply_url": f"https://djinni.co/jobs/{identifier}-x/", "countries": ["UA"]}


class DjinniCrawlTests(unittest.TestCase):
    def setUp(self):
        patcher = patch.object(market, "REQUEST_PAUSE", 0)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.calls = []

    def feed(self, params):
        self.calls.append(params)
        if params.get("primary_keyword") == "Python":
            if "region" in params:
                return [djinni_job(f"py-2y-{params['region']}-{n}") for n in range(30)]
            if params.get("exp_level") == "2y":
                return [djinni_job(f"py-2y-{n}") for n in range(100)]
            if "exp_level" in params:
                return [djinni_job(f"py-{params['exp_level']}-{n}") for n in range(10)]
            return [djinni_job(f"py-{n}") for n in range(100)]
        if params.get("primary_keyword") == "Java":
            return [djinni_job(f"java-{n}", "Java Developer") for n in range(5)]
        if "all_keywords" in params:
            return [djinni_job("py-1")]
        return [djinni_job("newest-1")]  # the newest-jobs feed

    def crawl(self, state, seconds=600):
        with patch("norway_job_agent.market._djinni_categories", return_value=["Python", "Java"]), \
                patch("norway_job_agent.market._djinni_jobs", side_effect=self.feed):
            return market.search_provider("djinni", ["Python Developer"], "UA", state=state, broad=True, seconds=seconds)

    def test_full_feeds_are_split_until_older_jobs_appear(self):
        state = {}
        run = self.crawl(state)
        keys = [tuple(sorted(call.items())) for call in self.calls]
        # Python filled its 100-job feed: split by experience, and 2y again by region.
        self.assertIn((("exp_level", "2y"), ("primary_keyword", "Python")), keys)
        self.assertIn((("exp_level", "2y"), ("primary_keyword", "Python"), ("region", "UKR")), keys)
        self.assertFalse(any(dict(key).get("primary_keyword") == "Java" and len(key) > 1 for key in keys))
        ids = {job["source_id"] for job in run.jobs}
        self.assertIn("java-4", ids)
        self.assertIn("py-2y-UKR-29", ids)
        self.assertIn("py-9y-9", ids)
        self.assertIn("Read all 2 Djinni categories", run.report()["coverage"])
        self.assertIn("djinni_full_read", state)

    def test_between_daily_crawls_a_refresh_reads_only_the_newest_jobs(self):
        state = {"djinni_full_read": datetime.now(timezone.utc).isoformat()}
        run = self.crawl(state)
        self.assertEqual(self.calls, [{"all_keywords": "Python Developer"}, {}])
        self.assertEqual({job["source_id"] for job in run.jobs}, {"py-1", "newest-1"})
        state["djinni_full_read"] = (datetime.now(timezone.utc) - timedelta(hours=market.DJINNI_CRAWL_HOURS + 1)).isoformat()
        self.calls.clear()
        self.crawl(state)
        self.assertGreater(len(self.calls), 10)

    def test_a_crawl_out_of_time_is_not_recorded_as_complete(self):
        state, run, seen = {}, market.ProviderRun("djinni"), set()
        with patch("norway_job_agent.market._djinni_categories", return_value=["Python", "Java"]), \
                patch("norway_job_agent.market._djinni_jobs", side_effect=self.feed):
            market._djinni_everything(run, [], state, seen, time.monotonic() - 1)
        self.assertTrue(run.has_more)
        self.assertNotIn("djinni_full_read", state)

    def test_category_list_includes_sub_categories(self):
        page = (b'<html><script type="application/json" class="tms-data">'
                + json.dumps({"groups": [{"label": "Development", "items": [
                    {"key": "JavaScript", "children": [{"key": "React.js"}, {"key": "Vue.js"}]},
                    {"key": "Python"}, {"key": "Python"}]}]}).encode() + b"</script></html>")
        with patch("norway_job_agent.market._fetch", return_value=(200, {}, page, "https://djinni.co/jobs/")):
            self.assertEqual(market._djinni_categories(), ["JavaScript", "React.js", "Vue.js", "Python"])
        with patch("norway_job_agent.market._fetch", return_value=(200, {}, b"<html></html>", "")), \
                self.assertRaisesRegex(SourceError, "category list"):
            market._djinni_categories()


def adzuna_result(identifier, title="Data Scientist", **changes):
    return {"id": str(identifier), "title": f"<strong>{title}</strong>", "description": "Python, SQL and statistics...",
            "redirect_url": f"https://www.adzuna.com/land/ad/{identifier}?se=x", "created": "2026-10-18T10:00:00Z",
            "company": {"display_name": "Fixture Inc"}, "location": {"display_name": "Austin, Travis County"},
            "contract_time": "full_time", "salary_min": 120000, "salary_max": 150000, "salary_is_predicted": "0", **changes}


KEY = {"app_id": "fixture1234", "app_key": "0123456789abcdef0123456789abcdef"}


class AdzunaTests(unittest.TestCase):
    def setUp(self):
        patcher = patch.object(adzuna, "PAUSE_SECONDS", 0)
        patcher.start()
        self.addCleanup(patcher.stop)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name)

    def test_quota_stays_below_every_free_limit_and_resets_by_period(self):
        today = date(2026, 10, 20)
        state = {}
        self.assertEqual(adzuna.remaining(state, today), 225)
        for _ in range(225):
            adzuna._count(state, today)
        self.assertEqual(adzuna.remaining(state, today), 0)
        self.assertEqual(adzuna.remaining(state, today + timedelta(days=1)), 225)
        state[adzuna.STATE_KEY].update(week_count=880, month_count=2240)
        self.assertEqual(adzuna.remaining(state, today), 0)
        state[adzuna.STATE_KEY]["day_count"] = 0
        self.assertEqual(adzuna.remaining(state, today), 10)  # 2,250 a month, 10 left

    def test_searches_page_by_page_within_the_budget(self):
        pages = {("Data Scientist", 1): [adzuna_result(n) for n in range(50)],
                 ("Data Scientist", 2): [adzuna_result(n) for n in range(50, 70)],
                 ("ML Engineer", 1): [adzuna_result(100, "ML Engineer")]}
        requested = []

        def reply(key, url):
            self.assertEqual(key, KEY)
            query = "Data Scientist" if "Data+Scientist" in url else "ML Engineer"
            page = int(url.split("/search/")[1].split("?")[0])
            requested.append((query, page))
            return 200, json.dumps({"count": 70 if query == "Data Scientist" else 1, "results": pages[(query, page)]}).encode()
        state = {}
        with patch("norway_job_agent.adzuna._status", side_effect=reply):
            run = market.search_provider("adzuna", ["Data Scientist", "ML Engineer"], "US", state=state,
                                         credentials=KEY, request_budget=40)
        self.assertEqual(requested, [("Data Scientist", 1), ("ML Engineer", 1), ("Data Scientist", 2)])
        self.assertEqual(len(run.jobs), 71)
        self.assertEqual(state[adzuna.STATE_KEY]["day_count"], 3)
        job = run.jobs[0]
        self.assertEqual((job["title"], job["company"], job["countries"], job["employment_type"]),
                         ("Data Scientist", "Fixture Inc", ["US"], "Full-time"))
        self.assertTrue(job["location"].endswith("United States"))
        self.assertEqual(job["raw_json"]["salary"], {"min": 120000, "max": 150000, "estimated": False})
        self.assertIn("Used 3 Adzuna requests", run.report()["coverage"])

    def test_budget_and_a_rejected_key_stop_searching(self):
        with patch("norway_job_agent.adzuna._status", return_value=(200, json.dumps({"count": 500, "results": [
                adzuna_result(n) for n in range(50)]}).encode())) as request:
            run = market.search_provider("adzuna", ["A", "B", "C"], "US", state={}, credentials=KEY, request_budget=2)
        self.assertEqual(request.call_count, 2)
        self.assertTrue(run.has_more)
        with patch("norway_job_agent.adzuna._status", return_value=(401, b"")) as request:
            run = market.search_provider("adzuna", ["A", "B"], "US", state={}, credentials=KEY, request_budget=40)
        self.assertEqual(request.call_count, 1)
        self.assertIn("did not accept", run.errors[0])
        used_up = {adzuna.STATE_KEY: adzuna._periods(date.today()) | {"day_count": 225, "week_count": 225, "month_count": 225}}
        with patch("norway_job_agent.adzuna._status") as request:
            run = market.search_provider("adzuna", ["A"], "US", state=used_up, credentials=KEY, request_budget=40)
        request.assert_not_called()
        self.assertIn("allowance", run.report()["coverage"])

    def test_key_is_checked_before_saving_and_only_a_hint_is_shown(self):
        with self.assertRaisesRegex(ValueError, "letters and digits"):
            service.save_adzuna_key(self.folder, "fixture 1234", KEY["app_key"])
        with patch("norway_job_agent.adzuna._status", return_value=(401, b"")), \
                self.assertRaisesRegex(SourceError, "did not accept"):
            service.save_adzuna_key(self.folder, KEY["app_id"], KEY["app_key"])
        self.assertIsNone(adzuna.read_key(self.folder))
        with patch("norway_job_agent.adzuna._status", return_value=(200, b"{}")):
            status = service.save_adzuna_key(self.folder, f" {KEY['app_id']} ", KEY["app_key"])
        self.assertEqual((status["configured"], status["app_id_hint"]), (True, "…1234"))
        self.assertNotIn(KEY["app_key"], json.dumps(status))
        self.assertEqual(adzuna.read_key(self.folder)["app_key"], KEY["app_key"])
        # Both checks count against the key's allowance.
        self.assertEqual(json.loads((self.folder / service.MARKET_STATE_FILE).read_text())[adzuna.STATE_KEY]["day_count"], 2)
        self.assertEqual(service.delete_adzuna_key(self.folder), {"configured": False})
        self.assertFalse((self.folder / adzuna.KEY_FILE).exists())

    def test_refresh_searches_adzuna_only_with_a_key_and_a_live_search_uses_two_requests(self):
        service.initialize(self.folder)
        settings = service.read_settings(self.folder)
        settings["sources"] = []
        settings["market"]["enabled"]["adzuna"] = True
        service.save_settings(self.folder, settings)
        calls = []

        def search(name, *args, **kwargs):
            credentials = kwargs.get("credentials") or {}
            calls.append((name, credentials.get("app_key"), kwargs.get("request_budget")))
            return market.ProviderRun(name)
        with patch("norway_job_agent.market.search_provider", side_effect=search):
            service.collect(self.folder)
            self.assertEqual(calls, [])
            self.assertFalse(next(site for site in service.discovery_coverage(self.folder, "US")["job_sites"]
                                  if site["id"] == "adzuna")["enabled"])
            (self.folder / adzuna.KEY_FILE).write_text(json.dumps(KEY), encoding="utf-8")
            service.collect(self.folder)
            service.market_search(self.folder, "Data Scientist", "US")
        self.assertEqual(calls, [("adzuna", KEY["app_key"], adzuna.REQUESTS_PER_REFRESH),
                                 ("adzuna", KEY["app_key"], adzuna.REQUESTS_PER_LIVE_SEARCH)])


if __name__ == "__main__":
    unittest.main()
