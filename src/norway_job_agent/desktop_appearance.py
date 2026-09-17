"""Persistent appearance controls that never rebuild or save the user's editors."""

from __future__ import annotations

import json
import tkinter as tk
from tkinter import ttk

from . import service
from .theme import apply_theme
from .text_interactions import SelectableLabel, install_text_actions


class AppearanceControls:
    def _load_appearance(self):
        self.ui_preferences = {"theme": "light", "sidebar_visible": True}
        try:
            values = json.loads((self.data_dir / "appearance.json").read_text(encoding="utf-8"))
            if isinstance(values, dict):
                if values.get("theme") in ("light", "dark"):
                    self.ui_preferences["theme"] = values["theme"]
                if isinstance(values.get("sidebar_visible"), bool):
                    self.ui_preferences["sidebar_visible"] = values["sidebar_visible"]
        except (OSError, ValueError):
            pass
        self.theme_name = self.ui_preferences["theme"]
        self.sidebar_visible = self.ui_preferences["sidebar_visible"]

    def _save_appearance(self):
        self.ui_preferences.update(theme=self.theme_name, sidebar_visible=self.sidebar_visible)
        try:
            service._write_json(self.data_dir / "appearance.json", self.ui_preferences)
        except OSError:
            self.status_message.set("Appearance changed for this session. Your preference could not be saved.")

    def _appearance_toolbar(self, parent):
        toolbar = ttk.Frame(parent, padding=(24, 12, 24, 10))
        toolbar.pack(fill="x")
        self.sidebar_toggle = self._button(toolbar, "Hide sidebar", self._toggle_sidebar)
        self.sidebar_toggle.pack(side="left")
        self.workspace_location = tk.StringVar(value="Workspace / Discover")
        ttk.Label(toolbar, textvariable=self.workspace_location, style="Muted.TLabel",
                  font=("Segoe UI", 9)).pack(side="left", padx=16)
        self.theme_button = self._button(toolbar, "Dark mode", self._toggle_theme)
        self.theme_button.pack(side="right")
        self._button(toolbar, "Shortcuts", self._show_shortcuts).pack(side="right", padx=8)
        ttk.Separator(parent).pack(fill="x")
        self.root.bind("<Control-b>", self._toggle_sidebar)
        self.root.bind("<Control-Shift-L>", self._toggle_theme)
        self.root.bind("<Control-slash>", self._show_shortcuts)

    def _finish_appearance(self):
        self._apply_appearance()
        self._show_sidebar(self.sidebar_visible)
        install_text_actions(self.root)

    def _apply_appearance(self):
        colors = apply_theme(self.root, self.theme_name)
        self.theme_button.configure(text="Light mode" if self.theme_name == "dark" else "Dark mode")
        self.description.tag_configure("muted", foreground=colors["muted"])
        self.description.tag_configure("match", foreground=colors["success"])
        self.description.tag_configure("warning", foreground=colors["warning"])
        self._update_navigation()

    def _toggle_theme(self, _event=None):
        if self.root.grab_current() is not None:
            return "break"
        self.theme_name = "dark" if self.theme_name == "light" else "light"
        self._apply_appearance()
        self._save_appearance()
        return "break"

    def _show_sidebar(self, visible):
        self.sidebar_visible = bool(visible)
        if self.sidebar_visible:
            self.sidebar.pack(side="left", fill="y", before=self.main)
        else:
            focused = self.root.focus_get()
            if focused is not None and str(focused).startswith(str(self.sidebar) + "."):
                self.sidebar_toggle.focus_set()
            self.sidebar.pack_forget()
        self.sidebar_toggle.configure(text="Hide sidebar" if self.sidebar_visible else "Show sidebar")

    def _toggle_sidebar(self, _event=None):
        if self.root.grab_current() is not None:
            return "break"
        self._show_sidebar(not self.sidebar_visible)
        self._save_appearance()
        return "break"

    def _copy_opportunity(self):
        if self.selected_id is None:
            self.status_message.set("Choose an opportunity to copy.")
            return
        job = service.get_vacancy(self.data_dir, self.selected_id)
        content = "\n".join(str(job.get(key) or "") for key in ("title", "company", "location", "source_url"))
        self.root.clipboard_clear()
        self.root.clipboard_append(content)
        self.status_message.set("Copied the job title, company, location and original link.")

    def _show_shortcuts(self, _event=None):
        if self.root.grab_current() is not None:
            return "break"
        dialog = self._dialog("Make yourself at home", 580, 590)
        body = ttk.Frame(dialog, padding=28)
        body.pack(fill="both", expand=True)
        SelectableLabel(body, text="A little less clicking", font=("Segoe UI", 22, "bold")).pack(fill="x", pady=(0, 16))
        guide = self._editor(body, readonly=True)
        guide.pack(fill="both", expand=True)
        guide.insert("1.0", "Ctrl K        Search opportunities\nCtrl B        Show or hide the sidebar\nCtrl Shift L  Switch light / dark theme\nCtrl S        Save the current work\nCtrl 1 / 2 / 3  Opportunities / Profile / Sources\nCtrl 4        Gmail alerts\nCtrl /        Open these shortcuts\n\nCopy what you need\nDrag to select headings, job titles or body text, then press Ctrl C. Right-click other labels to copy their text. In tables, right-click a cell to copy it or the selected rows.\n\nWrite comfortably\nText fields support right-click Cut, Copy, Paste and Select all. Read-only text supports selection and copying. Drag the divider to give the reading pane more space.")
        guide.configure(state="disabled")
        self._button(body, "Got it", dialog.destroy, primary=True).pack(anchor="e", pady=(16, 0))
        return "break"
