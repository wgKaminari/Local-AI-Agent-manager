"""Country selection, writing preferences and editable application preparation."""
from copy import deepcopy
import json
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from . import service
from .countries import COUNTRIES


def _get(widget):
    return widget.get() if isinstance(widget, tk.Variable) else widget.get("1.0", "end-1c").strip()


def _put(widget, value):
    if isinstance(widget, tk.Variable):
        widget.set(value)
    else:
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", value)


class ApplicationPages:
    def _active_country(self):
        return next((code for code, name in COUNTRIES.items() if name == self.country_filter.get()), "")

    def _country_changed(self, _event=None):
        country = self._active_country()
        if country and country not in self.country_presets_added:
            self._add_catalog_entries([entry for entry in self.catalog if country in entry.get("countries", [])])
            self.country_presets_added.append(country)
        self._refresh_jobs()
        self._render_sources()
        self._render_job_boards()
        self.status_message.set(f"Searching {self.country_filter.get()}. Verified country sources are ready; your shared profile is unchanged. Save settings to keep this selection.")

    def _writing_preferences_page(self, tabs):
        from .desktop_layout import ScrollForm
        from .application_answers import DEFAULT_WRITING_STYLE
        form = ScrollForm(tabs)
        tabs.add(form, text="Countries & writing")
        content = form.content
        ttk.Label(content, text="Country switching keeps your roles, skills and writing preferences shared.", wraplength=800).pack(anchor="w", pady=10)
        self.country_facts = {}
        for code, name in COUNTRIES.items():
            ttk.Label(content, text=f"{name} — actual work authorization", style="Section.TLabel").pack(anchor="w", pady=(12, 4))
            value = tk.StringVar()
            ttk.Entry(content, textvariable=value).pack(fill="x")
            self.country_facts[code] = value
        ttk.Label(content, text="Leave unknown permission blank. Country selection never implies work eligibility.", style="Muted.TLabel").pack(anchor="w", pady=8)
        self.style_fields = {}
        for key, label, height in (("tone", "Writing tone", 0), ("language", "Writing language override (blank uses profile)", 0),
                                   ("max_words", "Maximum words per answer / cover letter (20–500)", 0),
                                   ("instructions", "Style instructions", 3), ("examples", "Your writing examples — one paragraph per line, up to five", 5),
                                   ("avoid_phrases", "Phrases to avoid — one per line", 3)):
            ttk.Label(content, text=label, style="Section.TLabel").pack(anchor="w", pady=(16, 4))
            if height:
                widget = self._editor(content, height=height)
                widget.pack(fill="x")
            else:
                widget = tk.StringVar(value=str(DEFAULT_WRITING_STYLE[key]))
                ttk.Entry(content, textvariable=widget).pack(fill="x")
            self.style_fields[key] = widget
        ttk.Label(content, text="Examples guide phrasing only. Facts still come from your saved profile and CV.", style="Muted.TLabel").pack(anchor="w", pady=12)

    def _fill_writing_preferences(self, profile):
        from .application_answers import validate_writing_style
        for code, variable in self.country_facts.items():
            variable.set(profile.get("country_preferences", {}).get(code, {}).get("work_authorization", ""))
        style = validate_writing_style(profile.get("writing_style", {}))
        for key, widget in self.style_fields.items():
            _put(widget, "\n".join(style[key]) if isinstance(style[key], list) else str(style[key]))

    def _read_writing_preferences(self, profile):
        from .application_answers import validate_writing_style
        style = {key: _get(widget).strip() for key, widget in self.style_fields.items()}
        try:
            style["max_words"] = int(style["max_words"])
        except ValueError:
            raise ValueError("Maximum words must be a whole number from 20 to 500.") from None
        profile["writing_style"] = validate_writing_style(style)
        preferences = deepcopy(profile.get("country_preferences", {}))
        for code, variable in self.country_facts.items():
            value = variable.get().strip()
            if value or "work_authorization" in preferences.get(code, {}):
                preferences.setdefault(code, {})["work_authorization"] = value
        profile["country_preferences"] = preferences

    def _job_boards_page(self, parent):
        from .company_catalog import job_board_catalog
        self.boards = job_board_catalog()
        ttk.Label(parent, text="Researched starting points for the selected country. Select a board to see access and coverage details.", wraplength=850).pack(anchor="w", pady=10)
        country = ttk.Combobox(parent, textvariable=self.country_filter, values=("All countries", *COUNTRIES.values()), state="readonly")
        country.pack(anchor="w", pady=(0, 8))
        country.bind("<<ComboboxSelected>>", self._country_changed)
        self.boards_tree = ttk.Treeview(parent, columns=("name", "country", "mode"), show="headings", selectmode="browse", height=10)
        for key, label in (("name", "SOURCE"), ("country", "COUNTRIES"), ("mode", "ACCESS")):
            self.boards_tree.heading(key, text=label)
        self.boards_tree.pack(fill="both", expand=True)
        self.board_info = tk.StringVar()
        ttk.Label(parent, textvariable=self.board_info, wraplength=850, style="Muted.TLabel").pack(fill="x", pady=10)
        self.boards_tree.bind("<<TreeviewSelect>>", lambda _e: self._board_selected())
        self._button(parent, "Open selected source ↗", self._open_board).pack(anchor="w")
        self._render_job_boards()

    def _render_job_boards(self):
        if not hasattr(self, "boards_tree"):
            return
        self.boards_tree.delete(*self.boards_tree.get_children())
        for index, entry in enumerate(self.boards):
            if not self._active_country() or self._active_country() in entry["countries"]:
                self.boards_tree.insert("", "end", iid=str(index), values=(entry["name"], ", ".join(entry["countries"]), entry.get("access_mode", "manual")))

    def _board_selected(self):
        selection = self.boards_tree.selection()
        if selection:
            entry = self.boards[int(selection[0])]
            self.board_info.set(entry.get("note", "") + "\n" + entry["careers_url"])

    def _open_board(self):
        selection = self.boards_tree.selection()
        if selection:
            self._open_url(self.boards[int(selection[0])]["careers_url"])

    def _application_page(self, parent):
        self.application_payload = None
        self.application_baseline = "null"
        self.application_field_id = None
        actions = ttk.Frame(parent)
        actions.pack(fill="x", pady=(4, 8))
        self._button(actions, "Read questions", self._discover_application, async_action=True).pack(side="left")
        self._button(actions, "Paste questions", self._manual_questions).pack(side="left", padx=5)
        self._button(actions, "Draft answers", self._draft_answers, primary=True, async_action=True).pack(side="left")
        delivery = ttk.Frame(parent)
        delivery.pack(fill="x", pady=(0, 6))
        self._button(delivery, "Attach file", self._attach_application_file).pack(side="left")
        self._button(delivery, "Review in browser", self._preview_delivery, async_action=True).pack(side="left", padx=5)
        self._button(delivery, "Browser setup", self._browser_setup).pack(side="right")
        footer = ttk.Frame(parent)
        footer.pack(side="bottom", fill="x", pady=8)
        self._button(footer, "Save version", self._save_application).pack(side="left")
        self._button(footer, "History", self._application_history).pack(side="left", padx=5)
        self._button(footer, "Details", self._application_details).pack(side="left")
        self._button(footer, "Export answers", self._export_application).pack(side="right")
        self.application_notice = tk.StringVar(value="Read the public form or paste its questions. Login and conditional fields may need the original page.")
        ttk.Label(parent, textvariable=self.application_notice, wraplength=750, style="Muted.TLabel").pack(fill="x", pady=6)
        from .desktop_layout import ScrollForm
        body = ScrollForm(parent)
        body.pack(fill="both", expand=True)
        parent = body.content
        table = ttk.Frame(parent)
        table.pack(fill="x")
        self.application_tree = ttk.Treeview(table, columns=("question", "status"), show="headings", selectmode="browse", height=3)
        self.application_tree.heading("question", text="QUESTION")
        self.application_tree.heading("status", text="REVIEW")
        self.application_tree.column("question", width=400)
        self.application_tree.column("status", width=120)
        scrollbar = ttk.Scrollbar(table, command=self.application_tree.yview)
        self.application_tree.configure(yscrollcommand=scrollbar.set)
        self.application_tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        self.application_tree.bind("<<TreeviewSelect>>", self._select_application_field)
        self.application_question = tk.StringVar()
        question_label = ttk.Label(parent, textvariable=self.application_question, wraplength=650)
        question_label.pack(fill="x", pady=8)
        parent.bind("<Configure>", lambda event: question_label.configure(wraplength=max(200, event.width - 12)), add=True)
        self.application_answer = self._editor(parent, height=7)
        self.application_answer.pack(fill="x")

    def _load_application(self, job_id):
        history = service.application_history(self.data_dir, job_id) if job_id is not None else []
        self._set_application(history[0]["payload"] if history else None)
        if job_id is not None:
            from .storage import JobStore
            with JobStore(self.data_dir / "vacancies.db") as store:
                attempts = store.deliveries(job_id)
            if attempts:
                self.application_notice.set("Last delivery attempt: " + attempts[0]["status"] + ". Check the employer confirmation; the application stage remains under your control.")

    def _set_application(self, payload):
        self.application_payload = deepcopy(payload)
        self.application_field_id = None
        self.application_baseline = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        self.application_tree.delete(*self.application_tree.get_children())
        _put(self.application_answer, "")
        self.application_question.set("")
        if not payload:
            self.application_notice.set("Read the public form or paste its questions to prepare answers.")
            return
        form = payload.get("form", {})
        answers = {answer["field_id"]: answer for answer in payload.get("answers", [])}
        for index, field in enumerate(form.get("fields", [])):
            answer = answers.get(field["id"], {})
            self.application_tree.insert("", "end", iid=str(index), values=(field["label"], answer.get("status", "needs_input")))
        self.application_notice.set(f"{len(form.get('fields', []))} questions • {form.get('coverage', 'partial')} coverage. Details shows source limitations and review notes.")

    def _application_details(self):
        if not self.application_payload:
            return
        form = self.application_payload["form"]
        dialog = self._dialog("Application source and review notes", 650, 420)
        editor = self._editor(dialog)
        editor.pack(fill="both", expand=True)
        lines = [form.get("source_url", ""), "Coverage: " + form.get("coverage", "partial"), *form.get("warnings", []), *self.application_payload.get("review_notes", [])]
        editor.insert("1.0", "\n\n".join(lines))
        editor.configure(state="disabled")

    def _capture_answer(self):
        if self.application_payload is None or self.application_field_id is None:
            return
        field = next(item for item in self.application_payload["form"]["fields"] if item["id"] == self.application_field_id)
        answer = next((item for item in self.application_payload["answers"] if item["field_id"] == field["id"]), None)
        content = _get(self.application_answer).strip()
        if content == (answer or {}).get("answer", ""):
            return
        if answer is None:
            answer = {"field_id": field["id"], "label": field["label"]}
            self.application_payload["answers"].append(answer)
        selected = []
        if field.get("options") and content:
            labels = [line.strip() for line in content.splitlines() if line.strip()]
            for label in labels:
                option = next((item for item in field["options"] if label in {item["label"], item["value"]}), None)
                if option is None:
                    raise ValueError("Choose only listed option labels, one per line.")
                selected.append(option["value"])
            if field["type"] == "single_select" and len(selected) != 1:
                raise ValueError("This question accepts exactly one option.")
        if field.get("max_length") and len(content) > field["max_length"]:
            raise ValueError(f"This answer allows at most {field['max_length']} characters.")
        answer.update(answer=content, selected_options=selected, status="user_edited" if content else "needs_input", used_evidence=[], review_notes=["Edited by you; review before sending."])

    def _application_dirty(self):
        if not hasattr(self, "application_payload"):
            return False
        try:
            self._capture_answer()
        except ValueError:
            return True
        return json.dumps(self.application_payload, sort_keys=True, ensure_ascii=False) != self.application_baseline

    def _select_application_field(self, _event=None):
        selection = self.application_tree.selection()
        if not selection or not self.application_payload:
            return
        try:
            self._capture_answer()
        except ValueError as exc:
            self._error(exc)
            return
        field = self.application_payload["form"]["fields"][int(selection[0])]
        self.application_field_id = field["id"]
        answer = next((item for item in self.application_payload["answers"] if item["field_id"] == field["id"]), {})
        options = "\nOptions (one label per line): " + "; ".join(item["label"] for item in field["options"]) if field.get("options") else ""
        notes = " ".join(answer.get("review_notes", []))
        self.application_question.set(field["label"] + (" (required)" if field["required"] else "") + options + "\n" + notes)
        _put(self.application_answer, answer.get("answer", ""))

    def _replace_application_guard(self):
        if self._application_dirty():
            return self._save_application(notify=False)
        return True

    def _discover_application(self):
        if not self._require_job() or not self._replace_application_guard():
            return
        job_id = self.selected_id
        baseline = json.dumps(self.application_payload, sort_keys=True, ensure_ascii=False)
        def ready(payload):
            service.save_application(self.data_dir, job_id, payload)
            if self.selected_id == job_id and self._application_matches(baseline):
                self._set_application(payload)
            else:
                self.status_message.set("Questions saved in application history; current edits were kept.")
        self._run("Reading application questions…", lambda: service.discover_application(self.data_dir, job_id), ready)

    def _manual_questions(self):
        if not self._require_job() or not self._replace_application_guard():
            return
        job_id = self.selected_id
        dialog = self._dialog("Paste application questions", 650, 450)
        ttk.Label(dialog, text="One question per line. Choices: Question | Yes | No\nThese questions are drafts; they do not identify a sendable form.").pack(fill="x", pady=8)
        editor = self._editor(dialog)
        editor.pack(fill="both", expand=True)
        def save():
            try:
                if not _get(editor):
                    raise ValueError("Paste at least one question.")
                payload = service.discover_application(self.data_dir, job_id, _get(editor))
                service.save_application(self.data_dir, job_id, payload)
                self._set_application(payload)
                dialog.destroy()
            except Exception as exc:
                self._error(exc)
        self._button(dialog, "Use questions", save, primary=True).pack(pady=8)

    def _draft_answers(self):
        if not self._require_job() or not self.application_payload or not self._replace_application_guard():
            return
        job_id = self.selected_id
        form = deepcopy(self.application_payload["form"])
        baseline = json.dumps(self.application_payload, sort_keys=True, ensure_ascii=False)
        def ready(payload):
            if self.selected_id == job_id and self._application_matches(baseline):
                self._set_application(payload)
                self.status_message.set("Draft answers saved. Review factual claims and fill any missing details.")
            else:
                self.status_message.set("Generated answers saved in History; your newer edits were kept.")
        self._run("Drafting application answers with your local model…", lambda: service.prepare_application(self.data_dir, job_id, form), ready)

    def _application_matches(self, baseline):
        try:
            self._capture_answer()
        except ValueError:
            return False
        return json.dumps(self.application_payload, sort_keys=True, ensure_ascii=False) == baseline

    def _save_application(self, notify=True):
        if not self._require_job() or not self.application_payload:
            return False
        try:
            self._capture_answer()
            service.save_application(self.data_dir, self.selected_id, self.application_payload)
            self.application_baseline = json.dumps(self.application_payload, sort_keys=True, ensure_ascii=False)
            if notify:
                self.status_message.set("Application answers saved as a private version.")
            return True
        except Exception as exc:
            self._error(exc)
            return False

    def _application_history(self):
        if not self._require_job():
            return
        records = service.application_history(self.data_dir, self.selected_id)
        dialog = self._dialog("Application answer history", 600, 400)
        choices = tk.Listbox(dialog)
        choices.pack(fill="both", expand=True)
        for entry in records:
            choices.insert("end", f"{entry['created_at']} — {len(entry['payload'].get('answers', []))} answers")
        def restore():
            if choices.curselection() and self._replace_application_guard():
                self._set_application(records[choices.curselection()[0]]["payload"])
                self.application_baseline = "restored version"
                dialog.destroy()
        self._button(dialog, "Restore for editing", restore).pack(pady=8)

    def _export_application(self):
        if not self.application_payload or not self._save_application(notify=False):
            return
        path = filedialog.asksaveasfilename(parent=self.root, title="Export application answers", defaultextension=".txt", filetypes=[("Text", "*.txt")])
        if not path:
            return
        from pathlib import Path
        lines = ["Application answers — review before sending", self.application_payload["form"].get("source_url", ""), ""]
        answers = {item["field_id"]: item for item in self.application_payload["answers"]}
        for field in self.application_payload["form"]["fields"]:
            lines.extend([field["label"], answers.get(field["id"], {}).get("answer") or "[NEEDS YOUR INPUT]", ""])
        try:
            Path(path).write_text("\n".join(lines), encoding="utf-8")
            self.status_message.set("Application answers exported.")
        except OSError as exc:
            self._error(exc)

    def _attach_application_file(self):
        if not self.application_payload or not self.application_field_id:
            self.status_message.set("Select an upload question first.")
            return
        field = next(item for item in self.application_payload["form"]["fields"] if item["id"] == self.application_field_id)
        if field["type"] != "file":
            self.status_message.set("Files can only be attached to an upload question.")
            return
        path = filedialog.askopenfilename(parent=self.root, title="Choose the file to include in this application", filetypes=[("Application documents", "*.pdf *.docx *.doc *.txt"), ("All files", "*.*")])
        if path:
            self.application_payload.setdefault("attachments", {})[field["id"]] = path
            self.application_notice.set("File selected for " + field["label"] + ". Save, then review the filename and content before sending.")

    def _browser_setup(self):
        dialog = self._dialog("Optional browser delivery", 650, 400)
        text = self._editor(dialog)
        text.pack(fill="both", expand=True)
        text.insert("1.0", "Browser delivery supports standard public HTML application forms. You review the filled values and selected files before pressing Send reviewed application.\n\nInstall the optional free browser runtime in the Python environment that runs this app:\n\npython -m pip install \"playwright>=1.49,<2\"\npython -m playwright install chromium\n\nPages requiring JavaScript, employer login, embedded forms or CAPTCHA need manual completion. Use the original page and copy your prepared answers. Reading questions and drafting work without this optional installation.")
        text.configure(state="disabled")

    def _preview_delivery(self):
        if not self._require_job() or not self.application_payload or not self._save_application(notify=False):
            return
        from .browser_delivery import BrowserApplicationSession
        job_id = self.selected_id
        payload = deepcopy(self.application_payload)
        # Optional blanks are also part of the reviewed snapshot.
        answers = {answer["field_id"]: answer for answer in payload["answers"]}
        complete = [answers.get(field["id"], {"field_id": field["id"], "answer": "", "selected_options": []}) for field in payload["form"]["fields"]]
        session = BrowserApplicationSession()
        def prepare():
            try:
                return session.prepare(payload["form"], complete, attachments=payload.get("attachments", {}))
            except Exception:
                session.close()
                raise
        def ready(preview):
            dialog = self._dialog("Review application before sending", 800, 650)
            ttk.Label(dialog, text="Destination: " + preview["destination"], wraplength=750, style="Section.TLabel").pack(fill="x", pady=8)
            editor = self._editor(dialog)
            editor.pack(fill="both", expand=True)
            lines = ["Review every value below. Files are included only when listed.", ""]
            fields = {field["id"]: field for field in payload["form"]["fields"]}
            for item in preview.get("fields", []):
                field = fields.get(item["field_id"], {})
                label = field.get("label", item["field_id"])
                options = {option["value"]: option["label"] for option in field.get("options", [])}
                value = "\n".join(options.get(option, option) for option in item.get("selected_options", [])) or item.get("answer") or "[blank]"
                lines.append(label + "\n" + value)
            for item in preview.get("files", []):
                lines.extend(["FILE", json.dumps(item, ensure_ascii=False, indent=2)])
            lines.extend(preview.get("limitations", []))
            if preview.get("technical_fields"):
                lines.append("Site anti-forgery fields are retained for submission: " + ", ".join(preview["technical_fields"]))
            editor.insert("1.0", "\n\n".join(lines))
            editor.configure(state="disabled")
            attempted = False
            def close():
                if self.busy:
                    return
                session.close()
                dialog.destroy()
            dialog.protocol("WM_DELETE_WINDOW", close)
            buttons = ttk.Frame(dialog)
            buttons.pack(fill="x", pady=10)
            self._button(buttons, "Cancel / edit answers", close).pack(side="left")
            def send():
                nonlocal attempted
                if attempted:
                    return
                attempted = True
                send_button.state(["disabled"])
                def sent(result):
                    editor.configure(state="normal")
                    editor.insert("end", "\n\n" + result["message"] + "\nClose this window after checking the employer page.")
                    editor.configure(state="disabled")
                    self.status_message.set(result["message"])
                    if self.selected_id == job_id:
                        self.application_notice.set("Delivery attempt: " + result["status"] + ". " + result["message"])
                    messagebox.showinfo("Application delivery", result["message"], parent=self.root)
                def deliver():
                    try:
                        return service.send_reviewed_application(self.data_dir, job_id, session, preview)
                    except Exception:
                        session.close()
                        raise
                self._run("Sending the reviewed application once…", deliver, sent)
            send_button = self._button(buttons, "Send reviewed application", send, primary=True)
            send_button.pack(side="right")
        self._run("Opening a browser preview of your reviewed answers…", prepare, ready)
