"""Offline checks: no Google account, inbox, browser or external network used."""

import base64
import hashlib
import io
import json
import os
import sys
import tempfile
import time
import unittest
from email.message import EmailMessage
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from urllib.parse import parse_qs, quote, urlencode, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from norway_job_agent import gmail


PROFILE = {"target_roles": ["Data Scientist", "AI Engineer"], "related_roles": ["Data Analyst"], "skills": ["Python"], "excluded_keywords": ["Senior"]}
CLIENT = {"client_id": "fixture-123.apps.googleusercontent.com", "client_secret": "fixture-secret"}


def message(html="", plain="", message_id="message123", thread_id="thread456"):
    parts = []
    for mime, text in (("text/html", html), ("text/plain", plain)):
        if text:
            parts.append({"mimeType": mime, "body": {"data": base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")}})
    return {"id": message_id, "threadId": thread_id, "payload": {"mimeType": "multipart/alternative", "headers": [{"name": "From", "value": "Job Alerts <alerts@example.com>"}, {"name": "Subject", "value": "Your new vacancies"}, {"name": "Date", "value": "Tue, 8 Sep 2026 10:00:00 +0200"}], "parts": parts}}


class GmailParsingTests(unittest.TestCase):
    def test_digest_keeps_individual_related_jobs_and_separates_context(self):
        content = '''<div><a href="https://www.linkedin.com/jobs/view/12345?trackingId=secret">Data Scientist</a><p>Company: First AS</p><p>Location: Oslo</p><p>Python modelling</p></div>
        <div><a href="https://www.linkedin.com/jobs/view/98765">Office Manager</a><p>Company: Second AS</p><p>Location: Bergen</p></div>
        <div><a href="https://careers.example.com/jobs/analyst-7">Data Analyst</a><p>Company: Third AS</p></div>'''
        jobs = gmail.parse_message(message(html=content), PROFILE)
        self.assertEqual([job["title"] for job in jobs], ["Data Scientist", "Data Analyst"])
        self.assertEqual(jobs[0]["company"], "First AS")
        self.assertEqual(jobs[0]["location"], "Oslo")
        self.assertNotIn("Second AS", jobs[0]["description"])
        self.assertNotIn("trackingId", json.dumps(jobs))
        self.assertTrue(jobs[0]["raw_json"]["email_import"]["review_required"])
        self.assertEqual(jobs[0]["raw_json"]["email"]["received_at"], "2026-09-08T08:00:00+00:00")

    def test_tracking_wrappers_decode_without_network(self):
        target = "https://www.linkedin.com/jobs/view/data-scientist-123456/?trk=email&trackingId=private"
        wrapper = "https://mail.example.com/redirect?url=" + quote(quote(target, safe=""), safe="")
        with patch.object(gmail, "build_opener", side_effect=AssertionError("No network")):
            self.assertEqual(gmail.job_url(wrapper), "https://www.linkedin.com/jobs/view/123456")
            self.assertEqual(gmail.job_url("https://boards.greenhouse.io/fixture?gh_jid=4321&utm_source=email&email=user%40example.com"), "https://boards.greenhouse.io/fixture?gh_jid=4321")

    def test_private_account_search_and_non_job_urls_are_rejected(self):
        for url in ["javascript:alert(1)", "file:///tmp/jobs/1", "https://127.0.0.1/jobs/1", "https://10.0.0.1/jobs/1", "https://2130706433/jobs/1", "https://0x7f.0.0.1/jobs/1", "https://host.local/jobs/1", "https://user:secret@example.com/jobs/1", "https://example.com:8080/jobs/1", "https://linkedin.com/jobs/search?keywords=Python", "https://example.com/jobs/search", "https://example.com/unsubscribe?jobId=12", "https://example.com/", "https://notlever.co/company/arbitrary"]:
            with self.subTest(url=url):
                self.assertEqual(gmail.job_url(url), "")

    def test_repeated_title_and_cta_links_deduplicate(self):
        jobs = gmail.parse_message(message(html='<a href="https://jobs.example.com/jobs/1">Data Scientist</a><p>Company: Example AS</p><a href="https://jobs.example.com/jobs/1?utm_medium=mail">Apply now</a>'), PROFILE)
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["title"], "Data Scientist")

    def test_plain_text_title_and_url_are_paired_without_inventing_company(self):
        jobs = gmail.parse_message(message(plain="Data Scientist\nCompany: Example AS\nLocation: Trondheim\nhttps://example.com/jobs/42\n\nOffice Manager\nhttps://example.com/jobs/43"), PROFILE)
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["company"], "Example AS")
        self.assertEqual(jobs[0]["location"], "Trondheim")
        self.assertEqual(jobs[0]["source_url"], "https://example.com/jobs/42")
        self.assertNotIn("Office Manager", jobs[0]["description"])

    def test_generic_cta_with_only_subject_match_is_ignored(self):
        item = message(html='<a href="https://example.com/jobs/12">View job</a>')
        item["payload"]["headers"].append({"name": "Subject", "value": "Data Scientist jobs"})
        self.assertEqual(gmail.parse_message(item, PROFILE), [])

    def test_hidden_html_attachments_and_exclusions_are_not_candidates(self):
        item = message(html='<script><a href="https://example.com/jobs/1">Data Scientist</a></script><style>Python</style><a href="https://example.com/jobs/2">Senior Data Scientist</a>')
        item["payload"]["parts"].append({"mimeType": "text/html", "filename": "attached.html", "body": {"data": base64.urlsafe_b64encode(b'<a href="https://example.com/jobs/3">AI Engineer</a>').decode()}})
        self.assertEqual(gmail.parse_message(item, PROFILE), [])

    def test_message_link_uses_thread_id_and_omits_invalid_or_missing_ids(self):
        html = '<a href="https://example.com/jobs/1">AI Engineer</a>'
        meta = gmail.parse_message(message(html=html), PROFILE)[0]["raw_json"]["email"]
        self.assertEqual(meta["message_url"], "https://mail.google.com/mail/u/0/#all/thread456")
        for thread_id in ("", "../unexpected?secret=yes"):
            job = gmail.parse_message(message(html=html, thread_id=thread_id), PROFILE)[0]
            self.assertEqual(job["raw_json"]["email"]["message_url"], "")

    def test_malformed_mime_parts_fail_closed_without_crashing(self):
        self.assertEqual(gmail._body_parts({"headers": None, "mimeType": None, "body": [], "parts": None}), ([], []))
        self.assertEqual(gmail._body_parts({"mimeType": "text/plain", "body": {"data": "%%%invalid%%%"}}), ([], []))
        self.assertEqual(gmail._body_parts({"mimeType": "text/plain", "body": {"data": "YWJj"}}, depth=21), ([], []))
        for item in (None, [], {"payload": []}):
            with self.subTest(item=item), self.assertRaises(gmail.GmailError):
                gmail.parse_message(item, PROFILE)

    def test_bad_date_falls_back_to_internal_gmail_time(self):
        item = message(html='<a href="https://example.com/jobs/1">AI Engineer</a>')
        item["payload"]["headers"] = []
        item["internalDate"] = "0"
        self.assertEqual(gmail.parse_message(item, PROFILE)[0]["raw_json"]["email"]["received_at"], "1970-01-01T00:00:00+00:00")

    def test_eml_multipart_ignores_attachments_and_preserves_unicode(self):
        item = EmailMessage()
        item["From"] = "Alerts <alerts@example.com>"
        item["Subject"] = "Nye stillinger — Trondheim"
        item.set_content("Plain alternative without links")
        item.add_alternative('<a href="https://example.com/jobs/1">AI Engineer</a><p>Company: Ørn AS</p>', subtype="html")
        item.add_attachment(b'<a href="https://example.com/jobs/2">Data Scientist</a>', maintype="text", subtype="html", filename="attachment.html")
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "alert.eml"
            path.write_bytes(item.as_bytes())
            with patch.object(gmail, "build_opener", side_effect=AssertionError("No network")):
                jobs = gmail.import_eml(path, PROFILE)
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["company"], "Ørn AS")
        self.assertEqual(jobs[0]["raw_json"]["email"]["import_method"], "eml")
        self.assertEqual(jobs[0]["raw_json"]["email"]["message_url"], "")

    def test_eml_size_bound_and_empty_or_malformed_email(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "alert.eml"
            path.write_bytes(b"Not a MIME message")
            self.assertEqual(gmail.import_eml(path, PROFILE), [])
            with patch.object(gmail, "MAX_MESSAGE_BYTES", 1), self.assertRaisesRegex(gmail.GmailError, "larger"):
                gmail.import_eml(path, PROFILE)


class GmailAuthorizationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.client_path = self.folder / "client.json"
        self.client_path.write_text(json.dumps({"installed": CLIENT}), encoding="utf-8")
        self.token = {**CLIENT, "scope": gmail.SCOPE, "access_token": "fixture-access", "refresh_token": "fixture-refresh", "expires_at": time.time() + 3600}

    def test_oauth_state_path_and_duplicate_parameters_rejected(self):
        self.assertEqual(gmail._callback_result("/oauth2callback?state=expected&code=code", "expected"), ("code", ""))
        for callback in ("/else?state=expected&code=code", "https://evil.example/oauth2callback?state=expected&code=code", "/oauth2callback?state=wrong&code=code", "/oauth2callback?state=expected&state=expected&code=code", "/oauth2callback?state=expected&code=one&code=two", "/oauth2callback?state=expected&code="):
            with self.subTest(callback=callback), self.assertRaises(gmail.GmailError):
                gmail._callback_result(callback, "expected")
        self.assertIn("not granted", gmail._callback_result("/oauth2callback?state=expected&error=access_denied", "expected")[1])

    def test_desktop_oauth_uses_pkce_and_ignores_bad_state(self):
        owner = self
        captured = {}

        class FakeServer:
            def __init__(self, address, handler):
                owner.assertEqual(address, ("127.0.0.1", 0))
                self.server_port, self.handler, self.count = 54321, handler, 0

            def __enter__(self):
                captured["server"] = self
                return self

            def __exit__(self, *args):
                return False

            def handle_request(self):
                self.count += 1
                callback = object.__new__(self.handler)
                callback.server = self
                callback.headers = {"Host": "127.0.0.1:54321"}
                state = "wrong-state" if self.count == 1 else captured["query"]["state"][0]
                callback.path = "/oauth2callback?" + urlencode({"state": state, "code": "test-code"})
                callback.send_response = Mock()
                callback.send_header = Mock()
                callback.end_headers = Mock()
                callback.wfile = io.BytesIO()
                callback.do_GET()
                owner.assertEqual(callback.send_response.call_args.args[0], 400 if self.count == 1 else 200)

        def browser(url, **kwargs):
            captured["query"] = parse_qs(urlsplit(url).query)
            return True

        with patch.object(gmail, "HTTPServer", FakeServer), patch.object(gmail.webbrowser, "open", side_effect=browser), patch.object(gmail, "_request_json", return_value={"access_token": "access", "refresh_token": "refresh", "scope": gmail.SCOPE}) as exchange, patch.object(gmail, "_save_token") as save, patch.object(gmail, "connection_status", return_value={"connected": True}):
            self.assertTrue(gmail.connect(self.folder, self.client_path)["connected"])
        query = captured["query"]
        form = exchange.call_args.kwargs["form"]
        self.assertEqual(query["scope"], [gmail.SCOPE])
        self.assertEqual(query["code_challenge_method"], ["S256"])
        self.assertEqual(query["redirect_uri"], ["http://127.0.0.1:54321/oauth2callback"])
        challenge = base64.urlsafe_b64encode(hashlib.sha256(form["code_verifier"].encode()).digest()).decode().rstrip("=")
        self.assertEqual(query["code_challenge"], [challenge])
        self.assertEqual(form["code"], "test-code")
        self.assertEqual(captured["server"].count, 2)
        save.assert_called_once()

    def test_rejected_client_types_never_open_browser(self):
        for content in ({"web": CLIENT}, {"type": "service_account"}, {"installed": {"client_id": "attacker.example"}}, []):
            self.client_path.write_text(json.dumps(content), encoding="utf-8")
            with self.subTest(content=content), patch.object(gmail.webbrowser, "open") as browser, self.assertRaises(gmail.GmailError):
                gmail.connect(self.folder, self.client_path)
            browser.assert_not_called()

    def test_sign_in_cancellation_keeps_existing_credentials(self):
        cancellation = Mock()
        cancellation.is_set.return_value = True

        class FakeServer:
            def __init__(self, address, handler):
                self.server_port = 54321
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False

        with patch.object(gmail, "HTTPServer", FakeServer), patch.object(gmail.webbrowser, "open", return_value=True), patch.object(gmail, "_save_token") as save, patch.object(gmail, "_request_json") as request, self.assertRaisesRegex(gmail.GmailError, "cancelled"):
            gmail.connect(self.folder, self.client_path, cancel_event=cancellation)
        save.assert_not_called()
        request.assert_not_called()

    def test_broad_scope_and_malformed_token_response_rejected(self):
        for response in ({"access_token": "access", "scope": gmail.SCOPE + " https://www.googleapis.com/auth/gmail.send"}, {"access_token": "access", "scope": None}, {"access_token": "access", "expires_in": "bad"}, {"access_token": "access", "refresh_token": {}}, {"access_token": []}):
            with self.subTest(response=response), self.assertRaises(gmail.GmailError):
                gmail._normalize_token(response, CLIENT)

    def test_valid_access_token_does_not_refresh(self):
        with patch.object(gmail, "_load_token", return_value=self.token), patch.object(gmail, "_request_json") as request:
            self.assertEqual(gmail._access_token(self.folder), "fixture-access")
        request.assert_not_called()

    def test_expired_access_refreshes_and_preserves_refresh_token(self):
        self.token["expires_at"] = 0
        with patch.object(gmail, "_load_token", return_value=self.token), patch.object(gmail, "_request_json", return_value={"access_token": "new-access", "expires_in": 3600}) as request, patch.object(gmail, "_save_token") as save:
            self.assertEqual(gmail._access_token(self.folder), "new-access")
        self.assertEqual(request.call_args.args[0], gmail.TOKEN_URL)
        self.assertEqual(request.call_args.kwargs["form"]["grant_type"], "refresh_token")
        self.assertEqual(save.call_args.args[1]["refresh_token"], "fixture-refresh")

    def test_revoked_refresh_has_actionable_error_without_token_details(self):
        with patch.object(gmail, "_load_token", return_value={**self.token, "expires_at": 0}), patch.object(gmail, "_request_json", side_effect=gmail._HTTPFailure(400)), self.assertRaisesRegex(gmail.GmailError, "seven days") as raised:
            gmail._access_token(self.folder)
        self.assertNotIn("fixture-refresh", str(raised.exception))

    def test_corrupt_expiry_and_missing_refresh_require_reconnect(self):
        for token in ({**self.token, "expires_at": "broken"}, {**self.token, "expires_at": float("inf")}, {**self.token, "expires_at": 0, "refresh_token": ""}):
            with self.subTest(token=token), patch.object(gmail, "_load_token", return_value=token), self.assertRaises(gmail.GmailError):
                gmail._access_token(self.folder)

    def test_unauthorized_read_refreshes_once(self):
        with patch.object(gmail, "_access_token", side_effect=["expired", "new"]) as access, patch.object(gmail, "_request_json", side_effect=[gmail._HTTPFailure(401), {"messages": []}]) as request:
            self.assertEqual(gmail._gmail_get(self.folder, gmail.API_URL), {"messages": []})
        self.assertEqual(access.call_args_list[1].kwargs, {"force_refresh": True})
        self.assertEqual(request.call_count, 2)

    def test_credentials_roundtrip_and_disconnect_leave_other_data(self):
        gmail._save_token(self.folder, self.token)
        stored = gmail._token_path(self.folder).read_text(encoding="utf-8")
        if os.name == "nt":
            self.assertIn("windows-dpapi", stored)
            self.assertNotIn("fixture-refresh", stored)
        self.assertEqual(gmail._load_token(self.folder), self.token)
        self.assertTrue(gmail.connection_status(self.folder)["connected"])
        database = self.folder / "vacancies.db"
        database.write_text("retained")
        with patch.object(gmail, "_request_json") as request:
            self.assertFalse(gmail.disconnect(self.folder)["connected"])
        self.assertEqual(database.read_text(), "retained")
        request.assert_not_called()


class GmailTransportTests(unittest.TestCase):
    def test_transport_rejects_mail_mutations_redirects_and_other_origins(self):
        for url, form in ((gmail.API_URL + "/send", {}), (gmail.API_URL + "/message/modify", {}), (gmail.API_URL, {}), ("https://example.com/collect", None), ("http://gmail.googleapis.com/gmail/v1/users/me/messages", None), ("https://gmail.googleapis.com:443/gmail/v1/users/me/messages", None)):
            with self.subTest(url=url), patch.object(gmail, "build_opener") as opener, self.assertRaises(gmail.GmailError):
                gmail._request_json(url, form=form, access_token="private")
            opener.assert_not_called()
        with self.assertRaises(gmail.GmailError):
            gmail._NoRedirect().redirect_request(None, None, 302, "", {}, "https://example.com")

    def test_message_api_transport_only_gets_and_scrubs_failure_body(self):
        opener = Mock()
        opener.open.side_effect = HTTPError(gmail.API_URL, 403, "private server message", {}, io.BytesIO(b"secret content"))
        with patch.object(gmail, "build_opener", return_value=opener), self.assertRaises(gmail.GmailError) as raised:
            gmail._request_json(gmail.API_URL + "?q=job", access_token="private-token")
        self.assertEqual(opener.open.call_args.args[0].method, "GET")
        self.assertNotIn("private", str(raised.exception))
        self.assertNotIn("secret", str(raised.exception))

    def test_fetch_bounded_page_deduplicates_and_retries_failures_explicitly(self):
        fixture = message(html='<a href="https://example.com/jobs/1">AI Engineer</a>')
        calls = []
        def read(folder, url):
            calls.append(url)
            if len(calls) == 1:
                return {"messages": [{"id": "one"}, {"id": "two"}, {"id": "three"}], "nextPageToken": "cursor2"}
            if "/three?" in url:
                raise gmail._HTTPFailure(404)
            return fixture
        with patch.object(gmail, "_gmail_get", side_effect=read), patch.object(gmail.webbrowser, "open", side_effect=AssertionError("No links opened")):
            result = gmail.fetch_candidates(Path("unused"), PROFILE, query="from:linkedin.com", page_token="cursor1", max_messages=3)
        query = parse_qs(urlsplit(calls[0]).query)
        self.assertEqual(query["pageToken"], ["cursor1"])
        self.assertEqual(query["includeSpamTrash"], ["false"])
        self.assertEqual(result["messages_scanned"], 2)
        self.assertEqual(len(result["candidates"]), 1)
        self.assertEqual(result["next_page_token"], "cursor2")
        self.assertEqual(result["failed_message_ids"], ["three"])
        self.assertTrue(result["warnings"])
        self.assertTrue(all(urlsplit(url).hostname == "gmail.googleapis.com" for url in calls))

    def test_invalid_query_or_batch_size_does_not_read_mail(self):
        for options in ({"query": ""}, {"query": "x" * 2001}, {"max_messages": 101}, {"max_messages": True}, {"page_token": []}):
            with self.subTest(options=options), patch.object(gmail, "_gmail_get") as read, self.assertRaises(gmail.GmailError):
                gmail.fetch_candidates(Path("unused"), PROFILE, **options)
            read.assert_not_called()

    def test_bad_message_does_not_abort_other_alerts(self):
        fixture = message(html='<a href="https://example.com/jobs/1">AI Engineer</a>')
        with patch.object(gmail, "_gmail_get", side_effect=[{"messages": [{"id": "bad"}, {"id": "good"}]}, {"payload": []}, fixture]):
            result = gmail.fetch_candidates(Path("unused"), PROFILE)
        self.assertEqual(len(result["candidates"]), 1)
        self.assertEqual(result["failed_message_ids"], ["bad"])


if __name__ == "__main__":
    unittest.main()
