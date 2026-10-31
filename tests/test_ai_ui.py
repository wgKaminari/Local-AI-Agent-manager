"""Opt-in Chromium checks for the AI assistant screens, with a fake provider.

RUN_BROWSER_TESTS=1 enables these tests after installing Playwright Chromium.
No request leaves the computer: providers are replaced by a fake engine.
"""
import os
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from norway_job_agent import cloud_ai, service
from norway_job_agent.modern_desktop import LocalWorkspace

ANALYSIS = {"summary": "A backend role building Python services.", "verdict": "strong", "verdict_reason": "Python and SQL match.",
            "strengths": ["Python: Built a fictional reporting tool."], "gaps": ["Kubernetes"], "red_flags": [],
            "emphasise": ["Your reporting tool."],
            "key_facts": {"salary": "NOK 650 000", "tech_stack": ["Python", "SQL"], "seniority": "", "remote_policy": "Hybrid",
                          "visa_relocation": "", "languages_required": [], "employment_type": "", "deadline": "", "contact": ""}}


class FakeEngine:
    is_cloud = True
    label = "fake-model (Fake)"

    def __init__(self):
        self.calls = []

    def generate(self, system, messages, *, json_mode=False, max_tokens=16000, on_text=None, progress=None):
        self.calls.append(system)
        if "Analyse the vacancy" in system:
            text = json.dumps(ANALYSIS)
        elif "Draft the candidate's reply" in system:
            text = json.dumps({"subject": "Re: Interview", "reply": "Hello,\nThank you, I would be glad to meet.\nFictional Applicant",
                               "review_notes": ["Add your available times."]})
        else:
            text = "**Yes**, you fit well:\n\n- Python matches\n- SQL matches\n\n<img src=x onerror=alert(1)>"
        if on_text is not None:
            for end in range(5, len(text) + 5, 5):
                on_text(text[:end])
                time.sleep(0.02)
        return {"text": text}


