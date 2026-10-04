"""Reviewed-delivery contracts using a fake browser; never contact employers.

The fake browser returns native-control snapshots, routes every simulated
request through the production guard, and records whether submission occurred.
It tests Python orchestration, not Chromium's execution of the DOM scripts.
"""
import copy
import hashlib
import json
import sys
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from norway_job_agent import browser_delivery as delivery
from norway_job_agent.application_forms import parse_html_form


URL = "https://careers.example.com/apply/1"
DESTINATION = "https://careers.example.com/applications"
HTML = '''<form id="application" action="/applications" method="post">
<label>Full name<input name="name" required></label>
<label>Why this role?<textarea name="motivation"></textarea></label>
<button type="submit">Send application</button>
</form>'''
FILE_HTML = HTML.replace('method="post"', 'method="post" enctype="multipart/form-data"').replace(
    '<button', '<label>CV<input type="file" name="resume" required></label><button')


def control(name, kind="text", **changes):
    value = {"name": name, "type": kind, "disabled": False, "readOnly": False,
             "required": False, "maxLength": None, "value": "", "checked": None,
             "options": [], "visible": True, "files": []}
    value.update(changes)
    return value


def snapshot(upload=False):
    enctype = "multipart/form-data" if upload else "application/x-www-form-urlencoded"
    fields = [control("name", required=True), control("motivation", "textarea")]
    if upload:
        fields.append(control("resume", "file", required=True))
    return {"action": DESTINATION, "method": "post", "enctype": enctype, "target": "",
            "fields": fields, "buttons": [{"name": "", "value": "",
                "label": "Send application", "action": DESTINATION, "method": "post",
                "enctype": enctype, "target": ""}],
            "valid": False, "frames": 0, "captcha": False, "hidden": []}


def reviewed(upload=False):
    answers = [{"field_id": "html:name", "answer": "Test Candidate", "selected_options": []},
               {"field_id": "html:motivation", "answer": "I build Python tools.", "selected_options": []}]
    if upload:
        answers.append({"field_id": "html:resume", "answer": "", "selected_options": []})
    return answers


class FakePage:
    def __init__(self, html=HTML, state=None):
        self.html = html
        self.url = URL
        self.main_frame = object()
        self.state = state or snapshot()
        self.route_callback = None
        self.requests = []
        self.fill_calls = 0
        self.submit_calls = 0
        self.valid_after_fill = True
        self.submit_status = 200
        self.navigation_error = False
        self.uploaded = []

    def request(self, url, method="GET", *, main=True, navigation=True):
        request = SimpleNamespace(url=url, method=method,
            frame=self.main_frame if main else object(), is_navigation_request=lambda: navigation)
        route = SimpleNamespace(request=request, continue_=Mock(), abort=Mock())
        self.route_callback(route)
        self.requests.append(route)
        return route

    def goto(self, url, **_kwargs):
        self.url = url
        self.request(url)

    def content(self):
        return self.html

    def evaluate(self, script, argument):
        if script == delivery._SNAPSHOT:
            return copy.deepcopy(self.state)
        if script == delivery._FILL:
            self.fill_calls += 1
            for answer in argument["answers"]:
                for field in self.state["fields"]:
                    if field["name"] != answer["field_id"][5:] or field["type"] in {"file", "hidden"}:
                        continue
                    if field["type"] in {"checkbox", "radio"}:
                        field["checked"] = field["value"] in answer["selected_options"]
                    elif field["type"] in {"select-one", "select-multiple"}:
                        for option in field["options"]:
                            option["selected"] = option["value"] in answer["selected_options"]
                    else:
                        field["value"] = answer["answer"]
            self.state["valid"] = self.valid_after_fill
            return None
        if script == delivery._SUBMIT:
            self.submit_calls += 1
            self.request(self.state["action"], "POST")
            return None
        raise AssertionError("Unexpected browser evaluation")

    def expect_navigation(self, **_kwargs):
        page = self

        class Navigation:
            value = SimpleNamespace(status=page.submit_status)

            def __enter__(self):
                return self

            def __exit__(self, *args):
                if page.navigation_error:
                    raise TimeoutError("Simulated navigation timeout")

        return Navigation()

    def locator(self, selector):
        name = json.loads(selector.split("name=", 1)[1][:-1])
        fields = [field for field in self.state["fields"] if field["name"] == name and field["type"] == "file"]

        def set_files(file):
            self.uploaded.append(copy.deepcopy(file))
            fields[0]["files"] = [{"name": file["name"], "size": len(file["buffer"]),
                "type": file["mimeType"], "sha256": hashlib.sha256(file["buffer"]).hexdigest()}] if file else []

        return SimpleNamespace(count=lambda: len(fields), set_input_files=set_files)


