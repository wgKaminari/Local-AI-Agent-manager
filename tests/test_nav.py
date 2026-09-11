import copy
import io
import json
import unittest
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from unittest.mock import Mock, patch

from norway_job_agent import nav
from norway_job_agent.sources import SourceError


A = "11111111-1111-4111-8111-111111111111"
B = "22222222-2222-4222-8222-222222222222"
C = "33333333-3333-4333-8333-333333333333"
TOKEN = "test.private.token"
TAIL = nav.NAV_ORIGIN + "/api/v1/feed/tail"


def event(uuid=A, status="ACTIVE", title="Data scientist", **changes):
    result = {
        "id": "event-" + uuid, "url": "/api/v1/feedentry/" + uuid,
        "_feed_entry": {"uuid": uuid, "status": status, "title": title},
    }
    result.update(changes)
    return result


def page(items=(), next_url=None, feed_url="/api/v1/feed/tail"):
    return {"items": list(items), "next_url": next_url, "feed_url": feed_url}


def detail(uuid=A, status="ACTIVE", field="ad_content", **changes):
    result = {"uuid": uuid, "status": status}
    if status == "ACTIVE":
        result[field] = {
            "uuid": uuid, "title": "Data scientist", "employer": {"name": "Example AS"},
            "description": "<p>Python and statistics</p>",
            "workLocations": [{"city": "Oslo", "country": "NORGE"}],
            "applicationUrl": "https://example.com/apply/" + uuid,
            "published": "2026-09-01T10:00:00Z", "applicationDue": "2026-10-01",
            "engagementtype": "Fast", "contactList": [{"name": "Contact", "email": "contact@example.com"}],
        }
        result[field].update(changes)
    return result


def fetched(payload=None, *, status=200, headers=None, url=TAIL):
    body = b"" if payload is None else json.dumps(payload).encode()
    return status, headers or {}, body, url


