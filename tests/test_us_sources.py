"""US tech employer boards and Hacker News "Who is hiring?", offline."""

import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from norway_job_agent import employer_pack, hackernews, market, service, sources
from norway_job_agent.company_catalog import company_catalog
from norway_job_agent.company_sources import _workday_parts
from norway_job_agent.storage import JobStore
from support import offline_collection


def setUpModule():
    offline_collection()


GREENHOUSE = {"type": "greenhouse", "board": "acme", "name": "Acme"}
LEVER = {"type": "lever", "board": "lamp", "name": "Lamp"}
WORKDAY = {"type": "workday", "url": "https://bigco.wd5.myworkdayjobs.com/en-US/External", "name": "BigCo"}
CONFIGURED = {"type": "ashby", "board": "mine", "name": "Mine"}


def posting(kind, identifier, location, title="Data Scientist"):
    return {"title": title, "company": "Fixture Inc", "location": location, "description": "Python and statistics",
            "source": kind, "source_id": identifier, "source_url": f"https://jobs.example/{identifier}",
            "apply_url": f"https://jobs.example/{identifier}", "employment_type": "", "published_at": "", "deadline": ""}


def batch(source, jobs, *, error=None):
    if error:
        return {"result": {"source": source, "ok": False, "error": error}, "jobs": None, "snapshot": None, "kind": None}
    snapshot = None
    if source["type"] in {"greenhouse", "lever", "ashby"}:
        prefix = f"{source['board']}:" if source["type"] != "lever" else f"global:{source['board']}:"
        snapshot = (prefix, [job["source_id"] for job in jobs])
    return {"result": {"source": source, "fetched": len(jobs)}, "jobs": jobs, "snapshot": snapshot, "kind": source["type"]}


class EmployerPackFileTests(unittest.TestCase):
    def test_every_built_in_board_is_valid_unique_and_not_already_in_the_directory(self):
        entries = employer_pack.boards()
        self.assertGreater(len(entries), 300)
        keys = [employer_pack.source_key(entry) for entry in entries]
        self.assertEqual(len(keys), len(set(keys)))
        directory = employer_pack.configured_keys([entry["source"] for entry in company_catalog() if entry.get("source")])
        self.assertFalse(set(keys) & directory)
        for entry in entries:
            with self.subTest(entry=entry["name"]):
                self.assertTrue(entry["name"].strip())
                if entry["type"] == "workday":
                    _workday_parts(entry["url"])
                else:
                    self.assertIn(entry["type"], {"greenhouse", "lever", "ashby"})
                    sources._board(entry["board"])

    def test_the_job_site_is_listed_on_by_default_for_the_usa(self):
        catalog = {site["id"]: site for site in market.provider_catalog()}
        self.assertEqual(catalog["us_employers"]["countries"], ["US"])
        # On by default for real users (tests switch every job site off).
        self.assertFalse(market.PROVIDERS["us_employers"].opt_in or market.PROVIDERS["hackernews"].opt_in)


class EmployerPackCollectionTests(unittest.TestCase):
    def setUp(self):
        patcher = patch("norway_job_agent.employer_pack.boards", return_value=[GREENHOUSE, LEVER, WORKDAY, CONFIGURED])
        patcher.start()
        self.addCleanup(patcher.stop)
        self.state = {}
        self.calls = []

    def fetch(self, source):
        self.calls.append(source)
        if source["type"] == "greenhouse":
            return batch(source, [posting("greenhouse", "acme:1", "Austin, TX"), posting("greenhouse", "acme:2", "Berlin, Germany"),
                                  posting("greenhouse", "acme:3", "London, United Kingdom")])
        if source["type"] == "lever":
            return batch(source, [], error="Could not download from api.lever.co; check connectivity or try again later.")
        return batch(source, [posting("workday", f"bigco:External:{source['search_text']}", "Seattle, WA")])

    def search(self, **options):
        with patch("norway_job_agent.collection.fetch_source", side_effect=self.fetch):
            return market.search_provider("us_employers", ["Data Scientist", "ML Engineer", "Analyst"], "US",
                                          state=self.state, skip_boards=frozenset({"ashby:mine"}), **options)

    def test_reads_due_boards_keeps_jobs_in_your_countries_and_skips_configured_ones(self):
        run = self.search()
        self.assertNotIn("mine", [call.get("board") for call in self.calls])
        # Workday has no whole-board feed: the first two roles are searched.
        self.assertEqual([call["search_text"] for call in self.calls if call["type"] == "workday"], ["Data Scientist", "ML Engineer"])
        self.assertTrue(all(call["max_jobs"] == employer_pack.WORKDAY_JOBS for call in self.calls if call["type"] == "workday"))
        self.assertEqual({job["source_id"]: job["countries"] for job in run.jobs},
                         {"acme:1": ["US"], "acme:2": ["DE"], "bigco:External:Data Scientist": ["US"], "bigco:External:ML Engineer": ["US"]})
        self.assertEqual(run.snapshots, [("greenhouse", "acme:", ["acme:1", "acme:2", "acme:3"])])
        self.assertEqual(run.errors, ["Lamp: Could not download from api.lever.co; check connectivity or try again later."])
        report = run.report()
        self.assertTrue(report["ok"])
        self.assertIn("Read 2 of 4 US employer boards", report["coverage"])
        self.assertIn("1 are already among your sources", report["coverage"])

    def test_a_board_is_read_at_most_once_per_interval_and_a_failed_one_is_retried(self):
        self.search()
        self.calls.clear()
        run = self.search()
        self.assertEqual([call["type"] for call in self.calls], ["lever"])  # only the failed board
        self.assertIn("2 boards were read in the last", run.report()["coverage"])
        earlier = (datetime.now(timezone.utc) - timedelta(hours=employer_pack.REREAD_HOURS + 1)).isoformat()
        self.state[employer_pack.STATE_KEY] = {key: earlier for key in self.state[employer_pack.STATE_KEY]}
        self.calls.clear()
        self.search()
        self.assertEqual(sorted(call["type"] for call in self.calls), ["greenhouse", "lever", "workday", "workday"])

    def test_a_refresh_out_of_time_continues_next_time(self):
        run = self.search(seconds=-1)
        self.assertEqual(self.calls, [])
        self.assertTrue(run.has_more)
        self.assertIn("next refresh continues", run.report()["coverage"])
        self.assertEqual(self.state[employer_pack.STATE_KEY], {})


class EmployerPackRefreshTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name)
        service.initialize(self.folder)
        settings = service.read_settings(self.folder)
        settings["sources"] = [{"type": "ashby", "board": "mine", "name": "Mine", "countries": ["US"]}]
        settings["market"]["enabled"]["us_employers"] = True
        service.save_settings(self.folder, settings)
        patcher = patch("norway_job_agent.employer_pack.boards", return_value=[GREENHOUSE, CONFIGURED])
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_a_complete_board_retires_postings_it_no_longer_lists(self):
        for identifier in ("acme:1", "acme:2"):
            service.import_vacancy(self.folder, posting("greenhouse", identifier, "Austin, TX"))

        def fetch(source):
            if source["type"] == "ashby":
                return batch(source, [])
            return batch(source, [posting("greenhouse", "acme:1", "Austin, TX")])
        with patch("norway_job_agent.collection.fetch_source", side_effect=fetch) as fetched:
            report = service.collect(self.folder)
        # The configured board runs once, as your source, not again in the pack.
        self.assertEqual([call.args[0]["board"] for call in fetched.call_args_list].count("mine"), 1)
        row = next(row for row in report["sources"] if row["source"].get("provider") == "us_employers")
        self.assertEqual((row["updated"], row["withdrawn"]), (1, 1))
        with JobStore(self.folder / "vacancies.db") as store:
            active = dict(store._connection.execute("SELECT source_id, is_active FROM jobs WHERE source = 'greenhouse'").fetchall())
        self.assertEqual(active, {"acme:1": 1, "acme:2": 0})

    def test_a_live_job_site_search_never_reads_every_employer_board(self):
        names = []

        def search(name, *args, **kwargs):
            names.append(name)
            return market.ProviderRun(name)
        with patch("norway_job_agent.market.search_provider", side_effect=search):
            service.market_search(self.folder, "Data Scientist")
        self.assertNotIn("us_employers", names)

    def test_workday_adverts_unseen_for_a_month_leave_discovery(self):
        identifier = service.import_vacancy(self.folder, posting("workday", "bigco:External:1", "Seattle, WA"))[0]
        stale = (datetime.now(timezone.utc) - timedelta(days=service.STALE_BOARD_DAYS + 1)).isoformat()
        with JobStore(self.folder / "vacancies.db") as store:
            store._connection.execute("UPDATE jobs SET last_seen_at = ? WHERE id = ?", (stale, identifier))
            store._connection.commit()
        self.assertEqual(service.vacancies(self.folder), [])


NOW = datetime(2026, 10, 19, tzinfo=timezone.utc)


def post(identifier, header, body="<p>We build things. Apply at <a href=\"https:&#x2F;&#x2F;acme.example&#x2F;jobs\">our site</a></p>",
         days=3, author="founder"):
    return {"id": identifier, "author": author, "text": header + body,
            "created_at": (NOW - timedelta(days=days)).isoformat().replace("+00:00", "Z")}