class BrowserHarness:
    def __init__(self, page=None):
        self.page = page or FakePage()
        self.context = Mock()
        self.context.new_page.return_value = self.page
        self.context.route.side_effect = lambda _pattern, callback: setattr(self.page, "route_callback", callback)
        self.browser = Mock()
        self.browser.new_context.return_value = self.context
        self.playwright = Mock()
        self.playwright.chromium.launch.return_value = self.browser
        self.factory = Mock()
        self.factory.return_value.start.return_value = self.playwright
        self.stack = ExitStack()

    def __enter__(self):
        api = SimpleNamespace(sync_playwright=self.factory)
        self.stack.enter_context(patch.dict(sys.modules, {"playwright": SimpleNamespace(), "playwright.sync_api": api}))
        self.addresses = self.stack.enter_context(patch.object(delivery, "_public_addresses", return_value=["203.0.113.9"]))
        self.robots = self.stack.enter_context(patch.object(delivery, "_check_robots"))
        self.session = delivery.BrowserApplicationSession()
        self.stack.callback(self.session.close)
        return self

    def __exit__(self, *args):
        return self.stack.__exit__(*args)

    def prepare(self, form=None, answers=None, attachments=None):
        return self.session.prepare(form or parse_html_form(self.page.html, URL),
            reviewed("resume" in self.page.html) if answers is None else answers, attachments)


