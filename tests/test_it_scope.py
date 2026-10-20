"""IT jobs only: what refreshes keep, how sources read IT, offline."""

import json
import sys
import tempfile
import time
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from norway_job_agent import adzuna, market, nav, service
from norway_job_agent.storage import JobStore
from support import nav_fetch, offline_collection


def setUpModule():
    offline_collection()


def job(identifier, title, source="greenhouse", **changes):
    return {"title": title, "company": f"Fixture {identifier}", "location": "Berlin, Germany", "description": "",
            "source": source, "source_id": str(identifier), "source_url": f"https://jobs.example.test/{source}/{identifier}",
            "apply_url": f"https://jobs.example.test/{source}/{identifier}", "countries": ["DE"], **changes}


class Fixture(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name)
        service.initialize(self.folder)
        settings = service.read_settings(self.folder)
        settings["sources"] = []
        settings["market"]["it_only"] = True
        service.save_settings(self.folder, settings)

    def titles(self):
        return sorted(row["title"] for row in service.vacancies(self.folder))


class ItOnlyRefreshTests(Fixture):
    def test_the_setting_defaults_on_and_can_be_switched_off(self):
        with patch.object(market, "DEFAULT_IT_ONLY", True):
            self.assertTrue(market.validate_market_settings({})["it_only"])
        self.assertFalse(market.validate_market_settings({"it_only": False})["it_only"])
        with self.assertRaises(ValueError):
            market.validate_market_settings({"it_only": "yes"})

    def test_job_sites_and_employer_boards_keep_only_it_jobs(self):
        run = market.ProviderRun("himalayas")
        run.jobs = [job(1, "Python Developer", "himalayas"), job(2, "Account Executive", "himalayas")]
        settings = service.read_settings(self.folder)
        settings["sources"] = [{"type": "greenhouse", "board": "acme"}]
        settings["market"]["enabled"]["himalayas"] = True
        service.save_settings(self.folder, settings)
        board = {"result": {"source": {"type": "greenhouse", "board": "acme"}, "fetched": 2}, "kind": "greenhouse",
                 "jobs": [job("acme:1", "DevOps Engineer"), job("acme:2", "Office Manager")], "snapshot": ("acme:", ["acme:1", "acme:2"])}
        with patch("norway_job_agent.market.search_provider", return_value=run), \
                patch("norway_job_agent.collection.fetch_source", return_value=board):
            report = service.collect(self.folder)
        self.assertEqual(self.titles(), ["DevOps Engineer", "Python Developer"])
        rows = {row["source"].get("name") or row["source"].get("board"): row for row in report["sources"]}
        self.assertEqual(rows["Himalayas"]["outside_it"], 1)
        self.assertEqual(rows["acme"]["outside_it"], 1)

    def test_a_refresh_removes_stored_non_it_jobs_but_keeps_ones_you_touched(self):
        kept = service.import_vacancy(self.folder, job(1, "Sales Manager", "himalayas"))[0]
        service.import_vacancy(self.folder, job(2, "Kindergarten Teacher", "himalayas"))
        service.import_vacancy(self.folder, job(3, "Data Engineer", "himalayas"))
        manual = service.import_vacancy(self.folder, job(4, "Chef", "manual"))[0]
        service.update_workflow(self.folder, kept, "saved", "")
        report = service.collect(self.folder)
        self.assertGreaterEqual(report["removed"], 1)
        self.assertEqual(self.titles(), ["Chef", "Data Engineer", "Sales Manager"])
        self.assertIn(manual, {row["id"] for row in service.vacancies(self.folder)})

    def test_deleting_jobs_uses_indexes_on_every_referring_table(self):
        # Without them each deletion scanned 275,000 identities on real data.
        with JobStore(self.folder / "vacancies.db") as store:
            names = {row[0] for row in store._connection.execute("SELECT name FROM sqlite_master WHERE type = 'index'")}
            plan = " ".join(str(row[-1]) for row in store._connection.execute(
                "EXPLAIN QUERY PLAN SELECT 1 FROM job_identities WHERE job_id = 1"))
        self.assertTrue({"job_identities_job", "job_provenance_job", "application_deliveries_job"} <= names)
        self.assertIn("job_identities_job", plan)

    def test_switched_off_everything_is_kept(self):
        settings = service.read_settings(self.folder)
        settings["market"]["it_only"] = False
        service.save_settings(self.folder, settings)
        service.import_vacancy(self.folder, job(2, "Kindergarten Teacher", "himalayas"))
        service.collect(self.folder)
        self.assertEqual(self.titles(), ["Kindergarten Teacher"])

    def test_nav_keeps_its_it_category_and_drops_adverts_it_files_elsewhere(self):
        service.import_vacancy(self.folder, job("old", "Systemutvikler", "nav"))
        it = job("u1", "Senior Backend utvikler", "nav", raw_json={"occupation": "IT / Utvikling", "it_category": True})
        carpenter = job("old", "Tømrer – utvikler deg", "nav", raw_json={"occupation": "Håndverkere", "it_category": False})
        settings = service.read_settings(self.folder)
        settings["sources"] = [{"type": "nav"}]
        service.save_settings(self.folder, settings)
        with patch("norway_job_agent.service._fetch_nav", return_value=nav_fetch([it, carpenter])):
            report = service.collect(self.folder)
        self.assertEqual(self.titles(), ["Senior Backend utvikler"])
        self.assertEqual(report["sources"][0]["outside_it"], 1)


