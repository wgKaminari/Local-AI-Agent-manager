"""Stored matches, full-text search, paging, cleanup and collect-everything, offline."""

import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from norway_job_agent import job_index, market, nav_index, service
from norway_job_agent.storage import JobStore
from support import REAL_SYNC, offline_collection


def setUpModule():
    offline_collection()


class Fixture(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name)
        service.initialize(self.folder)
        profile = service.read_profile(self.folder)
        profile.update(target_roles=["Data Scientist"], related_roles=["Data Engineer"], skills=["Python"],
                       years_of_experience=3)
        service.save_profile(self.folder, profile)

    def add(self, identifier, **changes):
        job = {"title": "Data Scientist", "company": f"Employer {identifier}", "location": "Berlin, Germany",
               "description": "Python and statistics", "source": "manual", "source_id": identifier,
               "source_url": f"https://jobs.example/{identifier}", **changes}
        return service.import_vacancy(self.folder, job)[0]

    def store(self):
        store = JobStore(self.folder / "vacancies.db")
        self.addCleanup(store.close)
        return store


class StoredMatchTests(Fixture):
    def test_each_job_is_matched_once_until_its_content_or_the_profile_changes(self):
        first = self.add("a")
        self.add("b", title="Barista")
        from norway_job_agent import profile as profile_module
        with patch.object(profile_module, "match_job", wraps=profile_module.match_job) as matcher:
            service.list_page(self.folder)
            self.assertEqual(matcher.call_count, 2)
            # A refresh that sees the same advert again changes nothing.
            self.add("a")
            service.list_page(self.folder)
            self.assertEqual(matcher.call_count, 2)
            self.add("a", description="Python, statistics and SQL")
            service.list_page(self.folder)
            self.assertEqual(matcher.call_count, 3)
            profile = service.read_profile(self.folder)
            profile["skills"].append("SQL")
            service.save_profile(self.folder, profile)
            self.assertEqual(service.refresh_matches(self.folder)["matched"], 2)
        page = service.list_page(self.folder)
        self.assertEqual(page["jobs"][0]["id"], first)
        self.assertEqual(page["jobs"][0]["match"]["matched_skills"], ["Python", "SQL"])

    def test_lists_never_wait_for_a_running_rematch_and_compute_a_bounded_share(self):
        self.add("a")
        service.list_page(self.folder)
        for index in range(5):
            self.add(f"new{index}")
        with self.store() as store:
            lock = job_index._lock(store)
            lock.acquire()
            try:
                # A re-match holds the lock: the list answers from stored results.
                self.assertEqual(service.list_page(self.folder)["summary"]["shown"], 1)
            finally:
                lock.release()
        with patch.object(service, "LIST_MATCH_LIMIT", 2), \
                patch.object(service, "_rematch_in_background") as background:
            self.assertEqual(service.list_page(self.folder)["summary"]["shown"], 3)
        # More remained, so the list asked for one background re-match.
        background.assert_called_once()
        self.assertEqual(service.refresh_matches(self.folder)["matched"], 3)
        self.assertEqual(service.list_page(self.folder)["summary"]["shown"], 6)

    def test_a_background_rematch_finishes_what_lists_left(self):
        for index in range(4):
            self.add(f"bg{index}")
        with patch.object(service, "LIST_MATCH_LIMIT", 1):
            service.list_page(self.folder)
        for _ in range(100):
            if not service._background_rematches:
                break
            __import__("time").sleep(0.05)
        with self.store() as store:
            stale = store._connection.execute("SELECT count(*) FROM jobs j LEFT JOIN job_matches m ON m.job_id = j.id "
                                              "WHERE m.job_id IS NULL").fetchone()[0]
        self.assertEqual(stale, 0)

    def test_stored_deadlines_are_judged_against_today(self):
        identifier = self.add("a", deadline=(datetime.now() + timedelta(days=1)).date().isoformat())
        service.list_page(self.folder)
        with self.store() as store:
            store._connection.execute("UPDATE job_matches SET match_json = json_set(match_json, '$.expired', json('true'))")
            store._connection.commit()
        job = service.list_page(self.folder)["jobs"][0]
        self.assertEqual(job["id"], identifier)
        self.assertFalse(job["match"]["expired"])
        self.assertNotIn("The stated application deadline has passed.", job["match"]["warnings"])

    def test_version_three_databases_upgrade_and_index_existing_vacancies(self):
        path = self.folder / "legacy.db"
        with JobStore(path) as store:
            store.upsert_job({"title": "Softwareentwickler (m/w/d)", "company": "Alt GmbH", "source": "manual",
                              "source_id": "old", "source_url": "https://jobs.example/old", "description": "Linux"})
        with closing(sqlite3.connect(path)) as connection:
            for name in ("jobs_fts_insert", "jobs_fts_delete", "jobs_fts_update", "job_matches_stale"):
                connection.execute(f"DROP TRIGGER {name}")
            connection.execute("DROP TABLE jobs_fts")
            connection.execute("DROP TABLE job_matches")
            connection.execute("PRAGMA user_version = 3")
            connection.commit()
        with JobStore(path) as store:
            self.assertEqual(store._connection.execute("PRAGMA user_version").fetchone()[0], 4)
            found = store._connection.execute("SELECT rowid FROM jobs_fts WHERE jobs_fts MATCH 'linux'").fetchall()
        self.assertEqual(len(found), 1)


