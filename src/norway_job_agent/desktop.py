"""A local, standard-library desktop interface for application preparation.

Network and model work runs on daemon workers. Workers return data through a
queue; every Tk operation, including dialogs, stays on the main thread.
"""

from __future__ import annotations

import json
import queue
import re
import threading
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from .ui_components import ThemedScrolledText as ScrolledText
from urllib.parse import urlsplit

from . import service
from .desktop_layout import DesktopLayout, ScrollForm as _ScrollForm
from .desktop_connections import ConnectionPages
from .desktop_appearance import AppearanceControls
from .theme import palette_for
from .text_interactions import install_text_actions


STATUSES = ("new", "saved", "preparing", "ready", "applied", "interview", "rejected", "archived")
TRACKS = {"All vacancies": "", "Target roles": "target", "Related suggestions": "horizon", "Other roles": "other"}
TRACK_LABELS = {"target": "Target", "horizon": "Related", "other": "Other"}
LIST_FIELDS = {"target_roles", "related_roles", "skills", "preferred_locations", "excluded_keywords", "evidence"}
FIELD_LABELS = {
    "name": "Name", "summary": "Professional summary", "target_roles": "Target roles",
    "related_roles": "Related roles to explore", "skills": "Skills", "preferred_locations": "Preferred locations",
    "excluded_keywords": "Excluded terms", "evidence": "Experience and achievements",
    "work_authorization": "Actual work authorization", "cover_letter_language": "Cover letter language",
    "languages": "Actual language levels",
}


def _text(widget: tk.Text) -> str:
    return widget.get("1.0", "end-1c").strip()


def _replace(widget: tk.Text, content: str, *, readonly: bool = False) -> None:
    widget.configure(state="normal")
    widget.delete("1.0", "end")
    widget.insert("1.0", content)
    if readonly:
        widget.configure(state="disabled")


def _lines(value: str, *, commas: bool = True) -> list[str]:
    parts = re.split(r"[,\n]" if commas else r"\n", value)
    return list(dict.fromkeys(part.strip() for part in parts if part.strip()))


def _source_label(source: dict) -> str:
    kind = source.get("type", "unknown")
    if kind == "nav":
        return "NAV / Arbeidsplassen"
    target = source.get("name") or source.get("url") or source.get("board") or "No address"
    region = " · EU" if source.get("region") == "eu" else ""
    return f"{kind}{region} · {target}"


def _format_report(report: dict) -> str:
    rows = [f"Added {report.get('created', 0)} vacancies; refreshed {report.get('updated', 0)}."]
    sources = report.get("sources", [])
    if not sources:
        rows.append("No sources configured. Add NAV, company boards or vacancy URLs in Sources & AI.")
    for item in sources:
        source = item.get("source", {})
        label = _source_label(source) if isinstance(source, dict) else str(source)
        if item.get("ok"):
            rows.append(f"\n✓ {label}\n   Added {item.get('created', 0)}; refreshed {item.get('updated', 0)}.")
            if item.get("coverage"):
                rows.append("   " + item["coverage"])
            if item.get("experimental_token"):
                rows.append("   Using NAV's free experimental token. A registered NAV token can be configured for ongoing use.")
            if item.get("withdrawn"):
                rows.append(f"   Removed {item['withdrawn']} inactive vacancies from results.")
        else:
            rows.append(f"\nCould not collect: {label}\n   {item.get('error', 'Unknown source error')}")
    return "\n".join(rows)


