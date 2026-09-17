"""Presentation layer for the local job workspace. No network or storage access."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from tkinter import font as tkfont

from .ui_components import OpportunityList, RoundedButton, SearchEntry, TabDeck, ThemedScrolledText as ScrolledText
from .text_interactions import SelectableLabel

INK = "#202123"
MUTED = "#6b6b6b"
BORDER = "#e6e6e6"
WHITE = "#ffffff"
SIDEBAR = "#f9f9f9"


class ScrollForm(ttk.Frame):
    """Scroll a form under the pointer, without stealing another pane's wheel."""

    def __init__(self, parent):
        super().__init__(parent)
        self.canvas = tk.Canvas(self, highlightthickness=0, background=WHITE)
        scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.content = ttk.Frame(self.canvas, padding=(2, 2, 20, 20))
        window = self.canvas.create_window((0, 0), window=self.content, anchor="nw")
        self.content.bind("<Configure>", lambda _e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(window, width=e.width))
        self.canvas.configure(yscrollcommand=scrollbar.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        # A private bindtag is attached before widget classes, including Text.
        self._wheel_tag = f"ScrollForm{id(self)}"
        self.bind_class(self._wheel_tag, "<MouseWheel>", self._wheel)
        self.bind_class(self._wheel_tag, "<Button-4>", self._wheel)
        self.bind_class(self._wheel_tag, "<Button-5>", self._wheel)
        self.content.bind("<Map>", lambda _e: self._attach_wheel(self.content), add=True)
        self.canvas.bindtags((self._wheel_tag, *self.canvas.bindtags()))
        self.bind("<Destroy>", self._destroy_binding, add=True)

    def _attach_wheel(self, widget):
        if self._wheel_tag not in widget.bindtags():
            widget.bindtags((self._wheel_tag, *widget.bindtags()))
        for child in widget.winfo_children():
            self._attach_wheel(child)

    def _wheel(self, event):
        direction = -1 if getattr(event, "num", None) == 4 else 1
        if getattr(event, "delta", 0):
            direction = -1 if event.delta > 0 else 1
        widget = event.widget
        if isinstance(widget, tk.Text):
            first, last = widget.yview()
            if (direction < 0 and first > 0) or (direction > 0 and last < 1):
                widget.yview_scroll(direction * 3, "units")
                return "break"
        self.canvas.yview_scroll(direction * 3, "units")
        return "break"

    def _destroy_binding(self, event):
        if event.widget is self:
            for sequence in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
                self.unbind_class(self._wheel_tag, sequence)


class DesktopLayout:
    """Layout mixin; commands are supplied by Desktop's existing controller."""

    def _style(self):
        self.root.title("Norway Job Agent")
        width = min(1440, self.root.winfo_screenwidth() - 60)
        height = min(940, self.root.winfo_screenheight() - 90)
        self.root.geometry(f"{width}x{height}")
        self.root.minsize(min(1120, width), min(720, height))
        self.root.configure(background=WHITE)
        for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont"):
            tkfont.nametofont(name, root=self.root).configure(family="Segoe UI", size=10)
        from .theme import apply_theme
        apply_theme(self.root, self.theme_name)

    def _button(self, parent, label, command, *, primary=False, async_action=False):
        button = RoundedButton(parent, text=label, command=command, primary=primary)
        if async_action:
            self.async_buttons.append(button)
        return button

    def _editor(self, parent, *, height=10, readonly=False):
        text = ScrolledText(parent, wrap="word", height=height, width=20, relief="flat", borderwidth=0,
                            padx=18, pady=16, undo=not readonly, background=WHITE, foreground=INK,
                            insertbackground=INK, selectbackground="#dcece6", selectforeground=INK,
                            font=("Segoe UI", 11), spacing1=3, spacing3=7, highlightthickness=1,
                            highlightbackground=BORDER, highlightcolor="#aaaaaa")
        return text

    def _page_header(self, parent, title, subtitle):
        heading = ttk.Frame(parent)
        heading.pack(fill="x", pady=(0, 22))
        SelectableLabel(heading, text=title, style="Title.TLabel").pack(fill="x")
        label = SelectableLabel(heading, text=subtitle, style="Muted.TLabel", wraplength=850)
        label.pack(fill="x", pady=(6, 0))
        heading.bind("<Configure>", lambda e: label.configure(wraplength=max(250, e.width - 10)))
        return heading

    def _build(self):
        self.view_filter = "all"
        self.search_after = None
        self.nav_buttons = {}
        self.job_buttons = []
        self.sidebar = tk.Frame(self.root, background=SIDEBAR, width=220, padx=14, pady=22)
        self.sidebar.pack(side="left", fill="y")
        self.sidebar.pack_propagate(False)
        brand = tk.Frame(self.sidebar, background=SIDEBAR)
        brand.pack(fill="x", padx=8, pady=(2, 26))
        tk.Label(brand, text="n.", font=("Segoe UI", 26, "bold"), bg=SIDEBAR, fg=INK).pack(side="left")
        tk.Label(brand, text="Norway\nJob workspace", justify="left", font=("Segoe UI", 10), bg=SIDEBAR, fg=INK).pack(side="left", padx=10)
        self._button(self.sidebar, "+  Add opportunity", self._manual_vacancy, primary=True).pack(fill="x", pady=(0, 26))
        tk.Label(self.sidebar, text="WORKSPACE", font=("Segoe UI", 8, "bold"), bg=SIDEBAR, fg=MUTED,
                 anchor="w", padx=10).pack(fill="x", pady=(0, 9))
        for key, icon, label in (("all", "◈", "Discover"), ("foryou", "✧", "For you"),
                                 ("saved", "♡", "Saved"), ("progress", "≡", "Applications")):
            self._nav_button(key, f"{icon}   {label}", lambda k=key: self._navigate(k))
        tk.Frame(self.sidebar, bg=BORDER, height=1).pack(fill="x", padx=9, pady=21)
        self._nav_button("profile", "☷   Profile & CV", lambda: self._navigate("profile"))
        self._nav_button("gmail", "✉   Gmail alerts", lambda: self._navigate("gmail"))
        self._nav_button("settings", "⚙   Sources & AI", lambda: self._navigate("settings"))
        account = tk.Frame(self.sidebar, bg=SIDEBAR)
        account.pack(side="bottom", fill="x", padx=8)
        first_name = str(self.profile.get("name") or "Your workspace").split()[0]
        tk.Label(account, text=first_name, font=("Segoe UI", 10, "bold"), bg=SIDEBAR, fg=INK, anchor="w").pack(fill="x")
        tk.Label(account, text="Personal workspace", bg=SIDEBAR, fg=MUTED, font=("Segoe UI", 9), anchor="w").pack(fill="x", pady=(3, 16))
        tk.Label(account, text="Ctrl K  Search\nCtrl B  Hide sidebar", justify="left", bg=SIDEBAR,
                 fg=MUTED, font=("Segoe UI", 8)).pack(anchor="w")
        main = self.main = ttk.Frame(self.root)
        main.pack(side="left", fill="both", expand=True)
        footer = ttk.Frame(main, padding=(26, 8))
        footer.pack(side="bottom", fill="x")
        self.progress = ttk.Progressbar(footer, mode="indeterminate", length=50)
        status = SelectableLabel(footer, textvariable=self.status_message, style="Muted.TLabel", font=("Segoe UI", 9))
        status.pack(side="left", fill="x", expand=True)
        footer.bind("<Configure>", lambda e: status.configure(wraplength=max(250, e.width - 120)))
        self._appearance_toolbar(main)
        self.notebook = ttk.Notebook(main, style="Shell.TNotebook")
        self.notebook.pack(fill="both", expand=True)
        self.vacancies_page = ttk.Frame(self.notebook, padding=(26, 20, 26, 0))
        self.profile_page = ttk.Frame(self.notebook, padding=(32, 20, 32, 0))
        self.settings_page = ttk.Frame(self.notebook, padding=(32, 20, 32, 0))
        self.gmail_page = ttk.Frame(self.notebook, padding=(32, 20, 32, 0))
        self.notebook.add(self.vacancies_page, text="Opportunities")
        self.notebook.add(self.profile_page, text="Profile & CV")
        self.notebook.add(self.settings_page, text="Sources & AI")
        self.notebook.add(self.gmail_page, text="Gmail alerts")
        self._vacancies_page()
        self._profile_page()
        self._settings_page()
        self._gmail_page()
        self.notebook.bind("<<NotebookTabChanged>>", lambda _e: self._update_navigation())
        self.root.bind("<Control-k>", self._focus_search)
        self.root.bind("<Control-s>", self._save_current)
        self.root.bind("<Control-1>", lambda _e: self._navigate("all"))
        self.root.bind("<Control-2>", lambda _e: self._navigate("profile"))
        self.root.bind("<Control-3>", lambda _e: self._navigate("settings"))
        self.root.bind("<Control-4>", lambda _e: self._navigate("gmail"))
        self._update_navigation()

    def _nav_button(self, key, text, command):
        button = RoundedButton(self.sidebar, text=text, command=command, subtle=True,
                               anchor="w", height=43, font=("Segoe UI", 10))
        button.pack(fill="x", pady=3)
        self.nav_buttons[key] = button

    def _navigate(self, view):
        if view in {"profile", "settings", "gmail"}:
            self.notebook.select({"profile": self.profile_page, "settings": self.settings_page, "gmail": self.gmail_page}[view])
        else:
            self.view_filter = view
            self.query.set("")
            self.track_filter.set("All vacancies")
            self.status_filter.set("All statuses")
            self.notebook.select(self.vacancies_page)
            titles = {"all": ("Discover", "Find the right next step for your skills and ambitions."),
                      "foryou": ("Picked for your profile", "Target roles and related directions worth exploring."),
                      "saved": ("Your shortlist", "The opportunities you want to come back to."),
                      "progress": ("Applications", "Keep track of preparation, applications and interviews.")}
            title, subtitle = titles[view]
            self.page_title.set(title)
            self.page_subtitle.set(subtitle)
            self._refresh_jobs()
        self._update_navigation()

    def _update_navigation(self):
        selected = self.notebook.select()
        key = "profile" if selected == str(self.profile_page) else "settings" if selected == str(self.settings_page) else "gmail" if selected == str(self.gmail_page) else self.view_filter
        for name, button in self.nav_buttons.items():
            button.configure(selected=name == key,
                             font=("Segoe UI", 10, "bold" if name == key else "normal"))
        labels = {"all": "Discover", "foryou": "For you", "saved": "Saved", "progress": "Applications", "profile": "Profile", "settings": "Sources & AI", "gmail": "Gmail alerts"}
        if hasattr(self, "workspace_location"):
            self.workspace_location.set("Workspace / " + labels.get(key, "Discover"))

    def _vacancies_page(self):
        header = ttk.Frame(self.vacancies_page)
        header.pack(fill="x", pady=(0, 20))
        self._button(header, "Find opportunities", self._collect, primary=True, async_action=True).pack(side="right", padx=(14, 0))
        titles = ttk.Frame(header)
        titles.pack(side="left", fill="x", expand=True)
        self.page_title = tk.StringVar(value="Discover")
        self.page_subtitle = tk.StringVar(value="Find the right next step for your skills and ambitions.")
        SelectableLabel(titles, textvariable=self.page_title, font=("Segoe UI", 26, "bold")).pack(fill="x")
        subtitle = SelectableLabel(titles, textvariable=self.page_subtitle, style="Muted.TLabel")
        subtitle.pack(fill="x", pady=(4, 0))
        titles.bind("<Configure>", lambda e: subtitle.configure(wraplength=max(220, e.width)))
        split = self.opportunity_split = tk.PanedWindow(self.vacancies_page, orient="horizontal", bg=WHITE, sashwidth=12,
                              sashrelief="flat", borderwidth=0, opaqueresize=True)
        split.pack(fill="both", expand=True)
        browse = ttk.Frame(split)
        split.add(browse, width=330, minsize=280, stretch="never")
        search_box = ttk.Frame(browse)
        search_box.pack(fill="x", pady=(0, 10))
        ttk.Label(search_box, text="Search opportunities", style="Eyebrow.TLabel").pack(anchor="w", pady=(0, 6))
        self.query = tk.StringVar()
        self.search_entry = SearchEntry(search_box, textvariable=self.query, placeholder="Search title, company or skill…")
        self.search_entry.pack(fill="x")
        self.search_entry.bind("<Return>", lambda _e: self._search_now())
        self.search_entry.bind("<Escape>", lambda _e: self._clear_search())
        self.query.trace_add("write", lambda *_a: self._schedule_search())
        filters = ttk.Frame(browse)
        filters.pack(fill="x", pady=(0, 12))
        self.track_filter = tk.StringVar(value="All vacancies")
        track = ttk.Combobox(filters, textvariable=self.track_filter,
                             values=("All vacancies", "Target roles", "Related suggestions", "Other roles"), state="readonly", width=16)
        track.pack(side="left", fill="x", expand=True, padx=(0, 6))
        track.bind("<<ComboboxSelected>>", lambda _e: self._refresh_jobs())
        self.status_filter = tk.StringVar(value="All statuses")
        status = ttk.Combobox(filters, textvariable=self.status_filter,
                             values=("All statuses", "new", "saved", "preparing", "ready", "applied", "interview", "rejected", "archived"),
                             state="readonly", width=11)
        status.pack(side="left")
        status.bind("<<ComboboxSelected>>", lambda _e: self._refresh_jobs())
        count_row = ttk.Frame(browse)
        count_row.pack(fill="x", pady=(0, 8))
        self.count_text = tk.StringVar()
        ttk.Label(count_row, textvariable=self.count_text, style="Muted.TLabel", font=("Segoe UI", 9)).pack(side="left")
        reset = tk.Button(count_row, text="Reset", command=self._reset_filters, bg=WHITE, fg=MUTED,
                          relief="flat", borderwidth=0, cursor="hand2", font=("Segoe UI", 9), padx=4)
        reset.pack(side="right")
        self.tree = OpportunityList(browse)
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", self._select_job)
        ttk.Label(browse, text="Scores reflect keyword overlap with your profile.", style="Muted.TLabel", font=("Segoe UI", 8),
                  wraplength=300).pack(anchor="w", pady=(10, 0))
        self.detail_frame = ttk.Frame(split, padding=(18, 0, 0, 0))
        split.add(self.detail_frame, minsize=480, stretch="always")
        self.detail_empty = ttk.Frame(self.detail_frame)
        ttk.Label(self.detail_empty, text="A little closer to your next role", font=("Segoe UI", 20, "bold"),
                  wraplength=450, justify="center").pack(pady=(130, 12))
        ttk.Label(self.detail_empty, text="Find opportunities from your sources, or add a vacancy\nyou already have in mind.",
                  style="Muted.TLabel", justify="center").pack(pady=(0, 24))
        self._button(self.detail_empty, "Find opportunities", self._collect, primary=True, async_action=True).pack()
        self.detail_content = ttk.Frame(self.detail_frame)
        self.detail_content.pack(fill="both", expand=True)
        self.filtered_notice = tk.Label(self.detail_content, text="Open opportunity is outside the current filters.",
                                         bg="#f7f3e9", fg="#796437", anchor="w", padx=10, pady=8,
                                         font=("Segoe UI", 9))
        self.detail_heading = ttk.Frame(self.detail_content)
        self.detail_heading.pack(fill="x", pady=(0, 16))
        self.job_heading = tk.StringVar(value="Choose an opportunity")
        heading = self.job_title_label = SelectableLabel(self.detail_heading, textvariable=self.job_heading, font=("Segoe UI", 20, "bold"), wraplength=580)
        heading.pack(anchor="w", fill="x")
        self.job_meta = tk.StringVar()
        meta = self.job_meta_label = SelectableLabel(self.detail_heading, textvariable=self.job_meta, style="Muted.TLabel", wraplength=580)
        meta.pack(fill="x", pady=(7, 0))
        self.detail_heading.bind("<Configure>", lambda e: (heading.configure(wraplength=max(200, e.width)), meta.configure(wraplength=max(200, e.width))))
        action_row = ttk.Frame(self.detail_content)
        action_row.pack(fill="x", pady=(0, 16))
        for text, command in (("View original  ↗", lambda: self._open_job_link("source_url")),
                               ("Apply manually  ↗", lambda: self._open_job_link("apply_url")),
                               ("Copy details", self._copy_opportunity)):
            button = self._button(action_row, text, command)
            button.pack(side="left", padx=(0, 8))
            self.job_buttons.append(button)
        self.details_tabs = TabDeck(self.detail_content)
        self.details_tabs.pack(fill="both", expand=True)
        details = ttk.Frame(self.details_tabs)
        workflow = ttk.Frame(self.details_tabs)
        letter = ttk.Frame(self.details_tabs)
        self.details_tabs.add(details, text="Overview")
        self.details_tabs.add(workflow, text="Notes & status")
        self.details_tabs.add(letter, text="Cover letter")
        self.description = self._editor(details, readonly=True)
        self.description.pack(fill="both", expand=True)
        for tag, options in {"section": {"font": ("Segoe UI", 11, "bold"), "spacing1": 18, "spacing3": 6},
                             "muted": {"foreground": MUTED, "font": ("Segoe UI", 10)},
                             "warning": {"foreground": "#89602a"},
                             "match": {"foreground": "#28735e", "font": ("Segoe UI", 10, "bold")}}.items():
            self.description.tag_configure(tag, **options)
        workflow_top = ttk.Frame(workflow)
        workflow_top.pack(fill="x", pady=(6, 14))
        ttk.Label(workflow_top, text="Stage", style="Section.TLabel").pack(side="left", padx=(0, 10))
        self.workflow_status = tk.StringVar(value="new")
        ttk.Combobox(workflow_top, textvariable=self.workflow_status, values=("new", "saved", "preparing", "ready", "applied", "interview", "rejected", "archived"),
                     state="readonly", width=12).pack(side="left")
        self._button(workflow_top, "Save changes", self._save_workflow, primary=True).pack(side="right")
        ttk.Label(workflow, text="Your next step, questions and interview notes", style="Muted.TLabel").pack(anchor="w", pady=(0, 10))
        self.notes = self._editor(workflow)
        self.notes.pack(fill="both", expand=True)
        self.notes_state = tk.StringVar(value="Saved on this device")
        ttk.Label(workflow, textvariable=self.notes_state, style="Muted.TLabel", font=("Segoe UI", 9)).pack(anchor="w", pady=(8, 0))
        letter_actions = ttk.Frame(letter)
        letter_actions.pack(fill="x", pady=(4, 10))
        self._button(letter_actions, "✧  Draft with AI", self._generate_letter, primary=True, async_action=True).pack(side="left")
        self._button(letter_actions, "Save version", self._save_letter).pack(side="left", padx=6)
        self._button(letter_actions, "History", self._history).pack(side="right")
        bottom = ttk.Frame(letter)
        bottom.pack(side="bottom", fill="x", pady=(10, 0))
        self._button(bottom, "Export .txt", self._export_letter).pack(side="right")
        self.letter_state = tk.StringVar(value="Start a draft when you're ready")
        ttk.Label(bottom, textvariable=self.letter_state, style="Muted.TLabel", font=("Segoe UI", 9)).pack(side="left")
        self.review_text = tk.StringVar()
        self.review_panel = tk.Text(letter, height=2, wrap="word", bg=SIDEBAR, fg=MUTED, font=("Segoe UI", 9),
                                    relief="flat", padx=10, pady=8, borderwidth=0, cursor="arrow")
        self.review_panel.pack(side="bottom", fill="x", pady=(10, 0))
        self.review_text.trace_add("write", lambda *_a: self._update_review())
        ttk.Label(letter, text="Use your saved profile to start. Review and make it your own.", style="Muted.TLabel").pack(anchor="w", pady=(0, 10))
        self.letter_text = self._editor(letter)
        self.letter_text.pack(fill="both", expand=True)
        self.letter_placeholder = tk.Label(self.letter_text, text="Write your letter here…\n\nOr start a first draft with local AI.",
                                            font=("Segoe UI", 12), fg="#888888", bg=WHITE, justify="center")
        self.letter_placeholder.bind("<Button-1>", lambda _e: self.letter_text.focus_set())
        ttk.Label(self.detail_content, text="Prepared here. Submitted by you.", style="Muted.TLabel", font=("Segoe UI", 8)).pack(pady=(12, 0))

    def _profile_page(self):
        from .desktop import FIELD_LABELS
        self._page_header(self.profile_page, "Your profile", "The experience behind your next opportunity. You decide which facts to use.")
        actions = ttk.Frame(self.profile_page)
        actions.pack(side="bottom", fill="x", pady=(16, 0))
        self._button(actions, "Save profile", self._save_profile, primary=True).pack(side="right")
        self._button(actions, "Revert to saved", self._reload_profile).pack(side="right", padx=8)
        self.profile_state = tk.StringVar(value="Saved on this device")
        ttk.Label(actions, textvariable=self.profile_state, style="Muted.TLabel").pack(side="left")
        tabs = TabDeck(self.profile_page)
        tabs.pack(fill="both", expand=True)
        self.profile_tabs = tabs
        about = ScrollForm(tabs)
        search = ScrollForm(tabs)
        cv = ttk.Frame(tabs)
        tabs.add(about, text="About you")
        tabs.add(search, text="Search preferences")
        tabs.add(cv, text="Your CV")
        groups = [(about.content, (("name", 0, ""), ("summary", 3, "A brief introduction in your own words."),
                                   ("skills", 3, "Tools and skills you actually use. Separate with commas or new lines."),
                                   ("evidence", 5, "One factual project or achievement per line."),
                                   ("work_authorization", 2, "Your current situation in Norway."))),
                  (search.content, (("target_roles", 3, "The roles you want to focus on."),
                                    ("related_roles", 3, "Adjacent roles to include in For you."),
                                    ("preferred_locations", 2, "Leave empty to consider all locations."),
                                    ("excluded_keywords", 2, "Flag these terms when reviewing opportunities."),
                                    ("cover_letter_language", 0, "The language used for new cover letters.")))]
        for content, fields in groups:
            for key, height, hint in fields:
                self._profile_field(content, key, FIELD_LABELS[key], hint, height)
        for key, label, hint in (("english", "English level", "Your actual level, used in application materials."),
                                  ("norwegian", "Norwegian level", "Your actual level, used in application materials."),
                                  ("other_languages", "Other languages", "Separate with commas: Ukrainian: native, Russian: native")):
            self._profile_field(about.content, key, label, hint, 0)
        self._profile_field(search.content, "search_norwegian", "Norwegian requirements to consider",
                            "A search preference only. Your actual language level above is used in letters.", 0)
        cv_actions = ttk.Frame(cv)
        cv_actions.pack(fill="x", pady=(4, 14))
        self._button(cv_actions, "Import CV", self._import_cv, async_action=True).pack(side="left")
        self._button(cv_actions, "✧  Suggest profile", self._suggest_profile, primary=True, async_action=True).pack(side="left", padx=8)
        ttk.Label(cv, text="Paste your CV or import a text, Word or PDF file. You review every suggestion before using it.",
                  style="Muted.TLabel", wraplength=850).pack(anchor="w", pady=(0, 12))
        self.cv_text = self._editor(cv)
        self.cv_text.pack(fill="both", expand=True)
        ttk.Label(cv, text="Suggestions use your local AI model. PDF support can be installed in Sources & AI → Local AI.",
                  style="Muted.TLabel", font=("Segoe UI", 9)).pack(anchor="w", pady=(10, 0))

    def _profile_field(self, parent, key, label, hint, height):
        frame = ttk.Frame(parent, padding=(2, 10, 2, 14))
        frame.pack(fill="x")
        ttk.Label(frame, text=label, style="Section.TLabel").pack(anchor="w", pady=(0, 4))
        if hint:
            hint_label = ttk.Label(frame, text=hint, style="Muted.TLabel", wraplength=800)
            hint_label.pack(anchor="w", pady=(0, 9))
            frame.bind("<Configure>", lambda e: hint_label.configure(wraplength=max(250, e.width - 10)))
        if height:
            field = tk.Text(frame, height=height, width=35, wrap="word", padx=12, pady=10,
                            relief="flat", borderwidth=0, highlightthickness=1, highlightbackground=BORDER,
                            highlightcolor="#999999", undo=True, font=("Segoe UI", 11), bg=WHITE,
                            fg=INK, selectbackground="#dcece6", insertbackground=INK)
            field.pack(fill="x")
            self.fields[key] = field
        else:
            variable = tk.StringVar()
            ttk.Entry(frame, textvariable=variable).pack(fill="x")
            self.fields[key] = variable

    def _settings_page(self):
        self._page_header(self.settings_page, "Sources & AI", "Choose where opportunities come from and the model that helps you prepare.")
        actions = ttk.Frame(self.settings_page)
        actions.pack(side="bottom", fill="x", pady=(16, 0))
        self._button(actions, "Save settings", self._save_settings, primary=True).pack(side="right")
        self.settings_state = tk.StringVar(value="Saved on this device")
        ttk.Label(actions, textvariable=self.settings_state, style="Muted.TLabel").pack(side="left")
        tabs = TabDeck(self.settings_page)
        self.settings_tabs = tabs
        tabs.pack(fill="both", expand=True)
        sources = ttk.Frame(tabs)
        ai_form = ScrollForm(tabs)
        report = ttk.Frame(tabs)
        library = ttk.Frame(tabs)
        tabs.add(sources, text="Job sources")
        tabs.add(ai_form, text="Local AI")
        tabs.add(report, text="Collection activity")
        tabs.add(library, text="Company library")
        self._company_library(library)
        source_actions = ttk.Frame(sources)
        source_actions.pack(fill="x", pady=(4, 16))
        self._button(source_actions, "+  Add source", self._add_source).pack(side="left")
        self._button(source_actions, "Remove selected", self._remove_source).pack(side="left", padx=8)
        self._button(source_actions, "Collect now", self._collect, primary=True, async_action=True).pack(side="right")
        ttk.Label(sources, text="Connect NAV, company job boards or individual vacancy pages.", style="Muted.TLabel").pack(anchor="w", pady=(0, 16))
        table = ttk.Frame(sources)
        table.pack(fill="both", expand=True)
        self.source_tree = ttk.Treeview(table, columns=("type", "target", "region"), show="headings", selectmode="browse")
        for key, label, width in (("type", "SOURCE", 135), ("target", "BOARD OR VACANCY PAGE", 440), ("region", "REGION", 100)):
            self.source_tree.heading(key, text=label, anchor="w")
            self.source_tree.column(key, width=width, minwidth=70, stretch=key == "target")
        scrollbar = ttk.Scrollbar(table, command=self.source_tree.yview)
        self.source_tree.configure(yscrollcommand=scrollbar.set)
        self.source_tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        ttk.Label(sources, text="NAV starts with 90 days of updates, collected in batches. Progress is shown in Collection activity.",
                  style="Muted.TLabel", wraplength=850).pack(anchor="w", pady=(16, 0))
        ttk.Label(report, text="Latest collection", style="Section.TLabel").pack(anchor="w", pady=(4, 12))
        self.collection_report = self._editor(report, readonly=True)
        self.collection_report.pack(fill="both", expand=True)
        self.collection_report.insert("1.0", "Your source activity will appear here after the first collection.")
        self.collection_report.configure(state="disabled")
        ai = ai_form.content
        ttk.Label(ai, text="Your AI, on your computer", font=("Segoe UI", 17, "bold")).pack(anchor="w", pady=(12, 8))
        ttk.Label(ai, text="Use a local Ollama model for CV suggestions and cover letter drafts. No paid API is required.",
                  style="Muted.TLabel", wraplength=800).pack(anchor="w", pady=(0, 28))
        ttk.Label(ai, text="Model", style="Section.TLabel").pack(anchor="w", pady=(0, 8))
        model_row = ttk.Frame(ai)
        model_row.pack(fill="x")
        self.model = tk.StringVar(value=self.settings.get("model", "qwen3:4b"))
        self.model_combo = ttk.Combobox(model_row, textvariable=self.model, width=28)
        self.model_combo.pack(side="left")
        self._button(model_row, "Check models", self._check_models, async_action=True).pack(side="left", padx=10)
        self.model_status = tk.StringVar(value="Check models to see whether your local AI is ready.")
        ttk.Label(ai, textvariable=self.model_status, style="Muted.TLabel", wraplength=800).pack(anchor="w", pady=(12, 28))
        self._setup_controls(ai)
        ttk.Separator(ai).pack(fill="x", pady=(0, 24))
        ttk.Label(ai, text="First-time setup", style="Section.TLabel").pack(anchor="w", pady=(0, 12))
        ttk.Label(ai, text="1. Install Ollama, then choose Start Ollama above.\n2. Choose Download model (qwen3:4b is about 2.5 GB).\n3. Check setup and save your model choice.",
                  foreground=MUTED, justify="left").pack(anchor="w")
        self._button(ai, "Get Ollama  ↗", lambda: self._open_url("https://ollama.com/download")).pack(anchor="w", pady=(16, 24))
        ttk.Label(ai, text="Model downloads need internet access and disk space. You can collect, track and write manually before setup.",
                  style="Muted.TLabel", wraplength=800).pack(anchor="w")
        ttk.Label(ai, text=f"Data folder\n{self.data_dir}", style="Muted.TLabel", wraplength=800).pack(anchor="w", pady=(28, 0))

    def _schedule_search(self):
        if self.search_after is not None:
            self.root.after_cancel(self.search_after)
        self.search_after = self.root.after(250, self._search_now)

    def _search_now(self):
        if self.search_after is not None:
            self.root.after_cancel(self.search_after)
            self.search_after = None
        self._refresh_jobs()
        return "break"

    def _clear_search(self):
        self.query.set("")
        return "break"

    def _reset_filters(self):
        self.query.set("")
        self.track_filter.set("All vacancies")
        self.status_filter.set("All statuses")
        self._search_now()

    def _focus_search(self, _event=None):
        if self.root.grab_current() is not None:
            return
        self.notebook.select(self.vacancies_page)
        self.search_entry.focus_set()
        self.search_entry.selection_range(0, "end")
        return "break"

    def _save_current(self, _event=None):
        if self.root.grab_current() is not None:
            return
        page = self.notebook.select()
        if page == str(self.profile_page):
            self._save_profile()
        elif page == str(self.settings_page):
            self._save_settings()
        elif page == str(self.gmail_page):
            self.status_message.set("Select reviewed email opportunities and choose Add selected to vacancies.")
        elif self.selected_id is not None:
            if self.details_tabs.index("current") == 2:
                self._save_letter()
            else:
                self._save_workflow()
        return "break"

    def _update_review(self):
        self.review_panel.configure(state="normal")
        self.review_panel.delete("1.0", "end")
        self.review_panel.insert("1.0", self.review_text.get())
        self.review_panel.configure(state="disabled")

    def _sync_ui_state(self):
        """Small UI indicators; called on the main thread, never from workers."""
        if self.closed:
            return
        letter = self.letter_text.get("1.0", "end-1c").strip()
        if not letter and self.root.focus_get() is not self.letter_text:
            self.letter_placeholder.place(relx=0.5, rely=0.42, anchor="center")
        else:
            self.letter_placeholder.place_forget()
        words = len(letter.split())
        changed = letter != self.letter_baseline.strip()
        self.letter_state.set(f"{words} words · {'Unsaved changes' if changed else 'Saved version' if letter else 'No draft yet'}")
        notes_changed = (self.notes.get("1.0", "end-1c").strip() != self.notes_baseline or
                         self.workflow_status.get() != self.status_baseline)
        self.notes_state.set("Unsaved changes · Ctrl S to save" if notes_changed else "Saved on this device")
        try:
            profile_changed = self._read_form() != self.profile
        except ValueError:
            profile_changed = True
        self.profile_state.set("Unsaved changes" if profile_changed else "Saved on this device")
        self.settings_state.set("Unsaved changes" if self._current_settings() != self.settings else "Saved on this device")
        self.root.after(500, self._sync_ui_state)

    def _sync_selection_ui(self):
        if self.selected_id is None:
            self.detail_content.pack_forget()
            self.detail_empty.pack(fill="both", expand=True)
        else:
            self.detail_empty.pack_forget()
            self.detail_content.pack(fill="both", expand=True)
        if self.selected_id is not None and self.selected_id not in self.jobs:
            self.filtered_notice.pack(fill="x", before=self.detail_heading, pady=(0, 12))
        else:
            self.filtered_notice.pack_forget()
        if not self.jobs:
            if self.query.get() or self.track_filter.get() != "All vacancies" or self.status_filter.get() != "All statuses":
                self.tree.show_empty("No matches here", "Try a different search or reset your filters.")
            elif self.view_filter == "saved":
                self.tree.show_empty("Make room for the possibilities", "Set an opportunity's stage to saved in Notes & status to keep it here.")
            elif self.view_filter == "progress":
                self.tree.show_empty("Your next chapter starts here", "Move an opportunity to preparing, ready, applied or interview to track it here.")
            elif self.view_filter == "foryou":
                self.tree.show_empty("No profile matches yet", "Review your target and related roles in Profile & CV, or collect more opportunities.")
            else:
                self.tree.show_empty("A fresh start", "Find opportunities from your sources or add one using the sidebar.")
