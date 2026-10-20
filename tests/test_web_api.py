"""Loopback API integration tests with synthetic personal data and no network."""
import copy
import io
import json
import sys
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from norway_job_agent import service
from norway_job_agent.application_forms import parse_html_form, parse_manual_questions
from norway_job_agent.storage import JobStore
from norway_job_agent.web_api import create_app, close_resources
from support import nav_fetch, offline_collection


FORM_URL = "https://careers.example.com/apply/1"
FORM = parse_html_form('''<form action="/applications" method="post" enctype="multipart/form-data">
<label>Full name<input name="name" required></label>
<label>Resume<input name="resume" type="file"></label><button>Apply</button></form>''', FORM_URL)



def setUpModule():
    offline_collection()
    # Drafting starts an installed Ollama; tests use mocked generation and
    # must never launch the real local AI server.
    patcher = patch("norway_job_agent.runtime_setup.ensure_ollama")
    patcher.start()
    unittest.addModuleCleanup(patcher.stop)

class FakeBrowser:
    def __init__(self):
        self.calls = []
        self.sent = 0

    def prepare(self, form, answers, attachments):
        self.calls.append(("prepare", threading.get_ident()))
        self.attachments = copy.deepcopy(attachments)
        return {"token": "review-token", "destination": "https://careers.example.com/applications",
                "fields": copy.deepcopy(answers), "files": [], "status": "ready_for_review"}

    def send(self, token):
        self.calls.append(("send", threading.get_ident()))
        self.sent += 1
        return {"status": "submitted_unconfirmed", "message": "Fixture result"}

    def close(self):
        self.calls.append(("close", threading.get_ident()))


class LocalApiTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name)
        self.app = create_app(self.path, "session-secret")
        self.app.config.update(TESTING=True, LOCAL_ORIGIN="http://127.0.0.1:7654")
        self.client = self.app.test_client()
        self.headers = {"X-Session-Token": "session-secret", "Origin": "http://127.0.0.1:7654"}
        self.url = "http://127.0.0.1:7654"
        self.job_id = service.import_vacancy(self.path, {
            "title": "Data Scientist", "company": "Fixture Employer", "location": "Berlin, Germany",
            "source_url": FORM_URL, "description": "Build Python models.", "source": "manual"})[0]

    def tearDown(self):
        close_resources(self.app)
        self.app.extensions["local_state"].worker.shutdown(wait=True)
        self.temporary.cleanup()

    def request(self, method, route, **kwargs):
        kwargs.setdefault("headers", self.headers)
        kwargs.setdefault("base_url", self.url)
        return self.client.open(route, method=method, **kwargs)

    def post(self, route, data):
        return self.request("POST", route, json=data)

    def finish(self, response):
        self.assertEqual(response.status_code, 202, response.get_json())
        identifier = response.get_json()["task_id"]
        for _ in range(300):
            result = self.request("GET", f"/api/tasks/{identifier}").get_json()
            if result["status"] != "running":
                return result
            time.sleep(.01)
        self.fail("Background task did not finish")

    def payload(self):
        return {"job_id": self.job_id, "form": copy.deepcopy(FORM), "answers": [
            {"field_id": "html:name", "answer": "Fixture Person", "selected_options": [], "status": "user_edited"}]}

    def preview(self):
        result = self.finish(self.post("/api/delivery/preview", {"job_id": self.job_id, "payload": self.payload()}))
        self.assertEqual(result["status"], "done", result)
        return result["result"]

    def test_private_reads_require_token(self):
        for headers in ({}, {"X-Session-Token": "wrong"}):
            response = self.request("GET", "/api/bootstrap", headers=headers)
            self.assertEqual(response.status_code, 403)
            self.assertNotIn("profile", response.get_json())

    def test_cross_origin_and_null_origin_cannot_access_private_data(self):
        for origin in ("https://attacker.example", "http://127.0.0.1:9999", "null"):
            response = self.request("GET", "/api/profile", headers={**self.headers, "Origin": origin})
            self.assertEqual(response.status_code, 403)

    def test_cross_site_navigation_cannot_retrieve_session_html(self):
        result = self.request("GET", "/", headers={"Sec-Fetch-Site": "cross-site"})
        self.assertEqual(result.status_code, 403)
        self.assertNotIn("session-secret", result.get_data(as_text=True))

    def test_host_must_match_bound_address_exactly(self):
        for host in ("http://attacker.example:7654", "http://127.0.0.1:9999", "http://localhost:7654"):
            self.assertEqual(self.request("GET", "/api/profile", base_url=host).status_code, 403)

    def test_security_headers_and_no_cors(self):
        result = self.request("GET", "/api/bootstrap")
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.headers["Cache-Control"], "no-store")
        self.assertEqual(result.headers["X-Content-Type-Options"], "nosniff")
        self.assertIn("frame-ancestors 'none'", result.headers["Content-Security-Policy"])
        self.assertNotIn("unsafe-inline", result.headers["Content-Security-Policy"])
        self.assertNotIn("Access-Control-Allow-Origin", result.headers)

    def test_invalid_json_and_route_errors_are_json(self):
        for response in (self.request("POST", "/api/jobs", data="{broken", content_type="application/json"),
                         self.post("/api/jobs", []), self.request("GET", "/api/not-here"),
                         self.post("/api/tasks", {"action": "arbitrary-code"})):
            self.assertGreaterEqual(response.status_code, 400)
            self.assertIn("error", response.get_json())

    def test_private_paths_are_not_download_routes(self):
        for route in ("/assets/../../profile.json", "/assets/index.html", "/api/files/profile.json", "/profile.json"):
            response = self.request("GET", route)
            self.assertEqual(response.status_code, 404)

    def test_country_selection_keeps_shared_profile_unchanged(self):
        before = (self.path / "profile.json").read_bytes()
        result = self.post("/api/country", {"country": "DE"})
        self.assertEqual(result.status_code, 200, result.get_json())
        self.assertEqual(result.get_json()["active_country"], "DE")
        self.assertEqual(before, (self.path / "profile.json").read_bytes())
        self.assertTrue(self.request("GET", "/api/jobs?country=DE").get_json()["jobs"])
        self.assertEqual(self.request("GET", "/api/jobs?country=NO").get_json()["jobs"], [])

    def overlapping_settings_requests(self, method, route, payload):
        """Hold one read-modify-write open while another request arrives."""
        first_read, release = threading.Event(), threading.Event()
        second_started, second_done = threading.Event(), threading.Event()
        read_settings = service.read_settings
        entries = [{"id": code, "name": code, "countries": [code],
                    "source": {"type": "greenhouse", "board": code.lower()}}
                   for code in ("DE", "US")]

        def read(data_dir):
            result = read_settings(data_dir)
            if not first_read.is_set():
                first_read.set()
                if not release.wait(3):
                    raise RuntimeError("Fixture settings request was not released")
            return result

        def request(method, route, payload, *, second=False):
            if second:
                second_started.set()
            try:
                with self.app.test_client() as client:
                    return client.open(route, method=method, json=payload, headers=self.headers, base_url=self.url)
            finally:
                if second:
                    second_done.set()

        with patch("norway_job_agent.web_api.service.read_settings", side_effect=read), \
                patch("norway_job_agent.company_catalog.company_catalog", side_effect=lambda country: [entry for entry in entries if country in entry["countries"]]):
            with ThreadPoolExecutor(max_workers=2) as workers:
                first = workers.submit(request, "POST", "/api/country", {"country": "DE"})
                try:
                    self.assertTrue(first_read.wait(1))
                    second = workers.submit(request, method, route, payload, second=True)
                    self.assertTrue(second_started.wait(1))
                    self.assertFalse(second_done.wait(.1), "A settings mutation bypassed the existing read-modify-write")
                finally:
                    release.set()
                first_result, second_result = first.result(timeout=3), second.result(timeout=3)
        self.assertEqual(first_result.status_code, 200, first_result.get_json())
        self.assertEqual(second_result.status_code, 200, second_result.get_json())
        self.assertEqual(first_result.get_json()["active_country"], "DE")
        return read_settings(self.path), second_result.get_json()

    def test_concurrent_country_switches_keep_both_countries_presets(self):
        settings, response = self.overlapping_settings_requests("POST", "/api/country", {"country": "US"})
        self.assertEqual(settings["active_country"], "US")
        self.assertEqual(settings["country_presets_added"], ["DE", "US"])
        self.assertEqual({source.get("board") for source in settings["sources"] if source["type"] == "greenhouse"}, {"de", "us"})
        self.assertEqual(response, settings)

    def test_source_add_during_country_switch_keeps_both_changes(self):
        settings, response = self.overlapping_settings_requests("POST", "/api/sources/add", {"country": "US", "ids": ["US"]})
        self.assertEqual(settings["active_country"], "DE")
        self.assertEqual(settings["country_presets_added"], ["DE"])
        self.assertEqual({source.get("board") for source in settings["sources"] if source["type"] == "greenhouse"}, {"de", "us"})
        self.assertEqual(response, settings)

    def test_explicit_settings_save_is_ordered_after_pending_country_switch(self):
        requested = {"sources": [], "model": "qwen3:4b", "active_country": "US", "country_presets_added": ["DE", "US"]}
        settings, response = self.overlapping_settings_requests("PUT", "/api/settings", requested)
        # Saving fills in the default job-site section; the rest is as requested.
        self.assertEqual({key: value for key, value in settings.items() if key != "market"}, requested)
        self.assertEqual({key: value for key, value in response.items() if key != "market"}, requested)
        self.assertEqual(settings["market"]["searches"], [])

    def test_jobs_saved_and_application_views_preserve_workflow(self):
        self.post(f"/api/jobs/{self.job_id}/workflow", {"status": "saved", "notes": "My next step"})
        self.assertEqual(len(self.request("GET", "/api/jobs?view=saved").get_json()["jobs"]), 1)
        self.post(f"/api/jobs/{self.job_id}/workflow", {"status": "preparing", "notes": "Writing"})
        self.assertEqual(self.request("GET", "/api/jobs?view=saved").get_json()["jobs"], [])
        self.assertEqual(len(self.request("GET", "/api/jobs?view=applications").get_json()["jobs"]), 1)
        detail = self.request("GET", f"/api/jobs/{self.job_id}").get_json()
        self.assertEqual(detail["job"]["notes"], "Writing")
        self.assertEqual(detail["deliveries"], [])

    def test_archived_applications_are_reachable_with_explicit_status_filter(self):
        self.post(f"/api/jobs/{self.job_id}/workflow", {"status": "archived", "notes": "Keep for reference"})
        self.assertEqual(self.request("GET", "/api/jobs?view=applications").get_json()["jobs"], [])
        jobs = self.request("GET", "/api/jobs?view=applications&status=archived").get_json()["jobs"]
        self.assertEqual([job["id"] for job in jobs], [self.job_id])
        self.assertEqual(jobs[0]["notes"], "Keep for reference")

    def test_malformed_source_types_fail_without_changing_saved_settings(self):
        before = (self.path / "sources.json").read_bytes()
        for sources in (None, {}, [None], [[]], [{"type": []}], [{"type": {}}]):
            result = self.request("PUT", "/api/settings", json={"sources": sources})
            self.assertEqual(result.status_code, 400, result.get_json())
            self.assertEqual(before, (self.path / "sources.json").read_bytes())

    def test_appearance_persists_theme_and_preserves_legacy_sidebar_setting(self):
        service._write_json(self.path / "appearance.json", {"theme": "light", "sidebar_visible": False})
        profile = (self.path / "profile.json").read_bytes()
        settings = (self.path / "sources.json").read_bytes()
        response = self.request("PUT", "/api/appearance", json={"theme": "dark"})
        self.assertEqual(response.get_json(), {"theme": "dark", "sidebar_visible": False})
        self.assertEqual(self.request("GET", "/api/bootstrap").get_json()["appearance"], response.get_json())
        for value in ("unknown", [], None):
            self.assertEqual(self.request("PUT", "/api/appearance", json={"theme": value}).status_code, 400)
        self.assertEqual(self.request("GET", "/api/appearance").get_json(), response.get_json())
        self.assertEqual((self.path / "profile.json").read_bytes(), profile)
        self.assertEqual((self.path / "sources.json").read_bytes(), settings)

    def test_corrupt_appearance_uses_usable_defaults(self):
        for content in ("broken json", "[]", '{"theme": "sepia", "sidebar_visible": "no"}'):
            (self.path / "appearance.json").write_text(content, encoding="utf-8")
            self.assertEqual(self.request("GET", "/api/appearance").get_json(), {"theme": "light", "sidebar_visible": True})

    def test_for_you_excludes_unwanted_terms(self):
        profile = service.read_profile(self.path)
        profile["excluded_keywords"] = ["Python"]
        service.save_profile(self.path, profile)
        self.assertEqual(self.request("GET", "/api/jobs?view=for-you").get_json()["jobs"], [])

    def test_profile_invalid_save_is_atomic(self):
        before = (self.path / "profile.json").read_bytes()
        response = self.request("PUT", "/api/profile", json={"name": 18})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(before, (self.path / "profile.json").read_bytes())

    def test_edited_letter_and_answers_have_separate_versions(self):
        route = f"/api/jobs/{self.job_id}"
        for text in ("Original", "Edited"):
            self.assertEqual(self.post(route + "/letter", {"content": text}).status_code, 200)
        payload = self.payload()
        self.assertEqual(self.post(route + "/application", payload).status_code, 200)
        payload["answers"][0]["answer"] = "Updated person"
        self.assertEqual(self.post(route + "/application", payload).status_code, 200)
        detail = self.request("GET", route).get_json()
        self.assertEqual([entry["content"] for entry in detail["letters"]], ["Edited", "Original"])
        self.assertEqual([entry["payload"]["answers"][0]["answer"] for entry in detail["preparations"]], ["Updated person", "Fixture Person"])

    def test_application_from_different_job_rejected(self):
        payload = self.payload()
        payload["job_id"] = self.job_id + 1
        self.assertEqual(self.post(f"/api/jobs/{self.job_id}/application", payload).status_code, 400)
        self.assertEqual(service.application_history(self.path, self.job_id), [])

    def test_malformed_form_rejected_without_internal_error(self):
        for form in (None, [], {"fields": None}):
            payload = {**self.payload(), "form": form}
            self.assertEqual(self.post(f"/api/jobs/{self.job_id}/application", payload).status_code, 400)

    def test_background_collection_reports_live_progress(self):
        started, release = threading.Event(), threading.Event()
        def collect(data_dir, progress):
            progress("Checking fixture company")
            started.set()
            release.wait(3)
            return {"created": 2, "updated": 0}
        with patch("norway_job_agent.web_api.service.collect", side_effect=collect):
            response = self.post("/api/tasks", {"action": "collect"})
            self.assertTrue(started.wait(1))
            status = self.request("GET", "/api/tasks/" + response.get_json()["task_id"]).get_json()
            self.assertEqual(status["status"], "running")
            self.assertEqual(status["message"], "Checking fixture company")
            release.set()
            self.assertEqual(self.finish(response)["result"]["created"], 2)

    def test_background_failure_is_reported_without_discarding_data(self):
        with patch("norway_job_agent.web_api.service.available_models", side_effect=ValueError("Ollama is offline")):
            result = self.finish(self.post("/api/tasks", {"action": "models"}))
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["error"], "Ollama is offline")
        self.assertEqual(service.get_vacancy(self.path, self.job_id)["title"], "Data Scientist")

    def test_pdf_support_install_is_explicit_and_reports_completion(self):
        with patch("norway_job_agent.web_api.runtime_setup.install_pdf_support", return_value="PDF support is ready.") as install:
            self.request("GET", "/api/bootstrap")
            install.assert_not_called()
            result = self.finish(self.post("/api/tasks", {"action": "install-pdf"}))
        install.assert_called_once_with()
        self.assertEqual(result["status"], "done", result)
        self.assertEqual(result["result"]["message"], "PDF support is ready.")

    def test_manual_questions_persist_for_review(self):
        result = self.finish(self.post("/api/tasks", {"action": "questions", "job_id": self.job_id,
                                                       "manual_text": "Why this company?\nRelocate? | Yes | No"}))
        self.assertEqual(result["status"], "done", result)
        self.assertEqual(len(result["result"]["form"]["fields"]), 2)
        self.assertEqual(len(service.application_history(self.path, self.job_id)), 1)

    def test_cv_upload_extracts_text_without_persisting_original(self):
        response = self.request("POST", "/api/upload", data={"kind": "cv", "file": (io.BytesIO(b"Fixture CV\nPython skills"), "cv.txt")})
        self.assertEqual(response.get_json()["text"], "Fixture CV\nPython skills")
        self.assertFalse((self.path / "uploads").exists())

    def test_upload_rejects_executable_empty_and_oversize_files(self):
        for name, data in (("tool.exe", b"binary"), ("cv.pdf", b""), ("cv.pdf", b"x" * (10 * 1024 * 1024 + 1))):
            response = self.request("POST", "/api/upload", data={"kind": "attachment", "file": (io.BytesIO(data), name)})
            self.assertEqual(response.status_code, 400)
        self.assertFalse((self.path / "uploads").exists())

    def test_attachment_upload_stays_inside_private_storage(self):
        response = self.request("POST", "/api/upload", data={"kind": "attachment", "file": (io.BytesIO(b"fixture-pdf"), "../../private.pdf")})
        self.assertEqual(response.status_code, 200, response.get_json())
        uploaded = response.get_json()
        self.assertNotIn("path", uploaded)
        self.assertEqual(uploaded["name"], "private.pdf")
        payload = self.payload()
        payload["attachments"] = {"html:resume": uploaded["upload_id"]}
        result = self.post(f"/api/jobs/{self.job_id}/application", payload)
        self.assertEqual(result.status_code, 200, result.get_json())
        path = Path(service.application_history(self.path, self.job_id)[0]["payload"]["attachments"]["html:resume"])
        self.assertTrue(path.is_relative_to(self.path / "uploads"))
        detail = self.request("GET", f"/api/jobs/{self.job_id}").get_json()
        safe = detail["preparations"][0]["payload"]
        self.assertEqual(safe["attachments"]["html:resume"], uploaded["upload_id"])
        self.assertEqual(safe["attachment_files"]["html:resume"]["name"], "private.pdf")

    def test_arbitrary_file_path_is_never_attachment_authority(self):
        private = self.path / "sensitive.txt"
        private.write_text("Never upload", encoding="utf-8")
        for value in (str(private), "../sensitive.txt", {"path": str(private)}):
            payload = {**self.payload(), "attachments": {"html:resume": value}}
            self.assertEqual(self.post(f"/api/jobs/{self.job_id}/application", payload).status_code, 400)
            self.assertEqual(self.post("/api/delivery/preview", {"job_id": self.job_id, "payload": payload}).status_code, 400)

    def test_previously_selected_desktop_file_is_scoped_to_job_and_field(self):
        selected = self.path / "old-cv.pdf"
        selected.write_bytes(b"fixture")
        payload = {**self.payload(), "attachments": {"html:resume": str(selected)}}
        service.save_application(self.path, self.job_id, payload)
        safe = self.request("GET", f"/api/jobs/{self.job_id}").get_json()["preparations"][0]["payload"]
        self.assertEqual(self.post(f"/api/jobs/{self.job_id}/application", safe).status_code, 200)
        other_id = service.import_vacancy(self.path, {"title": "Other", "company": "Other", "source_url": FORM_URL + "2"})[0]
        safe["job_id"] = other_id
        self.assertEqual(self.post(f"/api/jobs/{other_id}/application", safe).status_code, 400)

    def test_gmail_credentials_never_return_as_data_and_are_temporary(self):
        credentials = {"installed": {"client_id": "fixture.apps.googleusercontent.com", "client_secret": "private-secret"}}
        result = self.request("POST", "/api/upload", data={"kind": "gmail", "file": (io.BytesIO(json.dumps(credentials).encode()), "credentials.json")})
        self.assertEqual(result.status_code, 200)
        self.assertNotIn("private-secret", result.get_data(as_text=True))
        upload_id = result.get_json()["upload_id"]
        with patch("norway_job_agent.web_api.gmail.connect", return_value={"connected": True}):
            self.assertEqual(self.finish(self.post("/api/tasks", {"action": "gmail-connect", "upload_id": upload_id}))["status"], "done")
        self.assertFalse(self.app.extensions["local_state"].uploads[upload_id]["path"].exists())

    def gmail_upload(self):
        credentials = {"installed": {"client_id": "fixture.apps.googleusercontent.com", "client_secret": "private-secret"}}
        result = self.request("POST", "/api/upload", data={"kind": "gmail", "file": (io.BytesIO(json.dumps(credentials).encode()), "credentials.json")})
        self.assertEqual(result.status_code, 200, result.get_json())
        return result.get_json()["upload_id"]

    def test_gmail_cancel_stops_connection_preserves_duplicate_guard_and_cleans_credentials(self):
        from norway_job_agent.gmail import GmailError
        started = threading.Event()
        observed = {}
        upload_id = self.gmail_upload()
        def connect(data_dir, path, *, cancel_event):
            observed["event"] = cancel_event
            started.set()
            if cancel_event.wait(3):
                raise GmailError("Gmail connection cancelled. Your existing connection was kept.")
            self.fail("Gmail connection was not cancelled")
        with patch("norway_job_agent.web_api.gmail.connect", side_effect=connect):
            response = self.post("/api/tasks", {"action": "gmail-connect", "upload_id": upload_id})
            try:
                self.assertTrue(started.wait(1))
                self.assertEqual(self.post("/api/tasks", {"action": "gmail-connect", "upload_id": upload_id}).status_code, 400)
                self.assertEqual(self.post("/api/tasks", {"action": "gmail-disconnect"}).status_code, 400)
                self.assertIs(self.app.extensions["local_state"].gmail_connect_cancel, observed["event"])
            finally:
                cancelled = self.post("/api/gmail/cancel", {})
            self.assertEqual(cancelled.get_json(), {"ok": True, "cancel_requested": True})
            result = self.finish(response)
        self.assertEqual(result["status"], "error")
        self.assertIn("cancelled", result["error"])
        self.assertIsNone(self.app.extensions["local_state"].gmail_connect_cancel)
        self.assertFalse(self.app.extensions["local_state"].uploads[upload_id]["path"].exists())
        self.assertEqual(self.post("/api/gmail/cancel", {}).get_json(), {"ok": True, "cancel_requested": False})
        with patch("norway_job_agent.web_api.gmail.connect", return_value={"connected": True}):
            next_response = self.post("/api/tasks", {"action": "gmail-connect", "upload_id": self.gmail_upload()})
            self.assertEqual(self.finish(next_response)["status"], "done")

    def test_cancel_queued_gmail_connection_never_opens_sign_in(self):
        state = self.app.extensions["local_state"]
        release = threading.Event()
        entered = [threading.Event() for _ in range(4)]
        for ready in entered:
            state.worker.submit(lambda ready=ready: (ready.set(), release.wait(3)))
        upload_id = self.gmail_upload()
        with patch("norway_job_agent.web_api.gmail.connect") as connect:
            try:
                self.assertTrue(all(ready.wait(1) for ready in entered))
                response = self.post("/api/tasks", {"action": "gmail-connect", "upload_id": upload_id})
                self.assertTrue(self.post("/api/gmail/cancel", {}).get_json()["cancel_requested"])
            finally:
                release.set()
            self.assertIn("cancelled", self.finish(response)["error"])
        connect.assert_not_called()
        self.assertFalse(state.uploads[upload_id]["path"].exists())

    def test_shutdown_cancels_running_gmail_sign_in(self):
        from norway_job_agent.gmail import GmailError
        started = threading.Event()
        def connect(data_dir, path, *, cancel_event):
            started.set()
            if cancel_event.wait(3):
                raise GmailError("Gmail connection cancelled")
            self.fail("Closing the app did not cancel Gmail sign-in")
        with patch("norway_job_agent.web_api.gmail.connect", side_effect=connect):
            response = self.post("/api/tasks", {"action": "gmail-connect", "upload_id": self.gmail_upload()})
            self.assertTrue(started.wait(1))
            close_resources(self.app)
            self.assertIn("cancelled", self.finish(response)["error"])

    def test_disconnect_does_not_race_a_gmail_token_refresh(self):
        started, release = threading.Event(), threading.Event()
        def fetch(*args, **kwargs):
            started.set()
            release.wait(3)
            return {"candidates": []}
        with patch("norway_job_agent.web_api.gmail.fetch_candidates", side_effect=fetch):
            response = self.post("/api/tasks", {"action": "gmail-fetch"})
            try:
                self.assertTrue(started.wait(1))
                self.assertEqual(self.post("/api/tasks", {"action": "gmail-disconnect"}).status_code, 400)
            finally:
                release.set()
            self.assertEqual(self.finish(response)["status"], "done")
        self.assertEqual(self.finish(self.post("/api/tasks", {"action": "gmail-disconnect"}))["status"], "done")

    def test_gmail_submission_failure_releases_cancel_state_for_retry(self):
        state = self.app.extensions["local_state"]
        upload_id = self.gmail_upload()
        with patch.object(state.worker, "submit", side_effect=ValueError("Executor unavailable")):
            response = self.post("/api/tasks", {"action": "gmail-connect", "upload_id": upload_id})
        self.assertEqual(response.status_code, 400)
        self.assertIsNone(state.gmail_connect_cancel)
        self.assertTrue(state.uploads[upload_id]["path"].exists())
        self.assertEqual(state.active_actions, set())

    def test_delivery_requires_review_then_explicit_matching_token(self):
        browser = FakeBrowser()
        with patch("norway_job_agent.web_api.BrowserApplicationSession", return_value=browser):
            result = self.preview()
            self.assertEqual(browser.sent, 0)
            route = "/api/delivery/" + result["preview_id"] + "/send"
            self.assertEqual(self.post(route, {"token": "wrong"}).status_code, 400)
            self.assertEqual(browser.sent, 0)
            sent = self.finish(self.post(route, {"token": result["preview"]["token"]}))
            self.assertEqual(sent["result"]["status"], "submitted_unconfirmed")
            self.assertEqual(browser.sent, 1)
            self.assertEqual(self.post(route, {"token": result["preview"]["token"]}).status_code, 400)
            self.assertEqual(browser.calls[0][1], browser.calls[1][1])
            self.assertEqual(service.get_vacancy(self.path, self.job_id)["status"], "new")
            detail = self.request("GET", f"/api/jobs/{self.job_id}").get_json()
            self.assertEqual(len(detail["deliveries"]), 1)

    def test_application_edits_invalidate_old_browser_preview(self):
        browser = FakeBrowser()
        with patch("norway_job_agent.web_api.BrowserApplicationSession", return_value=browser):
            result = self.preview()
            payload = self.payload()
            payload["answers"][0]["answer"] = "Edited after review"
            self.assertEqual(self.post(f"/api/jobs/{self.job_id}/application", payload).status_code, 200)
            response = self.post("/api/delivery/" + result["preview_id"] + "/send", {"token": "review-token"})
            self.assertEqual(response.status_code, 400)
            self.assertIn("edited", response.get_json()["error"])
            self.assertEqual(browser.sent, 0)

    def test_closed_preview_cannot_send(self):
        browser = FakeBrowser()
        with patch("norway_job_agent.web_api.BrowserApplicationSession", return_value=browser):
            result = self.preview()
            route = "/api/delivery/" + result["preview_id"]
            self.assertEqual(self.post(route + "/close", {}).status_code, 200)
            self.assertEqual(self.post(route + "/send", {"token": "review-token"}).status_code, 400)
            self.assertEqual(browser.sent, 0)

    def test_preview_failure_closes_and_releases_capacity(self):
        browser = FakeBrowser()
        with patch("norway_job_agent.web_api.BrowserApplicationSession", return_value=browser), patch.object(browser, "prepare", side_effect=ValueError("Unsupported form")):
            response = self.post("/api/delivery/preview", {"job_id": self.job_id, "payload": self.payload()})
            result = self.finish(response)
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["error"], "Unsupported form")
        self.assertEqual(self.app.extensions["local_state"].previews, {})
        self.assertEqual(browser.calls[0][0], "close")

    def test_shutdown_is_idempotent_and_rejects_new_work(self):
        close_resources(self.app)
        close_resources(self.app)
        self.assertEqual(self.post("/api/tasks", {"action": "models"}).status_code, 400)

    def test_non_ascii_tokens_are_rejected_without_server_errors(self):
        response = self.request("GET", "/api/profile", headers={**self.headers, "X-Session-Token": "évil-token"})
        self.assertEqual(response.status_code, 403)
        with patch("norway_job_agent.web_api.BrowserApplicationSession", return_value=FakeBrowser()):
            result = self.preview()
            self.assertEqual(self.post("/api/delivery/" + result["preview_id"] + "/send", {"token": "évil-token"}).status_code, 400)

    def test_invalid_action_and_form_types_do_not_start_tasks(self):
        for action in (None, [], {}, 4, True):
            self.assertEqual(self.post("/api/tasks", {"action": action}).status_code, 400)
        response = self.post("/api/tasks", {"action": "answers", "job_id": self.job_id, "form": {"fields": None}})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.app.extensions["local_state"].tasks, {})

    def test_missing_jobs_cannot_receive_letters_answers_or_notes(self):
        for suffix, body in (("letter", {"content": "Draft"}), ("application", self.payload()),
                             ("workflow", {"status": "saved", "notes": "Private notes"})):
            self.assertEqual(self.post("/api/jobs/99999/" + suffix, body).status_code, 400)

    def test_duplicate_background_actions_are_rejected_until_finished(self):
        started, release = threading.Event(), threading.Event()
        def collect(data_dir, progress):
            started.set()
            release.wait(3)
            return {"created": 0}
        with patch("norway_job_agent.web_api.service.collect", side_effect=collect):
            response = self.post("/api/tasks", {"action": "collect"})
            try:
                self.assertTrue(started.wait(1))
                self.assertEqual(self.post("/api/tasks", {"action": "collect"}).status_code, 400)
            finally:
                release.set()
            self.assertEqual(self.finish(response)["status"], "done")
            self.assertEqual(self.finish(self.post("/api/tasks", {"action": "collect"}))["status"], "done")

    def test_slower_generated_answers_keep_newer_explicit_save_current(self):
        started, release = threading.Event(), threading.Event()
        generated = self.payload()
        generated["answers"][0]["answer"] = "Generated earlier"
        def generate(*args, **kwargs):
            started.set()
            release.wait(3)
            return copy.deepcopy(generated)
        with patch("norway_job_agent.application_answers.generate_application_answers", side_effect=generate):
            response = self.post("/api/tasks", {"action": "answers", "job_id": self.job_id, "form": FORM})
            try:
                self.assertTrue(started.wait(1))
                edited = self.payload()
                edited["answers"][0]["answer"] = "My newer edit"
                self.assertEqual(self.post(f"/api/jobs/{self.job_id}/application", edited).status_code, 200)
            finally:
                release.set()
            result = self.finish(response)
        self.assertEqual(result["status"], "done", result)
        self.assertEqual(result["result"]["answers"][0]["answer"], "Generated earlier")
        history = service.application_history(self.path, self.job_id)
        self.assertEqual(history[0]["payload"]["answers"][0]["answer"], "My newer edit")
        self.assertEqual(history[1]["payload"]["answers"][0]["answer"], "Generated earlier")

    def test_slower_question_discovery_keeps_newer_explicit_save_current(self):
        started, release = threading.Event(), threading.Event()
        discovered = {"form": parse_manual_questions("An earlier question?"), "answers": [], "job_id": self.job_id}
        def discover(*args):
            started.set()
            release.wait(3)
            return copy.deepcopy(discovered)
        with patch("norway_job_agent.web_api.service.discover_application", side_effect=discover):
            response = self.post("/api/tasks", {"action": "questions", "job_id": self.job_id})
            try:
                self.assertTrue(started.wait(1))
                self.assertEqual(self.post(f"/api/jobs/{self.job_id}/application", self.payload()).status_code, 200)
                self.assertEqual(self.post("/api/tasks", {"action": "answers", "job_id": self.job_id, "form": FORM}).status_code, 400)
            finally:
                release.set()
            self.assertEqual(self.finish(response)["status"], "done")
        history = service.application_history(self.path, self.job_id)
        self.assertEqual(history[0]["payload"]["form"], FORM)
        self.assertEqual(history[1]["payload"]["form"], discovered["form"])

    def test_slower_generated_letter_keeps_newer_explicit_save_current(self):
        started, release = threading.Event(), threading.Event()
        def generate(*args, **kwargs):
            started.set()
            release.wait(3)
            return {"cover_letter": "Generated earlier", "review_notes": [], "used_evidence": []}
        with patch("norway_job_agent.local_ai.generate_cover_letter", side_effect=generate):
            response = self.post("/api/tasks", {"action": "letter", "job_id": self.job_id})
            try:
                self.assertTrue(started.wait(1))
                self.assertEqual(self.post(f"/api/jobs/{self.job_id}/letter", {"content": "My newer letter"}).status_code, 200)
            finally:
                release.set()
            result = self.finish(response)
        self.assertEqual(result["status"], "done", result)
        self.assertNotIn("draft_metadata", result["result"])
        self.assertEqual([entry["content"] for entry in service.draft_history(self.path, self.job_id)][:2],
                         ["My newer letter", "Generated earlier"])

    def test_generated_versions_persist_without_additional_restore_when_unedited(self):
        with patch("norway_job_agent.application_answers.generate_application_answers", return_value=self.payload()):
            result = self.finish(self.post("/api/tasks", {"action": "answers", "job_id": self.job_id, "form": FORM}))
        self.assertEqual(result["status"], "done", result)
        self.assertEqual(len(service.application_history(self.path, self.job_id)), 1)
        with patch("norway_job_agent.local_ai.generate_cover_letter", return_value={"cover_letter": "A generated letter"}):
            result = self.finish(self.post("/api/tasks", {"action": "letter", "job_id": self.job_id}))
        self.assertEqual(result["status"], "done", result)
        self.assertEqual(len(service.draft_history(self.path, self.job_id)), 1)

    def test_constructor_failure_releases_preview_capacity(self):
        with patch("norway_job_agent.web_api.BrowserApplicationSession", side_effect=ValueError("Install Chromium")):
            result = self.finish(self.post("/api/delivery/preview", {"job_id": self.job_id, "payload": self.payload()}))
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["error"], "Install Chromium")
        self.assertEqual(self.app.extensions["local_state"].previews, {})

    def test_queued_send_checks_for_edits_again_before_delivery(self):
        browser, release = FakeBrowser(), threading.Event()
        with patch("norway_job_agent.web_api.BrowserApplicationSession", return_value=browser):
            result = self.preview()
            entry = self.app.extensions["local_state"].previews[result["preview_id"]]
            entry["executor"].submit(lambda: release.wait(3))
            response = self.post("/api/delivery/" + result["preview_id"] + "/send", {"token": "review-token"})
            try:
                self.assertEqual(response.status_code, 202)
                self.assertEqual(self.post(f"/api/jobs/{self.job_id}/application", self.payload()).status_code, 200)
            finally:
                release.set()
            status = self.finish(response)
        self.assertEqual(status["status"], "error")
        self.assertIn("edited", status["error"])
        self.assertEqual(browser.sent, 0)
        with JobStore(self.path / "vacancies.db") as store:
            self.assertEqual(store.deliveries(self.job_id), [])

    def test_closing_a_preview_cancels_a_queued_send(self):
        browser, release = FakeBrowser(), threading.Event()
        with patch("norway_job_agent.web_api.BrowserApplicationSession", return_value=browser):
            result = self.preview()
            entry = self.app.extensions["local_state"].previews[result["preview_id"]]
            entry["executor"].submit(lambda: release.wait(3))
            route = "/api/delivery/" + result["preview_id"]
            response = self.post(route + "/send", {"token": "review-token"})
            try:
                self.assertEqual(response.status_code, 202)
                self.assertEqual(self.post(route + "/close", {}).status_code, 200)
            finally:
                release.set()
            status = self.finish(response)
            entry["executor"].shutdown(wait=True)
        self.assertEqual(status["status"], "error")
        self.assertIn("closed", status["error"])
        self.assertEqual(browser.sent, 0)
        self.assertEqual([name for name, _ in browser.calls], ["prepare", "close"])
        self.assertEqual(len({thread for _, thread in browser.calls}), 1)

    def test_duplicate_application_delivery_is_blocked_across_previews(self):
        first, second = FakeBrowser(), FakeBrowser()
        with patch("norway_job_agent.web_api.BrowserApplicationSession", side_effect=[first, second]):
            one = self.preview()
            result = self.finish(self.post("/api/delivery/" + one["preview_id"] + "/send", {"token": "review-token"}))
            self.assertEqual(result["status"], "done", result)
            two = self.preview()
            result = self.finish(self.post("/api/delivery/" + two["preview_id"] + "/send", {"token": "review-token"}))
        self.assertEqual(result["status"], "error")
        self.assertEqual(first.sent, 1)
        self.assertEqual(second.sent, 0)

    def test_attachment_handles_cannot_attach_to_a_text_question(self):
        result = self.request("POST", "/api/upload", data={"kind": "attachment", "file": (io.BytesIO(b"fixture"), "cv.pdf")})
        payload = {**self.payload(), "attachments": {"html:name": result.get_json()["upload_id"]}}
        self.assertEqual(self.post(f"/api/jobs/{self.job_id}/application", payload).status_code, 400)

    def test_unicode_and_long_upload_names_preserve_supported_extensions(self):
        for filename in ("резюме.pdf", "履歴書.PDF", "a" * 300 + ".pdf"):
            result = self.request("POST", "/api/upload", data={"kind": "attachment", "file": (io.BytesIO(b"fixture"), filename)})
            self.assertEqual(result.status_code, 200, result.get_json())
            uploaded = result.get_json()
            self.assertTrue(uploaded["name"].endswith(".pdf"))
            self.assertLessEqual(len(uploaded["name"]), 84)
            self.assertEqual(self.app.extensions["local_state"].uploads[uploaded["upload_id"]]["path"].read_bytes(), b"fixture")

    def test_task_submission_failure_does_not_leave_running_work(self):
        state = self.app.extensions["local_state"]
        with patch.object(state.worker, "submit", side_effect=RuntimeError("Executor unavailable")):
            with self.assertRaises(RuntimeError):
                state.task(lambda progress: None, key=("models", None))
        self.assertEqual(state.tasks, {})
        self.assertEqual(state.active_actions, set())


if __name__ == "__main__":
    unittest.main()