class HackerNewsTests(unittest.TestCase):
    def parse(self, header, **options):
        return hackernews.parse_post(post(101, header, **options), NOW)

    def test_the_first_line_gives_company_role_and_country(self):
        job = self.parse("PrairieLearn (Remote US) — Full-Stack Software Engineer — TypeScript &#x2F; React")
        self.assertEqual((job["company"], job["title"], job["countries"]), ("PrairieLearn", "Full-Stack Software Engineer", ["US"]))
        self.assertEqual(job["source_url"], "https://news.ycombinator.com/item?id=101")
        self.assertEqual(job["raw_json"]["external_apply_url"], "https://acme.example/jobs")
        job = self.parse("Shepherd (Series B) | ONSITE | San Francisco, CA &amp; New York City, NY | Full-time")
        self.assertEqual((job["title"], job["countries"], job["employment_type"]), ("Multiple roles", ["US"], "Full-time"))

    def test_regions_and_listed_countries(self):
        cases = {
            "Snout | Multiple Engineering Roles | Remote US or Ontario, Canada": ["US"],
            "PAGNOS | Cloud Engineer | REMOTE (Germany) | Full-time": ["DE"],
            "Mainmatter (https:&#x2F;&#x2F;mainmatter.com) | Lead Rust Engineer | REMOTE (EU)": ["NO", "DE", "UA"],
            "Fastly | Software Engineers | US, UK, EU, APAC ONSITE PREFERRED": ["US", "NO", "DE", "UA"],
            "Starbridge | Senior Product Engineer | NYC or Remote": ["US"],
            "Flexibits | Android Engineer | Remote": ["NO", "US", "DE", "UA"],
        }
        for header, countries in cases.items():
            with self.subTest(header=header):
                job = self.parse(header)
                self.assertEqual(sorted(job["countries"]), sorted(countries))
        self.assertEqual(self.parse("Mainmatter (https:&#x2F;&#x2F;mainmatter.com) | Lead Rust Engineer | REMOTE (EU)")["company"], "Mainmatter")
        self.assertEqual(self.parse("Flexibits | Android Engineer | Remote")["raw_json"]["remote_eligibility"], "Not stated")

    def test_posts_outside_supported_countries_or_old_ones_are_skipped(self):
        for header in ("Neon | DevOps Engineer | Utrecht, The Netherlands | HYBRID",
                       "OpenRent | Multiple roles | ONSITE+PART REMOTE",
                       "Acme | Engineer | London, UK | REMOTE",
                       "Just a question about hiring"):
            with self.subTest(header=header):
                self.assertIsNone(self.parse(header))
        self.assertIsNone(self.parse("Acme | Engineer | Remote (US)", days=60))
        self.assertIsNone(hackernews.parse_post(post(102, "Acme | Engineer | Remote (US)", author="whoishiring"), NOW))
        self.assertIsNone(hackernews.parse_post({"id": 103, "author": "x", "text": None, "created_at": "2026-10-18T00:00:00Z"}, NOW))

    def test_reads_the_latest_two_hiring_threads_not_who_wants_to_be_hired(self):
        threads = {"hits": [{"objectID": "3", "title": "Ask HN: Who wants to be hired? (October 2026)"},
                            {"objectID": "2", "title": "Ask HN: Who is hiring? (October 2026)"},
                            {"objectID": "1", "title": "Ask HN: Who is hiring? (September 2026)"},
                            {"objectID": "0", "title": "Ask HN: Who is hiring? (August 2026)"}]}
        items = {"2": {"children": [post(11, "Acme | Data Scientist | Remote (US)"), post(12, "Neon | Engineer | Utrecht, The Netherlands")]},
                 "1": {"children": [post(11, "Acme | Data Scientist | Remote (US)"), post(13, "Beta | ML Engineer | Berlin, Germany")]}}
        requested = []

        def reply(url):
            requested.append(url)
            return threads if "search_by_date" in url else items[url.rsplit("/", 1)[1]]
        with patch("norway_job_agent.hackernews._json", side_effect=reply):
            jobs = hackernews.fetch(NOW)
        self.assertEqual([job["source_id"] for job in jobs], ["11", "13"])
        self.assertEqual([url.rsplit("/", 1)[1] for url in requested[1:]], ["2", "1"])

    def test_job_site_run_tags_and_keeps_posts(self):
        with patch("norway_job_agent.hackernews.fetch", return_value=[self.parse("Acme | Data Scientist | Remote (US)")]):
            run = market.search_provider("hackernews", ["Data Scientist"], "")
        self.assertEqual(len(run.jobs), 1)
        self.assertEqual(run.jobs[0]["raw_json"]["matched_queries"], ["Data Scientist"])


if __name__ == "__main__":
    unittest.main()
