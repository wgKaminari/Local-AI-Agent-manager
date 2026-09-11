import json
import unittest
from unittest.mock import patch

from norway_job_agent import company_sources as companies, sources
from norway_job_agent.company_catalog import company_catalog


WORKDAY = {"type": "workday", "name": "Example", "url": "https://example.wd3.myworkdayjobs.com/en-US/External"}
CAREERS = {"type": "career_page", "name": "Example", "url": "https://careers.example.com/",
           "sitemap_url": "https://careers.example.com/vacancies.xml", "job_path": "/job/"}


def listing(paths, total=None):
    return {"jobPostings": [{"externalPath": path, "locationsText": "Oslo"} for path in paths],
            "total": len(paths) if total is None else total}


def detail(identifier="123"):
    return {"jobPostingInfo": {"title": "AI Engineer", "jobReqId": identifier,
            "location": "Oslo, Norway", "additionalLocations": ["Bergen, Norway"],
            "jobDescription": "<p>Build Python agents.</p>", "timeType": "Full time",
            "startDate": "2026-09-01", "endDate": "2026-10-01"}}


def job(identifier="1"):
    return sources._job(title="AI Engineer", company="ATS brand", source="job_url",
                        source_id=identifier, source_url="https://careers.example.com/job/" + identifier,
                        location="Oslo, Norway", description="Python ML")


def sitemap(rows):
    return ("<urlset xmlns='http://www.sitemaps.org/schemas/sitemap/0.9'>" + "".join(
        f"<url><loc>{url}</loc><lastmod>{date}</lastmod></url>" for url, date in rows) + "</urlset>").encode()


class CompanyValidationTests(unittest.TestCase):
    def test_invalid_configuration_is_rejected_before_network(self):
        bad = [None, {}, {**WORKDAY, "url": "https://attacker.example/External"},
               {**WORKDAY, "url": WORKDAY["url"] + "?account=private"},
               {**WORKDAY, "url": WORKDAY["url"] + "/job/123"},
               {**WORKDAY, "url": "https://user:secret@example.wd3.myworkdayjobs.com/External"},
               {**WORKDAY, "search_text": 10}, {**WORKDAY, "max_jobs": True},
               {**WORKDAY, "max_jobs": 101}, {**WORKDAY, "name": []},
               {**CAREERS, "sitemap_url": "https://other.example.com/sitemap.xml"},
               {**CAREERS, "job_path": "/"}, {**CAREERS, "url_keywords": "norway"},
               {"type": "smartrecruiters", "board": "../private"},
               {"type": "smartrecruiters", "board": "Example", "country": None}]
        with patch.object(companies, "_fetch") as fetch:
            for source in bad:
                with self.subTest(source=source), self.assertRaises(sources.SourceError):
                    companies.fetch_company_source(source)
            fetch.assert_not_called()

    def test_catalog_is_independent_and_manual_entries_are_not_fake_collectors(self):
        entries = company_catalog()
        self.assertGreaterEqual(len(entries), 25)
        self.assertEqual(len(entries), len({entry["id"] for entry in entries}))
        by_id = {entry["id"]: entry for entry in entries}
        for name in ("equinor", "dnv", "tieto", "autostore"):
            companies.validate_company_source(by_id[name]["source"])
        self.assertIsNone(by_id["google"]["source"])
        self.assertTrue(all(entry["careers_url"].startswith("https://") and entry["note"] for entry in entries))
        by_id["equinor"]["source"]["locations"].clear()
        by_id["equinor"]["source"]["max_jobs"] = 1
        fresh = {entry["id"]: entry for entry in company_catalog()}
        self.assertTrue(fresh["equinor"]["source"]["locations"])
        self.assertEqual(fresh["equinor"]["source"]["max_jobs"], 60)