class NavBatchTests(unittest.TestCase):
    def test_initial_since_normalization_and_no_contact_persistence(self):
        with patch.object(nav, "_fetch_nav", side_effect=[fetched(page([event()])), fetched(detail())]) as request:
            result = nav.fetch_nav_batch({}, ["data"], token=TOKEN)
        since = request.call_args_list[0].args[2]["If-Modified-Since"]
        days = (datetime.now(timezone.utc) - parsedate_to_datetime(since)).total_seconds() / 86400
        self.assertAlmostEqual(days, 90, places=3)
        self.assertEqual(result["jobs"][0]["source"], "nav")
        self.assertEqual(result["jobs"][0]["source_id"], A)
        self.assertEqual(result["jobs"][0]["location"], "Oslo, NORGE")
        self.assertEqual(result["jobs"][0]["description"], "Python and statistics")
        self.assertEqual(result["jobs"][0]["deadline"], "2026-10-01")
        self.assertNotIn("contactList", result["jobs"][0])
        self.assertNotIn("contact@example.com", json.dumps(result))
        self.assertNotIn(TOKEN, json.dumps(result))
        self.assertFalse(result["has_more"])
        self.assertEqual((result["pages"], result["seen"]), (1, 1))

    def test_page_limit_resumes_next_page_without_previous_validators(self):
        first = fetched(page([], "/api/v1/feed/two", "/api/v1/feed/one"), headers={"etag": "old", "last-modified": "old-date"})
        with patch.object(nav, "_fetch_nav", return_value=first):
            result = nav.fetch_nav_batch({}, ["data"], token=TOKEN, max_pages=1)
        self.assertTrue(result["has_more"])
        self.assertEqual(result["state"]["url"], nav.NAV_ORIGIN + "/api/v1/feed/two")
        self.assertEqual(result["state"]["etag"], "")
        self.assertEqual(result["state"]["last_modified"], "")
        with patch.object(nav, "_fetch_nav", return_value=fetched(page())) as request:
            second = nav.fetch_nav_batch(result["state"], ["data"], token=TOKEN)
        self.assertEqual(request.call_args.args[2], {})
        self.assertEqual(second["state"]["initial_since"], result["state"]["initial_since"])

    def test_pagination_within_batch_and_latest_duplicate_only(self):
        replies = [
            fetched(page([event()], "/api/v1/feed/two", "/api/v1/feed/one")),
            fetched(page([event(title="Senior data scientist")])),
            fetched(detail(title="Senior data scientist")),
        ]
        with patch.object(nav, "_fetch_nav", side_effect=replies) as request:
            result = nav.fetch_nav_batch({}, ["data"], token=TOKEN)
        self.assertEqual(len(result["jobs"]), 1)
        self.assertEqual(result["jobs"][0]["title"], "Senior data scientist")
        self.assertEqual(request.call_count, 3)
        self.assertEqual((result["pages"], result["seen"]), (2, 2))

    def test_tail_304_resume_preserves_validators_and_uses_tail_url(self):
        headers = {"etag": '"tail-v1"', "last-modified": "Wed, 09 Sep 2026 10:00:00 GMT"}
        with patch.object(nav, "_fetch_nav", return_value=fetched(page(), headers=headers)):
            first = nav.fetch_nav_batch({}, ["data"], token=TOKEN)
        with patch.object(nav, "_fetch_nav", return_value=fetched(status=304)) as request:
            second = nav.fetch_nav_batch(first["state"], ["data"], token=TOKEN)
        self.assertEqual(request.call_args.args, (TAIL, TOKEN, {
            "If-None-Match": '"tail-v1"', "If-Modified-Since": headers["last-modified"]
        }))
        self.assertEqual(second["state"], first["state"])
        self.assertEqual(second["jobs"], [])
        self.assertFalse(second["has_more"])

    def test_changed_tail_advances_and_resets_conditions(self):
        state = {"version": 1, "url": TAIL, "etag": "v1", "last_modified": "yesterday"}
        with patch.object(nav, "_fetch_nav", side_effect=[
            fetched(page([], "/api/v1/feed/new"), headers={"etag": "v2"}),
            fetched(page([], feed_url="/api/v1/feed/new"), headers={"etag": "new"}),
        ]) as request:
            result = nav.fetch_nav_batch(state, [], token=TOKEN)
        self.assertEqual(request.call_args_list[1].args[2], {})
        self.assertEqual(result["state"]["etag"], "new")
        self.assertTrue(result["state"]["url"].endswith("/new"))

    def test_inactive_outside_keywords_is_returned_without_detail_fetch(self):
        with patch.object(nav, "_fetch_nav", return_value=fetched(page([
            event(A, "INACTIVE", "Truck driver"), event(B, "ACTIVE", "School teacher")
        ]))) as request:
            result = nav.fetch_nav_batch({}, ["data"], token=TOKEN)
        self.assertEqual(result["withdrawn_ids"], [A])
        self.assertEqual(result["jobs"], [])
        self.assertEqual(request.call_count, 1)

    def test_known_id_refreshes_even_if_title_no_longer_matches(self):
        with patch.object(nav, "_fetch_nav", side_effect=[
            fetched(page([event(title="General consultant")])),
            fetched(detail(title="General consultant")),
        ]):
            result = nav.fetch_nav_batch({}, ["machine learning"], known_ids={A}, token=TOKEN)
        self.assertEqual(result["jobs"][0]["title"], "General consultant")

    def test_known_inactive_event_checks_current_status_for_reopening(self):
        with patch.object(nav, "_fetch_nav", side_effect=[
            fetched(page([event(status="INACTIVE", title="Unrelated title")])),
            fetched(detail(title="Reopened role")),
        ]):
            result = nav.fetch_nav_batch({}, ["data"], known_ids=[A], token=TOKEN)
        self.assertEqual(len(result["jobs"]), 1)
        self.assertEqual(result["withdrawn_ids"], [])

    def test_active_event_with_current_inactive_detail_withdraws(self):
        with patch.object(nav, "_fetch_nav", side_effect=[
            fetched(page([event()])), fetched(detail(status="INACTIVE")),
        ]):
            result = nav.fetch_nav_batch({}, ["data"], token=TOKEN)
        self.assertEqual(result["jobs"], [])
        self.assertEqual(result["withdrawn_ids"], [A])

    def test_detail_failure_does_not_mutate_input_checkpoint(self):
        state = {"version": 1, "url": TAIL, "etag": "v1", "last_modified": "yesterday"}
        original = copy.deepcopy(state)
        with patch.object(nav, "_fetch_nav", side_effect=[
            fetched(page([event()], "/api/v1/feed/next")), SourceError("Detail unavailable"),
        ]):
            with self.assertRaisesRegex(SourceError, "Detail unavailable"):
                nav.fetch_nav_batch(state, ["data"], token=TOKEN, max_pages=1)
        self.assertEqual(state, original)

    def test_supports_json_detail_field_and_safe_application_fallback(self):
        with patch.object(nav, "_fetch_nav", side_effect=[
            fetched(page([event()])),
            fetched(detail(field="json", applicationUrl="javascript:alert(1)", sourceurl="https://example.com/job")),
        ]):
            result = nav.fetch_nav_batch({}, ["data"], token=TOKEN)
        self.assertEqual(result["jobs"][0]["apply_url"], "https://example.com/job")

    def test_keyword_acronyms_do_not_match_inside_other_words(self):
        self.assertFalse(nav._matches(event(title="Chair designer"), ["ai"]))
        self.assertTrue(nav._matches(event(title="AI/ML Engineer"), ["ml"]))
        self.assertTrue(nav._matches(event(title="Maskinlæringsingeniør"), ["maskinlæring"]))
        self.assertTrue(nav._matches(event(title="Consultant", occupationCategories=[{"level2": "Data science"}]), ["data science"]))

    def test_invalid_current_detail_is_not_silently_skipped(self):
        for payload in (detail(B), {"uuid": A, "status": "UNKNOWN"}, detail(employer={})): 
            with self.subTest(payload=payload), patch.object(nav, "_fetch_nav", side_effect=[
                fetched(page([event()])), fetched(payload),
            ]):
                with self.assertRaises(SourceError):
                    nav.fetch_nav_batch({}, ["data"], token=TOKEN)


