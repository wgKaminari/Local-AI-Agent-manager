import io
import json
import unittest
from unittest.mock import Mock, patch

from norway_job_agent import sources


def response(data, status=200, headers=None):
    if not isinstance(data, bytes):
        data = json.dumps(data).encode()
    return status, headers or {}, data


def posting(**changes):
    value = {
        "@type": "JobPosting",
        "title": "Python developer",
        "description": "<p>Build useful tools.</p><ul><li>Python experience</li></ul>",
        "hiringOrganization": {"@type": "Organization", "name": "Example AS"},
        "identifier": {"@type": "PropertyValue", "value": "123"},
        "jobLocation": {"address": {
            "addressLocality": "Oslo", "addressCountry": {"name": "Norway"}
        }},
        "employmentType": ["FULL_TIME", "PERMANENT"],
        "datePosted": "2026-09-01",
        "validThrough": "2026-10-01",
        "url": "/jobs/123",
    }
    value.update(changes)
    return value


class ConnectorTests(unittest.TestCase):
    def test_greenhouse_uses_company_metadata_and_full_description(self):
        with patch.object(sources, "_json", side_effect=[
            {"name": "Example AS"},
            {"jobs": [{"id": 12, "title": "Developer", "location": {"name": "Oslo"},
                       "content": "&lt;p&gt;Python &amp; SQL&lt;/p&gt;",
                       "absolute_url": "https://example.com/jobs/12", "updated_at": "2026-09-08"}]},
        ]) as fetch:
            jobs = sources.fetch_greenhouse("example")
        self.assertEqual(jobs[0]["company"], "Example AS")
        self.assertEqual(jobs[0]["description"], "Python & SQL")
        self.assertEqual(jobs[0]["source_id"], "example:12")
        self.assertEqual(jobs[0]["published_at"], "")  # Updated is not published.
        self.assertTrue(fetch.call_args.args[0].endswith("/jobs?content=true"))

    def test_lever_keeps_all_description_sections_and_region(self):
        with patch.object(sources, "_json", return_value=[{
            "id": "abc", "text": "Developer", "descriptionPlain": "Introduction",
            "categories": {"location": "Bergen", "commitment": "Full-time"},
            "lists": [{"text": "Requirements", "content": "<li>Python</li>"}],
            "additionalPlain": "Benefits", "hostedUrl": "https://jobs.eu.lever.co/example/abc",
            "applyUrl": "https://jobs.eu.lever.co/example/abc/apply",
        }]) as fetch:
            job = sources.fetch_lever("example", "eu")[0]
        self.assertIn("api.eu.lever.co", fetch.call_args.args[0])
        self.assertEqual(job["source_id"], "eu:example:abc")
        self.assertEqual(job["description"], "Introduction\n\nRequirements\n\nPython\n\nBenefits")
        self.assertTrue(job["apply_url"].endswith("/apply"))

    def test_lever_paginates_and_rejects_repeated_pages(self):
        page = [{"id": str(number), "text": "Developer"} for number in range(100)]
        with patch.object(sources, "_json", side_effect=[page, []]) as fetch:
            self.assertEqual(len(sources.fetch_lever("example")), 100)
            self.assertIn("skip=100", fetch.call_args.args[0])
        with patch.object(sources, "_json", side_effect=[page, page]):
            with self.assertRaisesRegex(sources.SourceError, "repeated"):
                sources.fetch_lever("example")

    def test_invalid_board_and_region_are_rejected_before_network(self):
        with patch.object(sources, "_json") as fetch:
            for board in ("../admin", "", "https://example.com", "example?limit=999"):
                with self.subTest(board=board), self.assertRaises(sources.SourceError):
                    sources.fetch_greenhouse(board)
            with self.assertRaises(sources.SourceError):
                sources.fetch_lever("example", "unknown")
            fetch.assert_not_called()

    def test_jsonld_nested_graph_deduplicates_and_normalizes(self):
        payload = {"@graph": [posting(), posting(), {"@type": "Organization", "name": "Other"}]}
        page = '<script type="application/ld+json">' + json.dumps(payload) + "</script>"
        with patch.object(sources, "_fetch", return_value=(200, {}, page.encode(), "https://example.com/careers")) as fetch:
            jobs = sources.fetch_job_url("https://example.com/careers")
        fetch.assert_called_once_with("https://example.com/careers", respect_robots=True)
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["location"], "Oslo, Norway")
        self.assertEqual(jobs[0]["source_url"], "https://example.com/jobs/123")
        self.assertEqual(jobs[0]["source_id"], "https://example.com/jobs/123#123")
        self.assertEqual(jobs[0]["deadline"], "2026-10-01")
        self.assertEqual(jobs[0]["employment_type"], "FULL_TIME, PERMANENT")

    def test_jsonld_arrays_remote_and_missing_metadata(self):
        job = posting(jobLocation=None, jobLocationType="TELECOMMUTE", hiringOrganization=None,
                      identifier=None, url="javascript:alert(1)", **{"@type": ["Thing", "JobPosting"]})
        page = '<script type="APPLICATION/LD+JSON; charset=utf-8">' + json.dumps([job]) + "</script>"
        with patch.object(sources, "_fetch", return_value=(200, {}, page.encode(), "https://example.com/job")):
            result = sources.fetch_job_url("https://example.com/job")[0]
        self.assertEqual(result["company"], "")
        self.assertEqual(result["location"], "Remote")
        self.assertEqual(result["apply_url"], "https://example.com/job")

    def test_missing_or_invalid_structured_data_is_actionable(self):
        for page in (b"<h1>A job</h1>", b'<script type="application/ld+json">invalid</script>', b"<script type>hello</script>"):
            with self.subTest(page=page), patch.object(sources, "_fetch", return_value=(200, {}, page, "https://example.com")):
                with self.assertRaisesRegex(sources.SourceError, "Paste the vacancy text"):
                    sources.fetch_job_url("https://example.com")

    def test_description_preserves_escaped_code_inside_html(self):
        self.assertEqual(sources._plain("<p>Use List&lt;T&gt; &amp; Python.</p>"), "Use List<T> & Python.")


