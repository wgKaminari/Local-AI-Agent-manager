"""Optional real Chromium checks; every request is fulfilled by local fixtures.

Run with RUN_BROWSER_TESTS=1 after installing Playwright Chromium. No employer
or live application server is contacted, including for the test POST requests.
"""
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from norway_job_agent.application_forms import parse_html_form
from norway_job_agent.browser_delivery import BrowserApplicationSession, BrowserDeliveryError

URL = "https://jobs.example.test/apply"
HTML = """<!doctype html><html><body><form id="application" action="/submit" method="post">
<input type="hidden" name="csrf_token" value="fixture-csrf">
<label for="name">Full name</label><input id="name" name="name" required>
<label for="message">Why this role?</label><textarea id="message" name="message" required></textarea>
<label for="consent">Agree to the privacy terms</label><input type="checkbox" id="consent" name="consent" value="yes" required>
<button type="submit">Submit application</button></form></body></html>"""


class FixtureSession(BrowserApplicationSession):
    def __init__(self, html=HTML):
        super().__init__()
        self.html = html
        self.requests = []

    def _route(self, route):
        session = self
        class FixtureRoute:
            request = route.request
            def continue_(self):
                session.requests.append((self.request.method, self.request.url, self.request.post_data_buffer))
                route.fulfill(status=200, content_type="text/html", body=session.html if self.request.method == "GET" else "<h1>Fixture application received</h1>")
            def abort(self):
                route.abort()
        # Exercise production permission checks; only replace the transport.
        super()._route(FixtureRoute())


@unittest.skipUnless(os.environ.get("RUN_BROWSER_TESTS") == "1", "Opt-in real Chromium fixture checks")
class RealBrowserTests(unittest.TestCase):
    def setUp(self):
        from playwright.sync_api import BrowserType
        launch = BrowserType.launch
        self.patches = [
            patch.object(BrowserType, "launch", lambda obj, **kw: launch(obj, **{**kw, "headless": True})),
            patch("norway_job_agent.browser_delivery._public_addresses", return_value=["93.184.216.34"]),
            patch("norway_job_agent.browser_delivery._check_robots"),
        ]
        for item in self.patches:
            item.start()
        self.session = FixtureSession()
        self.answers = [
            {"field_id": "html:name", "answer": "Fictional Applicant", "selected_options": []},
            {"field_id": "html:message", "answer": "This is a fictional test answer.", "selected_options": []},
            {"field_id": "html:consent", "answer": "Agree to the privacy terms", "selected_options": ["yes"]},
        ]

    def tearDown(self):
        self.session.close()
        for item in reversed(self.patches):
            item.stop()

    def test_only_send_causes_one_post_and_double_send_is_rejected(self):
        preview = self.session.prepare(parse_html_form(HTML, URL), self.answers)
        self.assertEqual([item[0] for item in self.session.requests], ["GET"])
        self.assertNotIn("fixture-csrf", str(preview))
        result = self.session.send(preview["token"])
        self.assertEqual(result["status"], "submitted_unconfirmed")
        posts = [item for item in self.session.requests if item[0] == "POST"]
        self.assertEqual(len(posts), 1)
        self.assertIn(b"Fictional+Applicant", posts[0][2])
        self.assertIn(b"csrf_token=fixture-csrf", posts[0][2])
        with self.assertRaises(BrowserDeliveryError):
            self.session.send(preview["token"])

    def test_hidden_token_change_after_review_blocks_send(self):
        preview = self.session.prepare(parse_html_form(HTML, URL), self.answers)
        self.session._worker.submit(lambda: self.session._page.evaluate("document.querySelector('[name=csrf_token]').value='changed'")).result()
        with self.assertRaises(BrowserDeliveryError):
            self.session.send(preview["token"])
        self.assertFalse(any(item[0] == "POST" for item in self.session.requests))

    def test_submit_button_cannot_post_an_unreviewed_or_duplicate_answer(self):
        for name in ("extra_consent", "name"):
            with self.subTest(name=name):
                html = HTML.replace('<button type="submit">', '<button type="submit" name="' + name + '" value="unreviewed">')
                self.session.html = html
                with self.assertRaisesRegex(BrowserDeliveryError, "unreviewed application value"):
                    self.session.prepare(parse_html_form(html, URL), self.answers)
                self.assertFalse(any(item[0] == "POST" for item in self.session.requests))

    def test_exact_reviewed_file_bytes_are_uploaded(self):
        html = HTML.replace('method="post"', 'method="post" enctype="multipart/form-data"').replace('<button', '<label for="cv">CV</label><input type="file" name="cv" id="cv" required><button')
        self.session.html = html
        self.answers.append({"field_id": "html:cv", "answer": "", "selected_options": []})
        with tempfile.TemporaryDirectory() as directory:
            file = Path(directory) / "fictional-cv.txt"
            file.write_bytes(b"FICTIONAL CV FIXTURE")
            preview = self.session.prepare(parse_html_form(html, URL), self.answers, attachments={"html:cv": str(file)})
            self.assertEqual(preview["files"][0]["name"], file.name)
            file.write_bytes(b"CHANGED AFTER REVIEW")
            result = self.session.send(preview["token"])
            self.assertEqual(result["status"], "submitted_unconfirmed")
            post = next(item[2] for item in self.session.requests if item[0] == "POST")
            self.assertIn(b"FICTIONAL CV FIXTURE", post)
            self.assertNotIn(b"CHANGED AFTER REVIEW", post)


if __name__ == "__main__":
    unittest.main()