class NavOutsideTests(unittest.TestCase):
    def test_a_non_it_advert_is_read_once_until_nav_changes_it(self):
        from norway_job_agent import nav_index
        from test_market_search import U1, U2, feed_page
        from support import REAL_SYNC
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        folder = Path(temporary.name)
        with patch("norway_job_agent.nav_index.sync", REAL_SYNC), \
                patch("norway_job_agent.nav._get_token", return_value=("token", True)):
            with patch("norway_job_agent.nav._fetch_nav", return_value=feed_page([(U1, "ACTIVE", "Tømrer", "v1"),
                                                                                   (U2, "ACTIVE", "Utvikler", "v1")])):
                nav_index.sync(folder)
            nav_index.mark_hydrated(folder, [("v1", "[]", U1), ("v1", "[]", U2)])
            nav_index.mark_outside(folder, [U1])
            found = nav_index.find(folder, [], everything=True, skip_outside=True)
            self.assertEqual([item["uuid"] for item in found], [U2])  # U2 is not stored yet
            self.assertEqual(len(nav_index.find(folder, [], everything=True)), 2)  # IT jobs only switched off
            with patch("norway_job_agent.nav._fetch_nav", return_value=feed_page([(U1, "ACTIVE", "Tømrer", "v2")])):
                nav_index.sync(folder)
            self.assertIn(U1, [item["uuid"] for item in nav_index.find(folder, [], everything=True, skip_outside=True)])


class SourceScopeTests(unittest.TestCase):
    def test_a_board_configured_twice_downloads_once(self):
        settings = {"sources": [{"type": "nav"}, {"type": "nav"},
                                {"type": "lever", "board": "bekk", "region": "global", "countries": ["NO"]},
                                {"type": "lever", "board": "Bekk", "region": "global", "name": "Bekk", "countries": ["DE"]},
                                {"type": "greenhouse", "board": "acme", "countries": ["US"]},
                                {"type": "greenhouse", "board": "acme"}]}
        selected = service._collection_scope(settings, ["NO", "DE", "US"])
        self.assertEqual([item["type"] for item in selected], ["nav", "lever", "greenhouse"])
        self.assertEqual(selected[1]["countries"], ["NO", "DE"])
        self.assertNotIn("countries", selected[2])  # an unscoped copy keeps every posting


class SourceReadsTests(unittest.TestCase):
    def test_nav_occupation_category_and_styrk_code(self):
        content = {"occupationCategories": [{"level1": "IT", "level2": "Utvikling"}],
                   "categoryList": [{"categoryType": "STYRK08", "code": "2512"}]}
        self.assertEqual(nav._occupation(content), {"occupation": "IT / Utvikling", "styrk": ["2512"], "it_category": True})
        content = {"occupationCategories": [{"level1": "Håndverkere", "level2": "Tømrer"}],
                   "categoryList": [{"categoryType": "STYRK08", "code": "7115"}]}
        self.assertFalse(nav._occupation(content)["it_category"])
        self.assertTrue(nav._occupation({"categoryList": [{"categoryType": "STYRK08", "code": "3512"}]})["it_category"])
        self.assertEqual(nav._occupation({}), {})

    def test_bundesagentur_reads_its_it_occupation_fields(self):
        requested = []

        def reply(url, **kwargs):
            requested.append(url)
            return {"maxErgebnisse": 1, "ergebnisliste": [{"refnr": "10001-1", "titel": "Mitarbeiter Anwendungsbetreuung",
                                                           "hauptberuf": "Fachinformatiker/in", "arbeitgeber": "Fixture GmbH"}]}
        with patch("norway_job_agent.market._json", side_effect=reply), patch.object(market.time, "sleep"):
            run = market.search_provider("arbeitsagentur", [], "DE", state={}, broad=True, it_only=True)
        self.assertEqual(len(requested), len(market.BA_IT_FIELDS))
        self.assertTrue(all("berufsfeld=" in url and "was=" not in url for url in requested))
        self.assertEqual(run.jobs[0]["raw_json"]["it_category"], True)
        self.assertEqual(run.jobs[0]["raw_json"]["occupation"], "Fachinformatiker/in")

    def test_djinni_skips_non_it_categories_and_tags_the_rest(self):
        feeds = []

        def feed(params):
            feeds.append(params.get("primary_keyword"))
            return [job(params.get("primary_keyword", "new"), "Middle Python Developer", "djinni", countries=["UA"])]
        categories = [("Python", "Python"), ("Marketing", "Marketing"), ("Social Media", "Marketing"), ("Design", "Design")]
        with patch("norway_job_agent.market._djinni_categories", return_value=categories), \
                patch("norway_job_agent.market._djinni_jobs", side_effect=feed), patch.object(market, "REQUEST_PAUSE", 0):
            run = market.search_provider("djinni", [], "UA", state={}, broad=True, it_only=True)
        self.assertEqual(feeds, ["Python", "Design"])
        raws = {item["raw_json"]["board_category"]: item["raw_json"] for item in run.jobs}
        self.assertTrue(raws["Python"]["it_category"])
        self.assertNotIn("it_category", raws["Design"])  # the title decides


