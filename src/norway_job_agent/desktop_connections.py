"""Company discovery, read-only email review and local setup screens."""

from __future__ import annotations

import json
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from . import runtime_setup, service
from .ui_components import SearchEntry


class ConnectionPages:
    @staticmethod
    def _with_local_ai(action):
        runtime_setup.start_ollama()
        return action()

    def _setup_controls(self, parent):
        actions = ttk.Frame(parent)
        actions.pack(fill="x", pady=(0, 14))
        self._button(actions, "Check setup", self._check_setup, async_action=True).pack(side="left")
        self._button(actions, "Start Ollama", self._start_ollama, async_action=True).pack(side="left", padx=8)
        self._button(actions, "Download model", self._download_model, primary=True, async_action=True).pack(side="left")
        self.pdf_status = tk.StringVar(value="PDF import is ready." if runtime_setup.pdf_available() else "PDF import needs a one-time setup.")
        ttk.Label(parent, textvariable=self.pdf_status, style="Muted.TLabel").pack(anchor="w", pady=(0, 8))
        self._button(parent, "Install PDF support", self._install_pdf, async_action=True).pack(anchor="w", pady=(0, 20))

    def _show_ai_setup(self):
        self._navigate("settings")
        self.settings_tabs.select(1)

    def _check_setup(self):
        model = self.model.get().strip()
        def ready(result):
            self.model_status.set(result["message"])
            self.model_combo.configure(values=result["models"])
            self.pdf_status.set("PDF import is ready." if result["pdf"] else "PDF import needs a one-time setup. Choose Install PDF support.")
            self.status_message.set("Setup check finished. See Local AI for details.")
        self._run("Checking PDF support and local AI…", lambda: runtime_setup.setup_status(model), ready)

    def _install_pdf(self):
        def ready(message):
            self.pdf_status.set(message)
            self.status_message.set(message)
        self._run("Installing free PDF support in this app's Python environment…", runtime_setup.install_pdf_support, ready)

    def _start_ollama(self):
        def ready(message):
            self.model_status.set(message)
            self.status_message.set(message)
        self._run("Starting your installed Ollama runtime…", runtime_setup.start_ollama, ready)

    def _download_model(self):
        model = self.model.get().strip()
        from .local_ai import validate_model_name
        try:
            validate_model_name(model)
        except ValueError as exc:
            self._error(exc)
            return
        def ready(models):
            self.model_combo.configure(values=models)
            self.model_status.set(f"Ready — {model} is installed locally.")
            self.status_message.set("Local AI is ready. Save your model choice, then create a draft or import your CV.")
        self._run(f"Downloading {model}… You can keep browsing while it downloads.",
                  lambda: runtime_setup.download_model(model, lambda message: self.events.put(("model_progress", message, None))), ready)

    def _company_library(self, parent):
        from .company_catalog import company_catalog
        self.catalog = company_catalog()
        actions = ttk.Frame(parent)
        actions.pack(fill="x", pady=(4, 12))
        self.company_query = tk.StringVar()
        search = SearchEntry(actions, textvariable=self.company_query)
        search.pack(side="left", fill="x", expand=True, padx=(0, 12))
        self._button(actions, "Add supported companies", self._add_supported_companies, primary=True).pack(side="right")
        ttk.Label(parent, text="Select employers to follow. Automatic sources collect public postings; career links open in your browser.",
                  style="Muted.TLabel", wraplength=880).pack(anchor="w", pady=(0, 12))
        footer = ttk.Frame(parent)
        footer.pack(side="bottom", fill="x", pady=(12, 0))
        table = ttk.Frame(parent)
        table.pack(fill="both", expand=True)
        self.company_tree = ttk.Treeview(table, columns=("name", "mode", "added"), show="headings", selectmode="extended")
        for key, label, width in (("name", "COMPANY", 270), ("mode", "COLLECTION", 270), ("added", "WATCHLIST", 130)):
            self.company_tree.heading(key, text=label, anchor="w")
            self.company_tree.column(key, width=width, minwidth=90, stretch=key != "added")
        scrollbar = ttk.Scrollbar(table, command=self.company_tree.yview)
        self.company_tree.configure(yscrollcommand=scrollbar.set)
        self.company_tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        self.company_info = tk.StringVar(value="Choose a company to see its source and collection details.")
        info = ttk.Label(footer, textvariable=self.company_info, style="Muted.TLabel", wraplength=850)
        info.pack(anchor="w", pady=(0, 8))
        footer.bind("<Configure>", lambda event: info.configure(wraplength=max(250, event.width - 10)))
        self.company_tree.bind("<<TreeviewSelect>>", self._show_company_info)
        self.company_query.trace_add("write", lambda *_a: self._render_catalog())
        bottom = ttk.Frame(footer)
        bottom.pack(fill="x")
        self._button(bottom, "Add selected", self._add_selected_companies).pack(side="left")
        self._button(bottom, "Open careers page  ↗", self._open_company).pack(side="left", padx=8)
        self._render_catalog()

    def _catalog_source_exists(self, source):
        if not source:
            return False
        keys = ("type", "board", "url", "tenant", "site", "host", "company")
        return any(all(str(item.get(key, "")) == str(source.get(key, "")) for key in keys) for item in self.sources)

    def _render_catalog(self):
        if not hasattr(self, "company_tree"):
            return
        self.company_tree.delete(*self.company_tree.get_children())
        query = self.company_query.get().strip().casefold()
        for i, entry in enumerate(self.catalog):
            if query and query not in (entry["name"] + " " + entry.get("note", "")).casefold():
                continue
            source = entry.get("source")
            self.company_tree.insert("", "end", iid=str(i), values=(entry["name"], "Automatic" if source else "Career link / email alerts",
                                    "Added" if self._catalog_source_exists(source) else ""))

    def _show_company_info(self, _event=None):
        selection = self.company_tree.selection()
        if selection:
            entry = self.catalog[int(selection[0])]
            self.company_info.set(entry.get("note", "") + "\n" + entry["careers_url"])

    def _add_catalog_entries(self, entries):
        added = 0
        for entry in entries:
            source = entry.get("source")
            if source and not self._catalog_source_exists(source):
                if len(self.sources) >= 100:
                    break
                self.sources.append(json.loads(json.dumps(source)))
                added += 1
        self._render_sources()
        self._render_catalog()
        self.status_message.set(f"Added {added} company sources to your form. Save settings or collect to use them.")

    def _add_supported_companies(self):
        self._add_catalog_entries(self.catalog)

    def _add_selected_companies(self):
        chosen = self.company_tree.selection()
        if not chosen:
            self.status_message.set("Select one or more companies first.")
            return
        self._add_catalog_entries([self.catalog[int(i)] for i in chosen])

    def _open_company(self):
        selection = self.company_tree.selection()
        if selection:
            self._open_url(self.catalog[int(selection[0])]["careers_url"])

    def _gmail_page(self):
        from . import gmail
        self.email_candidates = []
        self.email_page_token = None
        self.email_last_query = None
        self.gmail_cancel = None
        self._page_header(self.gmail_page, "Job alerts, together", "Turn job emails into opportunities you can review, save and prepare for.")
        controls = ttk.Frame(self.gmail_page)
        controls.pack(fill="x", pady=(0, 10))
        self._button(controls, "Connect Gmail", self._connect_gmail, primary=True, async_action=True).pack(side="left")
        self._button(controls, "Setup guide", self._gmail_guide).pack(side="left", padx=8)
        self._button(controls, "Import .eml", self._import_eml, async_action=True).pack(side="left")
        self._button(controls, "Cancel sign-in", self._cancel_gmail_connect).pack(side="left", padx=8)
        self._button(controls, "Disconnect", self._disconnect_gmail).pack(side="right")
        self.gmail_status = tk.StringVar(value="Read-only access. The app never sends, deletes or marks emails as read.")
        ttk.Label(self.gmail_page, textvariable=self.gmail_status, style="Muted.TLabel", wraplength=900).pack(anchor="w", pady=(0, 14))
        query_row = ttk.Frame(self.gmail_page)
        query_row.pack(fill="x", pady=(0, 10))
        ttk.Label(query_row, text="Gmail search", style="Section.TLabel").pack(anchor="w", pady=(0, 6))
        self.gmail_query = tk.StringVar(value=gmail.DEFAULT_QUERY)
        ttk.Entry(query_row, textvariable=self.gmail_query).pack(fill="x")
        self.gmail_query.trace_add("write", self._gmail_query_changed)
        actions = ttk.Frame(self.gmail_page)
        actions.pack(fill="x", pady=(0, 14))
        self._button(actions, "Find job alerts", self._fetch_gmail, async_action=True).pack(side="left")
        self.email_next_button = self._button(actions, "Next batch", lambda: self._fetch_gmail(next_page=True), async_action=True)
        self.email_next_button.pack(side="left", padx=8)
        self.email_count = tk.StringVar(value="Search up to 50 emails per batch. Relevant job links appear below for review.")
        ttk.Label(actions, textvariable=self.email_count, style="Muted.TLabel", wraplength=520).pack(side="left", padx=8)
        footer = ttk.Frame(self.gmail_page)
        footer.pack(side="bottom", fill="x", pady=(14, 0))
        self._button(footer, "Add selected to vacancies", self._import_selected_emails, primary=True).pack(side="right")
        self._button(footer, "Select all", lambda: self.email_tree.selection_set(self.email_tree.get_children())).pack(side="left")
        self._button(footer, "Open job link  ↗", self._open_email_job).pack(side="left", padx=8)
        self._button(footer, "Edit details", self._edit_email_candidate).pack(side="left")
        split = ttk.Panedwindow(self.gmail_page, orient="vertical")
        split.pack(fill="both", expand=True)
        table = ttk.Frame(split)
        split.add(table, weight=3)
        self.email_tree = ttk.Treeview(table, columns=("title", "company", "location"), show="headings", selectmode="extended", height=6)
        for key, label, width in (("title", "OPPORTUNITY", 390), ("company", "COMPANY", 210), ("location", "LOCATION", 180)):
            self.email_tree.heading(key, text=label, anchor="w")
            self.email_tree.column(key, width=width, minwidth=90, stretch=True)
        scrollbar = ttk.Scrollbar(table, command=self.email_tree.yview)
        self.email_tree.configure(yscrollcommand=scrollbar.set)
        self.email_tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        self.email_tree.bind("<<TreeviewSelect>>", self._preview_email_job)
        preview = ttk.Frame(split, padding=(0, 12, 0, 0))
        split.add(preview, weight=2)
        self.email_preview = self._editor(preview, height=6, readonly=True)
        self.email_preview.pack(fill="both", expand=True)
        self._set_email_preview("Bring LinkedIn and other job alerts into one place, then choose the opportunities you want to keep.\n\nConnect Gmail or import a saved .eml message to begin.")
        self._refresh_gmail_status()

    def _refresh_gmail_status(self):
        from . import gmail
        status = gmail.connection_status(self.data_dir)
        self.gmail_status.set(status.get("message") or status.get("detail") or ("Connected with read-only access." if status.get("connected") else
                              "Not connected. Use your own desktop OAuth credentials, or import .eml files without connecting."))

    def _connect_gmail(self):
        from . import gmail
        if self.busy:
            self.status_message.set("Wait for the current action before connecting Gmail.")
            return
        path = filedialog.askopenfilename(parent=self.root, title="Choose Google Desktop OAuth credentials", filetypes=[("Google credentials JSON", "*.json")])
        if not path:
            return
        self.gmail_cancel = threading.Event()
        cancel = self.gmail_cancel
        def connect():
            try:
                return gmail.connect(self.data_dir, Path(path), cancel_event=cancel)
            finally:
                self.gmail_cancel = None
        self._run("Your browser will open for Google sign-in. Complete it yourself; the app requests read-only email access.",
                  connect, lambda _result: self._refresh_gmail_status())

    def _cancel_gmail_connect(self):
        if self.gmail_cancel is not None:
            self.gmail_cancel.set()
            self.status_message.set("Cancelling Google sign-in…")

    def _disconnect_gmail(self):
        from . import gmail
        if self.busy:
            self.status_message.set("Wait for the current action before disconnecting.")
            return
        try:
            gmail.disconnect(self.data_dir)
            self.email_page_token = None
            self.email_last_query = None
            self._refresh_gmail_status()
            self.status_message.set("Local Gmail connection removed. Imported vacancies are kept.")
        except Exception as exc:
            self._error(exc)

    def _gmail_guide(self):
        dialog = self._dialog("Connect your Gmail account", 820, 720)
        frame = ttk.Frame(dialog, padding=20)
        frame.pack(fill="both", expand=True)
        guide = self._editor(frame, readonly=True)
        guide.pack(fill="both", expand=True)
        path = Path(__file__).resolve().parents[2] / "docs/gmail-setup.md"
        guide.insert("1.0", path.read_text(encoding="utf-8") if path.exists() else "Create a Google Cloud desktop OAuth client with the Gmail API enabled. Download its credentials JSON, then choose Connect Gmail. No Gmail password is stored by the app.")
        guide.configure(state="disabled")
        self._button(frame, "Open Google Cloud Console  ↗", lambda: self._open_url("https://console.cloud.google.com/apis/dashboard")).pack(anchor="w", pady=(12, 0))

    def _fetch_gmail(self, next_page=False):
        from . import gmail
        query = self.gmail_query.get().strip()
        if next_page and (not self.email_page_token or query != self.email_last_query):
            self.status_message.set("Start a search first. Next batch is available only when more messages remain.")
            return
        token = (self.email_page_token or "") if next_page else ""
        profile = json.loads(json.dumps(self.profile))
        def ready(result):
            current_query = self.gmail_query.get().strip()
            self.email_page_token = result.get("next_page_token") if current_query == query else None
            self.email_last_query = query
            self._show_email_results(result, append=next_page)
            if current_query != query:
                self.status_message.set("Showing the completed search. Your query changed while it ran; choose Find job alerts to search the new query.")
        self._run("Reading job alerts with read-only Gmail access…", lambda: gmail.fetch_candidates(self.data_dir, profile, query=query, page_token=token, max_messages=50), ready)

    def _gmail_query_changed(self, *_args):
        if self.gmail_query.get().strip() != self.email_last_query:
            self.email_page_token = None

    def _import_eml(self):
        from . import gmail
        paths = filedialog.askopenfilenames(parent=self.root, title="Import saved job-alert messages", filetypes=[("Saved email", "*.eml")])
        if not paths:
            return
        profile = json.loads(json.dumps(self.profile))
        def work():
            candidates = []
            for path in paths[:50]:
                candidates.extend(gmail.import_eml(Path(path), profile))
            return {"candidates": candidates, "messages_scanned": min(50, len(paths))}
        self._run("Reading saved job-alert messages…", work, lambda result: self._show_email_results(result, append=True))

    def _show_email_results(self, result, *, append=False):
        previous = self.email_candidates if append else []
        selected_urls = {self.email_candidates[int(index)]["source_url"] for index in self.email_tree.selection()} if append else set()
        candidates = {item["source_url"]: item for item in previous}
        for item in result.get("candidates", []):
            candidates.setdefault(item["source_url"], item)
        self.email_candidates = list(candidates.values())
        self.email_tree.delete(*self.email_tree.get_children())
        for i, item in enumerate(self.email_candidates):
            self.email_tree.insert("", "end", iid=str(i), values=(item["title"], item.get("company", ""), item.get("location", "")))
        more = " More emails available." if self.email_page_token else ""
        self.email_count.set(f"{len(self.email_candidates)} opportunities to review.{more}")
        self.status_message.set(f"Read {result.get('messages_scanned', 0)} messages. Select opportunities to add; no emails were changed.")
        warnings = result.get("warnings", [])
        if warnings:
            self.status_message.set(f"Read {result.get('messages_scanned', 0)} messages with {len(warnings)} warnings. " + " ".join(str(value) for value in warnings[:3]))
        if self.email_candidates:
            selected = [str(index) for index, item in enumerate(self.email_candidates) if item["source_url"] in selected_urls]
            self.email_tree.selection_set(selected or ["0"])
            self._preview_email_job()
        else:
            self._set_email_preview("No relevant job links found in this batch. Try another Gmail search, read the next batch, or review your profile's target and related roles.")

    def _set_email_preview(self, text):
        self.email_preview.configure(state="normal")
        self.email_preview.delete("1.0", "end")
        self.email_preview.insert("1.0", text)
        self.email_preview.configure(state="disabled")

    def _preview_email_job(self, _event=None):
        selected = self.email_tree.selection()
        if selected:
            item = self.email_candidates[int(selected[0])]
            raw = item.get("raw_json", {})
            email = raw.get("email", {}) if isinstance(raw, dict) else {}
            origin = "\n".join(str(email.get(key) or "") for key in ("subject", "sender", "received_at"))
            self._set_email_preview(f"{item['title']}\n{item.get('company', '')} · {item.get('location', '')}\n\n{item.get('description', '')}\n\n{item['source_url']}\n\nEmail source\n{origin}")

    def _open_email_job(self):
        selected = self.email_tree.selection()
        if selected:
            self._open_url(self.email_candidates[int(selected[0])]["source_url"])

    def _edit_email_candidate(self):
        selected = self.email_tree.selection()
        if not selected:
            self.status_message.set("Choose an email opportunity to review its details.")
            return
        index = int(selected[0])
        item = self.email_candidates[index]
        dialog = self._dialog("Review opportunity details", 760, 680)
        frame = ttk.Frame(dialog, padding=20)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="Fill gaps from the original vacancy before importing.", style="Muted.TLabel").pack(anchor="w", pady=(0, 12))
        fields = {}
        for key, label in (("title", "Job title"), ("company", "Company"), ("location", "Location")):
            ttk.Label(frame, text=label, style="Section.TLabel").pack(anchor="w", pady=(8, 4))
            fields[key] = tk.StringVar(value=item.get(key, ""))
            ttk.Entry(frame, textvariable=fields[key]).pack(fill="x")
        ttk.Label(frame, text="Description / excerpt", style="Section.TLabel").pack(anchor="w", pady=(12, 6))
        description = self._editor(frame, height=8)
        description.pack(fill="both", expand=True)
        description.insert("1.0", item.get("description", ""))
        def save():
            values = {key: value.get().strip() for key, value in fields.items()}
            if not values["title"] or not values["company"]:
                messagebox.showinfo("Missing details", "Keep a title and company, or the explicit Not stated in email label until you know the employer.", parent=dialog)
                return
            item.update(values, description=description.get("1.0", "end-1c").strip())
            self.email_tree.item(str(index), values=(item["title"], item["company"], item["location"]))
            self._preview_email_job()
            dialog.destroy()
        self._button(frame, "Keep reviewed details", save, primary=True).pack(anchor="e", pady=(12, 0))

    def _import_selected_emails(self):
        chosen = self.email_tree.selection()
        if not chosen:
            self.status_message.set("Select the opportunities you want to add first.")
            return
        try:
            result = service.import_email_candidates(self.data_dir, [self.email_candidates[int(i)] for i in chosen])
            self._refresh_jobs(preserve_editor=True)
            self.status_message.set(f"Added {result['created']} vacancies; matched {result['existing']} existing records. Email provenance retained.")
        except Exception as exc:
            self._error(exc)