class WorkdayTests(unittest.TestCase):
    def setUp(self):
        sleep = patch.object(companies.time, "sleep")
        sleep.start()
        self.addCleanup(sleep.stop)

    def test_listing_post_is_read_only_and_details_are_normalized(self):
        with patch.object(companies, "_json", side_effect=[listing(["/job/Oslo/AI_JR123"]), detail()]) as fetch:
            report = companies.fetch_company_source({**WORKDAY, "search_text": "Norway"})
        first = fetch.call_args_list[0]
        self.assertTrue(first.args[0].endswith("/wday/cxs/example/External/jobs"))
        self.assertEqual(first.kwargs["method"], "POST")
        self.assertEqual(json.loads(first.kwargs["body"]), {"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": "Norway"})
        result = report["jobs"][0]
        self.assertEqual(result["title"], "AI Engineer")
        self.assertEqual(result["company"], "Example")
        self.assertEqual(result["description"], "Build Python agents.")
        self.assertEqual(result["location"], "Oslo, Norway; Bergen, Norway")
        self.assertEqual(result["source_id"], "example:External:123")
        self.assertEqual(result["deadline"], "2026-10-01")
        self.assertFalse(report["has_more"])
        self.assertIn("/en-US/External/job/", result["apply_url"])

    def test_paginates_and_reports_partial_coverage(self):
        paths = [f"/job/Oslo/AI_JR{i}" for i in range(20)]
        responses = [listing(paths, 22)] + [detail(str(i)) for i in range(20)] + [listing(["/job/Oslo/AI_JR20", "/job/Oslo/AI_JR21"], 22), detail("20"), detail("21")]
        with patch.object(companies, "_json", side_effect=responses) as fetch:
            report = companies.fetch_company_source(WORKDAY)
        self.assertEqual(len(report["jobs"]), 22)
        self.assertFalse(report["has_more"])
        self.assertEqual(json.loads(fetch.call_args_list[21].kwargs["body"])["offset"], 20)
        with patch.object(companies, "_json", side_effect=[listing([paths[0]], 22), detail()]):
            limited = companies.fetch_company_source({**WORKDAY, "max_jobs": 1})
        self.assertTrue(limited["has_more"])
        self.assertIn("partial", limited["coverage"])

    def test_empty_success_and_incomplete_responses_are_distinct(self):
        with patch.object(companies, "_json", return_value=listing([])):
            self.assertEqual(companies.fetch_company_source(WORKDAY)["jobs"], [])
        for response in (listing([], 2), {"total": 1}, {"total": True, "jobPostings": []}):
            with self.subTest(response=response), patch.object(companies, "_json", return_value=response), self.assertRaises(sources.SourceError):
                companies.fetch_company_source(WORKDAY)

    def test_repeated_page_does_not_return_partial_success(self):
        paths = [f"/job/Oslo/AI_JR{i}" for i in range(20)]
        responses = [listing(paths, 40)] + [detail(str(i)) for i in range(20)] + [listing(paths, 40)]
        with patch.object(companies, "_json", side_effect=responses), self.assertRaisesRegex(sources.SourceError, "repeated"):
            companies.fetch_company_source(WORKDAY)

    def test_invalid_posting_paths_never_reach_detail_download(self):
        for path in ("https://attacker.example/x", "/job/../admin", "/job/x?admin=1", "/job/x#fragment"):
            with self.subTest(path=path), patch.object(companies, "_json", return_value=listing([path])) as fetch, self.assertRaises(sources.SourceError):
                companies.fetch_company_source(WORKDAY)
            self.assertEqual(fetch.call_count, 1)

    def test_listing_page_size_cannot_be_ignored(self):
        with patch.object(companies, "_json", return_value=listing(["/job/1", "/job/2"])), self.assertRaisesRegex(sources.SourceError, "page size"):
            companies.fetch_company_source({**WORKDAY, "max_jobs": 1})

    def test_json_requests_observe_robots(self):
        with patch.object(companies, "_fetch", side_effect=sources.SourceError("disallowed by robots.txt")) as fetch, self.assertRaisesRegex(sources.SourceError, "robots"):
            companies.fetch_company_source(WORKDAY)
        self.assertTrue(fetch.call_args.kwargs["respect_robots"])