class DeliverySessionTests(unittest.TestCase):
    def test_prepare_fills_review_snapshot_without_submitting(self):
        with BrowserHarness() as harness:
            preview = harness.prepare()
            self.assertEqual(preview["status"], "ready_for_review")
            self.assertEqual(preview["destination"], DESTINATION)
            self.assertEqual(preview["fields"], reviewed())
            self.assertTrue(preview["token"])
            self.assertEqual(harness.page.fill_calls, 1)
            self.assertEqual(harness.page.submit_calls, 0)
            self.assertEqual([r.request.method for r in harness.page.requests], ["GET"])
            harness.robots.assert_called_once_with(URL, {})
            harness.addresses.assert_called_once_with("careers.example.com", 443)
            options = harness.browser.new_context.call_args.kwargs
            self.assertFalse(options["java_script_enabled"])
            self.assertFalse(options["accept_downloads"])
            self.assertEqual(options["service_workers"], "block")
            args = harness.playwright.chromium.launch.call_args.kwargs
            self.assertFalse(args["headless"])
            self.assertIn("--no-proxy-server", args["args"])
            self.assertIn("MAP careers.example.com 203.0.113.9, MAP * ~NOTFOUND", " ".join(args["args"]))

    def test_prepare_binds_copies_not_mutable_caller_objects(self):
        form, answers = parse_html_form(HTML, URL), reviewed()
        with BrowserHarness() as harness:
            preview = harness.prepare(form, answers)
            form["fields"][0]["label"] = "A different question"
            answers[0]["answer"] = "A different person"
            self.assertEqual(harness.session.send(preview["token"])["status"], "submitted_unconfirmed")

    def test_send_requires_matching_token_and_permits_only_one_attempt(self):
        with BrowserHarness() as harness:
            with self.assertRaises(delivery.BrowserDeliveryError):
                harness.session.send("invented")
            preview = harness.prepare()
            for token in (None, "invented", 1):
                with self.subTest(token=token), self.assertRaises(delivery.BrowserDeliveryError):
                    harness.session.send(token)
            self.assertEqual(harness.page.submit_calls, 0)
            result = harness.session.send(preview["token"])
            self.assertEqual(result["status"], "submitted_unconfirmed")
            self.assertEqual(harness.page.submit_calls, 1)
            with self.assertRaises(delivery.BrowserDeliveryError):
                harness.session.send(preview["token"])
            self.assertEqual(harness.page.submit_calls, 1)

    def test_changed_questions_block_submission(self):
        with BrowserHarness() as harness:
            preview = harness.prepare()
            harness.page.html = HTML.replace("Full name", "Preferred alias")
            with self.assertRaisesRegex(delivery.BrowserDeliveryError, "changed after review"):
                harness.session.send(preview["token"])
            self.assertEqual(harness.page.submit_calls, 0)

    def test_changed_native_values_destinations_and_constraints_block_submission(self):
        changes = (
            lambda state: state["fields"][0].update(value="Another person"),
            lambda state: state["fields"][0].update(required=False),
            lambda state: state["fields"][0].update(maxLength=10),
            lambda state: state["buttons"][0].update(value="subscribe"),
            lambda state: state["buttons"][0].update(action=DESTINATION + "/other"),
            lambda state: state.update(valid=False),
        )
        for change in changes:
            with self.subTest(change=changes.index(change)), BrowserHarness() as harness:
                preview = harness.prepare()
                change(harness.page.state)
                with self.assertRaisesRegex(delivery.BrowserDeliveryError, "changed after review"):
                    harness.session.send(preview["token"])
                self.assertEqual(harness.page.submit_calls, 0)

    def test_changed_form_before_preview_closes_browser_without_filling(self):
        form = parse_html_form(HTML, URL)
        with BrowserHarness(FakePage(HTML.replace("Full name", "Changed question"))) as harness:
            with self.assertRaisesRegex(delivery.BrowserDeliveryError, "form changed"):
                harness.prepare(form)
            self.assertEqual(harness.page.fill_calls, 0)
            harness.browser.close.assert_called_once()
            harness.playwright.stop.assert_called_once()

    def test_native_validation_failure_blocks_preview(self):
        with BrowserHarness() as harness:
            harness.page.valid_after_fill = False
            with self.assertRaisesRegex(delivery.BrowserDeliveryError, "rejected one or more"):
                harness.prepare()
            self.assertEqual(harness.page.submit_calls, 0)
            harness.browser.close.assert_called_once()

    def test_required_blank_answer_rejected_before_network_or_browser_start(self):
        with BrowserHarness() as harness:
            answers = reviewed()
            answers[0]["answer"] = " "
            with self.assertRaisesRegex(delivery.BrowserDeliveryError, "required"):
                harness.prepare(answers=answers)
            harness.addresses.assert_not_called()
            harness.factory.assert_not_called()

    def test_missing_optional_field_is_not_silently_filled_from_page_defaults(self):
        with BrowserHarness() as harness:
            with self.assertRaisesRegex(delivery.BrowserDeliveryError, "every form field"):
                harness.prepare(answers=reviewed()[:1])
            harness.factory.assert_not_called()

    def test_manual_or_ats_forms_cannot_be_submitted(self):
        for provider in ("manual", "greenhouse"):
            with self.subTest(provider=provider), BrowserHarness() as harness:
                form = parse_html_form(HTML, URL)
                form["provider"] = provider
                with self.assertRaisesRegex(delivery.BrowserDeliveryError, "native HTML"):
                    harness.prepare(form)
                harness.factory.assert_not_called()

    def test_http_and_nonstandard_ports_rejected_before_dns(self):
        for url in ("http://careers.example.com/apply/1", "https://careers.example.com:8443/apply/1"):
            with self.subTest(url=url), BrowserHarness() as harness:
                form = parse_html_form(HTML, url)
                with self.assertRaisesRegex(delivery.BrowserDeliveryError, "HTTPS"):
                    harness.prepare(form)
                harness.addresses.assert_not_called()

    def test_timeout_after_post_is_uncertain_and_never_retried(self):
        with BrowserHarness() as harness:
            preview = harness.prepare()
            harness.page.navigation_error = True
            result = harness.session.send(preview["token"])
            self.assertEqual(result["status"], "uncertain")
            self.assertEqual(harness.page.submit_calls, 1)
            with self.assertRaises(delivery.BrowserDeliveryError):
                harness.session.send(preview["token"])
            self.assertEqual(harness.page.submit_calls, 1)

    def test_http_error_never_claims_employer_acceptance(self):
        with BrowserHarness() as harness:
            preview = harness.prepare()
            harness.page.submit_status = 422
            self.assertEqual(harness.session.send(preview["token"])["status"], "uncertain")

    def test_failure_before_post_is_blocked_and_not_retried(self):
        with BrowserHarness() as harness:
            preview = harness.prepare()
            evaluate = harness.page.evaluate

            def fail_submit(script, argument):
                if script == delivery._SUBMIT:
                    raise RuntimeError("Simulated browser closure")
                return evaluate(script, argument)

            with patch.object(harness.page, "evaluate", side_effect=fail_submit):
                self.assertEqual(harness.session.send(preview["token"])["status"], "blocked")
            self.assertFalse(any(r.request.method == "POST" for r in harness.page.requests))
            with self.assertRaises(delivery.BrowserDeliveryError):
                harness.session.send(preview["token"])

    def test_hidden_anti_forgery_value_is_bound_without_exposing_it(self):
        state = snapshot()
        state["hidden"] = [{"name": "csrfmiddlewaretoken", "sha256": "a" * 64}]
        with BrowserHarness(FakePage(state=state)) as harness:
            preview = harness.prepare()
            self.assertEqual(preview["technical_fields"], ["csrfmiddlewaretoken"])
            self.assertNotIn("a" * 64, json.dumps(preview))
            state["hidden"][0]["sha256"] = "b" * 64
            with self.assertRaisesRegex(delivery.BrowserDeliveryError, "changed after review"):
                harness.session.send(preview["token"])
            self.assertEqual(harness.page.submit_calls, 0)

    def test_close_is_idempotent_and_does_not_submit(self):
        with BrowserHarness() as harness:
            harness.prepare()
            harness.session.close()
            harness.session.close()
            harness.browser.close.assert_called_once()
            harness.playwright.stop.assert_called_once()
            self.assertEqual(harness.page.submit_calls, 0)

    def test_all_requests_blocked_while_user_reviews(self):
        with BrowserHarness() as harness:
            harness.prepare()
            for url, method, main, navigation in (
                (DESTINATION, "POST", True, True), (URL, "GET", True, True),
                (URL, "GET", False, True), (URL + "/image", "GET", True, False),
                ("https://other.example.com/tracker", "GET", True, False),
            ):
                with self.subTest(url=url, method=method, main=main, navigation=navigation):
                    route = harness.page.request(url, method, main=main, navigation=navigation)
                    route.abort.assert_called_once()
                    route.continue_.assert_not_called()

    def test_send_route_allows_exact_post_once_and_same_origin_receipt_get_only(self):
        with BrowserHarness() as harness:
            preview = harness.prepare()
            # Exercise the router during the send phase; no actual request occurs.
            harness.session._phase = "sending"
            for url, method in ((DESTINATION + "/other", "POST"), (URL, "GET"),
                                ("https://other.example.com/applications", "POST"),
                                ("https://127.0.0.1/applications", "POST")):
                route = harness.page.request(url, method)
                route.abort.assert_called_once()
            route = harness.page.request(DESTINATION, "POST")
            route.continue_.assert_called_once()
            harness.page.request(DESTINATION, "POST").abort.assert_called_once()
            harness.page.request("https://careers.example.com/thank-you").continue_.assert_called_once()
            harness.page.request("https://other.example.com/thank-you").abort.assert_called_once()
            self.assertTrue(preview["token"])

    def test_open_route_rejects_cross_origin_and_subresource_requests(self):
        with BrowserHarness() as harness:
            harness.prepare()
            harness.session._phase = "opening"
            for url, method, main, navigation in (
                (URL, "POST", True, True), (URL, "GET", False, True),
                (URL, "GET", True, False), ("https://other.example.com", "GET", True, True),
                ("file:///C:/private.txt", "GET", True, True),
            ):
                route = harness.page.request(url, method, main=main, navigation=navigation)
                route.abort.assert_called_once()