class DouCrawlTests(unittest.TestCase):
    def setUp(self):
        patcher = patch.object(market, "REQUEST_PAUSE", 0)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.calls = []

    def feed(self, params):
        self.calls.append(dict(params))
        size = 25 if params.get("category") == "Python" and params.get("exp") != "0-1" and "city" not in params else 3
        if "remote" in params:
            size = 2
        tag = "-".join(f"{k}={v}" for k, v in sorted(params.items()))
        return [job(f"{tag}-{n}", "Python Developer", "dou", countries=["UA"]) for n in range(size)]

    def crawl(self, state, deadline=None):
        run, seen = market.ProviderRun("dou"), set()
        with patch("norway_job_agent.market._dou_filters", return_value=(["Python", "Sales", "Java"], ["Київ", "Львів"])), \
                patch("norway_job_agent.market._dou_jobs", side_effect=self.feed):
            market._dou_everything(run, [], state, seen, deadline or time.monotonic() + 60, it_only=True)
        return run

    def test_full_feeds_split_by_experience_then_city_and_non_it_is_skipped(self):
        state = {}
        run = self.crawl(state)
        categories = {call.get("category") for call in self.calls}
        self.assertEqual(categories, {"Python", "Java"})
        self.assertIn({"category": "Python", "exp": "3-5", "city": "Київ"}, self.calls)
        self.assertIn({"category": "Python", "exp": "3-5", "remote": ""}, self.calls)
        self.assertNotIn({"category": "Python", "exp": "0-1", "city": "Київ"}, self.calls)
        self.assertIn("dou_full_read", state)
        self.assertNotIn("dou_crawl", state)
        self.assertTrue(all(item["raw_json"]["it_category"] for item in run.jobs))

    def test_an_unfinished_crawl_resumes_on_the_next_refresh(self):
        state = {}
        run = self.crawl(state, deadline=time.monotonic() - 1)
        self.assertTrue(run.has_more)
        queue = list(state["dou_crawl"]["queue"])
        self.assertEqual([item["category"] for item in queue], ["Python", "Java"])
        self.calls.clear()
        self.crawl(state)
        self.assertEqual(self.calls[0], {"category": "Python"})
        self.assertIn("dou_full_read", state)
        # Between daily passes only the newest-jobs feed is read.
        self.calls.clear()
        self.crawl(state)
        self.assertEqual(self.calls, [{}])


def adzuna_result(identifier, title="Software Engineer"):
    return {"id": str(identifier), "title": title, "description": "Python", "redirect_url": f"https://www.adzuna.com/land/ad/{identifier}",
            "company": {"display_name": "Fixture Inc"}, "location": {"display_name": "Austin, Texas"}}


KEY = {"app_id": "fixture1234", "app_key": "0123456789abcdef0123456789abcdef"}


class AdzunaItTests(unittest.TestCase):
    def setUp(self):
        patcher = patch.object(adzuna, "PAUSE_SECONDS", 0)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_monthly_allowance_is_spread_over_the_days(self):
        today = date(2026, 10, 20)
        self.assertEqual(adzuna._daily_pace({}, today), int(2500 * 0.9 / 31))
        state = {}
        for _ in range(70):
            adzuna._count(state, today)
        self.assertEqual(adzuna._daily_pace(state, today), int(2500 * 0.9 / 31) - 70)

    def test_roles_first_then_the_newest_it_jobs_in_adzunas_it_category(self):
        requested = []

        def reply(key, url):
            requested.append(url)
            if "/categories" in url:
                return 200, json.dumps({"results": [{"tag": "it-jobs", "label": "IT Jobs"}, {"tag": "sales-jobs", "label": "Sales"}]}).encode()
            return 200, json.dumps({"count": 5000, "results": [adzuna_result(f"{len(requested)}-{n}") for n in range(50)]}).encode()
        state = {}
        with patch("norway_job_agent.adzuna._status", side_effect=reply):
            run = market.search_provider("adzuna", ["Data Scientist"], "US", state=state, credentials=KEY,
                                         request_budget=6, it_only=True)
        self.assertIn("/categories", requested[0])
        self.assertIn("what=Data+Scientist", requested[1])
        self.assertTrue(all("category=it-jobs" in url for url in requested[1:]))
        self.assertTrue(all("what=" not in url for url in requested[2:]))
        self.assertEqual(len(requested), 6)  # the category lookup counts too
        self.assertEqual(state["adzuna_it_category"], "it-jobs")
        self.assertTrue(all(item["raw_json"]["it_category"] for item in run.jobs))
        self.assertTrue(run.has_more)


if __name__ == "__main__":
    unittest.main()