class CareerPageTests(unittest.TestCase):
    def setUp(self):
        sleep = patch.object(companies.time, "sleep")
        sleep.start()
        self.addCleanup(sleep.stop)

    def test_sitemap_filters_origin_path_and_country_and_orders_recent_first(self):
        data = sitemap([
            ("https://careers.example.com/job/old-norway", "2026-08-01"),
            ("https://careers.example.com/job/new-norway", "2026-09-10"),
            ("https://careers.example.com/job/new-sweden", "2026-09-11"),
            ("https://careers.example.com/about/norway", "2026-09-11"),
            ("https://evil.example.com/job/norway", "2026-09-11"),
        ])
        with patch.object(companies, "_fetch", return_value=(200, {}, data, CAREERS["sitemap_url"])) as fetch, patch.object(companies, "fetch_job_url", side_effect=[[job("2")], [job("1")]]) as detail_fetch:
            report = companies.fetch_company_source({**CAREERS, "url_keywords": ["norway"]})
        fetch.assert_called_once_with(CAREERS["sitemap_url"], respect_robots=True)
        self.assertEqual([c.args[0] for c in detail_fetch.call_args_list], ["https://careers.example.com/job/new-norway", "https://careers.example.com/job/old-norway"])
        self.assertEqual([j["company"] for j in report["jobs"]], ["Example", "Example"])
        self.assertFalse(report["has_more"])
        self.assertIn("outside this scan", report["coverage"])

    def test_html_page_deduplicates_links_and_advertises_page_limit(self):
        source = {"type": "career_page", "url": "https://careers.example.com/jobs"}
        body = b'<a href="/jobs/123?track=1">A</a><a href="/jobs/123">B</a><a href="/jobs/">All</a>'
        with patch.object(companies, "_fetch", return_value=(200, {}, body, source["url"])), patch.object(companies, "fetch_job_url", return_value=[job()]) as detail_fetch:
            report = companies.fetch_company_source(source)
        detail_fetch.assert_called_once_with("https://careers.example.com/jobs/123")
        self.assertTrue(report["has_more"])
        self.assertIn("other pages", report["coverage"])

    def test_max_jobs_also_caps_multi_posting_pages(self):
        data = sitemap([("https://careers.example.com/job/norway", "2026-09-01")])
        with patch.object(companies, "_fetch", return_value=(200, {}, data, CAREERS["sitemap_url"])), patch.object(companies, "fetch_job_url", return_value=[job("1"), job("2"), job("3")]):
            report = companies.fetch_company_source({**CAREERS, "max_jobs": 1})
        self.assertEqual(len(report["jobs"]), 1)
        self.assertTrue(report["has_more"])
        self.assertIn("limit", report["coverage"])

    def test_partial_page_failures_are_visible(self):
        data = sitemap([("https://careers.example.com/job/1", "2026-09-01"), ("https://careers.example.com/job/2", "2026-09-02")])
        with patch.object(companies, "_fetch", return_value=(200, {}, data, CAREERS["sitemap_url"])), patch.object(companies, "fetch_job_url", side_effect=[[job()], sources.SourceError("HTTP 410")]):
            report = companies.fetch_company_source(CAREERS)
        self.assertEqual(len(report["jobs"]), 1)
        self.assertTrue(report["has_more"])
        self.assertIn("1 pages could not be read", report["coverage"])

    def test_empty_invalid_or_index_sitemap_never_claims_full_empty_success(self):
        for data in (sitemap([]), b"<sitemapindex/>", b"invalid", b'<!DOCTYPE foo [<!ENTITY x SYSTEM "file:///secret">]><urlset/>'):
            with self.subTest(data=data), patch.object(companies, "_fetch", return_value=(200, {}, data, CAREERS["sitemap_url"])), patch.object(companies, "fetch_job_url") as fetch, self.assertRaises(sources.SourceError):
                companies.fetch_company_source(CAREERS)
            fetch.assert_not_called()

    def test_all_failed_details_report_error(self):
        data = sitemap([("https://careers.example.com/job/1", "2026-09-01")])
        with patch.object(companies, "_fetch", return_value=(200, {}, data, CAREERS["sitemap_url"])), patch.object(companies, "fetch_job_url", side_effect=sources.SourceError("Missing JobPosting")), self.assertRaisesRegex(sources.SourceError, "Could not read any"):
            companies.fetch_company_source(CAREERS)


class SmartRecruitersTests(unittest.TestCase):
    def test_country_filter_and_description(self):
        source = {"type": "smartrecruiters", "board": "Example", "country": "no", "max_jobs": 2}
        responses = [{"content": [{"id": "abc"}], "totalFound": 1}, {
            "name": "ML Engineer", "company": {"name": "Example AS"},
            "location": {"city": "Oslo", "country": "no"},
            "jobAd": {"sections": {"jobDescription": {"text": "<p>Python ML</p>"},
                                   "qualifications": {"text": "<p>Statistics</p>"}}},
            "typeOfEmployment": {"label": "Full-time"},
            "postingUrl": "https://jobs.smartrecruiters.com/Example/abc",
        }]
        with patch.object(companies.time, "sleep"), patch.object(companies, "_json", side_effect=responses) as fetch:
            report = companies.fetch_company_source(source)
        self.assertIn("country=no", fetch.call_args_list[0].args[0])
        self.assertEqual(report["jobs"][0]["description"], "Python ML\n\nStatistics")
        self.assertEqual(report["jobs"][0]["source_id"], "Example:abc")
        self.assertFalse(report["has_more"])

    def test_inactive_posting_is_not_imported(self):
        with patch.object(companies.time, "sleep"), patch.object(companies, "_json", side_effect=[
            {"content": [{"id": "abc"}], "totalFound": 1}, {"name": "Old job", "active": False},
        ]):
            report = companies.fetch_company_source({"type": "smartrecruiters", "board": "Example"})
        self.assertEqual(report["jobs"], [])
        self.assertFalse(report["has_more"])

    def test_empty_page_before_total_is_an_error(self):
        with patch.object(companies, "_json", return_value={"content": [], "totalFound": 2}), self.assertRaisesRegex(sources.SourceError, "incomplete"):
            companies.fetch_company_source({"type": "smartrecruiters", "board": "Example"})


class DownloadRegressionTests(unittest.TestCase):
    def test_quoted_robots_rules_are_respected(self):
        data = json.dumps("User-agent: *\nDisallow: /private\n").encode()
        with patch.object(sources, "_fetch", return_value=(200, {}, data, "https://careers.example.com/robots.txt")), self.assertRaisesRegex(sources.SourceError, "robots"):
            sources._check_robots("https://careers.example.com/private/jobs", {})

    def test_post_cannot_redirect_to_another_host(self):
        with patch.object(sources, "_request_once", return_value=(307, {"location": "https://other.example.com/jobs"}, b"")) as request, self.assertRaisesRegex(sources.SourceError, "different host"):
            sources._fetch("https://example.wd3.myworkdayjobs.com/jobs", method="POST", body=b"{}")
        self.assertEqual(request.call_count, 1)


if __name__ == "__main__":
    unittest.main()
