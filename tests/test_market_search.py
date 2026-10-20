"""Whole-market job sites, LinkedIn's public search and the NAV index, offline."""

import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from norway_job_agent import linkedin_public, market, nav_index, service, sources
from norway_job_agent.profile import match_job
from norway_job_agent.sources import SourceError
from norway_job_agent.storage import JobStore
from support import REAL_SYNC, offline_collection


def setUpModule():
    offline_collection()


def board_job(source="arbeitsagentur", identifier="1", **changes):
    return {"title": "DevOps Engineer (m/w/d)", "company": "Fixture GmbH", "location": "Berlin", "description": "",
            "source": source, "source_id": identifier, "source_url": f"https://jobs.example/{source}/{identifier}",
            "apply_url": f"https://jobs.example/{source}/{identifier}", "countries": ["DE"], **changes}


class RelevanceTests(unittest.TestCase):
    def test_multi_word_search_keeps_role_title_whose_advert_has_the_other_word(self):
        self.assertTrue(market.matches_query(board_job("djinni", description="Linux servers and Kubernetes"), "Linux DevOps"))
        self.assertFalse(market.matches_query(board_job("djinni", description="Windows servers only"), "Linux DevOps"))
        self.assertFalse(market.matches_query(board_job("djinni", title="Sales Manager", description="Linux DevOps"), "Linux DevOps"))

    def test_sites_without_advert_text_get_the_benefit_of_their_full_text_search(self):
        self.assertTrue(market.matches_query(board_job("arbeitsagentur"), "Linux DevOps"))
        self.assertFalse(market.matches_query(board_job("arbeitsagentur", title="Lagerist"), "Linux DevOps"))

    def test_role_aliases_and_gender_markers_count_as_the_role(self):
        self.assertTrue(market.matches_query(board_job("remotive", title="Machine Learning Engineer", description="x"), "ML Engineer"))
        self.assertTrue(market.matches_query(board_job("dou", title="Data Scientist (m/w/d)", description="x"), "Data Scientist"))

    def test_queries_put_saved_searches_first_and_skip_alias_spellings(self):
        profile = {"target_roles": ["Machine Learning Engineer", "ML Engineer", "Data Scientist"], "related_roles": ["Data Analyst"],
                   "country_preferences": {"DE": {"target_roles": ["Data Scientist"]}}}
        market_settings = market.validate_market_settings({"searches": ["Linux DevOps", "linux  devops"]})
        self.assertEqual(market.search_queries(profile, market_settings),
                         ["Linux DevOps", "Machine Learning Engineer", "Data Scientist", "Data Analyst"])
        self.assertEqual(market.search_queries(profile, {**market_settings, "include_related_roles": False}, "DE"),
                         ["Linux DevOps", "Data Scientist"])

    def test_settings_default_on_except_linkedin_which_needs_the_terms_notice(self):
        # Tests switch every site off; the shipped defaults follow opt_in.
        self.assertTrue(all(not provider.opt_in for name, provider in market.PROVIDERS.items() if name != "linkedin"))
        self.assertTrue(market.PROVIDERS["linkedin"].opt_in)
        self.assertFalse(market.validate_market_settings(None)["enabled"]["linkedin"])
        with self.assertRaisesRegex(ValueError, "terms notice"):
            market.validate_market_settings({"enabled": {"linkedin": True}})
        accepted = market.validate_market_settings({"enabled": {"linkedin": True}, "linkedin_terms_accepted": True})
        self.assertTrue(accepted["enabled"]["linkedin"])
        with self.assertRaises(ValueError):
            market.validate_market_settings({"enabled": {"indeed": True}})


