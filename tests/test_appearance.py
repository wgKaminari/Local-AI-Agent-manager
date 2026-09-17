"""Appearance changes preserve ongoing work in real, isolated desktop widgets."""

import json
import sys
import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from norway_job_agent import service
from norway_job_agent.desktop import Desktop, _text
from norway_job_agent.text_interactions import SelectableLabel
from norway_job_agent.theme import PALETTES


class AppearanceInteractionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="norway-appearance-test-")
        self.data = Path(self.temp.name)
        service.initialize(self.data)
        profile = service.read_profile(self.data)
        profile.update(name="Fixture Person", target_roles=["AI Engineer"], skills=["Python"])
        service.save_profile(self.data, profile)
        self.job_id, _ = service.import_vacancy(self.data, {
            "title": "AI Engineer", "company": "Fixture Company", "location": "Oslo, Norway",
            "source_url": "https://example.com/jobs/appearance-fixture",
            "description": "Python engineering.\n" * 80,
        })
        self._create_app()

    def _create_app(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as exc:
            self.temp.cleanup()
            self.skipTest(f"A working Tk runtime/display is required: {exc}")
        self.root.withdraw()
        self.app = Desktop(self.root, self.data)
        self.root.update_idletasks()

    def _destroy_app(self):
        self.app.closed = True
        for timer in self.root.tk.call("after", "info"):
            self.root.tk.call("after", "cancel", timer)
        self.root.destroy()

    def tearDown(self):
        if hasattr(self, "app"):
            self._destroy_app()
        self.temp.cleanup()

    def test_theme_change_preserves_pending_profile_and_settings_without_saving_them(self):
        stored_profile = service.read_profile(self.data)
        stored_settings = service.read_settings(self.data)
        self.app._navigate("profile")
        self.app.fields["name"].set("Name edited but not saved")
        self.app.fields["summary"].insert("1.0", "A pending profile summary")
        self.app.model.set("fixture-model:latest")
        self.app.sources.append({"type": "job_url", "url": "https://example.com/jobs/pending"})
        profile_editor = self.app.fields["summary"]
        model_editor = self.app.model_combo

        self.app._toggle_theme()
        self.app._toggle_theme()

        self.assertEqual(self.app.fields["name"].get(), "Name edited but not saved")
        self.assertIn("A pending profile summary", _text(profile_editor))
        self.assertEqual(self.app.model.get(), "fixture-model:latest")
        self.assertEqual(self.app.sources[-1]["url"], "https://example.com/jobs/pending")
        self.assertIs(self.app.fields["summary"], profile_editor)
        self.assertIs(self.app.model_combo, model_editor)
        self.assertEqual(self.app.notebook.select(), str(self.app.profile_page))
        self.assertEqual(service.read_profile(self.data), stored_profile)
        self.assertEqual(service.read_settings(self.data), stored_settings)

    def test_theme_change_preserves_job_edits_selection_and_undo_history(self):
        notes, letter = self.app.notes, self.app.letter_text
        notes.insert("1.0", "A pending note")
        letter.insert("1.0", "First draft")
        letter.edit_separator()
        letter.insert("end-1c", " with another sentence")
        letter.edit_separator()
        letter.mark_set("insert", "1.5")
        letter.tag_add("sel", "1.0", "1.5")
        self.app.workflow_status.set("preparing")
        selected = self.app.tree.selection()

        self.app._toggle_theme()

        self.assertEqual(self.app.theme_name, "dark")
        self.assertEqual(letter.cget("background"), PALETTES["dark"]["surface"])
        self.assertEqual(self.app.selected_id, self.job_id)
        self.assertEqual(self.app.tree.selection(), selected)
        self.assertIs(self.app.notes, notes)
        self.assertIs(self.app.letter_text, letter)
        self.assertEqual(_text(notes), "A pending note")
        self.assertEqual(_text(letter), "First draft with another sentence")
        self.assertEqual(letter.index("insert"), "1.5")
        self.assertEqual(tuple(map(str, letter.tag_ranges("sel"))), ("1.0", "1.5"))
        self.assertEqual(self.app.workflow_status.get(), "preparing")
        self.assertEqual(service.get_vacancy(self.data, self.job_id)["notes"], "")
        self.assertEqual(service.draft_history(self.data, self.job_id), [])
        letter.edit_undo()
        self.assertEqual(_text(letter), "First draft")
        letter.edit_redo()
        self.assertEqual(_text(letter), "First draft with another sentence")

    def test_job_heading_remains_selectable_and_readonly_after_theme_change(self):
        title = self.app.job_title_label
        self.assertIsInstance(title, SelectableLabel)
        self.assertEqual(_text(title), "AI Engineer")
        title.tag_add("sel", "1.0", "1.2")

        self.app._toggle_theme()

        self.assertEqual(str(title.cget("state")), "disabled")
        self.assertEqual(title.get("sel.first", "sel.last"), "AI")
        title.insert("1.0", "Accidental edit")
        self.assertEqual(_text(title), "AI Engineer")
        self.app.job_heading.set("Updated title from selection")
        self.assertEqual(_text(title), "Updated title from selection")
        self.assertEqual(str(title.cget("state")), "disabled")

    def test_sidebar_toggle_preserves_editors_and_keeps_restore_control_accessible(self):
        notes = self.app.notes
        notes.insert("1.0", "Keep this while hiding the sidebar")
        selected = self.app.selected_id
        self.app._toggle_sidebar()

        self.assertFalse(self.app.sidebar_visible)
        self.assertEqual(self.app.sidebar.winfo_manager(), "")
        self.assertEqual(self.app.sidebar_toggle.winfo_manager(), "pack")
        self.assertFalse(str(self.app.sidebar_toggle).startswith(str(self.app.sidebar) + "."))
        self.assertIn("Show", self.app.sidebar_toggle.cget("text"))
        self.assertTrue(self.root.bind("<Control-b>"))
        self.assertEqual(self.app.main.winfo_manager(), "pack")

        self.app._toggle_sidebar()

        self.assertTrue(self.app.sidebar_visible)
        self.assertEqual(self.app.sidebar.winfo_manager(), "pack")
        self.assertIn("Hide", self.app.sidebar_toggle.cget("text"))
        self.assertEqual(self.app.selected_id, selected)
        self.assertIs(self.app.notes, notes)
        self.assertEqual(_text(notes), "Keep this while hiding the sidebar")

    def test_hidden_sidebar_retains_navigation_and_pending_profile_edits(self):
        self.app._show_sidebar(False)
        self.app._navigate("profile")
        self.app.fields["name"].set("Pending profile name")
        for target, page in (("settings", self.app.settings_page), ("gmail", self.app.gmail_page),
                             ("profile", self.app.profile_page)):
            self.app._navigate(target)
            self.assertEqual(self.app.notebook.select(), str(page))
            self.assertFalse(self.app.sidebar_visible)
            self.assertEqual(self.app.sidebar_toggle.winfo_manager(), "pack")
        for shortcut in ("<Control-1>", "<Control-2>", "<Control-3>"):
            self.assertTrue(self.root.bind(shortcut))
        self.assertEqual(self.app.fields["name"].get(), "Pending profile name")
        self.assertEqual(service.read_profile(self.data)["name"], "Fixture Person")

    def test_appearance_preferences_survive_restart(self):
        self.app._toggle_theme()
        self.app._toggle_sidebar()
        preferences = json.loads((self.data / "appearance.json").read_text(encoding="utf-8"))
        self.assertEqual(preferences, {"theme": "dark", "sidebar_visible": False})
        self._destroy_app()
        self._create_app()

        self.assertEqual(self.app.theme_name, "dark")
        self.assertFalse(self.app.sidebar_visible)
        self.assertEqual(self.app.sidebar.winfo_manager(), "")
        self.assertEqual(self.app.letter_text.cget("background"), PALETTES["dark"]["surface"])
        self.assertIn("Show", self.app.sidebar_toggle.cget("text"))
        self.assertIn("Light", self.app.theme_button.cget("text"))
        self.assertEqual(self.app.selected_id, self.job_id)

    def test_corrupt_and_invalid_preferences_fall_back_to_usable_defaults(self):
        for payload in ("broken JSON", "[]", '{"theme": "unknown", "sidebar_visible": "false"}',
                        '{"theme": null, "sidebar_visible": 0}'):
            with self.subTest(payload=payload):
                (self.data / "appearance.json").write_text(payload, encoding="utf-8")
                self.app._load_appearance()
                self.app._finish_appearance()
                self.assertEqual(self.app.theme_name, "light")
                self.assertTrue(self.app.sidebar_visible)
                self.assertEqual(self.app.sidebar.winfo_manager(), "pack")
                self.assertEqual(self.app.letter_text.cget("background"), PALETTES["light"]["surface"])

    def test_preference_write_failure_keeps_session_appearance_and_unsaved_work(self):
        self.app.notes.insert("1.0", "Still here after a preferences write error")
        with patch("norway_job_agent.desktop_appearance.service._write_json", side_effect=OSError("fixture disk error")):
            self.app._toggle_theme()
            self.app._toggle_sidebar()
        self.assertEqual(self.app.theme_name, "dark")
        self.assertFalse(self.app.sidebar_visible)
        self.assertEqual(_text(self.app.notes), "Still here after a preferences write error")
        self.assertIn("could not be saved", self.app.status_message.get())
        self.assertFalse((self.data / "appearance.json").exists())


if __name__ == "__main__":
    unittest.main()
