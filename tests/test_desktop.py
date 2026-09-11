"""Desktop interaction regressions using isolated data and real hidden widgets."""

import sys
import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from norway_job_agent import service
from norway_job_agent.desktop import Desktop, _text


class DesktopInteractionTests(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(f"A working Tk runtime/display is required: {exc}")
        self.root.withdraw()
        self.temp = tempfile.TemporaryDirectory(prefix="norway-desktop-test-")
        self.data = Path(self.temp.name)
        service.initialize(self.data)
        profile = service.read_profile(self.data)
        profile.update(name="Test Person", target_roles=["AI Engineer"], related_roles=["Data Engineer"], skills=["Python"])
        service.save_profile(self.data, profile)
        self.ids = []
        for title, status in (("AI Engineer", "new"), ("Data Engineer", "saved"), ("Office Manager", "interview")):
            job_id, _ = service.import_vacancy(self.data, {
                "title": title, "company": "Test Company", "location": "Oslo, Norway",
                "source_url": f"https://example.com/jobs/{len(self.ids)}",
                "description": "Python experience. " + "Long description.\n" * 80,
            })
            service.update_workflow(self.data, job_id, status, "")
            self.ids.append(job_id)
        self.app = Desktop(self.root, self.data)
        self.error_patch = patch.object(self.app, "_error", side_effect=lambda exc: (_ for _ in ()).throw(exc))
        self.error_patch.start()
        self.root.update_idletasks()

    def tearDown(self):
        if hasattr(self, "app"):
            self.app.closed = True
            self.error_patch.stop()
        if hasattr(self, "root"):
            for timer in self.root.tk.call("after", "info"):
                self.root.tk.call("after", "cancel", timer)
            self.root.destroy()
        if hasattr(self, "temp"):
            self.temp.cleanup()

    def test_sidebar_views_select_relevant_job_when_editor_is_clean(self):
        self.app._navigate("saved")
        self.assertEqual(set(self.app.jobs), {self.ids[1]})
        self.assertEqual(self.app.selected_id, self.ids[1])
        self.app._navigate("progress")
        self.assertEqual(set(self.app.jobs), {self.ids[2]})
        self.assertEqual(self.app.selected_id, self.ids[2])
        self.app._navigate("foryou")
        self.assertEqual(set(self.app.jobs), set(self.ids[:2]))
        self.assertIn(self.app.selected_id, self.ids[:2])

    def test_initial_selection_does_not_scroll_an_unmapped_list(self):
        first = self.app.tree.get_children()[0]
        self.app.tree.see(first)
        self.root.update_idletasks()
        self.assertEqual(self.app.tree.yview()[0], 0.0)

    def test_filter_preserves_hidden_unsaved_letter(self):
        original = self.app.selected_id
        self.app.letter_text.insert("1.0", "My unsaved letter")
        self.app.query.set("impossible search with no results")
        self.app._search_now()
        self.assertFalse(self.app.jobs)
        self.assertEqual(self.app.selected_id, original)
        self.assertEqual(_text(self.app.letter_text), "My unsaved letter")
        self.assertEqual(self.app.filtered_notice.winfo_manager(), "pack")

    def test_empty_results_clear_clean_editor(self):
        self.app.query.set("impossible search with no results")
        self.app._search_now()
        self.assertIsNone(self.app.selected_id)
        self.assertEqual(self.app.detail_empty.winfo_manager(), "pack")
        self.assertEqual(_text(self.app.letter_text), "")

    def test_cancel_selection_restores_card_and_editor(self):
        original = self.app.selected_id
        target = next(job_id for job_id in self.ids if job_id != original)
        self.app.notes.insert("1.0", "Do not lose this note")
        with patch("norway_job_agent.desktop.messagebox.askyesnocancel", return_value=None) as question:
            self.app.tree.selection_set(str(target))
        question.assert_called_once()
        self.assertEqual(self.app.selected_id, original)
        self.assertEqual(self.app.tree.selection(), (str(original),))
        self.assertEqual(_text(self.app.notes), "Do not lose this note")

    def test_save_before_selection_writes_original_job_only(self):
        original = self.app.selected_id
        target = next(job_id for job_id in self.ids if job_id != original)
        self.app.notes.insert("1.0", "Original job notes")
        self.app.letter_text.insert("1.0", "Original job letter")
        with patch("norway_job_agent.desktop.messagebox.askyesnocancel", return_value=True):
            self.app.tree.selection_set(str(target))
        self.assertEqual(service.get_vacancy(self.data, original)["notes"], "Original job notes")
        self.assertEqual(service.draft_history(self.data, original)[0]["content"], "Original job letter")
        self.assertEqual(service.draft_history(self.data, target), [])
        self.assertEqual(self.app.selected_id, target)

    def test_saving_stage_keeps_original_editor_open(self):
        self.app._navigate("saved")
        original = self.app.selected_id
        self.app.workflow_status.set("preparing")
        self.app.notes.insert("1.0", "Next step")
        self.app._save_workflow()
        self.assertEqual(self.app.selected_id, original)
        self.assertEqual(_text(self.app.notes), "Next step")
        self.assertNotIn(original, self.app.jobs)
        self.assertEqual(service.get_vacancy(self.data, original)["status"], "preparing")

    def test_search_debounces_and_explicit_search_cancels_timer(self):
        self.app.query.set("AI")
        first = self.app.search_after
        self.app.query.set("Data Engineer")
        second = self.app.search_after
        self.assertNotEqual(first, second)
        self.assertNotIn(first, self.root.tk.call("after", "info"))
        self.app._search_now()
        self.assertIsNone(self.app.search_after)
        self.assertEqual(set(self.app.jobs), {self.ids[1]})

    def test_profile_navigation_keeps_edits_until_context_save(self):
        self.app._navigate("profile")
        self.app.fields["name"].set("Edited Name")
        self.app._navigate("all")
        self.app._navigate("profile")
        self.assertEqual(self.app.fields["name"].get(), "Edited Name")
        self.assertEqual(service.read_profile(self.data)["name"], "Test Person")
        self.app._save_current()
        self.assertEqual(service.read_profile(self.data)["name"], "Edited Name")

    def test_async_letter_result_keeps_edits_made_while_generating(self):
        callbacks = []
        with patch.object(self.app, "_run", side_effect=lambda label, work, ready: callbacks.append(ready)):
            self.app._generate_letter()
        self.app.letter_text.insert("1.0", "More recent typing")
        with patch("norway_job_agent.desktop.messagebox.showinfo") as notice:
            callbacks[0]({"cover_letter": "Generated result", "review_notes": [], "used_evidence": []})
        self.assertEqual(_text(self.app.letter_text), "More recent typing")
        notice.assert_called_once()

    @staticmethod
    def _email_candidate(key, title="AI Engineer"):
        return {
            "source": "gmail", "source_id": "email-job-" + key,
            "source_url": "https://example.com/jobs/email-" + key,
            "apply_url": "https://example.com/jobs/email-" + key,
            "title": title, "company": "Email Employer", "location": "Oslo, Norway",
            "description": "A job-alert excerpt for review.",
            "raw_json": {"email": {"message_id": "message-" + key, "subject": "Fixture job alert"}},
        }

    def test_gmail_first_fetch_uses_empty_cursor_and_saved_profile_snapshot(self):
        work = []
        self.app.gmail_query.set("  from:linkedin.com newer_than:30d  ")
        with patch.object(self.app, "_run", side_effect=lambda label, fetch, ready: work.append((fetch, ready))):
            self.app._fetch_gmail()
        self.app.profile["target_roles"].append("Later edit")
        with patch("norway_job_agent.gmail.fetch_candidates", return_value={"candidates": [], "next_page_token": "second", "messages_scanned": 2}) as fetch:
            work[0][1](work[0][0]())
        options = fetch.call_args.kwargs
        self.assertEqual(options, {"query": "from:linkedin.com newer_than:30d", "page_token": "", "max_messages": 50})
        self.assertNotIn("Later edit", fetch.call_args.args[1]["target_roles"])
        self.assertEqual(self.app.email_page_token, "second")
        self.assertEqual(self.app.email_last_query, options["query"])

    def test_gmail_next_page_appends_and_preserves_review_selection(self):
        first = self._email_candidate("one")
        second = self._email_candidate("two", "Data Engineer")
        self.app._show_email_results({"candidates": [first, second], "messages_scanned": 2})
        self.app.email_tree.selection_set("1")
        self.app.email_last_query = self.app.gmail_query.get().strip()
        self.app.email_page_token = "next-cursor"
        result = {"candidates": [first, self._email_candidate("three")], "next_page_token": "", "messages_scanned": 2}
        with patch.object(self.app, "_run", side_effect=lambda label, work, ready: ready(work())), patch("norway_job_agent.gmail.fetch_candidates", return_value=result) as fetch:
            self.app._fetch_gmail(next_page=True)
        self.assertEqual(fetch.call_args.kwargs["page_token"], "next-cursor")
        self.assertEqual(len(self.app.email_candidates), 3)
        self.assertEqual(self.app.email_tree.selection(), ("1",))
        self.assertIn("Data Engineer", _text(self.app.email_preview))
        self.assertFalse(self.app.email_page_token)
        with patch.object(self.app, "_run") as run:
            self.app._fetch_gmail(next_page=True)
        run.assert_not_called()

    def test_gmail_query_change_invalidates_cursor_and_restarts_search(self):
        self.app.email_last_query = self.app.gmail_query.get().strip()
        self.app.email_page_token = "old-cursor"
        self.app.gmail_query.set("label:job-alerts")
        self.assertIsNone(self.app.email_page_token)
        with patch.object(self.app, "_run") as run:
            self.app._fetch_gmail(next_page=True)
        run.assert_not_called()
        with patch.object(self.app, "_run", side_effect=lambda label, work, ready: ready(work())), patch("norway_job_agent.gmail.fetch_candidates", return_value={"candidates": [], "next_page_token": "new-cursor"}) as fetch:
            self.app._fetch_gmail()
        self.assertEqual(fetch.call_args.kwargs["page_token"], "")
        self.assertEqual(fetch.call_args.kwargs["query"], "label:job-alerts")
        self.assertEqual(self.app.email_page_token, "new-cursor")

    def test_gmail_query_changed_during_fetch_does_not_adopt_stale_cursor(self):
        callbacks = []
        with patch.object(self.app, "_run", side_effect=lambda label, work, ready: callbacks.append(ready)):
            self.app._fetch_gmail()
        self.app.gmail_query.set("subject:another search")
        callbacks[0]({"candidates": [self._email_candidate("one")], "next_page_token": "stale-cursor", "messages_scanned": 1})
        self.assertIsNone(self.app.email_page_token)
        self.assertEqual(len(self.app.email_candidates), 1)
        self.assertIn("query changed", self.app.status_message.get())
        with patch.object(self.app, "_run") as run:
            self.app._fetch_gmail(next_page=True)
        run.assert_not_called()

    def test_gmail_imports_selected_only_and_keeps_unsaved_job_edits(self):
        selected_id = self.app.selected_id
        self.app.notes.insert("1.0", "Unsaved notes stay here")
        self.app.letter_text.insert("1.0", "Unsaved draft stays here")
        self.app.workflow_status.set("preparing")
        self.app._show_email_results({"candidates": [self._email_candidate("one"), self._email_candidate("two")], "messages_scanned": 1})
        self.app.email_tree.selection_set("1")
        self.app._import_selected_emails()
        stored = service.vacancies(self.data)
        email_urls = [item["source_url"] for item in stored if item["source"] == "gmail"]
        self.assertEqual(email_urls, ["https://example.com/jobs/email-two"])
        self.assertEqual(self.app.selected_id, selected_id)
        self.assertEqual(_text(self.app.notes), "Unsaved notes stay here")
        self.assertEqual(_text(self.app.letter_text), "Unsaved draft stays here")
        self.assertEqual(self.app.workflow_status.get(), "preparing")
        self.assertEqual(service.get_vacancy(self.data, selected_id)["notes"], "")
        self.assertEqual(service.draft_history(self.data, selected_id), [])
        self.app._import_selected_emails()
        self.assertEqual(len(service.vacancies(self.data)), len(stored))

    def test_gmail_import_without_selection_does_not_write(self):
        self.app._show_email_results({"candidates": [self._email_candidate("one")]})
        self.app.email_tree.selection_remove(*self.app.email_tree.selection())
        with patch("norway_job_agent.desktop_connections.service.import_email_candidates") as save:
            self.app._import_selected_emails()
        save.assert_not_called()

    def test_gmail_busy_connection_does_not_open_picker_or_replace_cancellation(self):
        self.app.busy = True
        with patch("norway_job_agent.desktop_connections.filedialog.askopenfilename") as picker, patch.object(self.app, "_run") as run:
            self.app._connect_gmail()
        picker.assert_not_called()
        run.assert_not_called()
        self.assertIsNone(self.app.gmail_cancel)

    def test_async_progress_preserves_busy_state_until_completion(self):
        with patch("norway_job_agent.desktop.threading.Thread"):
            self.app._run("Starting fixture work", lambda: None, lambda result: None)
        self.assertTrue(self.app.busy)
        self.assertIn("disabled", self.app.email_next_button.state())
        self.app.events.put(("progress", "Downloading fixture: 25%", None))
        self.app._poll()
        self.assertTrue(self.app.busy)
        self.assertEqual(self.app.progress.winfo_manager(), "pack")
        self.assertIn("disabled", self.app.email_next_button.state())
        self.assertEqual(self.app.status_message.get(), "Downloading fixture: 25%")
        completed = []
        self.app.events.put((True, "done", completed.append))
        self.app._poll()
        self.assertFalse(self.app.busy)
        self.assertEqual(self.app.progress.winfo_manager(), "")
        self.assertNotIn("disabled", self.app.email_next_button.state())
        self.assertEqual(completed, ["done"])


if __name__ == "__main__":
    unittest.main()