class ProviderParsingTests(unittest.TestCase):
    def test_arbeitsagentur_results_become_german_vacancies_with_lazy_text(self):
        page = {"maxErgebnisse": 1, "ergebnisliste": [{
            "stellenangebotsTitel": "Linux DevOps Spezialist (m/w/d)", "firma": "Fixture GmbH", "referenznummer": "10001-1003794037-S",
            "arbeitszeitVollzeit": True, "veroeffentlichungszeitraum": {"von": "2026-10-05"},
            "stellenlokationen": [{"adresse": {"ort": "Berlin", "region": "BERLIN", "land": "DEUTSCHLAND"}}]}]}
        with patch("norway_job_agent.market._json", return_value=page) as fetch:
            run = market.search_provider("arbeitsagentur", ["Linux DevOps"], "DE")
        self.assertEqual(fetch.call_args.kwargs["headers"], {"X-API-Key": "jobboerse-jobsuche"})
        job = run.jobs[0]
        self.assertEqual((job["title"], job["company"], job["countries"]), ("Linux DevOps Spezialist (m/w/d)", "Fixture GmbH", ["DE"]))
        self.assertEqual(job["source_url"], "https://www.arbeitsagentur.de/jobsuche/jobdetail/10001-1003794037-S")
        self.assertTrue(market.needs_details(job))
        self.assertTrue(run.report()["ok"])

    def test_dou_titles_split_into_role_employer_and_place(self):
        items = [{"title": "Middle DevOps Engineer в Patrianna Limited, віддалено", "description": "<p>Linux</p>",
                  "link": "https://jobs.dou.ua/companies/patrianna/vacancies/331999/?utm_source=jobsrss", "pubDate": "Sat, 17 Oct 2026 17:40:22 +0300"},
                 {"title": "DevOps Engineer в Fixture, від $1200, Львів", "description": "Linux",
                  "link": "https://jobs.dou.ua/companies/fixture/vacancies/332000/", "pubDate": ""}]
        with patch("norway_job_agent.market._rss", return_value=items):
            run = market.search_provider("dou", ["DevOps"], "UA")
        first, second = run.jobs
        self.assertEqual((first["title"], first["company"], first["location"]), ("Middle DevOps Engineer", "Patrianna Limited", "Remote, Ukraine"))
        self.assertEqual(first["source_url"], "https://jobs.dou.ua/companies/patrianna/vacancies/331999/")
        self.assertEqual((second["company"], second["location"]), ("Fixture", "Львів, Ukraine"))

    def test_remote_eligibility_is_read_only_from_explicit_regions(self):
        self.assertEqual(market._remote_countries("Europe"), ["NO", "DE", "UA"])
        self.assertEqual(market._remote_countries("USA only"), ["US"])
        self.assertEqual(market._remote_countries("Worldwide"), ["NO", "US", "DE", "UA"])
        self.assertEqual(market._remote_countries("China"), [])

    def test_one_failing_query_does_not_hide_the_others(self):
        def search(query, country, limit):
            if query == "broken":
                raise SourceError("temporary")
            return [board_job("remotive", title="Data Scientist", description="Python", countries=["DE"])]
        with patch.dict(market._SEARCHERS, {"remotive": search}), patch("norway_job_agent.market.time.sleep"):
            run = market.search_provider("remotive", ["broken", "Data Scientist"], "DE")
        self.assertEqual(len(run.jobs), 1)
        self.assertIn("broken: temporary", run.errors[0])
        self.assertTrue(run.report()["ok"])

    def test_other_country_results_are_dropped(self):
        found = [board_job("remotive", title="Data Scientist", description="x", countries=["US"])]
        with patch.dict(market._SEARCHERS, {"remotive": lambda q, c, l: found}):
            self.assertEqual(market.search_provider("remotive", ["Data Scientist"], "DE").jobs, [])


LINKEDIN_SEARCH = """<!DOCTYPE html><li><div class="base-card" data-entity-urn="urn:li:jobPosting:4290000001">
<a class="base-card__full-link" href="https://no.linkedin.com/jobs/view/data-scientist-at-fixture-4290000001?trk=x"></a>
<h3 class="base-search-card__title">
  Senior Data Scientist
</h3><h4 class="base-search-card__subtitle"><a href="#">Fixture &amp; Co</a></h4>
<span class="job-search-card__location">Oslo, Oslo, Norway</span>
<time class="job-search-card__listdate" datetime="2026-10-01">2 weeks ago</time></div></li>
<li><div data-entity-urn="urn:li:jobPosting:4290000002"><h3 class="base-search-card__title">ML Engineer</h3>
<h4 class="base-search-card__subtitle">Remote Co</h4><span class="job-search-card__location">European Economic Area</span></div></li>"""
LINKEDIN_DETAIL = """<div class="description__text"><div class="show-more-less-html__markup relative">
<p>Build <strong>Python</strong> models.</p><ul><li>3+ years of experience</li></ul></div></div>
<h3 class="description__job-criteria-subheader">Seniority level</h3><span class="description__job-criteria-text">Mid-Senior level</span>
<h3 class="description__job-criteria-subheader">Employment type</h3><span class="description__job-criteria-text">Full-time</span>
<code id="applyUrl" style="display: none"><!--"https://www.linkedin.com/jobs/view/externalApply/4290000001?url=https%3A%2F%2Fboards.greenhouse.io%2Ffixture%2Fjobs%2F123&urlHash=x"--></code>"""


