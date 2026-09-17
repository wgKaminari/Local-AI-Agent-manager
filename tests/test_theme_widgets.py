"""Real Tk regressions for appearance changes and custom control interactions."""

import sys
import tkinter as tk
import unittest
from pathlib import Path
from tkinter import ttk
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from norway_job_agent.theme import PALETTES, apply_theme, palette_for
from norway_job_agent.ui_components import OpportunityList, RoundedButton, SearchEntry, TabDeck, ThemedScrolledText


class ThemeWidgetTests(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(f"A working Tk display is required: {exc}")
        self.root.geometry("680x440+0+0")
        self.root.attributes("-alpha", 0.0)
        self.errors = []
        self.root.report_callback_exception = lambda *error: self.errors.append(error)

    def tearDown(self):
        if hasattr(self, "root"):
            self.root.destroy()
            self.assertEqual(self.errors, [], "Tk callback errors")

    def flush(self):
        self.root.update_idletasks()
        self.root.update()
        self.root.update_idletasks()

    def test_theme_changes_keep_editor_identity_selection_scroll_and_undo(self):
        editor = tk.Text(self.root, undo=True, autoseparators=False, height=8)
        editor.pack(fill="both", expand=True)
        original = "".join(f"Draft line {index}\n" for index in range(100))
        editor.insert("1.0", original)
        editor.edit_reset()
        editor.insert("end", "My unsaved addition")
        editor.edit_separator()
        editor.tag_add("sel", "50.0", "50.5")
        editor.mark_set("insert", "50.5")
        self.flush()
        editor.yview_moveto(.4)
        baseline = editor.yview()
        widget_id = str(editor)
        for name in ("dark", "light", "dark"):
            apply_theme(self.root, name)
            self.flush()
            self.assertEqual(str(editor), widget_id)
            self.assertEqual(editor.get("1.0", "end-1c"), original + "My unsaved addition")
            self.assertEqual(tuple(map(str, editor.tag_ranges("sel"))), ("50.0", "50.5"))
            self.assertEqual(editor.index("insert"), "50.5")
            self.assertAlmostEqual(editor.yview()[0], baseline[0], places=4)
            self.assertEqual(editor.cget("background"), PALETTES[name]["surface"])
            if "inactiveselectbackground" in editor.keys():
                self.assertEqual(editor.cget("inactiveselectbackground"), PALETTES[name]["selection"])
        editor.edit_undo()
        self.assertEqual(editor.get("1.0", "end-1c"), original)

    def test_new_dialog_and_children_inherit_current_theme(self):
        apply_theme(self.root, "dark")
        self.flush()
        dialog = tk.Toplevel(self.root)
        dialog.attributes("-alpha", 0.0)
        body = ttk.Frame(dialog)
        body.pack(fill="both", expand=True)
        editor = tk.Text(body, height=2, background="white", foreground="black", state="disabled")
        editor.pack()
        title = tk.Label(body, text="New dialog", background="#ffffff", foreground="#202123")
        title.pack()
        self.flush()
        self.assertIs(palette_for(dialog), PALETTES["dark"])
        self.assertEqual(dialog.cget("background"), PALETTES["dark"]["bg"])
        self.assertEqual(title.cget("foreground"), PALETTES["dark"]["text"])
        self.assertEqual(editor.cget("background"), PALETTES["dark"]["surface"])
        self.assertEqual(editor.cget("state"), "disabled")
        apply_theme(self.root, "light")
        self.flush()
        self.assertIs(palette_for(editor), PALETTES["light"])

    def test_themed_scrolled_text_keeps_text_and_scrollbar_geometry_api(self):
        editor = ThemedScrolledText(self.root, undo=True, height=5)
        editor.pack(fill="both", expand=True)
        editor.insert("1.0", "A long editable document\n" * 100)
        self.flush()
        self.assertIsInstance(editor, tk.Text)
        self.assertIsInstance(editor.vbar, ttk.Scrollbar)
        self.assertEqual(editor.frame.winfo_manager(), "pack")
        command = editor.vbar.cget("command")
        editor.tk.call(command, "moveto", .5)
        self.flush()
        self.assertGreater(editor.yview()[0], .4)
        position = editor.yview()
        for name in ("dark", "light"):
            apply_theme(self.root, name)
            self.flush()
            self.assertAlmostEqual(editor.vbar.get()[0], editor.yview()[0], places=4)
            self.assertAlmostEqual(editor.yview()[0], position[0], places=4)
            self.assertEqual(editor.frame.cget("background"), PALETTES[name]["surface"])
            self.assertEqual(editor.cget("background"), PALETTES[name]["surface"])
        editor.insert("end", "An unsaved change")
        self.assertTrue(editor.get("1.0", "end-1c").endswith("An unsaved change"))

    def test_switching_themes_does_not_accumulate_map_handlers(self):
        apply_theme(self.root, "dark")
        binding = self.root.bind_all("<Map>")
        for _ in range(3):
            apply_theme(self.root, "light")
            apply_theme(self.root, "dark")
        self.assertEqual(self.root.bind_all("<Map>"), binding)
        with self.assertRaises(ValueError):
            apply_theme(self.root, "unknown")
        self.assertEqual(self.root._app_theme, "dark")

    def test_search_placeholder_never_changes_value_and_matches_surface(self):
        query = tk.StringVar(self.root)
        search = SearchEntry(self.root, query, placeholder="Search opportunities")
        search.pack(fill="x")
        for name in ("dark", "light"):
            apply_theme(self.root, name)
            self.flush()
            self.assertEqual(query.get(), "")
            self.assertEqual(search.get(), "")
            self.assertEqual(search._placeholder_label.cget("background"), search._entry.cget("background"))
            self.assertEqual(search._entry.cget("background"), PALETTES[name]["surface"])
        query.set("Python")
        self.flush()
        self.assertFalse(search._placeholder_label.winfo_manager())
        search.selection_range(0, "end")
        apply_theme(self.root, "dark")
        self.assertTrue(search._entry.selection_present())
        self.assertEqual(search.get(), "Python")
        search.destroy()
        query.set("No stale trace")
        self.flush()

    def test_rounded_button_keeps_state_and_command_across_appearance_changes(self):
        clicks = []
        button = RoundedButton(self.root, "Saved", command=lambda: clicks.append("saved"),
                               selected=True, subtle=True, anchor="w")
        button.pack(fill="x")
        self.flush()
        button.state(["disabled"])
        apply_theme(self.root, "dark")
        button.invoke()
        self.assertEqual(clicks, [])
        self.assertIn("disabled", button.state())
        self.assertTrue(button.cget("selected"))
        button.state(["!disabled"])
        button.configure(text="Applications", selected=False, anchor="center")
        button._keyboard_invoke(None)
        self.assertEqual(clicks, ["saved"])
        self.assertEqual(button.cget("text"), "Applications")
        self.assertFalse(button.cget("selected"))
        button._press(None)
        button._release(SimpleNamespace(x=-1, y=-1))
        self.assertEqual(clicks, ["saved"], "Releasing outside an action must not invoke it")

    def test_opportunity_copy_does_not_change_selection_or_emit_selection_event(self):
        listing = OpportunityList(self.root)
        listing.pack(fill="both", expand=True)
        listing.insert("", "end", iid="first", values=("AI Engineer", "First company", "Oslo", "85%", "Primary", "saved"))
        listing.insert("", "end", iid="second", values=("Mathematician", "Second company", "Bergen", "70%", "Related", "new"))
        self.flush()
        listing.selection_set("first")
        changes = []
        listing.bind("<<TreeviewSelect>>", lambda _event: changes.append(listing.selection()))
        with patch.object(listing, "clipboard_clear") as clear, patch.object(listing, "clipboard_append") as append:
            listing.copy_summary("second")
        clear.assert_called_once()
        self.assertIn("Mathematician\nSecond company\nBergen", append.call_args.args[0])
        self.assertEqual(listing.selection(), ("first",))
        self.assertEqual(changes, [])

    def test_card_list_keeps_scroll_and_selection_when_theme_changes(self):
        listing = OpportunityList(self.root)
        listing.pack(fill="both", expand=True)
        for index in range(25):
            listing.insert("", "end", iid=str(index), values=(f"Data scientist {index}", "Fixture employer", "Oslo"))
        self.flush()
        listing.selection_set("12")
        listing.yview("moveto", .5)
        original_position = listing.yview()[0]
        for name in ("dark", "light"):
            apply_theme(self.root, name)
            self.flush()
            self.assertEqual(listing.selection(), ("12",))
            self.assertAlmostEqual(listing.yview()[0], original_position, places=4)
            item = listing._backgrounds["12"]
            self.assertEqual(listing._canvas.itemcget(item, "fill"), PALETTES[name]["selected"])

    def test_opportunity_menu_survives_nonblocking_popup_until_user_copies(self):
        listing = OpportunityList(self.root)
        listing.pack(fill="both", expand=True)
        listing.insert("", "end", iid="first", values=("AI Engineer", "First company", "Oslo"))
        listing.insert("", "end", iid="second", values=("Mathematician", "Second company", "Bergen"))
        self.flush()
        listing.selection_set("first")
        changes = []
        listing.bind("<<TreeviewSelect>>", lambda _event: changes.append(listing.selection()))
        top, bottom = listing._bounds["second"]
        event = SimpleNamespace(keysym="", x=30, y=int((top + bottom) / 2), x_root=30, y_root=30)
        posted = []
        # X11 posting returns while the menu is still awaiting a user's action.
        with patch.object(tk.Menu, "tk_popup", autospec=True,
                          side_effect=lambda menu, *_args: posted.append(menu)):
            listing._context_menu(event)
        self.flush()
        self.assertEqual(len(posted), 1)
        menu = posted[0]
        self.assertTrue(menu.winfo_exists(), "The menu must remain usable after the next idle cycle")
        with patch.object(listing, "clipboard_clear"), patch.object(listing, "clipboard_append") as append:
            menu.invoke(0)
        self.assertIn("Mathematician\nSecond company\nBergen", append.call_args.args[0])
        self.assertEqual(listing.selection(), ("first",))
        self.assertEqual(changes, [])

    def test_tabs_switch_by_keyboard_without_rebuilding_page_contents(self):
        deck = TabDeck(self.root)
        deck.pack(fill="both", expand=True)
        first, second = ttk.Frame(deck), ttk.Frame(deck)
        draft = tk.Text(first, undo=True)
        draft.pack()
        draft.insert("1.0", "Draft in a background page")
        deck.add(first, text="Description")
        deck.add(second, text="Cover letter")
        self.flush()
        deck._step(str(first), 1)
        apply_theme(self.root, "dark")
        self.flush()
        self.assertEqual(deck.select(), str(second))
        self.assertEqual(draft.get("1.0", "end-1c"), "Draft in a background page")
        deck._step(str(second), 1)
        self.assertEqual(deck.select(), str(first))


if __name__ == "__main__":
    unittest.main()