class Desktop(DesktopLayout, ConnectionPages, AppearanceControls):
    def __init__(self, root: tk.Tk, data_dir: Path):
        self.root = root
        self.data_dir = data_dir
        self.events: queue.Queue = queue.Queue()
        self.busy = False
        self.closed = False
        self.async_buttons: list[ttk.Button] = []
        self.selected_id: int | None = None
        self.letter_baseline = ""
        self.notes_baseline = ""
        self.status_baseline = "new"
        self.ignore_selection = False
        self.profile = service.read_profile(data_dir)
        self.settings = service.read_settings(data_dir)
        self.sources = [dict(item) for item in self.settings.get("sources", [])]
        self.jobs: dict[int, dict] = {}
        self.fields: dict[str, tk.Text | tk.StringVar] = {}
        self.status_message = tk.StringVar(value="Your workspace is ready.")
        self._load_appearance()
        self._style()
        self._build()
        self._fill_profile(self.profile)
        self._render_sources()
        previous_report = data_dir / "last-collection.json"
        if previous_report.exists():
            try:
                _replace(self.collection_report, _format_report(json.loads(previous_report.read_text(encoding="utf-8"))), readonly=True)
            except (OSError, ValueError):
                pass
        self._refresh_jobs()
        self._finish_appearance()
        self.root.protocol("WM_DELETE_WINDOW", self._close)
        self.root.after(100, self._poll)
        self.root.after(500, self._sync_ui_state)

    def _error(self, error):
        from .local_ai import LocalAIError
        if isinstance(error, LocalAIError):
            self._show_ai_setup()
            self.model_status.set(str(error))
        self.status_message.set("Action could not be completed. Your saved data remains available.")
        messagebox.showerror("Norway Job Agent", str(error), parent=self.root)

    def _run(self, label, work, on_success):
        if self.busy:
            messagebox.showinfo("Work in progress", "Wait for the current collection or AI request to finish.", parent=self.root)
            return
        self.busy = True
        self.status_message.set(label)
        self.progress.pack(side="right", padx=(12, 0))
        self.progress.start(12)
        for button in self.async_buttons:
            button.state(["disabled"])

        def worker():
            try:
                result = work()
            except Exception as exc:  # Worker errors must be visible in the UI.
                self.events.put((False, exc, None))
            else:
                self.events.put((True, result, on_success))

        threading.Thread(target=worker, daemon=True, name="norway-agent-worker").start()

    def _poll(self):
        if self.closed:
            return
        try:
            while True:
                ok, result, callback = self.events.get_nowait()
                if ok in ("progress", "model_progress"):
                    self.status_message.set(result)
                    if ok == "model_progress":
                        self.model_status.set(result)
                    continue
                self.busy = False
                self.progress.stop()
                self.progress.pack_forget()
                for button in self.async_buttons:
                    button.state(["!disabled"])
                if ok:
                    try:
                        callback(result)
                    except Exception as exc:
                        self._error(exc)
                else:
                    self._error(result)
        except queue.Empty:
            pass
        self.root.after(100, self._poll)

    def _refresh_jobs(self, select_id=None, *, preserve_editor=False):
        try:
            if self.search_after is not None:
                self.root.after_cancel(self.search_after)
                self.search_after = None
            status = self.status_filter.get()
            jobs = service.vacancies(self.data_dir, query=self.query.get().strip(), status="" if status == "All statuses" else status)
            view = getattr(self, "view_filter", "all")
            if view == "foryou":
                jobs = [job for job in jobs if job.get("match", {}).get("track") in {"target", "horizon"}]
            elif view == "saved":
                jobs = [job for job in jobs if job.get("status") == "saved"]
            elif view == "progress":
                jobs = [job for job in jobs if job.get("status") in {"preparing", "ready", "applied", "interview"}]
            track = TRACKS[self.track_filter.get()]
            jobs = [job for job in jobs if not track or job.get("match", {}).get("track", "other") == track]
            self.jobs = {int(job["id"]): job for job in jobs}
            keep = self.selected_id
            if select_id is not None and select_id in self.jobs and select_id != self.selected_id:
                if self._guard_job_changes():
                    keep = select_id
            elif keep not in self.jobs and not preserve_editor and not self._job_has_changes():
                keep = jobs[0]["id"] if jobs else None
            self.ignore_selection = True
            self.tree.delete(*self.tree.get_children())
            for job in jobs:
                match = job.get("match", {})
                score = match.get("score")
                self.tree.insert("", "end", iid=str(job["id"]), values=(job.get("title", ""), job.get("company", ""), job.get("location", ""),
                                 "—" if score is None else f"{score}%", TRACK_LABELS.get(match.get("track"), "Other"), job.get("status", "new")))
            if keep in self.jobs:
                self.tree.selection_set(str(keep))
                self.tree.see(str(keep))
            self.ignore_selection = False
            self.count_text.set(f"{len(jobs)} {'vacancy' if len(jobs) == 1 else 'vacancies'} shown")
            if keep is None:
                self._clear_job()
            elif keep != self.selected_id:
                self._load_job(keep)
            elif self.selected_id in self.jobs:
                self._show_description(self.jobs[self.selected_id])
            elif self.selected_id is not None:
                current = service.get_vacancy(self.data_dir, self.selected_id)
                if not current.get("is_active", True):
                    self._show_description(current)
            self._sync_selection_ui()
        except Exception as exc:
            self.ignore_selection = False
            self._error(exc)

    def _job_has_changes(self):
        return self.selected_id is not None and (
            _text(self.notes) != self.notes_baseline or self.workflow_status.get() != self.status_baseline or
            _text(self.letter_text) != self.letter_baseline.strip())

    def _clear_job(self):
        self.selected_id = None
        self.job_heading.set("Choose an opportunity")
        self.job_meta.set("")
        self.letter_baseline = self.notes_baseline = ""
        self.status_baseline = "new"
        self.workflow_status.set("new")
        _replace(self.notes, "")
        _replace(self.letter_text, "")
        _replace(self.description, "", readonly=True)
        self.review_text.set("")

    def _select_job(self, _event=None):
        if self.ignore_selection:
            return
        selected = self.tree.selection()
        if not selected:
            return
        job_id = int(selected[0])
        if job_id == self.selected_id:
            return
        if not self._guard_job_changes():
            self.ignore_selection = True
            if self.selected_id is not None and self.tree.exists(str(self.selected_id)):
                self.tree.selection_set(str(self.selected_id))
            else:
                self.tree.selection_remove(*self.tree.selection())
            self.ignore_selection = False
            return
        try:
            self._load_job(job_id)
        except Exception as exc:
            self._error(exc)

    def _load_job(self, job_id):
        job = service.get_vacancy(self.data_dir, job_id)
        drafts = service.draft_history(self.data_dir, job_id)
        self.selected_id = job_id
        self.selected_provenance = job.get("provenance", [])
        self.job_heading.set(job.get("title", ""))
        self.job_meta.set(" · ".join(str(value) for value in (job.get("company"), job.get("location")) if value))
        self._show_description(job)
        self.status_baseline = job.get("status", "new")
        self.workflow_status.set(self.status_baseline)
        self.notes_baseline = job.get("notes", "")
        _replace(self.notes, self.notes_baseline)
        self.letter_baseline = drafts[0].get("content", "") if drafts else ""
        _replace(self.letter_text, self.letter_baseline)
        self.review_text.set(f"Latest saved version: {drafts[0].get('created_at', '')}" if drafts else "No draft yet. Generate one with local AI, or write your own here.")
        self._sync_selection_ui()

    def _show_description(self, job):
        scroll = self.description.yview()[0] if getattr(self, "_description_id", None) == job.get("id") else 0
        self._description_id = job.get("id")
        match = job.get("match", {})
        reasons = list(match.get("reasons", []))
        if not reasons:
            for key, label in (("matched_roles", "Roles"), ("matched_skills", "Skills"), ("matched_locations", "Locations")):
                if match.get(key):
                    reasons.append(label + ": " + ", ".join(match[key]))
        score = match.get("score")
        self.description.configure(state="normal")
        self.description.delete("1.0", "end")
        def add(content, tag=None):
            self.description.insert("end", content + "\n", tag or ())
        add(f"{'No match score' if score is None else str(score) + '% keyword match'}  ·  {TRACK_LABELS.get(match.get('track'), 'Other')} role", "match")
        if not job.get("is_active", True):
            add("This vacancy is no longer active. Your notes and drafts are retained.", "warning")
        if job.get("deadline"):
            add("Deadline: " + str(job["deadline"]), "muted")
        if reasons:
            add("Why this could fit", "section")
            for reason in reasons:
                add("• " + str(reason), "muted")
        if match.get("warnings"):
            add("Things to review", "section")
            for warning in match["warnings"]:
                add("• " + str(warning), "warning")
        add("About the opportunity", "section")
        add(str(job.get("description") or "No description available. Open the original vacancy to review it."))
        add("Source links", "section")
        add(str(job.get("source_url", "")), "muted")
        if job.get("apply_url") != job.get("source_url"):
            add(str(job.get("apply_url", "")), "muted")
        provenance = job.get("provenance", getattr(self, "selected_provenance", []))
        if provenance:
            add("Email sources", "section")
            for origin in provenance:
                add(" · ".join(str(origin.get(key) or "") for key in ("subject", "sender", "received_at")), "muted")
                if origin.get("message_url"):
                    add(str(origin["message_url"]), "muted")
        self.description.configure(state="disabled")
        self.description.yview_moveto(scroll)

    def _require_job(self):
        if self.selected_id is None:
            messagebox.showinfo("Select a vacancy", "Select a vacancy in the list first.", parent=self.root)
            return None
        return self.selected_id

    def _save_workflow(self, notify=True):
        job_id = self._require_job()
        if job_id is None:
            return False
        try:
            status, notes = self.workflow_status.get(), _text(self.notes)
            service.update_workflow(self.data_dir, job_id, status, notes)
            self.status_baseline, self.notes_baseline = status, notes
            self._refresh_jobs(preserve_editor=True)
            if notify:
                self.status_message.set("Status and private notes saved.")
            return True
        except Exception as exc:
            self._error(exc)
            return False

    def _guard_job_changes(self):
        if self.selected_id is None:
            return True
        dirty_notes = _text(self.notes) != self.notes_baseline or self.workflow_status.get() != self.status_baseline
        dirty_letter = _text(self.letter_text) != self.letter_baseline.strip()
        if not dirty_notes and not dirty_letter:
            return True
        answer = messagebox.askyesnocancel("Unsaved vacancy changes", "Save your edited letter, status and notes before leaving this vacancy?", parent=self.root)
        if answer is None:
            return False
        if answer:
            if dirty_notes and not self._save_workflow(notify=False):
                return False
            if dirty_letter and not self._save_letter(notify=False):
                return False
        return True

    def _open_url(self, url):
        try:
            parts = urlsplit(str(url).strip())
            if parts.scheme.lower() not in {"http", "https"} or not parts.hostname or parts.username is not None or parts.password is not None:
                raise ValueError("This link must be a complete HTTP or HTTPS URL without credentials.")
            if not webbrowser.open(str(url).strip()):
                raise OSError("Could not open your browser. Copy the link from the vacancy details.")
        except Exception as exc:
            self._error(exc)

    def _open_job_link(self, field):
        job_id = self._require_job()
        if job_id is None:
            return
        try:
            job = service.get_vacancy(self.data_dir, job_id)
            url = job.get(field) or job.get("source_url", "")
            self._open_url(url)
        except Exception as exc:
            self._error(exc)

    def _collect(self):
        if not self._save_settings(notify=False):
            return
        if not self.sources:
            self.notebook.select(self.settings_page)
            messagebox.showinfo("Add your first source", "Add NAV, a Greenhouse or Lever company board, or a public vacancy URL, then collect again.", parent=self.root)
            return

        def ready(report):
            _replace(self.collection_report, _format_report(report), readonly=True)
            self._refresh_jobs()
            failures = sum(not source.get("ok") for source in report.get("sources", []))
            more = " Some sources report partial coverage. See their coverage notes in Collection activity." if any(source.get("has_more") for source in report.get("sources", [])) else ""
            self.status_message.set(f"Collection finished: {report.get('created', 0)} added, {report.get('updated', 0)} refreshed, {failures} source failures.{more} Details in Sources & AI.")
            if failures:
                failed = [source for source in report.get("sources", []) if not source.get("ok")]
                details = "\n\n".join(f"{_source_label(item['source'])}\n{item.get('error', 'Could not read this source.')}" for item in failed[:3])
                messagebox.showwarning("Some sources need attention",
                                       f"Added {report.get('created', 0)} vacancies. {failures} sources could not be collected.\n\n{details}\n\nSee Sources & AI → Collection activity for the full report.", parent=self.root)

        self._run("Collecting public sources… You can continue browsing your saved vacancies.",
                  lambda: service.collect(self.data_dir, progress=lambda message: self.events.put(("progress", message, None))), ready)

    def _manual_vacancy(self):
        dialog = self._dialog("Add a vacancy", 690, 650)
        frame = ttk.Frame(dialog, padding=18)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="Paste a vacancy you found anywhere", style="Section.TLabel").pack(anchor="w", pady=(0, 10))
        variables = {}
        for key, label in (("title", "Job title *"), ("company", "Company *"), ("location", "Location"),
                           ("source_url", "Original vacancy URL *"), ("apply_url", "Application URL (optional)")):
            ttk.Label(frame, text=label).pack(anchor="w", pady=(5, 3))
            variable = tk.StringVar()
            ttk.Entry(frame, textvariable=variable).pack(fill="x")
            variables[key] = variable
        ttk.Label(frame, text="Vacancy description").pack(anchor="w", pady=(8, 3))
        description = ScrolledText(frame, wrap="word", height=10, padx=8, pady=6)
        description.pack(fill="both", expand=True)
        actions = ttk.Frame(frame)
        actions.pack(fill="x", pady=(12, 0))

        def save():
            try:
                job = {key: value.get().strip() for key, value in variables.items()}
                job["description"] = _text(description)
                job["source"] = "manual"
                job_id, created = service.import_vacancy(self.data_dir, job)
                dialog.destroy()
                self.query.set("")
                self.view_filter = "all"
                self.notebook.select(self.vacancies_page)
                self.page_title.set("Discover opportunities")
                self.page_subtitle.set("Your next step in Norway, one opportunity at a time.")
                self._update_navigation()
                self.status_filter.set("All statuses")
                self.track_filter.set("All vacancies")
                self._refresh_jobs(select_id=job_id)
                self.status_message.set("Vacancy added." if created else "Existing vacancy refreshed; saved workflow retained.")
            except Exception as exc:
                messagebox.showerror("Could not save vacancy", str(exc), parent=dialog)

        self._button(actions, "Save vacancy", save, primary=True).pack(side="right")
        self._button(actions, "Cancel", dialog.destroy).pack(side="right", padx=8)

    def _read_form(self):
        profile = dict(self.profile)
        for key, widget in self.fields.items():
            if key in {"english", "norwegian", "search_norwegian", "other_languages"}:
                continue
            value = widget.get().strip() if isinstance(widget, tk.StringVar) else _text(widget)
            profile[key] = _lines(value, commas=key != "evidence") if key in LIST_FIELDS else value
        languages = {}
        for item in _lines(self.fields["other_languages"].get()):
            if ":" not in item:
                raise ValueError("Other languages should use language: level, for example Ukrainian: native.")
            name, level = item.split(":", 1)
            if not name.strip() or not level.strip():
                raise ValueError("Provide both a language name and its level.")
            languages[name.strip()] = level.strip()
        for language, field in (("English", "english"), ("Norwegian", "norwegian")):
            value = self.fields[field].get().strip()
            if value:
                languages[language] = value
        profile["languages"] = languages
        search_languages = dict(profile.get("search_languages", {}))
        norwegian = self.fields["search_norwegian"].get().strip()
        if norwegian:
            search_languages["Norwegian"] = norwegian
        else:
            search_languages.pop("Norwegian", None)
        profile["search_languages"] = search_languages
        profile["cv_text"] = _text(self.cv_text)
        return profile

    def _fill_profile(self, profile):
        for key, widget in self.fields.items():
            if key in {"english", "norwegian", "search_norwegian", "other_languages"}:
                continue
            value = profile.get(key, "")
            value = "\n".join(value) if isinstance(value, list) else str(value)
            if isinstance(widget, tk.StringVar):
                widget.set(value)
            else:
                _replace(widget, value)
        languages = profile.get("languages", {})
        self.fields["english"].set(languages.get("English", ""))
        self.fields["norwegian"].set(languages.get("Norwegian", ""))
        self.fields["search_norwegian"].set(profile.get("search_languages", {}).get("Norwegian", ""))
        self.fields["other_languages"].set(", ".join(f"{name}: {level}" for name, level in languages.items() if name not in {"English", "Norwegian"}))
        _replace(self.cv_text, profile.get("cv_text", ""))

    def _save_profile(self, notify=True):
        try:
            profile = self._read_form()
            service.save_profile(self.data_dir, profile)
            self.profile = profile
            self._refresh_jobs()
            if notify:
                self.status_message.set("Profile saved. Vacancy matches now use these facts and preferences.")
            return True
        except Exception as exc:
            self._error(exc)
            return False

    def _reload_profile(self):
        if not messagebox.askyesno("Reload saved profile", "Replace the profile form and CV text with the last saved version?", parent=self.root):
            return
        try:
            self.profile = service.read_profile(self.data_dir)
            self._fill_profile(self.profile)
            self.status_message.set("Saved profile loaded.")
        except Exception as exc:
            self._error(exc)

    def _import_cv(self):
        path = filedialog.askopenfilename(parent=self.root, title="Import CV text", filetypes=[("CV files", "*.txt *.md *.docx *.pdf"), ("All files", "*.*")])
        if not path:
            return
        from .runtime_setup import pdf_available
        if Path(path).suffix.lower() == ".pdf" and not pdf_available():
            self._show_ai_setup()
            self.status_message.set("Choose Install PDF support here, then import your CV again.")
            return
        original = _text(self.cv_text)

        def ready(content):
            if _text(self.cv_text) != original:
                if not messagebox.askyesno("Replace CV text", "You edited the CV while importing. Replace the current CV text with the imported file?", parent=self.root):
                    return
            elif original and not messagebox.askyesno("Replace CV text", "Replace the CV text in this form with the imported file?", parent=self.root):
                return
            _replace(self.cv_text, content)
            self.status_message.set("CV imported into the form. Review it, then save your profile or request suggestions.")

        self._run("Reading your CV file…", lambda: service.import_cv_text(Path(path)), ready)

    def _suggest_profile(self):
        cv = _text(self.cv_text)
        if not cv:
            messagebox.showinfo("Add your CV", "Paste or import CV text first.", parent=self.root)
            return
        if not self._save_settings(notify=False):
            return
        self._run("Local AI is reading your CV… This may take a few minutes.", lambda: self._with_local_ai(lambda: service.suggest_profile(self.data_dir, cv)), self._review_suggestion)

    def _review_suggestion(self, suggestion):
        if not isinstance(suggestion, dict):
            raise ValueError("The local model did not return a profile suggestion.")
        dialog = self._dialog("Review CV suggestions", 790, 700)
        frame = ttk.Frame(dialog, padding=18)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="Check the facts before using them", style="Section.TLabel").pack(anchor="w")
        ttk.Label(frame, text="Select the suggestions to copy into your profile form. You can edit them there before saving.\n"
                              "Actual language levels and work authorization must match your situation.",
                  wraplength=720, style="Muted.TLabel").pack(anchor="w", pady=(5, 12))
        if suggestion.get("_review_notes"):
            ttk.Label(frame, text="\n".join(suggestion["_review_notes"]), wraplength=720, style="Muted.TLabel").pack(anchor="w", pady=(0, 10))
        form = _ScrollForm(frame)
        form.pack(fill="both", expand=True)
        selected = {}
        for key in FIELD_LABELS:
            if key not in suggestion or suggestion[key] in (None, "", [], {}):
                continue
            variable = tk.BooleanVar(value=key not in {"languages", "work_authorization"})
            selected[key] = variable
            ttk.Checkbutton(form.content, text=FIELD_LABELS[key], variable=variable).pack(anchor="w", pady=(8, 3))
            value = suggestion[key]
            if isinstance(value, list):
                text = "\n".join("• " + str(item) for item in value)
            elif isinstance(value, dict):
                text = "\n".join(f"{name}: {level}" for name, level in value.items())
            else:
                text = str(value)
            ttk.Label(form.content, text=text, wraplength=665, justify="left", style="Muted.TLabel").pack(anchor="w", padx=(24, 0))
        if not selected:
            ttk.Label(form.content, text="The model did not suggest any supported profile fields.").pack(anchor="w")
        actions = ttk.Frame(frame)
        actions.pack(fill="x", pady=(12, 0))

        def use():
            try:
                profile = self._read_form()
                for key, variable in selected.items():
                    if variable.get():
                        profile[key] = suggestion[key]
                self._fill_profile(profile)
                dialog.destroy()
                self.status_message.set("Suggestions copied into the profile form. Review and edit them, then click Save profile.")
            except Exception as exc:
                messagebox.showerror("Could not use suggestions", str(exc), parent=dialog)

        self._button(actions, "Use selected suggestions in form", use, primary=True).pack(side="right")
        self._button(actions, "Discard suggestions", dialog.destroy).pack(side="right", padx=8)
        self.status_message.set("CV suggestions are ready for your review; your saved profile has not changed.")

    def _render_sources(self):
        self.source_tree.delete(*self.source_tree.get_children())
        for index, source in enumerate(self.sources):
            target = "Arbeidsplassen · free experimental feed" if source.get("type") == "nav" else source.get("name") or source.get("url") or source.get("board") or source.get("tenant", "")
            self.source_tree.insert("", "end", iid=str(index), values=(source.get("type", ""), target, source.get("region", "")))
        self._render_catalog()

    def _add_source(self):
        dialog = self._dialog("Add public source", 650, 510)
        frame = ttk.Frame(dialog, padding=18)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="Source type", style="Section.TLabel").pack(anchor="w")
        kinds = {"NAV / Arbeidsplassen": "nav", "Greenhouse company board": "greenhouse", "Lever company board": "lever", "Public vacancy URL": "job_url"}
        kind = tk.StringVar(value="NAV / Arbeidsplassen")
        combo = ttk.Combobox(frame, textvariable=kind, values=list(kinds), state="readonly")
        combo.pack(fill="x", pady=(5, 10))
        help_text = tk.StringVar()
        ttk.Label(frame, textvariable=help_text, wraplength=600, style="Muted.TLabel").pack(anchor="w", pady=(0, 12))
        target_label = tk.StringVar()
        ttk.Label(frame, textvariable=target_label).pack(anchor="w")
        target = tk.StringVar()
        target_entry = ttk.Entry(frame, textvariable=target)
        target_entry.pack(fill="x", pady=(5, 12))
        region_row = ttk.Frame(frame)
        region_row.pack(fill="x")
        ttk.Label(region_row, text="Lever region").pack(side="left", padx=(0, 8))
        region = tk.StringVar(value="global")
        region_combo = ttk.Combobox(region_row, textvariable=region, values=("global", "eu"), state="disabled", width=15)
        region_combo.pack(side="left")
        ttk.Label(frame, text="Optional company-board location filter (comma separated)").pack(anchor="w", pady=(12, 4))
        location_filter = tk.StringVar()
        location_entry = ttk.Entry(frame, textvariable=location_filter)
        location_entry.pack(fill="x")

        def update(_event=None):
            value = kinds[kind.get()]
            target_label.set("No address needed" if value == "nav" else "Vacancy URL" if value == "job_url" else "Company board name")
            target_entry.configure(state="disabled" if value == "nav" else "normal")
            help_text.set({"nav": "Free experimental NAV feed. Searches your target and related role terms, beginning with the last 90 days of updates. Each collection reads a limited batch. No API key needed for the experiment.",
                           "greenhouse": "For a careers address such as boards.greenhouse.io/company, enter company. You can also paste the board URL.",
                           "lever": "For jobs.lever.co/company, enter company. For jobs.eu.lever.co/company choose the EU region. You can also paste the board URL.",
                           "job_url": "Paste one public job detail page containing JobPosting structured data. General search pages and some dynamic career sites are not supported; use Add vacancy when needed."}[value])
            region_combo.configure(state="readonly" if value == "lever" else "disabled")
            location_entry.configure(state="normal" if value in {"lever", "greenhouse"} else "disabled")

        combo.bind("<<ComboboxSelected>>", update)
        update()
        actions = ttk.Frame(frame)
        actions.pack(side="bottom", fill="x", pady=(18, 0))

        def add():
            try:
                source_type = kinds[kind.get()]
                value = target.get().strip()
                if not value and source_type != "nav":
                    raise ValueError("Enter a board name or vacancy URL.")
                source = {"type": source_type}
                selected_region = region.get()
                if source_type == "nav":
                    pass
                elif source_type == "job_url":
                    parts = urlsplit(value)
                    if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password:
                        raise ValueError("Enter a complete public HTTP or HTTPS vacancy URL.")
                    source["url"] = value
                else:
                    if "://" in value:
                        parts = urlsplit(value)
                        allowed = {"boards.greenhouse.io", "job-boards.greenhouse.io", "job-boards.eu.greenhouse.io"} if source_type == "greenhouse" else {"jobs.lever.co", "jobs.eu.lever.co"}
                        if parts.scheme not in {"http", "https"} or parts.hostname not in allowed or parts.username or parts.password:
                            raise ValueError("That URL does not belong to the selected board provider. Enter the board name instead.")
                        segments = [part for part in parts.path.split("/") if part]
                        if not segments:
                            raise ValueError("The board URL needs a company name in its path.")
                        value = segments[0]
                        if parts.hostname == "jobs.eu.lever.co":
                            selected_region = "eu"
                    if not re.fullmatch(r"[A-Za-z0-9_-]+", value):
                        raise ValueError("Use the company board name: letters, numbers, underscores or hyphens.")
                    source["board"] = value
                    if source_type == "lever":
                        source["region"] = selected_region
                    if location_filter.get().strip():
                        source["locations"] = _lines(location_filter.get())
                if source in self.sources:
                    raise ValueError("This source is already in your list.")
                self.sources.append(source)
                self._render_sources()
                dialog.destroy()
                self.status_message.set("Source added to the form. Click Save settings, or Collect from sources to save and collect.")
            except Exception as exc:
                messagebox.showerror("Could not add source", str(exc), parent=dialog)

        self._button(actions, "Add source", add, primary=True).pack(side="right")
        self._button(actions, "Cancel", dialog.destroy).pack(side="right", padx=8)

    def _remove_source(self):
        selected = self.source_tree.selection()
        if not selected:
            messagebox.showinfo("Select a source", "Select a source in the list first.", parent=self.root)
            return
        del self.sources[int(selected[0])]
        self._render_sources()
        self.status_message.set("Source removed from the form. Save settings to keep this change. Existing vacancies are retained.")

    def _current_settings(self):
        return {**self.settings, "model": self.model.get().strip(), "sources": [dict(item) for item in self.sources]}

    def _save_settings(self, notify=True):
        try:
            settings = self._current_settings()
            service.save_settings(self.data_dir, settings)
            self.settings = settings
            if notify:
                self.status_message.set("Sources and local AI model saved.")
            return True
        except Exception as exc:
            self._error(exc)
            return False

    def _check_models(self):
        def ready(models):
            self.model_combo.configure(values=models)
            chosen = self.model.get().strip()
            if models:
                if chosen in models:
                    self.model_status.set(f"Ollama is available. Selected model {chosen} is installed.")
                else:
                    self.model_status.set("Ollama is available. Choose an installed model from the list, then save settings. Installed: " + ", ".join(models))
            else:
                self.model_status.set("Ollama is running, but no models were found. Run: ollama pull qwen3:4b")
            self.status_message.set("Local model check finished.")

        self._run("Checking your local Ollama models…", service.available_models, ready)

    def _generate_letter(self):
        job_id = self._require_job()
        if job_id is None or not self._guard_job_changes() or not self._save_settings(notify=False):
            return
        original = _text(self.letter_text)

        def ready(result):
            if self.selected_id == job_id and _text(self.letter_text) == original:
                content = str(result.get("cover_letter", ""))
                _replace(self.letter_text, content)
                self.letter_baseline = content
                notes = result.get("review_notes", [])
                evidence = result.get("used_evidence", [])
                review = "Review: " + ("; ".join(str(item) for item in notes) if isinstance(notes, list) else str(notes))
                if evidence:
                    review += " | Evidence used: " + ("; ".join(str(item) for item in evidence) if isinstance(evidence, list) else str(evidence))
                self.review_text.set(review)
                self.details_tabs.select(2)
            else:
                messagebox.showinfo("Draft saved", "The generated letter was saved in the original vacancy's Draft history. Your current edits have been kept.", parent=self.root)
            self.status_message.set("Generated draft saved. Check its claims and review notes before using it.")

        self._run("Writing a cover letter with local AI… This may take a few minutes.", lambda: self._with_local_ai(lambda: service.write_letter(self.data_dir, job_id)), ready)

    def _save_letter(self, notify=True):
        job_id = self._require_job()
        if job_id is None:
            return False
        try:
            content = _text(self.letter_text)
            if not content:
                raise ValueError("Write or generate a cover letter before saving. Clearing the editor does not delete saved drafts.")
            draft_id = service.save_edited_letter(self.data_dir, job_id, content)
            self.letter_baseline = content
            if notify:
                self.status_message.set(f"Edited draft saved as version {draft_id}.")
            return True
        except Exception as exc:
            self._error(exc)
            return False

    def _export_letter(self):
        job_id = self._require_job()
        if job_id is None:
            return
        content = _text(self.letter_text)
        if not content:
            messagebox.showinfo("No letter to export", "Generate a cover letter or write one in the editor first.", parent=self.root)
            return
        path = filedialog.asksaveasfilename(parent=self.root, title="Export reviewed cover letter", defaultextension=".txt",
                                          initialfile=f"cover-letter-{job_id}.txt", filetypes=[("Text document", "*.txt")])
        if not path:
            return
        try:
            target = Path(path)
            # Windows save dialogs normally ask too; this also covers platforms
            # whose native dialog omits overwrite confirmation.
            if target.exists() and not messagebox.askyesno("Replace existing file", f"Replace this file?\n{target}", parent=self.root):
                return
            target.write_text(content + "\n", encoding="utf-8")
            self.status_message.set(f"Cover letter exported to {target}")
        except Exception as exc:
            self._error(exc)

    def _history(self):
        job_id = self._require_job()
        if job_id is None:
            return
        try:
            drafts = service.draft_history(self.data_dir, job_id)
        except Exception as exc:
            self._error(exc)
            return
        if not drafts:
            messagebox.showinfo("Draft history", "There are no saved drafts for this vacancy yet.", parent=self.root)
            return
        dialog = self._dialog("Saved cover letters", 860, 700)
        frame = ttk.Frame(dialog, padding=18)
        frame.pack(fill="both", expand=True)
        choices = [f"Version {draft['id']} · {draft.get('created_at', '')} · {draft.get('model') or 'Edited manually'}" for draft in drafts]
        choice = ttk.Combobox(frame, values=choices, state="readonly")
        choice.pack(fill="x", pady=(0, 10))
        choice.current(0)
        preview = ScrolledText(frame, wrap="word", padx=10, pady=8)
        preview.pack(fill="both", expand=True)
        _replace(preview, drafts[0]["content"], readonly=True)
        choice.bind("<<ComboboxSelected>>", lambda _event: _replace(preview, drafts[choice.current()]["content"], readonly=True))
        actions = ttk.Frame(frame)
        actions.pack(fill="x", pady=(12, 0))

        def use():
            if _text(self.letter_text) != self.letter_baseline.strip():
                if not messagebox.askyesno("Replace current edits", "Replace the unsaved letter in your editor with this saved version?", parent=dialog):
                    return
            content = drafts[choice.current()]["content"]
            _replace(self.letter_text, content)
            self.letter_baseline = content
            self.review_text.set("Loaded a saved version for review and editing.")
            dialog.destroy()
            self.details_tabs.select(2)

        self._button(actions, "Load in editor", use, primary=True).pack(side="right")
        self._button(actions, "Close", dialog.destroy).pack(side="right", padx=8)

    def _dialog(self, title, width, height):
        dialog = tk.Toplevel(self.root)
        dialog.title(title)
        dialog.transient(self.root)
        actual_width = min(width, self.root.winfo_screenwidth() - 80)
        actual_height = min(height, self.root.winfo_screenheight() - 100)
        dialog.geometry(f"{actual_width}x{actual_height}")
        dialog.configure(background=palette_for(self.root)["bg"])
        x = max(0, self.root.winfo_rootx() + (self.root.winfo_width() - actual_width) // 2)
        y = max(0, self.root.winfo_rooty() + (self.root.winfo_height() - actual_height) // 2)
        dialog.geometry(f"+{x}+{y}")
        dialog.minsize(min(480, actual_width), min(320, actual_height))
        dialog.grab_set()
        dialog.bind("<Escape>", lambda _event: dialog.destroy())
        dialog.after_idle(lambda: install_text_actions(dialog) if dialog.winfo_exists() else None)
        return dialog

    def _close(self):
        if self.busy:
            messagebox.showinfo("Work in progress", "A collection or local AI request is running. Please let it finish before closing so you can review the result.", parent=self.root)
            return
        if not self._guard_job_changes():
            return
        try:
            profile_dirty = self._read_form() != self.profile
            settings_dirty = self._current_settings() != self.settings
        except ValueError:
            profile_dirty, settings_dirty = True, self._current_settings() != self.settings
        if profile_dirty or settings_dirty:
            answer = messagebox.askyesnocancel("Unsaved profile or settings", "Save your profile and source settings before closing?", parent=self.root)
            if answer is None:
                return
            if answer and ((profile_dirty and not self._save_profile(notify=False)) or (settings_dirty and not self._save_settings(notify=False))):
                return
        self.closed = True
        self.root.destroy()


def launch(data_dir: Path) -> None:
    """Initialize local data and run the desktop until its window is closed."""
    data_dir = Path(data_dir).expanduser().resolve()
    service.initialize(data_dir)
    root = tk.Tk()
    try:
        Desktop(root, data_dir)
    except Exception:
        root.destroy()
        raise
    root.mainloop()