class NavTransportTests(unittest.TestCase):
    def test_public_token_is_fetched_without_bearer_and_never_persisted(self):
        token_reply = (200, {}, b"public.experimental.token", nav.PUBLIC_TOKEN_URL)
        with patch.dict(nav.os.environ, {}, clear=True), \
                patch.object(nav, "_fetch_nav", side_effect=[token_reply, fetched(page())]) as request:
            result = nav.fetch_nav_batch({}, ["data"])
        self.assertEqual(request.call_args_list[0].args, (nav.PUBLIC_TOKEN_URL, None))
        self.assertEqual(request.call_args_list[1].args[1], "public.experimental.token")
        self.assertTrue(result["experimental_token"])
        self.assertNotIn("public.experimental.token", json.dumps(result))

    def test_environment_token_avoids_public_token_request(self):
        with patch.dict(nav.os.environ, {"NAV_API_TOKEN": TOKEN}), \
                patch.object(nav, "_fetch_nav", return_value=fetched(page())) as request:
            result = nav.fetch_nav_batch({}, ["data"])
        self.assertEqual(request.call_count, 1)
        self.assertEqual(request.call_args.args[1], TOKEN)
        self.assertFalse(result["experimental_token"])

    def test_off_origin_initial_url_is_rejected_before_token_fetch(self):
        with patch.object(nav, "_get_token") as token:
            with self.assertRaises(SourceError):
                nav.fetch_nav_batch({"url": "https://evil.example/feed"}, ["data"])
            token.assert_not_called()

    def test_off_origin_redirect_never_receives_bearer(self):
        with patch.object(nav, "_request_nav_once", return_value=(302, {"location": "https://evil.example/api"}, b"")) as request:
            with self.assertRaisesRegex(SourceError, "another host"):
                nav._fetch_nav(nav.FEED_URL, TOKEN)
        self.assertEqual(request.call_count, 1)
        self.assertEqual(request.call_args.args[0], nav.FEED_URL)

    def test_off_origin_feed_links_never_receive_bearer(self):
        payloads = [page([], "https://evil.example/feed"), page([event(url="https://evil.example/detail")])]
        for payload in payloads:
            with self.subTest(payload=payload), patch.object(nav, "_request_nav_once", return_value=(200, {}, json.dumps(payload).encode())) as request:
                with self.assertRaisesRegex(SourceError, "another host"):
                    nav.fetch_nav_batch({}, ["data"], token=TOKEN)
                self.assertEqual(request.call_count, 1)

    def test_nav_origin_requires_https_exact_host_and_no_credentials(self):
        for url in ("http://pam-stilling-feed.nav.no/api", "https://pam-stilling-feed.nav.no.evil.example/api",
                    "https://pam-stilling-feed.nav.no.:443/api", "https://user@pam-stilling-feed.nav.no/api",
                    "https://pam-stilling-feed.nav.no:444/api", "http://127.0.0.1/api"):
            with self.subTest(url=url), patch.object(nav, "_public_addresses") as dns:
                with self.assertRaises(SourceError):
                    nav._request_nav_once(url, TOKEN, {})
                dns.assert_not_called()

    def test_transport_pins_ip_and_only_adds_bearer_to_nav_request(self):
        fake_response = Mock(status=200)
        fake_response.getheaders.return_value = []
        fake_response.read1.return_value = b""
        connection = Mock()
        connection.getresponse.return_value = fake_response
        connection.request.side_effect = lambda *args, **kwargs: connection._create_connection((nav.NAV_HOST, 443))
        with patch.object(nav, "_public_addresses", return_value=["93.184.216.34"]), \
                patch.object(nav.http.client, "HTTPSConnection", return_value=connection) as https, \
                patch.object(nav.socket, "create_connection") as connect:
            nav._request_nav_once(nav.FEED_URL, TOKEN, {})
        self.assertEqual(https.call_args.args[:2], (nav.NAV_HOST, 443))
        self.assertEqual(connect.call_args.args[0], ("93.184.216.34", 443))
        self.assertEqual(connection.request.call_args.kwargs["headers"]["Authorization"], "Bearer " + TOKEN)

    def test_transport_enforces_body_limit(self):
        fake_response = Mock(status=200)
        fake_response.getheaders.return_value = []
        fake_response.read1.side_effect = io.BytesIO(b"x" * (nav.MAX_RESPONSE_BYTES + 1)).read
        connection = Mock()
        connection.getresponse.return_value = fake_response
        with patch.object(nav, "_public_addresses", return_value=["93.184.216.34"]), \
                patch.object(nav.http.client, "HTTPSConnection", return_value=connection):
            with self.assertRaisesRegex(SourceError, "2 MB"):
                nav._request_nav_once(nav.FEED_URL, TOKEN, {})

    def test_invalid_parameters_do_not_fetch_token_or_network(self):
        for options in ({"max_pages": 0}, {"max_pages": True}, {"lookback_days": 181}, {"lookback_days": -1}):
            with self.subTest(options=options), patch.object(nav, "_get_token") as token:
                with self.assertRaises(SourceError):
                    nav.fetch_nav_batch({}, ["data"], **options)
                token.assert_not_called()


if __name__ == "__main__":
    unittest.main()