class FetchSafetyTests(unittest.TestCase):
    def test_rejects_unsupported_credentials_and_restricted_sources(self):
        urls = ("file:///etc/passwd", "ftp://example.com/job", "https://user:password@example.com",
                "https://www.linkedin.com/jobs/1", "https://finn.no/job/1", "https://sub.finn.no/job",
                "https://linkedin.com./job", "https://ＦＩＮＮ.no/job", "https://example.com\n/job")
        with patch.object(sources, "_request_once") as request:
            for url in urls:
                with self.subTest(url=url), self.assertRaises(sources.SourceError):
                    sources._fetch(url)
            request.assert_not_called()

    def test_dns_rejects_private_local_reserved_and_mixed_answers(self):
        for addresses in (["127.0.0.1"], ["10.0.0.1"], ["169.254.169.254"], ["::1"], ["fc00::1"],
                          ["::ffff:127.0.0.1"], ["192.0.2.1"], ["93.184.216.34", "192.168.0.1"]):
            answers = [(2, 1, 6, "", (address, 443)) for address in addresses]
            with self.subTest(addresses=addresses), patch.object(sources.socket, "getaddrinfo", return_value=answers):
                with self.assertRaisesRegex(sources.SourceError, "destinations"):
                    sources._public_addresses("example.com", 443)

    def test_redirect_to_private_destination_is_revalidated(self):
        original_request = sources._request_once

        def request(url):
            if url == "https://example.com/job":
                return response(b"", 302, {"location": "http://127.0.0.1/private"})
            return original_request(url)

        answers = [(2, 1, 6, "", ("127.0.0.1", 80))]
        with patch.object(sources, "_request_once", side_effect=request), \
                patch.object(sources.socket, "getaddrinfo", return_value=answers), \
                patch.object(sources.http.client, "HTTPConnection") as connect:
            with self.assertRaises(sources.SourceError):
                sources._fetch("https://example.com/job")
            connect.assert_not_called()

    def test_robots_disallow_prevents_page_download(self):
        with patch.object(sources, "_request_once", return_value=response(b"User-agent: *\nDisallow: /private\n")) as request:
            with self.assertRaisesRegex(sources.SourceError, "robots.txt"):
                sources._fetch("https://example.com/private/job", respect_robots=True)
        request.assert_called_once_with("https://example.com/robots.txt")

    def test_robots_unavailable_fails_closed_and_missing_allows(self):
        with patch.object(sources, "_request_once", return_value=response(b"", 503)) as request:
            with self.assertRaisesRegex(sources.SourceError, "HTTP 503"):
                sources._fetch("https://example.com/job", respect_robots=True)
            self.assertEqual(request.call_count, 1)
        with patch.object(sources, "_request_once", side_effect=[response(b"", 404), response(b"page")]) as request:
            self.assertEqual(sources._fetch("https://example.com/job", respect_robots=True)[2], b"page")
            self.assertEqual(request.call_count, 2)

    def test_cross_origin_redirect_checks_destination_robots(self):
        replies = [response(b"", 404), response(b"", 302, {"location": "https://other.example/job"}),
                   response(b"User-agent: *\nDisallow: /\n")]
        with patch.object(sources, "_request_once", side_effect=replies) as request:
            with self.assertRaisesRegex(sources.SourceError, "robots.txt"):
                sources._fetch("https://example.com/job", respect_robots=True)
        self.assertEqual(request.call_args.args[0], "https://other.example/robots.txt")

    def test_download_cap_for_declared_and_actual_response_size(self):
        for headers, body in ([("Content-Length", str(sources.MAX_RESPONSE_BYTES + 1))], b""), ([], b"x" * (sources.MAX_RESPONSE_BYTES + 1)):
            fake_response = Mock(status=200)
            fake_response.getheaders.return_value = headers
            fake_response.read1.side_effect = io.BytesIO(body).read
            connection = Mock()
            connection.getresponse.return_value = fake_response
            with patch.object(sources, "_public_addresses", return_value=["93.184.216.34"]), \
                    patch.object(sources.http.client, "HTTPSConnection", return_value=connection):
                with self.assertRaisesRegex(sources.SourceError, "2 MB"):
                    sources._request_once("https://example.com/job")
                connection.close.assert_called_once()

    def test_connection_is_pinned_to_validated_ip_with_original_tls_host(self):
        fake_response = Mock(status=200)
        fake_response.getheaders.return_value = []
        fake_response.read1.return_value = b""
        connection = Mock()
        connection.getresponse.return_value = fake_response
        connection.request.side_effect = lambda *args, **kwargs: connection._create_connection(("example.com", 443))
        with patch.object(sources, "_public_addresses", return_value=["93.184.216.34"]), \
                patch.object(sources.http.client, "HTTPSConnection", return_value=connection) as https, \
                patch.object(sources.socket, "create_connection") as connect:
            sources._request_once("https://example.com/job")
        self.assertEqual(https.call_args.args[:2], ("example.com", 443))
        self.assertEqual(connect.call_args.args[0], ("93.184.216.34", 443))

    def test_invalid_json_api_and_rate_limits_are_actionable(self):
        with patch.object(sources, "_request_once", return_value=response(b"not json")):
            with self.assertRaisesRegex(sources.SourceError, "valid JSON"):
                sources._json("https://example.com/api")
        with patch.object(sources, "_request_once", return_value=response(b"", 429)):
            with self.assertRaisesRegex(sources.SourceError, "rate limiting"):
                sources._fetch("https://example.com/job")


if __name__ == "__main__":
    unittest.main()
