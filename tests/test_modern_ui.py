"""Opt-in Chromium checks for the desktop's bundled interface and real local API.

RUN_BROWSER_TESTS=1 enables these tests after installing Playwright Chromium.
Every fixture is fictional; nonlocal browser traffic is blocked and fails the test.
"""
import os
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from norway_job_agent import service
from norway_job_agent.application_forms import parse_html_form
from norway_job_agent.company_catalog import company_catalog
from norway_job_agent.modern_desktop import LocalWorkspace


@unittest.skipUnless(os.environ.get("RUN_BROWSER_TESTS") == "1", "Opt-in real Chromium desktop-interface checks")
class ModernInterfaceTests(unittest.TestCase):
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
        profile.update(name="Fictional Applicant", summary="Build useful software.",
                       skills=["Python", "SQL"], target_roles=["Python Developer"],
                       evidence=["Built a fictional reporting tool."], preferred_locations=[])
        service.save_profile(self.data, profile)
        settings = service.read_settings(self.data)
        settings.update(active_country="", sources=[])
        service.save_settings(self.data, settings)
        self.ids = {}
        for country, location, company in (("NO", "Oslo, Norway", "Nordic Fixture"),
                                           ("DE", "Berlin, Germany", "Berlin Fixture"),
                                           ("US", "Boston, MA, USA", "Boston Fixture"),
                                           ("UA", "Kyiv, Ukraine", "Kyiv Fixture")):
            self.ids[country] = service.import_vacancy(self.data, {
                "title": "Python Developer", "company": company, "location": location,
                "description": "Build reliable Python services and SQL reports.",
                "source": "manual", "source_url": f"https://jobs.example.test/{country}",
            })[0]
        self.workspace = LocalWorkspace(self.data).start()
        self.context = self.browser.new_context(viewport={"width": 1440, "height": 960})
        self.blocked, self.page_errors, self.console_errors = [], [], []
        allowed = urlsplit(self.workspace.url).netloc

        def route(request_route):
            url = request_route.request.url
            if urlsplit(url).netloc == allowed:
                request_route.continue_()
            else:
                self.blocked.append(url)
                request_route.abort()

        self.context.route("**/*", route)
        self.page = self.context.new_page()
        self.page.on("pageerror", lambda error: self.page_errors.append(str(error)))
        self.page.on("console", lambda message: self.console_errors.append(message.text) if message.type == "error" else None)
        self.page.set_default_timeout(5000)
        self.page.goto(self.workspace.url)
        self.expect(self.page.locator(".job-card")).to_have_count(4)
        self.expect(self.page.locator("#job-detail h2")).to_have_text("Python Developer")

    def tearDown(self):
        try:
            self.context.close()
            self.workspace.close()
            self.workspace.app.extensions["local_state"].worker.shutdown(wait=True)
            self.temporary.cleanup()
        finally:
            self.assertEqual(self.blocked, [], "The interface attempted nonlocal network access")
            self.assertEqual(self.page_errors, [], "An uncaught frontend error occurred")

    def select(self, country="NO"):
        self.page.locator(f'[data-job="{self.ids[country]}"]').click()
        self.expect(self.page.locator("#job-detail .detail-company")).to_contain_text({
            "NO": "Nordic Fixture", "DE": "Berlin Fixture", "US": "Boston Fixture", "UA": "Kyiv Fixture"}[country])

    def tab(self, name):
        self.page.get_by_role("tab", name=name, exact=True).click()

    def nav(self, label):
        self.page.get_by_role("navigation", name="Main navigation").get_by_role("button", name=label, exact=True).click()
        self.expect(self.page.get_by_role("heading", name=label, exact=True)).to_be_visible()

    def saved(self):
        self.expect(self.page.locator("#notifications")).to_contain_text("Saved to your private workspace.")

    def test_every_main_workspace_loads_without_script_or_console_errors(self):
        for name in ("For you", "Saved", "Applications", "Profile & writing", "Source library", "Job alerts", "Settings & activity", "Discover"):
            self.nav(name)
        self.assertEqual(self.console_errors, [])
        self.expect(self.page.locator("#task-status")).to_contain_text("All saved")

    def test_country_switch_filters_jobs_and_preserves_shared_profile_bytes(self):
        before = (self.data / "profile.json").read_bytes()
        for country, employer in (("DE", "Berlin Fixture"), ("UA", "Kyiv Fixture"), ("US", "Boston Fixture"), ("NO", "Nordic Fixture")):
            self.page.get_by_label("Search country").select_option(country)
            self.expect(self.page.locator(".job-card")).to_have_count(1)
            self.expect(self.page.locator(".job-card")).to_contain_text(employer)
            self.assertEqual((self.data / "profile.json").read_bytes(), before)
        self.assertEqual(service.read_settings(self.data)["active_country"], "NO")

    def test_unsaved_notes_and_letter_survive_tabs_navigation_and_filters(self):
        self.select()
        self.tab("Notes & stage")
        self.page.get_by_label("Your private notes").fill("Ask about the team and working hours.")
        self.tab("Cover letter")
        self.page.get_by_label("Your cover letter").fill("My thoughtful draft stays with this role.")
        self.nav("Source library")
        self.nav("Discover")
        self.expect(self.page.get_by_label("Your cover letter")).to_have_value("My thoughtful draft stays with this role.")
        self.page.get_by_label("Search opportunities").fill("no-matching-role")
        self.expect(self.page.locator(".job-card")).to_have_count(0)
        self.expect(self.page.get_by_label("Your cover letter")).to_have_value("My thoughtful draft stays with this role.")
        self.expect(self.page.locator("#detail-content")).to_contain_text("outside the current filters")
        self.tab("Notes & stage")
        self.expect(self.page.get_by_label("Your private notes")).to_have_value("Ask about the team and working hours.")
        self.assertTrue(self.page.evaluate("window.hasUnsavedChanges()"))
        self.assertEqual(service.get_vacancy(self.data, self.ids["NO"])["notes"], "")

    def test_search_keeps_keyboard_focus_and_selection_after_debounce(self):
        search = self.page.get_by_label("Search opportunities")
        search.fill("Nordic")
        search.focus()
        search.evaluate("element => element.setSelectionRange(1, 4)")
        self.expect(self.page.locator(".job-card")).to_have_count(1)
        self.expect(self.page.locator(".job-card")).to_contain_text("Nordic Fixture")
        self.expect(search).to_be_focused()
        self.assertEqual(search.evaluate("element => [element.selectionStart, element.selectionEnd]"), [1, 4])
        self.page.keyboard.type("ew")
        self.expect(search).to_have_value("Newic")

    def test_invalid_language_edit_survives_navigation_and_cannot_replace_saved_profile(self):
        before = (self.data / "profile.json").read_bytes()
        self.nav("Profile & writing")
        languages = self.page.get_by_label(re.compile("^Actual language proficiency"))
        raw = "English: fluent\nGerman level still being decided"
        languages.fill(raw)
        self.nav("Discover")
        self.nav("Profile & writing")
        self.expect(languages).to_have_value(raw)
        self.assertTrue(self.page.evaluate("window.hasUnsavedChanges()"))
        self.page.get_by_role("button", name="Save profile", exact=True).first.click()
        self.expect(self.page.locator("#notifications")).to_contain_text("Language: level")
        self.assertEqual((self.data / "profile.json").read_bytes(), before)
        languages.fill("English: fluent\nGerman: A2")
        self.page.get_by_role("button", name="Save profile", exact=True).first.click()
        self.expect(self.page.locator("#notifications")).to_contain_text("Profile saved")
        self.assertEqual(service.read_profile(self.data)["languages"], {"English": "fluent", "German": "A2"})

    def test_unfinished_source_json_survives_navigation_then_saves_when_corrected(self):
        before = (self.data / "sources.json").read_bytes()
        self.nav("Settings & activity")
        self.page.get_by_text("Advanced source configuration", exact=True).click()
        source_editor = self.page.get_by_label("Advanced sources JSON")
        raw = '[\n  {"type": "greenhouse", "board":'
        source_editor.fill(raw)
        self.nav("Discover")
        self.nav("Settings & activity")
        self.page.get_by_text("Advanced source configuration", exact=True).click()
        self.expect(source_editor).to_have_value(raw)
        self.assertTrue(self.page.evaluate("window.hasUnsavedChanges()"))
        self.assertEqual((self.data / "sources.json").read_bytes(), before)
        source_editor.fill("[]")
        self.page.get_by_role("button", name="Save settings", exact=True).click()
        self.expect(self.page.locator("#notifications")).to_contain_text("Settings saved")
        self.assertEqual(service.read_settings(self.data)["sources"], [])
        self.assertFalse(self.page.evaluate("window.hasUnsavedChanges()"))

    def test_theme_persists_on_disk_and_after_reload_once_save_completes(self):
        for theme in ("dark", "light"):
            with self.page.expect_response(lambda response: response.url.endswith("/api/appearance") and response.request.method == "PUT") as saved:
                self.page.get_by_role("button", name="Switch light or dark theme").click()
            self.assertEqual(saved.value.status, 200)
            self.assertEqual(json.loads((self.data / "appearance.json").read_text(encoding="utf-8"))["theme"], theme)
            self.page.reload()
            self.expect(self.page.locator("body")).to_have_attribute("data-theme", theme)
            self.expect(self.page.locator(".job-card")).to_have_count(4)

    def test_notes_and_stage_save_persist_after_reload(self):
        self.select()
        self.tab("Notes & stage")
        self.page.get_by_label("Your private notes").fill("Reviewed and ready to discuss.")
        self.page.get_by_label("Application stage").select_option("preparing")
        self.page.get_by_role("button", name="Save notes & stage").click()
        self.saved()
        row = service.get_vacancy(self.data, self.ids["NO"])
        self.assertEqual((row["status"], row["notes"]), ("preparing", "Reviewed and ready to discuss."))
        self.page.reload()
        self.select()
        self.tab("Notes & stage")
        self.expect(self.page.get_by_label("Your private notes")).to_have_value("Reviewed and ready to discuss.")
        self.expect(self.page.get_by_label("Application stage")).to_have_value("preparing")

    def test_bookmark_cannot_reset_an_applied_or_interview_stage(self):
        self.select()
        self.tab("Notes & stage")
        for stage in ("applied", "interview"):
            self.page.get_by_label("Application stage").select_option(stage)
            self.page.get_by_role("button", name="Save notes & stage").click()
            self.saved()
            self.expect(self.page.locator('[data-action="bookmark"]')).to_be_disabled()
            self.expect(self.page.locator('[data-action="bookmark"]')).to_have_text(stage.title())
            self.assertEqual(service.get_vacancy(self.data, self.ids["NO"])["status"], stage)
        self.page.reload()
        self.select()
        self.expect(self.page.locator('[data-action="bookmark"]')).to_be_disabled()
        self.expect(self.page.locator('[data-action="bookmark"]')).to_have_text("Interview")

    def test_letter_history_restores_old_version_without_losing_newer_edit(self):
        self.select()
        self.tab("Cover letter")
        for text in ("First saved letter.", "Second saved letter."):
            self.page.get_by_label("Your cover letter").fill(text)
            self.page.get_by_role("button", name="Save version", exact=True).click()
            self.saved()
            self.expect(self.page.locator("#task-status")).to_contain_text("All saved")
        self.page.get_by_label("Your cover letter").fill("Newer unsaved work is valuable.")
        self.page.get_by_role("button", name="Version history").click()
        first = self.page.locator(".history-row").filter(has_text="First saved letter.")
        first.get_by_role("button", name="Restore for editing").click()
        self.expect(self.page.get_by_label("Your cover letter")).to_have_value("First saved letter.")
        history = service.draft_history(self.data, self.ids["NO"])
        self.assertEqual(history[0]["content"], "Newer unsaved work is valuable.")
        self.page.get_by_role("button", name="Save version", exact=True).click()
        self.saved()
        self.assertEqual(service.draft_history(self.data, self.ids["NO"])[0]["content"], "First saved letter.")

    def test_profile_validation_and_writing_style_save(self):
        self.nav("Profile & writing")
        self.page.get_by_label(re.compile("^Actual language proficiency")).fill("English without level separator")
        self.page.get_by_role("button", name="Save profile", exact=True).first.click()
        self.expect(self.page.locator("#notifications")).to_contain_text("Language: level")
        self.page.get_by_label(re.compile("^Actual language proficiency")).fill("English: fluent\nGerman: A2")
        self.page.get_by_label("Tone", exact=True).fill("Warm, precise, confident")
        self.page.get_by_label(re.compile("^Maximum words")).fill("120")
        self.page.get_by_label("Style instructions", exact=True).fill("Use short sentences and concrete evidence.")
        self.page.get_by_label(re.compile("^Phrases to avoid")).fill("leverage\nsynergy")
        self.page.get_by_role("button", name="Save profile", exact=True).first.click()
        self.expect(self.page.locator("#notifications")).to_contain_text("Profile saved")
        profile = service.read_profile(self.data)
        self.assertEqual(profile["languages"], {"English": "fluent", "German": "A2"})
        self.assertEqual(profile["writing_style"]["max_words"], 120)
        self.assertEqual(profile["writing_style"]["tone"], "Warm, precise, confident")
        self.assertEqual(profile["writing_style"]["avoid_phrases"], ["leverage", "synergy"])
        self.assertFalse(self.page.evaluate("window.hasUnsavedChanges()"))

    def test_pasted_questions_render_labelled_radio_choices_and_save_answers(self):
        self.select()
        self.tab("Application")
        self.page.get_by_role("button", name="Paste questions").click()
        self.page.get_by_label("Questions and available options").fill("Preferred arrangement | Remote | Hybrid | On-site\nWhy this role?")
        self.page.get_by_role("button", name="Use these questions").click()
        self.expect(self.page.get_by_role("radio", name="Remote", exact=True)).to_be_visible()
        self.page.get_by_role("radio", name="Hybrid", exact=True).check()
        self.page.get_by_label("Why this role?", exact=True).fill("The Python work matches my documented experience.")
        self.page.get_by_role("button", name="Save version", exact=True).click()
        self.saved()
        payload = service.application_history(self.data, self.ids["NO"])[0]["payload"]
        answers = {a["label"]: a for a in payload["answers"]}
        self.assertEqual(answers["Preferred arrangement"]["selected_options"], ["Hybrid"])
        self.assertEqual(answers["Why this role?"]["answer"], "The Python work matches my documented experience.")
        self.page.get_by_role("button", name="Clear selection").click()
        self.expect(self.page.get_by_role("radio", name="Hybrid", exact=True)).not_to_be_checked()

    def test_multiple_choice_checkboxes_preserve_exact_option_values(self):
        form = parse_html_form('''<form action="/submit" method="post"><fieldset><legend>Available days</legend>
        <label>Monday<input type="checkbox" name="days" value="mon"></label>
        <label>Tuesday<input type="checkbox" name="days" value="tue"></label>
        <label>Friday<input type="checkbox" name="days" value="fri"></label></fieldset></form>''', "https://jobs.example.test/NO")
        service.save_application(self.data, self.ids["NO"], {"job_id": self.ids["NO"], "form": form, "answers": []})
        self.page.reload()
        self.select()
        self.tab("Application")
        self.page.get_by_role("checkbox", name="Monday", exact=True).check()
        self.page.get_by_role("checkbox", name="Friday", exact=True).check()
        self.page.get_by_role("button", name="Save version", exact=True).click()
        self.saved()
        payload = service.application_history(self.data, self.ids["NO"])[0]["payload"]
        chosen = {value for answer in payload["answers"] for value in answer["selected_options"]}
        self.assertEqual(chosen, {"mon", "fri"})
        self.page.get_by_role("button", name="History", exact=True).click()
        self.expect(self.page.get_by_role("dialog")).to_contain_text("Monday")
        self.expect(self.page.get_by_role("dialog")).to_contain_text("Friday")

    def test_country_and_access_filters_find_and_add_one_source(self):
        source = next(row for row in company_catalog("DE") if row.get("source"))
        self.page.get_by_label("Search country").select_option("DE")
        self.expect(self.page.locator(".job-card")).to_have_count(1)
        self.nav("Source library")
        self.page.get_by_label("Source access").select_option("automatic")
        self.page.get_by_label("Search source library").fill(source["name"])
        card = self.page.locator(".catalog-card").filter(has=self.page.get_by_role("heading", name=source["name"], exact=True))
        self.expect(card).to_have_count(1)
        self.expect(card).to_contain_text("Germany")
        self.expect(card).to_contain_text("Automatic collector")
        # Switching countries already installs the verified starter source set.
        button = card.get_by_role("button", name=re.compile("^(Add source|Added)$"))
        if button.is_enabled():
            button.click()
        self.expect(card.get_by_role("button", name="Added", exact=True)).to_be_disabled()
        self.page.get_by_label("Search source library").fill("no such fixture company")
        self.expect(self.page.get_by_role("heading", name="No sources match")).to_be_visible()

    def test_quick_actions_keyboard_focus_stays_inside_modal_and_escape_restores(self):
        trigger = self.page.get_by_role("button", name=re.compile("^Quick actions"))
        trigger.focus()
        self.page.keyboard.press("Control+k")
        self.expect(self.page.get_by_label("Search quick actions")).to_be_focused()
        self.page.get_by_label("Search quick actions").fill("profile")
        self.page.keyboard.press("Tab")
        self.expect(self.page.get_by_role("dialog").get_by_role("button", name="Profile & writing", exact=True)).to_be_focused()
        for _ in range(5):
            self.page.keyboard.press("Tab")
            self.assertTrue(self.page.evaluate("document.querySelector('#modal').contains(document.activeElement)"))
        self.page.keyboard.press("Escape")
        self.expect(self.page.get_by_role("dialog")).not_to_be_visible()
        self.expect(trigger).to_be_focused()
        self.tab("Overview")
        self.page.keyboard.press("ArrowRight")
        self.expect(self.page.get_by_role("tab", name="Notes & stage")).to_be_focused()
        self.expect(self.page.get_by_label("Your private notes")).to_be_visible()

    def test_save_shortcut_supports_non_latin_layout_and_logical_latin_keys(self):
        self.select()
        self.tab("Notes & stage")
        notes = self.page.get_by_label("Your private notes")
        for key, code, text in (("і", "KeyS", "Зберегти цю нотатку."),
                                ("s", "KeyK", "Logical Latin save still works.")):
            notes.fill(text)
            with self.page.expect_response(lambda response: response.url.endswith("/workflow")
                                           and response.request.method == "POST") as saved:
                prevented = notes.evaluate("""(element, shortcut) => {
                    const event = new KeyboardEvent('keydown', {
                        ...shortcut, ctrlKey: true, bubbles: true, cancelable: true
                    });
                    element.dispatchEvent(event);
                    return event.defaultPrevented;
                }""", {"key": key, "code": code})
            self.assertTrue(prevented, "Save must suppress the browser's default shortcut")
            self.assertEqual(saved.value.status, 200)
            self.expect(self.page.locator("#task-status")).to_contain_text("All saved")
            self.assertEqual(service.get_vacancy(self.data, self.ids["NO"])["notes"], text)
            self.expect(self.page.get_by_role("dialog")).not_to_be_visible()
        self.page.reload()
        self.select()
        self.tab("Notes & stage")
        self.expect(notes).to_have_value("Logical Latin save still works.")

    def test_non_latin_search_theme_and_commands_shortcuts_keep_modal_focus_behavior(self):
        def shortcut(key, code, shift=False):
            return self.page.evaluate("""shortcut => {
                const event = new KeyboardEvent('keydown', {
                    ...shortcut, ctrlKey: true, bubbles: true, cancelable: true
                });
                document.activeElement.dispatchEvent(event);
                return event.defaultPrevented;
            }""", {"key": key, "code": code, "shiftKey": shift})

        self.assertTrue(shortcut("а", "KeyF"))
        self.expect(self.page.get_by_label("Search opportunities")).to_be_focused()
        self.assertFalse(shortcut("д", "KeyL"), "Theme shortcut requires Shift")
        with self.page.expect_response(lambda response: response.url.endswith("/api/appearance")
                                       and response.request.method == "PUT") as saved:
            self.assertTrue(shortcut("Д", "KeyL", shift=True))
        self.assertEqual(saved.value.status, 200)
        self.expect(self.page.locator("body")).to_have_attribute("data-theme", "dark")
        trigger = self.page.get_by_role("button", name=re.compile("^Quick actions"))
        trigger.focus()
        self.assertTrue(shortcut("л", "KeyK"))
        self.expect(self.page.get_by_label("Search quick actions")).to_be_focused()
        self.page.keyboard.press("Shift+Tab")
        self.assertTrue(self.page.evaluate("document.querySelector('#modal').contains(document.activeElement)"))
        self.page.keyboard.press("Escape")
        self.expect(self.page.get_by_role("dialog")).not_to_be_visible()
        self.expect(trigger).to_be_focused()

    def test_external_strings_are_plain_text_and_do_not_create_active_html(self):
        payload = '<img src="https://evil.example.test/x" onerror="window.testXSS=1">'
        job_id = service.import_vacancy(self.data, {
            "title": "Untrusted employer text " + payload, "company": "Escaping Fixture",
            "location": "Berlin, Germany", "description": '<script>window.testXSS=2</script>' + payload,
            "source": "manual", "source_url": "https://jobs.example.test/escape",
        })[0]
        self.page.reload()
        self.page.locator(f'[data-job="{job_id}"]').click()
        self.expect(self.page.locator("#job-detail h2")).to_contain_text(payload)
        self.expect(self.page.locator("#detail-content .prose")).to_contain_text("<script>")
        self.assertEqual(self.page.locator("#job-detail img, #job-detail script").count(), 0)
        self.assertIsNone(self.page.evaluate("window.testXSS"))

    def test_native_close_bridge_tracks_edits_and_saves_in_order(self):
        self.page.evaluate("""() => {
            window.closeUpdates = [];
            window.pywebview = {api: {update_close_state: (...args) => {
                window.closeUpdates.push(args);
                return Promise.resolve(true);
            }}};
            window.dispatchEvent(new Event('pywebviewready'));
        }""")
        self.assertFalse(self.page.evaluate("window.closeUpdates.at(-1)[1]"))
        self.select()
        self.tab("Notes & stage")
        self.page.get_by_label("Your private notes").fill("Keep this edit before closing.")
        self.assertTrue(self.page.evaluate("window.closeUpdates.at(-1)[1]"))
        self.page.get_by_role("button", name="Save notes & stage").click()
        self.saved()
        self.assertFalse(self.page.evaluate("window.closeUpdates.at(-1)[1]"))
        revisions = self.page.evaluate("window.closeUpdates.map(row => row[2])")
        self.assertEqual(revisions, sorted(set(revisions)))
        self.assertTrue(self.page.evaluate("window.closeUpdates.every(row => row[0] === document.querySelector('meta[name=session-token]').content)"))

    def test_experience_profile_field_controls_recommendations_without_hiding_saved_work(self):
        senior = service.import_vacancy(self.data, {
            "title": "Senior Python Developer", "company": "Senior Fixture", "location": "Boston, MA, USA",
            "description": "Python and SQL. At least 5 years of professional experience required.",
            "source": "manual", "source_url": "https://jobs.example.test/senior"})[0]
        service.update_workflow(self.data, senior, "saved", "Review a referral separately")
        self.nav("Profile & writing")
        self.page.get_by_label(re.compile("^Years of professional experience")).fill("2.5")
        self.page.get_by_role("button", name="Save profile", exact=True).first.click()
        self.expect(self.page.locator("#notifications")).to_contain_text("Profile saved")
        self.assertEqual(service.read_profile(self.data)["years_of_experience"], 2.5)
        self.nav("For you")
        self.expect(self.page.locator(".job-card")).to_have_count(4)
        self.expect(self.page.locator(".coverage-notice")).to_contain_text("1 above your experience")
        self.expect(self.page.locator(".coverage-notice")).to_contain_text("2.5 years")
        self.page.get_by_label("Experience level", exact=True).select_option("all")
        self.expect(self.page.locator(".job-card")).to_have_count(5)
        self.page.locator(f'[data-job="{senior}"]').click()
        self.expect(self.page.locator("#detail-content")).to_contain_text("Experience mismatch")
        self.expect(self.page.locator("#detail-content")).to_contain_text("5")
        self.nav("Saved")
        self.expect(self.page.locator(f'[data-job="{senior}"]')).to_be_visible()
        self.page.reload()
        self.nav("Profile & writing")
        self.expect(self.page.get_by_label(re.compile("^Years of professional experience"))).to_have_value("2.5")

    def test_source_library_reports_enabled_collectors_separately_from_directory(self):
        self.nav("Source library")
        self.expect(self.page.locator(".coverage-notice")).to_contain_text("0 configured collectors")
        self.expect(self.page.locator(".coverage-notice")).to_contain_text("available employer collectors")
        self.expect(self.page.get_by_role("button", name="Add all country collectors", exact=True)).to_be_visible()

    def test_country_sources_survive_a_later_settings_save(self):
        self.nav("Settings & activity")
        self.page.get_by_label("Model name", exact=False).fill("fixture-model")
        self.page.get_by_role("button", name="Save settings", exact=True).click()
        self.expect(self.page.locator("#notifications")).to_contain_text("Settings saved")
        self.page.get_by_label("Search country").select_option("DE")
        self.expect(self.page.locator("#notifications")).to_contain_text("Country changed")
        sources = service.read_settings(self.data)["sources"]
        self.assertTrue(sources)
        self.page.get_by_label("Model name", exact=False).fill("second-fixture-model")
        self.page.get_by_role("button", name="Save settings", exact=True).click()
        self.expect(self.page.locator("#notifications")).to_contain_text("Settings saved")
        saved = service.read_settings(self.data)
        self.assertEqual(saved["sources"], sources)
        self.assertEqual(saved["active_country"], "DE")
        self.assertEqual(saved["model"], "second-fixture-model")

    def test_compact_dark_workspace_has_reachable_editors_and_no_horizontal_overflow(self):
        self.select()
        self.page.set_viewport_size({"width": 900, "height": 640})
        self.page.get_by_role("button", name="Switch light or dark theme").click()
        self.expect(self.page.locator("body")).to_have_attribute("data-theme", "dark")
        self.tab("Notes & stage")
        self.page.get_by_label("Your private notes").fill("Compact screen edit")
        self.page.get_by_role("button", name="Save notes & stage").click()
        self.saved()
        for view in ("Discover", "Profile & writing", "Source library", "Settings & activity"):
            self.nav(view)
            dimensions = self.page.evaluate("({width:document.documentElement.clientWidth, scroll:document.documentElement.scrollWidth, mainWidth:document.querySelector('#main').clientWidth, mainScroll:document.querySelector('#main').scrollWidth})")
            self.assertLessEqual(dimensions["scroll"], dimensions["width"] + 1, view)
            self.assertLessEqual(dimensions["mainScroll"], dimensions["mainWidth"] + 1, view)
        self.page.reload()
        self.expect(self.page.locator("body")).to_have_attribute("data-theme", "dark")


if __name__ == "__main__":
    unittest.main()
