"""Real Tk checks for selection, safe copy menus and responsive display text."""

import sys
import tkinter as tk
import unittest
from pathlib import Path
from tkinter import ttk
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from norway_job_agent.text_interactions import (
    SelectableLabel, _build_context_menu, _select_all, install_text_actions,
)
from norway_job_agent.theme import PALETTES


class TextInteractionTests(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(f"A working Tk display is required: {exc}")
        self.root.geometry("560x440+0+0")
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

    @staticmethod
    def labels(menu):
        return [menu.entrycget(index, "label") for index in range(menu.index("end") + 1)
                if menu.type(index) != "separator"]

    def test_display_text_supports_selection_and_copy_without_edits(self):
        label = SelectableLabel(self.root, text="A synthetic job title")
        label.pack(fill="x")
        self.flush()
        _select_all(label)
        self.assertEqual(label.get("sel.first", "sel.last"), "A synthetic job title")
        label.event_generate("<<Copy>>")
        self.flush()
        self.assertEqual(label.clipboard_get(), "A synthetic job title")
        label.clipboard_clear()
        label.clipboard_append("Synthetic replacement")
        label.event_generate("<<Paste>>")
        label.event_generate("<<Cut>>")
        label.insert("end", "not allowed")
        label.delete("1.0", "end")
        self.flush()
        self.assertEqual(label.get("1.0", "end-1c"), "A synthetic job title")
        self.assertEqual(str(label.cget("state")), "disabled")
        self.assertEqual(self.labels(_build_context_menu(label)), ["Copy", "Copy all", "Select all"])

    def test_variable_updates_and_rebinding_detach_old_variable(self):
        first = tk.StringVar(self.root, "First value")
        second = tk.StringVar(self.root, "Second value")
        label = SelectableLabel(self.root, textvariable=first)
        label.pack(fill="x")
        self.flush()
        first.set("Updated value")
        self.assertEqual(label.cget("text"), "Updated value")
        label.configure(textvariable=second, wraplength=250, font=("Segoe UI", 12))
        first.set("Detached value")
        self.assertEqual(label.get("1.0", "end-1c"), "Second value")
        second.set("Current value")
        self.assertEqual(label.get("1.0", "end-1c"), "Current value")
        label.configure(textvariable=None, text="Static value")
        second.set("Detached too")
        self.assertEqual(label.cget("text"), "Static value")
        label.destroy()
        first.set("After destroy")
        second.set("After destroy")

    def test_height_tracks_actual_width_and_shrinks_when_text_changes(self):
        frame = ttk.Frame(self.root, width=220)
        frame.pack(fill="x")
        label = SelectableLabel(frame, text="This long synthetic title should wrap naturally without clipping. " * 5)
        label.pack(fill="x")
        self.flush()
        wider = int(label.cget("height"))
        self.root.geometry("300x440+0+0")
        self.flush()
        narrower = int(label.cget("height"))
        self.assertGreater(narrower, wider)
        label.configure(text="Short title")
        self.flush()
        self.assertEqual(int(label.cget("height")), 1)
        label.configure(text="First line\nSecond line")
        self.flush()
        self.assertEqual(int(label.cget("height")), 2)

    def test_display_text_drag_selection_and_theme_updates_remain_readonly(self):
        label = SelectableLabel(self.root, text="Synthetic title for selection")
        label.pack(fill="x")
        self.flush()
        start = label.bbox("1.0")
        finish = label.bbox("1.9")
        label.event_generate("<Button-1>", x=start[0], y=start[1] + 3)
        label.event_generate("<B1-Motion>", x=finish[0], y=finish[1] + 3)
        label.event_generate("<ButtonRelease-1>", x=finish[0], y=finish[1] + 3)
        self.flush()
        self.assertEqual(label.get("sel.first", "sel.last"), "Synthetic")
        label.configure(background="#171717", foreground="#eeeeee",
                        selectbackground="#356856", selectforeground="#ffffff",
                        font=("Segoe UI", 14), state="normal")
        self.flush()
        self.assertEqual(label.get("sel.first", "sel.last"), "Synthetic")
        self.assertEqual(str(label.cget("state")), "disabled")
        self.assertEqual(label.cget("background"), "#171717")

    def test_actions_can_be_installed_on_new_dialogs_without_duplicate_bindings(self):
        install_text_actions(self.root)
        dialog = tk.Toplevel(self.root)
        dialog.attributes("-alpha", 0.0)
        text = tk.Text(dialog)
        text.insert("1.0", "Synthetic dialog content")
        text.pack()
        install_text_actions(dialog)
        installed = text.bind("<Control-a>")
        install_text_actions(self.root)
        self.assertEqual(text.bind("<Control-a>"), installed)
        _select_all(text)
        menu = _build_context_menu(text)
        menu.invoke(self.labels(menu).index("Copy"))
        self.assertEqual(self.root.clipboard_get(), "Synthetic dialog content")
        dialog.destroy()

    def test_label_menu_copies_current_textvariable(self):
        text = tk.StringVar(self.root, "Old status")
        label = ttk.Label(self.root, textvariable=text)
        label.pack()
        install_text_actions(self.root)
        text.set("Synthetic current status")
        menu = _build_context_menu(label)
        self.assertEqual(self.labels(menu), ["Copy text"])
        menu.invoke(0)
        self.assertEqual(self.root.clipboard_get(), "Synthetic current status")

    def test_menu_uses_current_palette_before_mapping_and_preserves_selection(self):
        self.root._app_palette = PALETTES["dark"]
        label = SelectableLabel(self.root, text="Synthetic selected title")
        label.pack(fill="x")
        self.flush()
        _select_all(label)
        focus = self.root.focus_get()
        menu = _build_context_menu(label)
        self.assertEqual(str(menu.cget("background")), PALETTES["dark"]["surface"])
        self.assertEqual(str(menu.cget("foreground")), PALETTES["dark"]["text"])
        self.assertEqual(str(menu.cget("disabledforeground")), PALETTES["dark"]["disabled_text"])
        self.assertEqual(label.get("sel.first", "sel.last"), "Synthetic selected title")
        self.assertEqual(self.root.focus_get(), focus)
        if "inactiveselectbackground" in label.keys():
            self.assertEqual(str(label.cget("inactiveselectbackground")), PALETTES["dark"]["selection"])
        self.root._app_palette = PALETTES["light"]
        updated = _build_context_menu(label)
        self.assertEqual(str(updated.cget("background")), PALETTES["light"]["surface"])
        self.assertEqual(label.get("sel.first", "sel.last"), "Synthetic selected title")

    def test_entry_actions_preserve_existing_bindings_and_selection(self):
        entry = ttk.Entry(self.root)
        entry.insert(0, "Synthetic editable text")
        entry.pack()
        entry.bind("<Button-3>", lambda _event: None)
        original = entry.bind("<Button-3>")
        install_text_actions(self.root)
        installed = entry.bind("<Button-3>")
        install_text_actions(self.root)
        self.assertIn(original.strip(), installed)
        self.assertEqual(entry.bind("<Button-3>"), installed)
        entry.selection_range(0, 9)
        menu = _build_context_menu(entry)
        self.assertEqual(self.labels(menu), ["Cut", "Copy", "Paste", "Copy all", "Select all"])
        menu.invoke(1)
        self.assertEqual(entry.clipboard_get(), "Synthetic")
        self.assertTrue(entry.selection_present())
        entry.configure(state="readonly")
        self.assertEqual(self.labels(_build_context_menu(entry)), ["Copy", "Copy all", "Select all"])

    def test_tree_copies_clicked_display_column_and_selected_rows(self):
        tree = ttk.Treeview(self.root, columns=("title", "company"), displaycolumns=("company", "title"), show="headings")
        tree.pack(fill="both", expand=True)
        first = tree.insert("", "end", values=("AI Engineer", "Synthetic Company"))
        second = tree.insert("", "end", values=("Data Scientist", "Example Company"))
        self.flush()
        tree.selection_set(first, second)
        x, y, width, height = tree.bbox(second, "company")
        menu = _build_context_menu(tree, SimpleNamespace(x=x + 5, y=y + height // 2))
        menu.invoke(0)
        self.assertEqual(tree.clipboard_get(), "Example Company")
        self.assertEqual(tree.selection(), (first, second))
        menu.invoke(1)
        self.assertEqual(tree.clipboard_get(), "Synthetic Company\tAI Engineer\nExample Company\tData Scientist")


if __name__ == "__main__":
    unittest.main()