class SearchAndPagingTests(Fixture):
    def test_search_matches_word_prefixes_compounds_short_words_and_phrases(self):
        self.add("a", title="Softwareentwickler (m/w/d)", description="Linux und Python")
        self.add("b", title="Gopher keeper", description="Animals")
        self.add("c", title="Go developer", description="Golang services")
        self.add("d", title="Machine Learning Engineer", description="PyTorch")
        titles = lambda query: {job["title"] for job in service.list_page(self.folder, query=query, limit=50)["jobs"]}
        self.assertEqual(titles("Pyth"), {"Softwareentwickler (m/w/d)"})
        self.assertEqual(titles("entwickler"), {"Softwareentwickler (m/w/d)"})
        self.assertEqual(titles("go"), {"Go developer"})
        self.assertEqual(titles('"python linux"'), set())
        self.assertEqual(titles("linux python"), {"Softwareentwickler (m/w/d)"})
        self.assertEqual(titles("ML Engineer"), {"Machine Learning Engineer"})

    def test_pages_cover_every_vacancy_once_in_order(self):
        for index in range(23):
            self.add(f"job{index:02d}", title="Data Scientist" if index % 3 else "Data Engineer")
        first = service.list_page(self.folder, limit=10)
        self.assertEqual((first["summary"]["shown"], len(first["jobs"]), first["summary"]["has_more"]), (23, 10, True))
        ids = [job["id"] for offset in (0, 10, 20) for job in service.list_page(self.folder, offset=offset, limit=10)["jobs"]]
        self.assertEqual(len(ids), 23)
        self.assertEqual(len(set(ids)), 23)
        self.assertFalse(service.list_page(self.folder, offset=20, limit=10)["summary"]["has_more"])
        with self.assertRaises(ValueError):
            service.list_page(self.folder, limit=0)

    def test_list_api_pages_and_rejects_bad_numbers(self):
        from norway_job_agent.web_api import close_resources, create_app
        for index in range(5):
            self.add(f"api{index}")
        app = create_app(self.folder, "token")
        self.addCleanup(lambda: (close_resources(app), app.extensions["local_state"].worker.shutdown(wait=True)))
        app.config.update(TESTING=True, LOCAL_ORIGIN="http://127.0.0.1:7654")
        client = app.test_client()
        headers = {"X-Session-Token": "token", "Origin": "http://127.0.0.1:7654"}
        data = client.get("/api/jobs?offset=2&limit=2", headers=headers, base_url="http://127.0.0.1:7654").get_json()
        self.assertEqual((len(data["jobs"]), data["summary"]["shown"], data["summary"]["has_more"]), (2, 5, True))
        bad = client.get("/api/jobs?offset=-1", headers=headers, base_url="http://127.0.0.1:7654")
        self.assertEqual(bad.status_code, 400)