class LinkedInTests(unittest.TestCase):
    def test_search_cards_parse_without_login(self):
        first, second = linkedin_public.parse_search(LINKEDIN_SEARCH, "NO")
        self.assertEqual((first["title"], first["company"], first["countries"]), ("Senior Data Scientist", "Fixture & Co", ["NO"]))
        self.assertEqual(first["source_url"], "https://www.linkedin.com/jobs/view/4290000001")
        self.assertEqual(first["published_at"], "2026-10-01")
        # Listed in a Norway search, it is available there (often remote).
        self.assertEqual(second["countries"], ["NO"])

    def test_details_give_text_criteria_and_the_employer_application(self):
        details = linkedin_public.parse_details(LINKEDIN_DETAIL)
        self.assertIn("Build Python models.", details["description"])
        self.assertIn("3+ years of experience", details["description"])
        self.assertEqual((details["employment_type"], details["seniority"]), ("Full-time", "Mid-Senior level"))
        self.assertEqual(details["external_apply_url"], "https://boards.greenhouse.io/fixture/jobs/123")

    def test_refusal_starts_a_cooldown_and_nothing_is_retried(self):
        state = {}
        with patch("norway_job_agent.linkedin_public._request_retrying", return_value=(429, {}, b"")) as request, \
                patch("norway_job_agent.linkedin_public.time.sleep"):
            with self.assertRaisesRegex(SourceError, "slow down"):
                linkedin_public._get("https://www.linkedin.com/jobs-guest/x", state)
            with self.assertRaisesRegex(SourceError, "paused until"):
                linkedin_public._get("https://www.linkedin.com/jobs-guest/y", state)
        self.assertEqual(request.call_count, 1)
        self.assertGreater(datetime.fromisoformat(state["linkedin_cooldown_until"]), datetime.now(timezone.utc))

    def test_each_run_has_a_small_request_budget(self):
        state = {"_linkedin_requests": linkedin_public.MAX_REQUESTS_PER_RUN}
        with patch("norway_job_agent.linkedin_public._request_retrying") as request:
            with self.assertRaisesRegex(SourceError, "budget"):
                linkedin_public._get("https://www.linkedin.com/jobs-guest/x", state)
        request.assert_not_called()

    def test_search_stops_at_cooldown_and_reports_it(self):
        state = {"linkedin_cooldown_until": (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat()}
        run = market.search_provider("linkedin", ["Data Scientist"], "NO", state=state)
        self.assertEqual(run.jobs, [])
        self.assertIn("slow down", run.stopped)

    def test_linkedin_is_reachable_only_through_the_opt_in_module(self):
        with self.assertRaisesRegex(SourceError, "manual-import"):
            sources._url_parts("https://www.linkedin.com/jobs/view/1")
        sources._url_parts("https://www.linkedin.com/jobs/view/1", allow_restricted=True)
        with self.assertRaisesRegex(SourceError, "header"):
            sources._request_once("https://example.com/", headers={"User-Agent": "Mozilla/5.0"})


def feed_page(entries, next_url=None, url="https://pam-stilling-feed.nav.no/api/v1/feed/p1"):
    items = [{"url": f"/api/v1/feed/entry/{uuid}", "_feed_entry": {"uuid": uuid, "status": status, "title": title,
              "businessName": "Fixture AS", "municipal": "OSLO", "sistEndret": modified}}
             for uuid, status, title, modified in entries]
    return 200, {"etag": "e", "last-modified": "Sat, 17 Oct 2026 10:00:00 GMT"}, json.dumps(
        {"items": items, "next_url": next_url, "feed_url": url}).encode(), url


def nav_detail(uuid, title):
    return 200, {}, json.dumps({"uuid": uuid, "status": "ACTIVE", "ad_content": {
        "uuid": uuid, "title": title, "employer": {"name": "Fixture AS"}, "description": "<p>Linux og Kubernetes</p>",
        "workLocations": [{"city": "Oslo", "country": "Norge"}]}}).encode(), ""


U1, U2, U3 = ("11111111-1111-4111-8111-111111111111", "22222222-2222-4222-8222-222222222222",
              "33333333-3333-4333-8333-333333333333")


class NavIndexTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name)
        for patcher in (patch("norway_job_agent.nav_index.sync", REAL_SYNC),
                        patch("norway_job_agent.nav._get_token", return_value=("fixture-token", True))):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_sync_reads_every_page_to_the_tail_not_five(self):
        pages = [feed_page([(f"{index:08d}-1111-4111-8111-111111111111", "ACTIVE", f"Role {index}", "v1")],
                           next_url=f"/api/v1/feed/p{index + 1}" if index < 11 else None,
                           url=f"https://pam-stilling-feed.nav.no/api/v1/feed/p{index}") for index in range(12)]
        with patch("norway_job_agent.nav._fetch_nav", side_effect=pages):
            report = nav_index.sync(self.folder)
        self.assertEqual((report["pages"], report["active"], report["has_more"]), (12, 12, False))

    def test_interrupted_sync_resumes_from_the_last_saved_page(self):
        first = feed_page([(U1, "ACTIVE", "DevOps Engineer", "v1")], next_url="/api/v1/feed/p2")
        with patch("norway_job_agent.nav._fetch_nav", side_effect=[first, SourceError("dropped")]):
            with self.assertRaisesRegex(SourceError, "dropped"):
                nav_index.sync(self.folder)
        with nav_index.NavIndex(self.folder) as index:
            self.assertEqual(index.cursor()["url"], "https://pam-stilling-feed.nav.no/api/v1/feed/p2")
            self.assertEqual(index.counts()["active"], 1)

    def test_a_feed_that_stops_answering_still_loads_adverts_already_indexed(self):
        # A busy all-country refresh once lost every NAV advert because one
        # feed page did not arrive.
        service.initialize(self.folder)
        first = feed_page([(U1, "ACTIVE", "Data Scientist", "v1")], next_url="/api/v1/feed/p2")
        dropped = sources._TransientSourceError("Could not download from NAV; check connectivity or try again later.")
        with patch("norway_job_agent.nav._fetch_nav", side_effect=[first, dropped, nav_detail(U1, "Data Scientist")]):
            report = service.collect(self.folder)
        nav = report["sources"][0]
        self.assertTrue(nav["ok"], nav)
        self.assertEqual((nav["created"], nav["has_more"]), (1, True))
        self.assertIn("stopped answering", nav["coverage"])
        with nav_index.NavIndex(self.folder) as index:
            self.assertEqual(index.cursor()["url"], "https://pam-stilling-feed.nav.no/api/v1/feed/p2")
        # With nothing read at all the error is still reported.
        with patch("norway_job_agent.nav._fetch_nav", side_effect=dropped), \
                self.assertRaisesRegex(SourceError, "Could not download from NAV"):
            nav_index.sync(self.folder)

    def test_failed_advert_downloads_are_reported_and_retried_next_refresh(self):
        page = feed_page([(U1, "ACTIVE", "DevOps Engineer", "v1"), (U2, "ACTIVE", "DevOps Lead", "v1")])
        with patch("norway_job_agent.nav._fetch_nav", return_value=page):
            nav_index.sync(self.folder)
        candidates = nav_index.find(self.folder, ["DevOps"])
        dropped = sources._TransientSourceError("Could not download from NAV; check connectivity or try again later.")

        def detail(url, token, headers=None):
            if url.endswith(U2):
                raise dropped
            return nav_detail(U1, "DevOps Engineer")
        with patch("norway_job_agent.nav._fetch_nav", side_effect=detail):
            loaded = nav_index.hydrate(self.folder, candidates)
        self.assertEqual([job["source_id"] for job in loaded["jobs"]], [U1])
        self.assertEqual((loaded["failed"], loaded["remaining"]), (1, 1))
        self.assertIn("Could not download", loaded["failure"])
        nav_index.mark_hydrated(self.folder, loaded["marks"])
        self.assertEqual([item["uuid"] for item in nav_index.find(self.folder, ["DevOps"])], [U2])

    def test_rate_limiting_stops_advert_downloads_for_this_run(self):
        entries = [(f"{index:08d}-1111-4111-8111-111111111111", "ACTIVE", f"DevOps {index}", "v1") for index in range(300)]
        with patch("norway_job_agent.nav._fetch_nav", return_value=feed_page(entries)):
            nav_index.sync(self.folder)
        candidates = nav_index.find(self.folder, ["DevOps"])
        limited = SourceError("NAV is rate limiting requests; retry later. The checkpoint was not advanced.")
        with patch("norway_job_agent.nav._fetch_nav", side_effect=limited) as request:
            loaded = nav_index.hydrate(self.folder, candidates)
        self.assertEqual(request.call_count, 120)  # one batch, not all 300
        self.assertEqual((loaded["failed"], loaded["remaining"]), (120, 300))

    def test_any_new_search_matches_all_indexed_titles_and_confirms_in_full_text(self):
        page = feed_page([(U1, "ACTIVE", "DevOps Engineer", "v1"), (U2, "ACTIVE", "Barnehagelærer", "v1"),
                          (U3, "ACTIVE", "Senior DevOps-utvikler", "v1")])
        with patch("norway_job_agent.nav._fetch_nav", return_value=page):
            nav_index.sync(self.folder)
        candidates = nav_index.find(self.folder, ["Linux DevOps"])
        self.assertEqual({item["uuid"] for item in candidates}, {U1, U3})
        details = {U1: nav_detail(U1, "DevOps Engineer"), U3: (200, {}, json.dumps({"uuid": U3, "status": "INACTIVE"}).encode(), "")}
        with patch("norway_job_agent.nav._fetch_nav", side_effect=lambda url, token, headers=None: details[url.rsplit("/", 1)[1]]):
            loaded = nav_index.hydrate(self.folder, candidates)
        self.assertEqual([job["source_id"] for job in loaded["jobs"]], [U1])
        self.assertEqual(loaded["withdrawn"], [U3])
        # After the collector stores them, the same search does not download again.
        nav_index.mark_hydrated(self.folder, loaded["marks"])
        nav_index.mark_inactive(self.folder, loaded["withdrawn"])
        self.assertEqual(nav_index.find(self.folder, ["Linux DevOps"]), [])
        # A different search re-checks the advert once.
        self.assertEqual([item["uuid"] for item in nav_index.find(self.folder, ["DevOps"])], [U1])

    def test_withdrawals_are_applied_once(self):
        page = feed_page([(U1, "INACTIVE", "...", "v2")])
        with patch("norway_job_agent.nav._fetch_nav", return_value=page):
            nav_index.sync(self.folder)
        self.assertEqual(nav_index.pending_withdrawals(self.folder), [U1])
        nav_index.mark_withdrawals_applied(self.folder, [U1])
        self.assertEqual(nav_index.pending_withdrawals(self.folder), [])

    def test_refresh_saves_confirmed_nav_adverts(self):
        service.initialize(self.folder)
        page = feed_page([(U1, "ACTIVE", "Data Scientist", "v1")])
        with patch("norway_job_agent.nav._fetch_nav", side_effect=[page, nav_detail(U1, "Data Scientist")]):
            report = service.collect(self.folder)
        nav = report["sources"][0]
        self.assertTrue(nav["ok"], nav)
        self.assertEqual(nav["created"], 1)
        self.assertIn("1 active Norwegian vacancies", nav["coverage"])
        self.assertEqual(service.vacancies(self.folder)[0]["title"], "Data Scientist")


class ServiceMarketTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name)
        service.initialize(self.folder)
        settings = service.read_settings(self.folder)
        settings["sources"] = []
        settings["market"]["enabled"]["arbeitsagentur"] = True
        service.save_settings(self.folder, settings)

    def run_with(self, jobs):
        run = market.ProviderRun("arbeitsagentur", jobs=jobs, queries=["Linux DevOps"], fetched=len(jobs))
        return patch("norway_job_agent.market.search_provider", return_value=run)

    def test_live_search_saves_results_so_the_local_search_finds_them(self):
        jobs = [board_job(), board_job(identifier="2", title="Linux Administrator"), board_job(identifier="3", title="Lagerist")]
        with self.run_with(jobs):
            report = service.market_search(self.folder, "Linux DevOps", "DE")
        self.assertEqual((report["found"], report["created"]), (3, 3))
        # Adverts without text yet match on a specific search word in the title.
        results = service.vacancies(self.folder, query="linux devops", country="DE")
        self.assertEqual({job["title"] for job in results}, {"DevOps Engineer (m/w/d)", "Linux Administrator"})

    def test_search_box_matches_words_in_any_order_and_short_words_whole(self):
        service.import_vacancy(self.folder, board_job("manual", "a", description="We run Linux in production"))
        service.import_vacancy(self.folder, board_job("manual", "b", title="Gopher keeper", description="Animals"))
        service.import_vacancy(self.folder, board_job("manual", "c", title="Go developer", description="Golang"))
        self.assertEqual([job["source_id"] for job in service.vacancies(self.folder, query="Linux DevOps")], ["a"])
        self.assertEqual([job["source_id"] for job in service.vacancies(self.folder, query="go")], ["c"])
        self.assertEqual(service.vacancies(self.folder, query='"devops linux"'), [])

    def test_role_alias_search_finds_other_spellings(self):
        service.import_vacancy(self.folder, board_job("manual", "a", title="Machine Learning Engineer"))
        self.assertEqual(len(service.vacancies(self.folder, query="ML Engineer")), 1)

    def test_same_job_on_several_sites_shows_once_with_the_employer_copy(self):
        service.import_vacancy(self.folder, board_job("linkedin", "9", title="Data Scientist", company="Fixture AS"))
        service.import_vacancy(self.folder, board_job("greenhouse", "fixture:1", title="Data Scientist", company="Fixture",
                                                      description="Full advert"))
        service.import_vacancy(self.folder, board_job("arbeitsagentur", "8", title="Data Scientist (m/w/d)", company="Fixture GmbH"))
        jobs = service.vacancies(self.folder)
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["source"], "greenhouse")
        self.assertEqual({item["source"] for item in jobs[0]["also_on"]}, {"linkedin", "arbeitsagentur"})

    def test_two_openings_on_one_site_stay_separate(self):
        service.import_vacancy(self.folder, board_job("greenhouse", "x:1", title="Data Scientist"))
        service.import_vacancy(self.folder, board_job("greenhouse", "x:2", title="Data Scientist"))
        self.assertEqual(len(service.vacancies(self.folder)), 2)

    def test_board_adverts_not_seen_for_a_month_leave_discovery_but_saved_ones_stay(self):
        old, saved = service.import_vacancy(self.folder, board_job(identifier="old"))[0], service.import_vacancy(self.folder, board_job(identifier="kept", title="SRE"))[0]
        service.update_workflow(self.folder, saved, "saved", "")
        stale = (datetime.now(timezone.utc) - timedelta(days=service.STALE_BOARD_DAYS + 1)).isoformat()
        with JobStore(self.folder / "vacancies.db") as store:
            store._connection.execute("UPDATE jobs SET last_seen_at = ?", (stale,))
            store._connection.commit()
        self.assertEqual([job["id"] for job in service.vacancies(self.folder)], [saved])
        self.assertEqual({job["id"] for job in service.vacancies(self.folder, include_inactive=True)}, {old, saved})

    def test_opening_a_board_advert_loads_and_keeps_its_full_text(self):
        identifier = service.import_vacancy(self.folder, board_job())[0]
        details = {"description": "Full advert about Linux", "external_apply_url": "https://employer.example/jobs/77"}
        with patch("norway_job_agent.market.fetch_details", return_value=details) as fetch:
            job = service.load_details(self.folder, identifier)
            service.load_details(self.folder, identifier)
        fetch.assert_called_once()
        self.assertEqual(job["description"], "Full advert about Linux")
        self.assertEqual(json.loads(job["raw_json"])["external_apply_url"], "https://employer.example/jobs/77")
        # The employer link never merges this record into the employer's own.
        self.assertEqual(job["apply_url"], board_job()["apply_url"])

    def test_live_search_does_not_overlap_a_refresh(self):
        service._collection_lock.acquire()
        try:
            with self.assertRaisesRegex(ValueError, "refresh is running"):
                service.market_search(self.folder, "DevOps")
        finally:
            service._collection_lock.release()

    def test_coverage_lists_country_job_sites(self):
        coverage = service.discovery_coverage(self.folder, "DE")
        names = {site["id"] for site in coverage["job_sites"]}
        self.assertIn("arbeitsagentur", names)
        self.assertNotIn("djinni", names)