@unittest.skipUnless(os.environ.get("RUN_BROWSER_TESTS") == "1", "Opt-in real Chromium desktop-interface checks")
class AiInterfaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from playwright.sync_api import sync_playwright
        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch(headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()

    def setUp(self):
        from playwright.sync_api import expect
        self.expect = expect
        self.temporary = tempfile.TemporaryDirectory()
        self.data = Path(self.temporary.name)
        service.initialize(self.data)
        profile = service.read_profile(self.data)
        profile.update(name="Fictional Applicant", summary="Build useful software.", skills=["Python", "SQL"],
                       target_roles=["Python Developer"], evidence=["Built a fictional reporting tool."], preferred_locations=[])
        service.save_profile(self.data, profile)
        settings = service.read_settings(self.data)
        settings.update(active_country="", sources=[])
        settings["market"]["enabled"] = {name: False for name in settings["market"]["enabled"]}
        service.save_settings(self.data, settings)
        self.job_id = service.import_vacancy(self.data, {
            "title": "Python Developer", "company": "Nordic Fixture", "location": "Oslo, Norway",
            "description": "Build reliable Python services and SQL reports. Salary NOK 650 000. Hybrid.",
            "source": "manual", "source_url": "https://jobs.example.test/NO"})[0]
        self.engine = FakeEngine()
        self.workspace = LocalWorkspace(self.data).start()
        self.context = self.browser.new_context(viewport={"width": 1440, "height": 960})
        self.context.grant_permissions(["clipboard-read", "clipboard-write"])
        self.blocked, self.page_errors, self.console_errors = [], [], []
        allowed = urlsplit(self.workspace.url).netloc

        def route(request_route):
            if urlsplit(request_route.request.url).netloc == allowed:
                request_route.continue_()
            else:
                self.blocked.append(request_route.request.url)
                request_route.abort()

        self.context.route("**/*", route)
        self.page = self.context.new_page()
        self.page.on("pageerror", lambda error: self.page_errors.append(str(error)))
        self.page.on("console", lambda message: self.console_errors.append(message.text) if message.type == "error" else None)
        self.page.on("dialog", lambda dialog: dialog.accept())
        self.page.set_default_timeout(5000)
        self.page.goto(self.workspace.url)
        self.expect(self.page.locator(".job-card")).to_have_count(1)

    def tearDown(self):
        try:
            self.context.close()
            self.workspace.close()
            self.workspace.app.extensions["local_state"].worker.shutdown(wait=True)
            self.temporary.cleanup()
        finally:
            self.assertEqual(self.blocked, [], "The interface attempted nonlocal network access")
            self.assertEqual(self.page_errors, [], "An uncaught frontend error occurred")
            self.assertEqual(self.console_errors, [])

    def fake_provider(self):
        patcher = patch.object(cloud_ai, "engine", return_value=self.engine)
        patcher.start()
        self.addCleanup(patcher.stop)

    def nav(self, label):
        self.page.get_by_role("navigation", name="Main navigation").get_by_role("button", name=label, exact=True).click()

    def test_adding_a_key_and_choosing_claude_needs_confirmation(self):
        models = [{"id": "claude-opus-5-5", "name": "Claude Opus 5.5"}, {"id": "claude-haiku-5-5", "name": "Claude Haiku 5.5"}]
        with patch.object(cloud_ai, "list_models", return_value=models):
            self.nav("Settings & activity")
            panel = self.page.locator("#ai-panel")
            self.expect(panel.locator(".panel-title .pill")).to_have_text("Local · free")
            self.expect(panel.locator('input[name=ai-provider][value=anthropic]')).to_be_disabled()
            panel.locator("#ai-key-anthropic").fill("sk-ant-api03-FictionalKey-0123456789abcd")
            panel.get_by_role("button", name="Check and save key").first.click()
            self.expect(self.page.locator("#notifications")).to_contain_text("Claude key checked and saved")
        self.expect(panel.locator("#ai-key-anthropic")).to_have_count(0)
        self.expect(panel.locator("[data-ai-model=anthropic] option")).to_have_count(2)
        # The choice changes only after you confirm what is sent.
        panel.locator('input[name=ai-provider][value=anthropic]').click()
        modal = self.page.locator("#modal")
        self.expect(panel.locator('input[name=ai-provider][value=local]')).to_be_checked()
        self.expect(modal).to_contain_text("your profile and CV text")
        modal.get_by_role("button", name="Use Claude").click()
        self.expect(panel.locator(".panel-title .pill")).to_have_text("Claude")
        self.expect(self.page.locator(".statusbar .right")).to_contain_text("AI: Claude")
        self.assertEqual(cloud_ai.read_settings(self.data)["provider"], "anthropic")
        panel.locator("[data-ai-model=anthropic]").select_option("claude-haiku-5-5")
        self.expect(self.page.locator("#notifications")).to_contain_text("Claude will use claude-haiku-5-5")
        self.assertEqual(cloud_ai.engine(self.data).model, "claude-haiku-5-5")
        self.assertNotIn("FictionalKey", self.page.content())
        panel.locator('input[name=ai-provider][value=local]').check()
        self.expect(panel.locator(".panel-title .pill")).to_have_text("Local · free")

    def test_analysing_a_vacancy_shows_fit_and_key_facts(self):
        self.fake_provider()
        self.page.locator(f'[data-job="{self.job_id}"]').click()
        self.page.get_by_role("tab", name="AI insights", exact=True).click()
        self.expect(self.page.locator("#detail-content")).to_contain_text("Let AI read this vacancy for you.")
        self.page.get_by_role("button", name="Analyse vacancy").click()
        content = self.page.locator("#detail-content")
        self.expect(content.locator(".insight-head .pill")).to_have_text("Strong fit")
        self.expect(content).to_contain_text("NOK 650 000")
        self.expect(content).to_contain_text("By fake-model (Fake)")
        self.page.get_by_role("tab", name="Overview", exact=True).click()
        self.expect(self.page.locator("#detail-content .key-facts")).to_contain_text("Hybrid")

    def test_chat_about_a_vacancy_streams_and_escapes_the_answer(self):
        self.fake_provider()
        self.page.locator(f'[data-job="{self.job_id}"]').click()
        self.page.locator("#job-detail").get_by_role("button", name="Ask AI").first.click()
        thread = self.page.locator("#chat-thread")
        self.expect(thread.locator(".chat-job")).to_contain_text("Python Developer · Nordic Fixture")
        self.page.locator("#chat-input").fill("Am I a good fit?")
        self.page.locator("#chat-input").press("Control+Enter")
        self.expect(thread.locator("#pending-answer")).to_be_visible()
        answer = thread.locator(".msg.assistant .bubble").last
        self.expect(answer.locator("strong")).to_have_text("Yes")
        self.expect(answer.locator("li")).to_have_count(2)
        self.expect(answer).to_contain_text("<img src=x onerror=alert(1)>")
        self.assertEqual(answer.locator("img").count(), 0)
        self.expect(self.page.locator(".chat-item")).to_have_count(1)
        self.expect(self.page.locator(".chat-item")).to_contain_text("Am I a good fit?")
        self.assertIn("Nordic Fixture", self.engine.calls[0])
        # A new chat starts empty; the saved one opens again.
        self.page.get_by_role("button", name="New chat").click()
        self.expect(thread).to_contain_text("How can I help with your job search?")
        self.page.locator(".chat-open").click()
        self.expect(thread.locator(".msg")).to_have_count(2)
        self.page.locator(".chat-item").get_by_role("button", name="Delete chat Am I a good fit?").click()
        self.expect(self.page.locator(".chat-item")).to_have_count(0)

    def test_reply_drafts_open_from_a_vacancy(self):
        self.fake_provider()
        self.page.locator(f'[data-job="{self.job_id}"]').click()
        self.page.get_by_role("tab", name="AI insights", exact=True).click()
        self.page.locator("#detail-content").get_by_role("button", name="Draft a reply").click()
        modal = self.page.locator("#modal")
        self.expect(modal).to_contain_text("About: Python Developer · Nordic Fixture")
        modal.locator("#reply-email").fill("We would like to invite you to an interview. Which times suit you?")
        modal.get_by_role("button", name="Draft reply").click()
        self.expect(modal.locator("#reply-text")).to_have_value("Hello,\nThank you, I would be glad to meet.\nFictional Applicant")
        self.expect(modal).to_contain_text("Add your available times.")
        modal.get_by_role("button", name="Copy reply").click()
        self.expect(self.page.locator("#notifications")).to_contain_text("Reply copied")

    def test_chat_on_a_phone_sized_window(self):
        self.fake_provider()
        self.page.set_viewport_size({"width": 390, "height": 800})
        self.page.get_by_role("button", name="More").click()
        self.page.locator("#modal").get_by_role("button", name="AI chat").click()
        self.expect(self.page.locator(".chat-list")).to_be_visible()
        self.page.get_by_role("button", name="New chat").click()
        self.expect(self.page.locator(".chat-list")).to_be_hidden()
        self.page.locator("#chat-thread").get_by_role("button", name="How can I improve my CV summary?").click()
        self.expect(self.page.locator(".msg.assistant")).to_have_count(1)
        self.page.get_by_role("button", name="All conversations").click()
        self.expect(self.page.locator(".chat-item")).to_have_count(1)


if __name__ == "__main__":
    unittest.main()