class CleanupTests(Fixture):
    def age(self, identifier, days, **columns):
        stamp = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        assignments = ", ".join(["last_seen_at = ?", *(f"{key} = ?" for key in columns)])
        with self.store() as store:
            store._connection.execute(f"UPDATE jobs SET {assignments} WHERE id = ?", [stamp, *columns.values(), identifier])
            store._connection.commit()

    def test_long_closed_and_unseen_vacancies_go_but_your_work_stays(self):
        withdrawn = self.add("withdrawn", source="nav")
        unseen = self.add("unseen", source="arbeitsagentur")
        expired = self.add("expired", source="greenhouse", deadline="2026-01-01")
        recent = self.add("recent", source="arbeitsagentur")
        saved = self.add("saved", source="arbeitsagentur")
        noted = self.add("noted", source="nav")
        drafted = self.add("drafted", source="nav")
        mine = self.add("mine", source="manual", deadline="2025-01-01")
        self.age(withdrawn, 40, is_active=0)
        self.age(unseen, 40)
        self.age(saved, 90)
        self.age(noted, 90, is_active=0)
        self.age(drafted, 90, is_active=0)
        self.age(mine, 400)
        service.update_workflow(self.folder, saved, "saved", "")
        service.update_workflow(self.folder, noted, "new", "Call the recruiter")
        service.save_edited_letter(self.folder, drafted, "My letter")
        service.list_page(self.folder)
        with self.store() as store:
            removed = job_index.cleanup(store)
            remaining = {row[0] for row in store._connection.execute("SELECT id FROM jobs")}
            matches = {row[0] for row in store._connection.execute("SELECT job_id FROM job_matches")}
            indexed = store._connection.execute("SELECT count(*) FROM jobs_fts").fetchone()[0]
        self.assertEqual(removed, 3)
        self.assertEqual(remaining, {recent, saved, noted, drafted, mine})
        self.assertEqual(matches, remaining)
        self.assertEqual(indexed, 5)
        self.assertNotIn(expired, remaining)

    def test_refresh_reports_cleanup(self):
        old = self.add("old", source="remotive")
        self.age(old, 45)
        settings = service.read_settings(self.folder)
        settings["sources"] = []
        service.save_settings(self.folder, settings)
        self.assertEqual(service.collect(self.folder)["removed"], 1)


