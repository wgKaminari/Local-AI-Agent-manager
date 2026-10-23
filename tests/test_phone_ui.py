"""Opt-in Chromium checks of the phone interface, paired over a local listener.

RUN_BROWSER_TESTS=1 enables these tests after installing Playwright Chromium.
Every fixture is fictional; nonlocal browser traffic is blocked and fails the test.
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from norway_job_agent import service
from norway_job_agent.modern_desktop import LocalWorkspace

PHONE = {"viewport": {"width": 390, "height": 844}, "is_mobile": True, "has_touch": True, "device_scale_factor": 3,
         "user_agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148"}
SHOTS = os.environ.get("PHONE_SCREENSHOTS")


@unittest.skipUnless(os.environ.get("RUN_BROWSER_TESTS") == "1", "Opt-in real Chromium phone-interface checks")
class PhoneInterfaceTests(unittest.TestCase):
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
        profile.update(name="Fictional Applicant", skills=["Python", "SQL"], target_roles=["Python Developer"],
                       evidence=["Built a fictional reporting tool."], preferred_locations=[])
        service.save_profile(self.data, profile)
        settings = service.read_settings(self.data)
        settings.update(active_country="", sources=[])
        settings["market"]["enabled"] = {name: False for name in settings["market"]["enabled"]}
        service.save_settings(self.data, settings)
        for number, (location, company) in enumerate((("Oslo, Norway", "Nordic Fixture"), ("Berlin, Germany", "Berlin Fixture"),
                                                       ("Boston, MA, USA", "Boston Fixture"))):
            service.import_vacancy(self.data, {
                "title": f"Python Developer {number}", "company": company, "location": location,
                "description": "Build reliable Python services and SQL reports.", "source": "manual",
                "source_url": f"https://jobs.example.test/{number}"})
        self.workspace = LocalWorkspace(self.data)
        self.workspace.phone._find_address = lambda: "127.0.0.1"
        self.workspace.phone.default_port = 0
        self.workspace.start()
        self.blocked, self.errors = [], []

    def tearDown(self):
        try:
            for context in getattr(self, "contexts", []):
                context.close()
            self.workspace.close()
            self.workspace.app.extensions["local_state"].worker.shutdown(wait=True)
            self.temporary.cleanup()
        finally:
            self.assertEqual(self.blocked, [], "The interface attempted nonlocal network access")
            self.assertEqual(self.errors, [], "An uncaught frontend error occurred")

    def page(self, url, **options):
        context = self.browser.new_context(**options)
        self.contexts = [*getattr(self, "contexts", []), context]
        allowed = {urlsplit(self.workspace.url).netloc, *([urlsplit(self.workspace.phone.status()["url"]).netloc]
                                                          if self.workspace.phone.status()["url"] else [])}

        def route(request_route):
            if urlsplit(request_route.request.url).netloc in allowed or request_route.request.url.startswith("data:"):
                request_route.continue_()
            else:
                self.blocked.append(request_route.request.url)
                request_route.abort()
        context.route("**/*", route)
        page = context.new_page()
        page.on("pageerror", lambda error: self.errors.append(str(error)))
        page.set_default_timeout(6000)
        page.goto(url)
        return page

    def shot(self, page, name):
        if SHOTS:
            page.screenshot(path=str(Path(SHOTS) / f"{name}.png"))

    def no_sideways_scroll(self, page, label):
        size = page.evaluate("({width: document.documentElement.clientWidth, scroll: document.documentElement.scrollWidth})")
        self.assertLessEqual(size["scroll"], size["width"] + 1, label)

    def test_the_pc_turns_phone_access_on_and_shows_a_qr_code(self):
        desktop = self.page(self.workspace.url, viewport={"width": 1280, "height": 900})
        desktop.get_by_role("navigation", name="Main navigation").get_by_role("button", name="Settings & activity").click()
        panel = desktop.locator("#phone-panel")
        self.expect(panel).to_contain_text("Phone access")
        panel.get_by_role("button", name="Turn on phone access").click()
        self.expect(panel.locator("img.qr")).to_be_visible()
        self.expect(panel.locator(".pair-code")).to_have_text(self.workspace.phone.status()["code"])
        self.shot(desktop, "pc-phone-access")

    def test_a_phone_pairs_with_the_qr_link_and_uses_every_view(self):
        status = self.workspace.phone.enable()
        phone = self.page(f"{status['url']}/?pair={status['code']}", **PHONE)
        self.expect(phone.locator(".job-card").first).to_be_visible()
        self.assertEqual(phone.evaluate("location.search"), "")  # the code is not kept in the address
        tabs = phone.locator(".sidebar nav .nav-item:visible")
        self.expect(tabs).to_have_count(5)
        self.shot(phone, "phone-discover")
        self.no_sideways_scroll(phone, "Discover")
        # A job opens full screen, and Back returns to the list.
        phone.locator(".job-card").first.click()
        self.expect(phone.locator("#job-detail h2")).to_be_visible()
        self.expect(phone.locator(".results-panel")).to_be_hidden()
        self.shot(phone, "phone-job")
        self.no_sideways_scroll(phone, "Job detail")
        phone.get_by_role("button", name="All opportunities").click()
        self.expect(phone.locator(".results-panel")).to_be_visible()
        for label in ("For you", "Saved", "Applications"):
            tabs.filter(has_text=label).click()
            self.expect(phone.get_by_role("heading", name=label, exact=True)).to_be_visible()
            self.no_sideways_scroll(phone, label)
        for label in ("Profile & writing", "Source library", "Job alerts", "Settings & activity"):
            phone.get_by_role("button", name="More", exact=True).click()
            phone.locator("#modal").get_by_role("button", name=label).click()
            self.expect(phone.get_by_role("heading", name=label, exact=True)).to_be_visible()
            self.no_sideways_scroll(phone, label)
            self.shot(phone, "phone-" + "-".join(label.lower().replace("&", "").split()))
        # Settings on the phone: this phone's panel, no PC-only controls.
        self.expect(phone.get_by_text("Paired with your PC")).to_be_visible()
        self.expect(phone.get_by_role("button", name="Add Start menu & desktop shortcuts")).to_have_count(0)
        # The phone's theme is its own.
        before = (self.data / "appearance.json").read_text(encoding="utf-8") if (self.data / "appearance.json").exists() else ""
        phone.get_by_role("button", name="Switch theme").first.click()
        after = (self.data / "appearance.json").read_text(encoding="utf-8") if (self.data / "appearance.json").exists() else ""
        self.assertEqual(before, after)
        self.assertEqual([item["name"] for item in self.workspace.phone.status()["devices"]], ["iPhone"])

    def test_without_a_code_the_phone_asks_for_one_and_explains_a_wrong_code(self):
        status = self.workspace.phone.enable()
        phone = self.page(status["url"] + "/", **PHONE)
        self.expect(phone.get_by_role("heading", name="Connect to Job Agent on your PC")).to_be_visible()
        self.no_sideways_scroll(phone, "Pairing")
        self.shot(phone, "phone-pairing")
        phone.get_by_label("Pairing code").fill("WRON-GCOD")
        phone.get_by_role("button", name="Connect").click()
        self.expect(phone.get_by_role("alert")).to_contain_text("not right")
        phone.get_by_label("Pairing code").fill(status["code"])
        phone.get_by_role("button", name="Connect").click()
        self.expect(phone.locator(".job-card").first).to_be_visible()
        # Removed on the PC: the phone is asked to pair again.
        self.workspace.phone.remove(self.workspace.phone.status()["devices"][0]["id"])
        phone.reload()
        self.expect(phone.get_by_role("heading", name="Connect to Job Agent on your PC")).to_be_visible()
        saved = json.loads((self.data / "phone-access.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["devices"], [])


if __name__ == "__main__":
    unittest.main()