class SnapshotValidationTests(unittest.TestCase):
    def test_named_submitters_cannot_add_values_or_override_reviewed_answers(self):
        for name in ("consent", "name", "action"):
            with self.subTest(name=name), BrowserHarness() as harness:
                harness.page.state["buttons"][0].update(name=name, value="unreviewed")
                with self.assertRaisesRegex(delivery.BrowserDeliveryError, "unreviewed application value"):
                    harness.prepare()
                self.assertEqual(harness.page.fill_calls, 0)
                self.assertEqual(harness.page.submit_calls, 0)
                harness.browser.close.assert_called_once()

    def test_unknown_hidden_answers_and_colliding_hidden_fields_are_rejected(self):
        cases = [
            [{"name": "consent", "sha256": "a" * 64}],
            [{"name": "csrf_work_authorization", "sha256": "a" * 64}],
            [{"name": "", "sha256": "a" * 64}],
            [{"name": "csrf", "sha256": "a" * 64}] * 2,
        ]
        form = parse_html_form(HTML, URL)
        for hidden in cases:
            with self.subTest(hidden=hidden):
                state = snapshot()
                state["hidden"] = hidden
                with self.assertRaisesRegex(delivery.BrowserDeliveryError, "Hidden application values"):
                    delivery._validate_snapshot(state, form, reviewed())
        # A recognized anti-forgery name cannot also be an answer name.
        state = snapshot()
        state["fields"][0]["name"] = "csrf"
        state["hidden"] = [{"name": "csrf", "sha256": "a" * 64}]
        answers = reviewed()
        answers[0]["field_id"] = "html:csrf"
        with self.assertRaisesRegex(delivery.BrowserDeliveryError, "Hidden application values"):
            delivery._validate_snapshot(state, form, answers)

    def test_recognized_anti_forgery_controls_are_allowed(self):
        for name in ("csrfmiddlewaretoken", "_csrf", "__RequestVerificationToken", "authenticity_token", "__VIEWSTATE"):
            state = snapshot()
            state["hidden"] = [{"name": name, "sha256": "a" * 64}]
            with self.subTest(name=name):
                self.assertEqual(delivery._validate_snapshot(state, parse_html_form(HTML, URL), reviewed()), DESTINATION)

    def test_rejects_unreviewable_native_form_shapes(self):
        changes = [
            lambda state: state.update(frames=1),
            lambda state: state.update(captcha=True),
            lambda state: state.update(buttons=[]),
            lambda state: state["buttons"].append(copy.deepcopy(state["buttons"][0])),
            lambda state: state.update(method="get"),
            lambda state: state["buttons"][0].update(target="_blank"),
            lambda state: state["buttons"][0].update(action="https://other.example.com/send"),
            lambda state: state["buttons"][0].update(action=DESTINATION + "/override"),
            lambda state: state["buttons"][0].update(enctype="text/plain"),
            lambda state: state["fields"].append(control("extra", required=True)),
            lambda state: state["fields"][0].update(readOnly=True),
            lambda state: state["fields"][0].update(visible=False),
            lambda state: state["fields"][0].update(type="password"),
            lambda state: state["fields"].append(control("name")),
        ]
        form = parse_html_form(HTML, URL)
        for index, change in enumerate(changes):
            with self.subTest(index=index):
                state = snapshot()
                change(state)
                with self.assertRaises(delivery.BrowserDeliveryError):
                    delivery._validate_snapshot(state, form, reviewed())

    def test_browser_cannot_change_reviewed_choices(self):
        answers = [{"field_id": "html:tool", "answer": "Python", "selected_options": ["python"]}]
        cases = [control("tool", "radio", value="sql", checked=True),
                 control("tool", "select-one", options=[{"value": "sql", "selected": True}])]
        for field in cases:
            with self.subTest(kind=field["type"]), self.assertRaisesRegex(delivery.BrowserDeliveryError, "changed a reviewed selection"):
                delivery._validate_filled_values({"fields": [field]}, answers)

    def test_reviewed_choices_cannot_invent_values_or_repeat_single_select(self):
        form = parse_html_form('<form><label>Tools<select name="tool"><option value="py">Python</option><option value="sql">SQL</option></select></label></form>', URL)
        for choices in (["invented"], ["py", "sql"], ["py", "py"]):
            with self.subTest(choices=choices), self.assertRaises(delivery.BrowserDeliveryError):
                delivery._reviewed_answers(form, [{"field_id": "html:tool", "answer": "", "selected_options": choices}])


class AttachmentTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "candidate-cv.pdf"
        self.path.write_bytes(b"fixture PDF bytes")
        self.form = parse_html_form(FILE_HTML, URL)

    def test_preview_uploads_only_picked_file_and_exposes_digest_without_path_or_bytes(self):
        with BrowserHarness(FakePage(FILE_HTML, snapshot(upload=True))) as harness:
            preview = harness.prepare(attachments={"html:resume": self.path})
            self.assertEqual(harness.page.submit_calls, 0)
            self.assertEqual(harness.page.uploaded[0]["buffer"], b"fixture PDF bytes")
            self.assertEqual(preview["files"], [{"field_id": "html:resume", "name": self.path.name,
                "size": 17, "sha256": hashlib.sha256(b"fixture PDF bytes").hexdigest()}])
            self.assertNotIn(str(self.path), json.dumps(preview))
            self.assertNotIn("fixture PDF bytes", json.dumps(preview))
            # The reviewed bytes are frozen even if the source file later changes.
            self.path.write_bytes(b"changed after preview")
            self.assertEqual(harness.session.send(preview["token"])["status"], "submitted_unconfirmed")

    def test_changed_browser_attachment_blocks_send(self):
        with BrowserHarness(FakePage(FILE_HTML, snapshot(upload=True))) as harness:
            preview = harness.prepare(attachments={"html:resume": self.path})
            harness.page.state["fields"][-1]["files"][0]["sha256"] = "0" * 64
            with self.assertRaisesRegex(delivery.BrowserDeliveryError, "changed after review"):
                harness.session.send(preview["token"])
            self.assertEqual(harness.page.submit_calls, 0)

    def test_missing_required_file_and_typed_paths_are_rejected(self):
        with BrowserHarness(FakePage(FILE_HTML, snapshot(upload=True))) as harness:
            with self.assertRaisesRegex(delivery.BrowserDeliveryError, "required"):
                harness.prepare()
            answers = reviewed(upload=True)
            answers[-1]["answer"] = str(self.path)
            with self.assertRaisesRegex(delivery.BrowserDeliveryError, "file picker"):
                harness.prepare(answers=answers, attachments={"html:resume": self.path})
            harness.factory.assert_not_called()

    def test_upload_requires_multipart_encoding(self):
        html = FILE_HTML.replace(' enctype="multipart/form-data"', "")
        state = snapshot(upload=True)
        state["enctype"] = state["buttons"][0]["enctype"] = "application/x-www-form-urlencoded"
        with BrowserHarness(FakePage(html, state)) as harness:
            with self.assertRaisesRegex(delivery.BrowserDeliveryError, "encode file uploads"):
                harness.prepare(attachments={"html:resume": self.path})
            self.assertEqual(harness.page.submit_calls, 0)

    def test_unmatched_missing_and_empty_attachments_rejected(self):
        for attachments in ({"html:name": self.path}, {"html:resume": self.path.with_name("missing.pdf")},
                            {"html:resume": ""}, [str(self.path)]):
            with self.subTest(attachments=attachments), self.assertRaises(delivery.BrowserDeliveryError):
                delivery._load_attachments(self.form, attachments)
        self.path.write_bytes(b"")
        with self.assertRaises(delivery.BrowserDeliveryError):
            delivery._load_attachments(self.form, {"html:resume": self.path})

    def test_individual_and_total_byte_limits_are_enforced(self):
        with patch.object(delivery, "MAX_ATTACHMENT_BYTES", 16), self.assertRaises(delivery.BrowserDeliveryError):
            delivery._load_attachments(self.form, {"html:resume": self.path})
        form = parse_html_form(FILE_HTML.replace('<button', '<input type="file" name="letter"><button'), URL)
        with patch.object(delivery, "MAX_TOTAL_ATTACHMENT_BYTES", 30), self.assertRaises(delivery.BrowserDeliveryError):
            delivery._load_attachments(form, {"html:resume": self.path, "html:letter": self.path})


if __name__ == "__main__":
    unittest.main()