class CollectEverythingTests(Fixture):
    def test_job_sites_keep_every_vacancy_and_tag_the_searches_it_answers(self):
        found = [{"title": "Barista", "company": "Cafe", "location": "Berlin", "description": "Coffee", "source": "remotive",
                  "source_id": "1", "source_url": "https://remotive.example/1", "countries": ["DE"]},
                 {"title": "Data Scientist", "company": "Lab", "location": "Berlin", "description": "Python", "source": "remotive",
                  "source_id": "2", "source_url": "https://remotive.example/2", "countries": ["DE"]},
                 {"title": "Data Scientist", "company": "Far", "location": "Remote (China)", "description": "x", "source": "remotive",
                  "source_id": "3", "source_url": "https://remotive.example/3", "countries": []}]
        with patch.dict(market._SEARCHERS, {"remotive": lambda query, country, limit, **_: [dict(job) for job in found]}), \
                patch("norway_job_agent.market.time.sleep"):
            run = market.search_provider("remotive", ["Data Scientist"], "DE", broad=True)
        self.assertEqual([job["source_id"] for job in run.jobs], ["1", "2"])
        self.assertEqual([job["raw_json"]["matched_queries"] for job in run.jobs], [[], ["Data Scientist"]])
        self.assertEqual(run.queries[0], "Data Scientist")
        self.assertIn("category:data", run.queries)

    def test_arbeitsagentur_reads_deep_once_then_only_new_days_per_search(self):
        calls = []

        def search(query, country, limit, **options):
            calls.append((query, options.get("since_days")))
            return []
        state = {"arbeitsagentur_synced": {"Informatik": (datetime.now() - timedelta(days=3)).strftime("%Y-%m-%d")}}
        with patch.dict(market._SEARCHERS, {"arbeitsagentur": search}), patch("norway_job_agent.market.time.sleep"):
            market.search_provider("arbeitsagentur", ["Data Scientist"], "DE", broad=True, state=state)
        since = dict(calls)
        self.assertEqual(since["Informatik"], 4)
        self.assertEqual(since["Data Scientist"], 60)
        self.assertEqual(state["arbeitsagentur_synced"]["Software"], datetime.now().strftime("%Y-%m-%d"))

    def test_a_spent_time_budget_keeps_what_was_read_and_continues_next_time(self):
        def search(query, country, limit, **options):
            raise market._Budget([{"title": "Data Scientist", "company": "A", "location": "Berlin", "description": "",
                                   "source": "arbeitsagentur", "source_id": "x1", "source_url": "https://ba.example/x1",
                                   "countries": ["DE"]}])
        state = {}
        with patch.dict(market._SEARCHERS, {"arbeitsagentur": search}):
            run = market.search_provider("arbeitsagentur", ["Data Scientist"], "DE", broad=True, state=state)
        self.assertEqual(len(run.jobs), 1)
        self.assertTrue(run.has_more)
        self.assertNotIn("Data Scientist", state["arbeitsagentur_synced"])

    def test_dou_and_remotive_browse_whole_categories(self):
        with patch("norway_job_agent.market._rss", return_value=[]) as rss:
            market._dou("category:Data Science", "UA", 50)
            market._dou("python", "UA", 50)
        self.assertIn("category=Data+Science", rss.call_args_list[0].args[0])
        self.assertIn("search=python", rss.call_args_list[1].args[0])
        with patch("norway_job_agent.market._json", return_value={"jobs": []}) as fetch:
            market._remotive("category:software-dev", "DE", 50)
        self.assertIn("category=software-dev", fetch.call_args.args[0])

    def test_nav_everything_loads_every_unstored_advert_with_matches_first(self):
        from test_market_search import U1, U2, U3, feed_page, nav_detail
        with patch("norway_job_agent.nav_index.sync", REAL_SYNC), \
                patch("norway_job_agent.nav._get_token", return_value=("token", True)):
            page = feed_page([(U1, "ACTIVE", "Barnehagelærer", "v1"), (U2, "ACTIVE", "Data Scientist", "v1"),
                              (U3, "ACTIVE", "Sykepleier", "v1")])
            with patch("norway_job_agent.nav._fetch_nav", return_value=page):
                nav_index.sync(self.folder)
            candidates = nav_index.find(self.folder, ["Data Scientist"], everything=True)
            self.assertEqual([item["uuid"] for item in candidates][0], U2)
            self.assertEqual(len(candidates), 3)
            details = {uuid: nav_detail(uuid, title) for uuid, title in ((U1, "Barnehagelærer"), (U2, "Data Scientist"), (U3, "Sykepleier"))}
            with patch("norway_job_agent.nav._fetch_nav", side_effect=lambda url, token, headers=None: details[url.rsplit("/", 1)[1]]):
                loaded = nav_index.hydrate(self.folder, candidates)
        self.assertEqual(len(loaded["jobs"]), 3)
        tags = {job["source_id"]: job["raw_json"]["matched_queries"] for job in loaded["jobs"]}
        self.assertEqual(tags[U2], ["Data Scientist"])
        self.assertEqual(tags[U1], [])
        nav_index.mark_hydrated(self.folder, loaded["marks"])
        self.assertEqual(nav_index.find(self.folder, ["Data Scientist"], everything=True, known={U1, U2, U3}), [])

    def test_each_site_searches_with_its_countrys_preferences_in_one_refresh(self):
        profile = service.read_profile(self.folder)
        profile["country_preferences"] = {"DE": {"target_roles": ["Datenwissenschaftler"]}}
        service.save_profile(self.folder, profile)
        settings = service.read_settings(self.folder)
        settings.update(active_country="NO", sources=[])
        for name in ("arbeitsagentur", "djinni", "remotive"):
            settings["market"]["enabled"][name] = True
        service.save_settings(self.folder, settings)
        calls = {}

        def search(name, queries, country, **options):
            calls[name] = (country, queries)
            return market.ProviderRun(name)
        with patch("norway_job_agent.market.search_provider", side_effect=search):
            service.collect(self.folder)
        # Norway is the view; Germany and Ukraine are still collected.
        self.assertEqual(calls["arbeitsagentur"], ("DE", ["Datenwissenschaftler", "Data Engineer"]))
        self.assertEqual(calls["djinni"][0], "UA")
        self.assertEqual(calls["remotive"][0], "")

    def test_collect_everything_can_be_switched_off(self):
        settings = service.read_settings(self.folder)
        self.assertTrue(settings["market"]["collect_everything"])
        settings["market"]["collect_everything"] = False
        settings["market"]["enabled"]["remotive"] = True
        settings["sources"] = []
        service.save_settings(self.folder, settings)
        with patch("norway_job_agent.market.search_provider", return_value=market.ProviderRun("remotive")) as search:
            service.collect(self.folder)
        self.assertFalse(search.call_args.kwargs["broad"])


if __name__ == "__main__":
    unittest.main()