class RoleMatchingTests(unittest.TestCase):
    profile = {"target_roles": ["Machine Learning Engineer", "AI Engineer", "Data Scientist"], "skills": ["Python"]}

    def test_close_roles_are_related_not_claimed_as_targets(self):
        result = match_job({"title": "Senior MLOps Engineer"}, self.profile)
        self.assertEqual(result["track"], "horizon")
        self.assertIn("Machine learning (close role)", result["related_roles"])
        self.assertEqual(result["matched_roles"], [])

    def test_close_roles_need_a_target_in_that_field(self):
        self.assertEqual(match_job({"title": "MLOps Engineer"}, {"target_roles": ["Accountant"]})["track"], "other")

    def test_german_and_ukrainian_spellings_match_targets(self):
        for title in ("KI-Entwickler (m/w/d)", "Datenwissenschaftler*in", "ML-Ingenieur"):
            self.assertEqual(match_job({"title": title}, self.profile)["track"], "target", title)

    def test_jobs_in_your_field_count_as_related_roles(self):
        profile = {"target_roles": ["Machine Learning Engineer", "Data Scientist"], "related_roles": ["Statistician"],
                   "skills": ["Python"], "years_of_experience": 2.5}
        cases = {
            ("Software Engineer, Applied AI Solutions", ""): "horizon",
            ("PhD Candidate in Maritime AI", "Research"): "horizon",
            ("Machine Learning Fellow", ""): "horizon",
            ("Statistikar innan arbeidsmarknad", ""): "horizon",
            ("Software Engineer", "We train machine learning models with PyTorch and LLMs for NLP."): "horizon",
            ("Software Engineer", "Java and Spring services."): "other",
            ("Senior Product Manager, AI Studio", ""): "other",
            ("AI Sales Director", "machine learning"): "other",
            ("Jurist innen kunstig intelligens", ""): "other",
        }
        for (title, text), track in cases.items():
            with self.subTest(title=title):
                self.assertEqual(match_job({"title": title, "description": text}, profile)["track"], track)

    def test_related_roles_get_role_credit_instead_of_a_41_percent_ceiling(self):
        profile = {"target_roles": ["Data Scientist"], "related_roles": ["Data Analyst"], "skills": ["Python", "SQL"]}
        result = match_job({"title": "Data Analyst", "description": "Python and SQL"}, profile)
        self.assertEqual(result["track"], "horizon")
        self.assertGreaterEqual(result["score"], 80)

    def test_ambiguous_skills_need_supporting_context(self):
        profile = {"skills": ["PPO", "R", "Regression"]}
        benefits = match_job({"title": "Engineer", "description": "Medical PPO plan. Regression testing of R&D builds."}, profile)
        self.assertEqual(benefits["matched_skills"], [])
        research = match_job({"title": "Engineer", "description": "Reinforcement learning with PPO; statistics in R; regression models."}, profile)
        self.assertEqual(research["matched_skills"], ["PPO", "R", "Regression"])

    def test_country_views_put_local_jobs_before_remote_anywhere_ones(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        folder = Path(temporary.name)
        service.initialize(folder)
        profile = service.read_profile(folder)
        profile.update(target_roles=["Data Scientist"], skills=["Python"])
        service.save_profile(folder, profile)
        service.import_vacancy(folder, board_job("remotive", "r1", title="Data Scientist", location="Remote (Worldwide)",
                                                 description="Python", countries=["NO", "DE"]))
        service.import_vacancy(folder, board_job("manual", "l1", title="Data Scientist", company="Local AS", location="Oslo, Norway",
                                                 description="", countries=["NO"]))
        titles = [job["location"] for job in service.list_page(folder, view="for-you", country="NO")["jobs"]]
        self.assertEqual(titles, ["Oslo, Norway", "Remote (Worldwide)"])

    def test_long_skill_lists_do_not_dilute_the_score(self):
        skills = ["Python", "SQL", "Docker", "Git", "Linux", "AWS", "pandas", "PyTorch"] + [f"Skill {index}" for index in range(98)]
        result = match_job({"title": "Engineer", "description": "Python, SQL, Docker, Git, Linux, AWS, pandas and PyTorch"},
                           {"skills": skills})
        # Eight of 106 skills is full skill credit.
        self.assertEqual(result["score"], 100)


if __name__ == "__main__":
    unittest.main()
