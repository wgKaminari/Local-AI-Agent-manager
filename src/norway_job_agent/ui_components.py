"""Small dependency-free widgets for the application's quiet, local workspace.

The opportunity list keeps the selection API used by ttk.Treeview so selection
and unsaved-draft handling remain the responsibility of the desktop controller.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk


PAPER = "#ffffff"
INK = "#202123"
MUTED = "#6b6b6b"
BORDER = "#e6e6e6"
HOVER = "#ececec"


def _background(widget: tk.Misc) -> str:
    try:
        return str(widget.cget("background"))
    except tk.TclError:
        try:
            style = widget.cget("style") or widget.winfo_class()
            return ttk.Style(widget).lookup(style, "background") or PAPER
        except tk.TclError:
            return PAPER


def _rounded(canvas: tk.Canvas, x1: float, y1: float, x2: float, y2: float,
             radius: float = 12, **kwargs) -> int:
    radius = min(radius, (x2 - x1) / 2, (y2 - y1) / 2)
    return canvas.create_polygon(
        x1 + radius, y1, x2 - radius, y1, x2, y1, x2, y1 + radius,
        x2, y2 - radius, x2, y2, x2 - radius, y2, x1 + radius, y2,
        x1, y2, x1, y2 - radius, x1, y1 + radius, x1, y1,
        smooth=True, splinesteps=24, **kwargs,
    )


class RoundedButton(tk.Canvas):
    """A compact rounded action with mouse, focus and keyboard feedback."""

    def __init__(self, parent, text: str, command=None, *, primary: bool = False,
                 subtle: bool = False, **kwargs):
        self._label = text
        self._command = command
        self._primary = primary
        self._subtle = subtle
        self._disabled = kwargs.pop("state", "normal") == "disabled"
        self._hovered = False
        self._pressed = False
        self._focused = False
        self._font = tkfont.Font(parent, font=kwargs.pop("font", ("Segoe UI", 10)))
        self._auto_width = "width" not in kwargs
        kwargs.setdefault("width", self._font.measure(text) + 32)
        kwargs.setdefault("height", 38)
        kwargs.setdefault("background", _background(parent))
        kwargs.setdefault("highlightthickness", 0)
        kwargs.setdefault("borderwidth", 0)
        kwargs.setdefault("takefocus", not self._disabled)
        kwargs.setdefault("cursor", "hand2" if not self._disabled else "")
        super().__init__(parent, **kwargs)
        self.bind("<Configure>", self._draw)
        self.bind("<Enter>", self._enter)
        self.bind("<Leave>", self._leave)
        self.bind("<ButtonPress-1>", self._press)
        self.bind("<ButtonRelease-1>", self._release)
        self.bind("<FocusIn>", self._focus_in)
        self.bind("<FocusOut>", self._focus_out)
        self.bind("<space>", self._keyboard_invoke)
        self.bind("<Return>", self._keyboard_invoke)
        self._draw()

    def _enter(self, _event):
        self._hovered = True
        self._draw()

    def _leave(self, _event):
        self._hovered = False
        self._draw()

    def _press(self, _event):
        if not self._disabled:
            self.focus_set()
            self._pressed = True
            self._draw()

    def _release(self, event):
        invoke = self._pressed and 0 <= event.x < self.winfo_width() and 0 <= event.y < self.winfo_height()
        self._pressed = False
        self._draw()
        if invoke:
            self.invoke()

    def _focus_in(self, _event):
        self._focused = True
        self._draw()

    def _focus_out(self, _event):
        self._focused = False
        self._pressed = False
        self._draw()

    def _keyboard_invoke(self, _event):
        self.invoke()
        return "break"

    def invoke(self):
        if not self._disabled and self._command is not None:
            return self._command()
        return None

    def state(self, statespec=None):
        """Support the ttk state calls used by asynchronous actions."""
        if statespec is None:
            return tuple(name for name, enabled in (
                ("disabled", self._disabled), ("focus", self._focused), ("active", self._hovered)
            ) if enabled)
        previous = self._disabled
        for state in statespec:
            if state == "disabled":
                self._disabled = True
            elif state == "!disabled":
                self._disabled = False
        if self._disabled:
            self._pressed = False
        super().configure(takefocus=not self._disabled, cursor="" if self._disabled else "hand2")
        self._draw()
        return ("!disabled" if self._disabled else "disabled",) if previous != self._disabled else ()

    def configure(self, cnf=None, **kwargs):
        if isinstance(cnf, str):
            if cnf in {"text", "state", "command"}:
                return (cnf, cnf, cnf.title(), "", self.cget(cnf))
            return super().configure(cnf, **kwargs)
        options = dict(cnf or {})
        options.update(kwargs)
        if not options:
            return super().configure()
        if "text" in options:
            self._label = str(options.pop("text"))
            if self._auto_width and "width" not in options:
                options["width"] = self._font.measure(self._label) + 32
        if "command" in options:
            self._command = options.pop("command")
        if "primary" in options:
            self._primary = bool(options.pop("primary"))
        if "subtle" in options:
            self._subtle = bool(options.pop("subtle"))
        if "font" in options:
            self._font = tkfont.Font(self, font=options.pop("font"))
            if self._auto_width and "width" not in options:
                options["width"] = self._font.measure(self._label) + 32
        if "state" in options:
            self.state(["disabled" if options.pop("state") == "disabled" else "!disabled"])
        result = super().configure(**options) if options else None
        self._draw()
        return result

    config = configure

    def cget(self, key):
        if key == "text":
            return self._label
        if key == "state":
            return "disabled" if self._disabled else "normal"
        if key == "command":
            return self._command
        return super().cget(key)

    __getitem__ = cget

    def _draw(self, _event=None):
        if not self.winfo_exists():
            return
        self.delete("all")
        width = max(self.winfo_width(), int(float(super().cget("width")))) if self.winfo_width() <= 1 else self.winfo_width()
        height = max(self.winfo_height(), int(float(super().cget("height")))) if self.winfo_height() <= 1 else self.winfo_height()
        if self._disabled:
            fill, foreground, outline = "#f1f1f1", "#999999", "#f1f1f1"
        elif self._primary:
            fill = "#414141" if self._hovered or self._pressed else "#212121"
            foreground, outline = PAPER, fill
        else:
            fill = HOVER if self._hovered or self._pressed else (_background(self.master) if self._subtle else PAPER)
            foreground = INK
            outline = fill if self._subtle else BORDER
        _rounded(self, 2, 2, width - 2, height - 2, 13, fill=fill, outline=outline, width=1)
        if self._focused and not self._disabled:
            _rounded(self, 1, 1, width - 1, height - 1, 14, fill="", outline="#777777", width=1)
        self.create_text(width / 2, height / 2 - 1, text=self._label, font=self._font, fill=foreground)


class _TabButton(RoundedButton):
    def __init__(self, parent, text, command):
        self.selected = False
        super().__init__(parent, text, command, subtle=True, height=38)

    def _draw(self, _event=None):
        if not self.winfo_exists():
            return
        self.delete("all")
        width = self.winfo_width() if self.winfo_width() > 1 else int(float(super().cget("width")))
        height = self.winfo_height() if self.winfo_height() > 1 else 38
        fill = "#eeeeee" if self.selected else ("#f6f6f6" if self._hovered else _background(self.master))
        outline = "#929292" if self._focused else fill
        _rounded(self, 1, 1, width - 1, height - 1, 12, fill=fill, outline=outline, width=1)
        self.create_text(width / 2, height / 2 - 1, text=self._label,
                         font=self._font, fill=INK if self.selected else MUTED)


class TabDeck(ttk.Frame):
    """Flat segmented navigation with ttk's existing page-management API.

    Pages can be created with this frame as their parent. Each segment remains
    a keyboard-focusable action; left/right arrows switch between the segments.
    """

    def __init__(self, parent, **kwargs):
        super().__init__(parent, **kwargs)
        self._buttons: dict[str, _TabButton] = {}
        style = ttk.Style(self)
        style.layout("FlatDeck.TNotebook.Tab", [])
        style.configure("FlatDeck.TNotebook", borderwidth=0, tabmargins=0,
                        background=PAPER, bordercolor=PAPER, lightcolor=PAPER, darkcolor=PAPER)
        self._strip = ttk.Frame(self)
        self._strip.pack(fill="x", pady=(0, 12))
        self._notebook = ttk.Notebook(self, style="FlatDeck.TNotebook", takefocus=False)
        self._notebook.pack(fill="both", expand=True)
        self._notebook.bind("<<NotebookTabChanged>>", self._changed)

    def add(self, child, **kwargs):
        label = str(kwargs.pop("text", ""))
        self._notebook.add(child, text=label, **kwargs)
        name = str(child)
        if name not in self._buttons:
            button = _TabButton(self._strip, label, lambda page=child: self.select(page))
            button.pack(side="left", padx=(0, 4))
            button.bind("<Left>", lambda _event, page=name: self._step(page, -1))
            button.bind("<Right>", lambda _event, page=name: self._step(page, 1))
            self._buttons[name] = button
        else:
            self._buttons[name].configure(text=label)
        self._sync()

    def select(self, tab=None):
        if tab is None:
            return self._notebook.select()
        result = self._notebook.select(tab)
        self._sync()
        return result

    def index(self, tab):
        return self._notebook.index(tab)

    def tabs(self):
        return self._notebook.tabs()

    def _sync(self):
        selected = self._notebook.select()
        for name, button in self._buttons.items():
            button.selected = name == selected
            button._draw()

    def _changed(self, _event):
        self._sync()
        self.event_generate("<<NotebookTabChanged>>", when="now")

    def _step(self, page, direction):
        pages = list(self._notebook.tabs())
        if pages:
            next_page = pages[(pages.index(page) + direction) % len(pages)]
            self.select(next_page)
            self._buttons[next_page].focus_set()
        return "break"


class SearchEntry(tk.Canvas):
    """A rounded search field that delegates editing and key events to Entry."""

    def __init__(self, parent, textvariable=None, **kwargs):
        entry_font = kwargs.pop("font", ("Segoe UI", 10))
        kwargs.setdefault("background", _background(parent))
        kwargs.setdefault("height", 42)
        kwargs.setdefault("width", 280)
        kwargs.setdefault("highlightthickness", 0)
        kwargs.setdefault("borderwidth", 0)
        kwargs.setdefault("takefocus", False)
        super().__init__(parent, **kwargs)
        self._entry = tk.Entry(self, textvariable=textvariable, font=entry_font,
                               background=PAPER, foreground=INK, insertbackground=INK,
                               relief="flat", borderwidth=0, highlightthickness=0,
                               selectbackground="#d9e7f5", selectforeground=INK)
        self._entry.place(x=15, y=10, relwidth=1, width=-30, relheight=1, height=-20)
        super().bind("<Configure>", self._draw)
        super().bind("<Button-1>", lambda _event: self._entry.focus_set())
        self._entry.bind("<FocusIn>", self._draw, add="+")
        self._entry.bind("<FocusOut>", self._draw, add="+")
        self._draw()

    def bind(self, sequence=None, func=None, add=None):
        return self._entry.bind(sequence, func, add)

    def focus_set(self):
        self._entry.focus_set()

    def selection_range(self, start, end):
        return self._entry.selection_range(start, end)

    def get(self):
        return self._entry.get()

    def insert(self, index, string):
        return self._entry.insert(index, string)

    def delete(self, first, last=None):
        return self._entry.delete(first, last)

    def icursor(self, index):
        return self._entry.icursor(index)

    def _draw(self, _event=None):
        if not self.winfo_exists():
            return
        super().delete("search-border")
        width = self.winfo_width() if self.winfo_width() > 1 else int(float(self.cget("width")))
        height = self.winfo_height() if self.winfo_height() > 1 else int(float(self.cget("height")))
        outline = "#929292" if self._entry.focus_get() == self._entry else BORDER
        _rounded(self, 1, 1, width - 1, height - 1, 15, fill=PAPER, outline=outline,
                 width=1, tags="search-border")


class OpportunityList(tk.Frame):
    """A scrolling, keyboard-friendly list of complete opportunity summaries."""

    def __init__(self, parent, **kwargs):
        kwargs.setdefault("background", PAPER)
        super().__init__(parent, **kwargs)
        self._items: dict[str, tuple] = {}
        self._order: list[str] = []
        self._selected: str | None = None
        self._hovered: str | None = None
        self._bounds: dict[str, tuple[float, float]] = {}
        self._backgrounds: dict[str, int] = {}
        self._empty = ("No opportunities yet", "Collect from your sources or add a vacancy to get started.")
        self._pending = None
        self._pending_see: str | None = None
        self._canvas = tk.Canvas(self, background=self.cget("background"), highlightthickness=0,
                                 borderwidth=0, takefocus=True, width=330)
        self._scrollbar = ttk.Scrollbar(self, orient="vertical", command=self._canvas.yview)
        self._canvas.configure(yscrollcommand=self._scrollbar.set)
        self._scrollbar.pack(side="right", fill="y")
        self._canvas.pack(side="left", fill="both", expand=True)
        self._canvas.bind("<Configure>", self._schedule_draw)
        self._canvas.bind("<Map>", self._schedule_draw)
        self._canvas.bind("<Button-1>", self._click)
        self._canvas.bind("<Motion>", self._motion)
        self._canvas.bind("<Leave>", self._leave)
        self._canvas.bind("<MouseWheel>", self._wheel)
        self._canvas.bind("<Button-4>", lambda _event: self._scroll(-3))
        self._canvas.bind("<Button-5>", lambda _event: self._scroll(3))
        self._canvas.bind("<Up>", lambda _event: self._move(-1))
        self._canvas.bind("<Down>", lambda _event: self._move(1))
        self._canvas.bind("<Home>", lambda _event: self._edge(first=True))
        self._canvas.bind("<End>", lambda _event: self._edge(first=False))
        self._canvas.bind("<Prior>", lambda _event: self._move(-4))
        self._canvas.bind("<Next>", lambda _event: self._move(4))
        self._canvas.bind("<FocusIn>", self._focus_changed)
        self._canvas.bind("<FocusOut>", self._focus_changed)
        self._schedule_draw()

    def get_children(self, item=None):
        return tuple(self._order) if not item else ()

    def exists(self, iid):
        return str(iid) in self._items

    def insert(self, parent, index, iid=None, values=(), **_kwargs):
        item_id = str(iid) if iid is not None else str(len(self._items) + 1)
        if item_id in self._items:
            raise tk.TclError(f"Item {item_id} already exists")
        self._items[item_id] = tuple(values)
        if index == "end":
            self._order.append(item_id)
        else:
            self._order.insert(int(index), item_id)
        self._schedule_draw()
        return item_id

    def delete(self, *items):
        for iid in items:
            item_id = str(iid)
            self._items.pop(item_id, None)
            if item_id in self._order:
                self._order.remove(item_id)
            if self._selected == item_id:
                self._selected = None
            if self._pending_see == item_id:
                self._pending_see = None
        self._schedule_draw()

    def selection(self):
        return (self._selected,) if self._selected is not None else ()

    def selection_set(self, items):
        if isinstance(items, (list, tuple)):
            item_id = str(items[0]) if items else None
        else:
            item_id = str(items)
        if item_id is not None and item_id not in self._items:
            raise tk.TclError(f"Item {item_id} not found")
        if self._selected == item_id:
            return
        previous = self._selected
        self._selected = item_id
        self._paint_card(previous)
        self._paint_card(item_id)
        self.event_generate("<<TreeviewSelect>>", when="now")

    def selection_remove(self, *items):
        if self._selected in {str(item) for item in items}:
            self.selection_set(())

    def show_empty(self, title: str, body: str):
        self._empty = (title, body)
        self._schedule_draw()

    def focus_set(self):
        self._canvas.focus_set()

    def see(self, iid):
        item_id = str(iid)
        if item_id not in self._items:
            return
        # The controller selects a card during construction, before Tk has
        # allocated the viewport. Scrolling against that 1px placeholder would
        # incorrectly move the first selected card above the visible area.
        if not self._canvas.winfo_ismapped() or self._canvas.winfo_height() <= 1:
            self._pending_see = item_id
            return
        self._pending_see = None
        if self._pending is not None:
            self.after_cancel(self._pending)
            self._pending = None
            self._draw()
        bounds = self._bounds.get(item_id)
        if not bounds:
            return
        top, bottom = bounds
        visible_top = self._canvas.canvasy(0)
        viewport = self._canvas.winfo_height()
        total = max(float(self._canvas.cget("scrollregion").split()[-1]), 1)
        if top < visible_top:
            self._canvas.yview_moveto(max(0, top - 6) / total)
        elif bottom > visible_top + viewport:
            self._canvas.yview_moveto(max(0, bottom + 6 - viewport) / total)

    def yview(self, *args):
        return self._canvas.yview(*args)

    def _scroll(self, amount):
        self._canvas.yview_scroll(amount, "units")
        return "break"

    def _wheel(self, event):
        if event.delta:
            return self._scroll(-max(1, abs(event.delta) // 120) * (1 if event.delta > 0 else -1) * 3)
        return "break"

    def _move(self, delta):
        if not self._order:
            return "break"
        current = self._order.index(self._selected) if self._selected in self._order else (-1 if delta > 0 else len(self._order))
        index = min(len(self._order) - 1, max(0, current + delta))
        self.selection_set(self._order[index])
        if self._selected is not None:
            self.see(self._selected)
        return "break"

    def _edge(self, *, first):
        if self._order:
            iid = self._order[0 if first else -1]
            self.selection_set(iid)
            if self._selected is not None:
                self.see(self._selected)
        return "break"

    def _item_at(self, event):
        y = self._canvas.canvasy(event.y)
        for iid, (top, bottom) in self._bounds.items():
            if top <= y <= bottom:
                return iid
        return None

    def _click(self, event):
        self._canvas.focus_set()
        iid = self._item_at(event)
        if iid is not None:
            self.selection_set(iid)

    def _motion(self, event):
        hovered = self._item_at(event)
        if hovered != self._hovered:
            old = self._hovered
            self._hovered = hovered
            self._paint_card(old)
            self._paint_card(hovered)
            self._canvas.configure(cursor="hand2" if hovered else "")

    def _leave(self, _event):
        old = self._hovered
        self._hovered = None
        self._paint_card(old)

    def _focus_changed(self, _event):
        self._paint_card(self._selected)

    def _paint_card(self, iid):
        if iid not in self._backgrounds:
            return
        selected = iid == self._selected
        fill = "#eeeeee" if selected else ("#f7f7f7" if iid == self._hovered else PAPER)
        outline = "#bdbdbd" if selected and self._canvas.focus_get() == self._canvas else fill
        self._canvas.itemconfigure(self._backgrounds[iid], fill=fill, outline=outline)

    def _schedule_draw(self, _event=None):
        if self._pending is None:
            self._pending = self.after_idle(self._draw)

    def _draw(self):
        self._pending = None
        if not self.winfo_exists():
            return
        self._canvas.delete("all")
        self._bounds.clear()
        self._backgrounds.clear()
        width = max(160, self._canvas.winfo_width())
        text_width = width - 42
        y = 7
        if not self._order:
            y = max(52, self._canvas.winfo_height() * 0.22)
            item = self._canvas.create_text(width / 2, y, text=self._empty[0], anchor="n",
                                           width=text_width, justify="center", fill=INK,
                                           font=("Segoe UI", 13, "bold"))
            bottom = self._canvas.bbox(item)[3]
            item = self._canvas.create_text(width / 2, bottom + 12, text=self._empty[1], anchor="n",
                                           width=text_width, justify="center", fill=MUTED,
                                           font=("Segoe UI", 10))
            self._canvas.configure(scrollregion=(0, 0, width, self._canvas.bbox(item)[3] + 30))
            return
        for iid in self._order:
            values = self._items[iid] + ("",) * 6
            title, company, location, score, track, status = (str(value) if value is not None else "" for value in values[:6])
            top = y
            heading = self._canvas.create_text(21, y + 15, anchor="nw", width=text_width,
                                               text=title or "Untitled opportunity", fill=INK,
                                               font=("Segoe UI", 11, "bold"))
            y = self._canvas.bbox(heading)[3] + 7
            company_line = company or "Company not specified"
            item = self._canvas.create_text(21, y, anchor="nw", width=text_width,
                                           text=company_line, fill="#4e4e4e", font=("Segoe UI", 10))
            y = self._canvas.bbox(item)[3] + 3
            if location:
                item = self._canvas.create_text(21, y, anchor="nw", width=text_width,
                                               text=location, fill=MUTED, font=("Segoe UI", 9))
                y = self._canvas.bbox(item)[3] + 8
            else:
                y += 5
            match = f"{score} keyword match" if score and score != "—" else "Match not available"
            info = "  ·  ".join(part for part in (match, track) if part)
            item = self._canvas.create_text(21, y, anchor="nw", width=text_width,
                                           text=info, fill=MUTED, font=("Segoe UI", 9))
            y = self._canvas.bbox(item)[3] + 8
            if status and status.lower() != "new":
                item = self._canvas.create_text(21, y, anchor="nw", width=text_width,
                                               text=status.replace("_", " ").capitalize(),
                                               fill="#37765c", font=("Segoe UI", 9, "bold"))
                y = self._canvas.bbox(item)[3] + 7
            bottom = y + 9
            self._bounds[iid] = (top, bottom)
            self._backgrounds[iid] = _rounded(self._canvas, 5, top, width - 5, bottom,
                                              13, fill=PAPER, outline=PAPER, width=1)
            self._canvas.tag_lower(self._backgrounds[iid])
            self._paint_card(iid)
            y = bottom + 7
        self._canvas.configure(scrollregion=(0, 0, width, y), yscrollincrement=18)
        # Consume only an explicit deferred request. Ordinary resizing/redrawing
        # keeps the reader's scroll position instead of snapping to selection.
        if self._pending_see is not None and self._canvas.winfo_ismapped() and self._canvas.winfo_height() > 1:
            pending = self._pending_see
            self._pending_see = None
            self.see(pending)

    def destroy(self):
        if self._pending is not None:
            self.after_cancel(self._pending)
            self._pending = None
        super().destroy()
