"""Selectable display text and ordinary desktop copy menus.

Call ``install_text_actions(container)`` after building a page or dialog. The
installer is idempotent and adds bindings without replacing application ones.
``SelectableLabel`` installs its own actions and otherwise behaves like a small
read-only Text: selection works with the mouse, Ctrl+A and Ctrl+C.
"""

from __future__ import annotations

import math
import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk

from .theme import palette_for


def _parent_background(parent: tk.Misc) -> str:
    try:
        return str(parent.cget("background"))
    except tk.TclError:
        style = str(parent.cget("style") or parent.winfo_class())
        return ttk.Style(parent).lookup(style, "background") or "#ffffff"


class SelectableLabel(tk.Text):
    """A borderless, automatically sized, selectable replacement for a label.

    ``wraplength`` controls requested width; a geometry manager may expand or
    shrink the actual width. Height follows the actual wrapped text. Label text
    is never editable, including through paste, cut or the middle mouse button.
    """

    def __init__(self, parent, text="", textvariable=None, style="", wraplength=0, **kwargs):
        self._selectable_label = True
        self._style = style
        self._theme_role = "muted" if "muted" in style.lower() else "body"
        self._text = str(text)
        self._variable = None
        self._variable_trace = None
        self._resize_job = None
        self._wraplength = max(0, int(wraplength or 0))
        self._anchor = kwargs.pop("anchor", "w")
        self._justify = kwargs.pop("justify", "left")
        self._auto_width = "width" not in kwargs
        label_style = ttk.Style(parent)
        kwargs.setdefault("font", label_style.lookup(style or "TLabel", "font") or "TkDefaultFont")
        kwargs.setdefault("foreground", label_style.lookup(style or "TLabel", "foreground") or "#202123")
        kwargs.setdefault("background", _parent_background(parent))
        palette = palette_for(parent)
        kwargs.setdefault("selectbackground", palette["selection"])
        kwargs.setdefault("selectforeground", palette["text"])
        padding = kwargs.pop("padding", None)
        if padding is not None:
            values = (padding,) if isinstance(padding, (int, float)) else tuple(padding)
            if values:
                kwargs.setdefault("padx", values[0])
                kwargs.setdefault("pady", values[1] if len(values) > 1 else values[0])
        kwargs.setdefault("padx", 0)
        kwargs.setdefault("pady", 0)
        kwargs.setdefault("width", 1)
        kwargs.setdefault("height", 1)
        kwargs.setdefault("borderwidth", 0)
        kwargs.setdefault("highlightthickness", 0)
        kwargs.setdefault("relief", "flat")
        kwargs.setdefault("takefocus", False)
        kwargs.setdefault("cursor", "xterm")
        kwargs.setdefault("exportselection", False)
        kwargs["wrap"] = "word"
        kwargs["undo"] = False
        kwargs["state"] = "disabled"
        super().__init__(parent, **kwargs)
        if "inactiveselectbackground" in self.keys():
            super().configure(inactiveselectbackground=palette["selection"])
        self.tag_configure("alignment", justify=self._justify)
        self.bind("<Configure>", self._schedule_resize, add=True)
        self.bind("<Destroy>", self._on_destroy, add=True)
        self._set_variable(textvariable)
        self._replace_text(self._text)
        install_text_actions(self)

    def _set_variable(self, variable):
        if self._variable_trace is not None:
            name, command = self._variable_trace
            try:
                self.tk.call("trace", "remove", "variable", name, "write", command)
            except tk.TclError:
                pass
            self.deletecommand(command)
            self._variable_trace = None
        self._variable = variable
        if variable is not None and str(variable):
            name = str(variable)
            command = self.register(self._variable_changed)
            self.tk.call("trace", "add", "variable", name, "write", command)
            self._variable_trace = (name, command)
            self._text = str(self.getvar(name))

    def _variable_changed(self, *_args):
        self._replace_text(str(self.getvar(str(self._variable))))

    def _replace_text(self, value):
        self._text = value
        super().configure(state="normal")
        self.delete("1.0", "end")
        self.insert("1.0", value, "alignment")
        super().configure(state="disabled")
        self._request_width()
        self._schedule_resize()

    def _request_width(self):
        if not self._auto_width:
            return
        font = tkfont.Font(self, font=super().cget("font"))
        pixels = max((font.measure(line) for line in self._text.splitlines()), default=0)
        if self._wraplength:
            pixels = min(pixels, self._wraplength)
        # Text width is measured in average characters, unlike label wraplength.
        width = max(1, math.ceil(pixels / max(1, font.measure("0"))))
        if int(super().cget("width")) != width:
            super().configure(width=width)

    def _schedule_resize(self, _event=None):
        if self._resize_job is None:
            self._resize_job = self.after_idle(self._resize_to_text)

    def _resize_to_text(self):
        self._resize_job = None
        if not self.winfo_exists() or self.winfo_width() <= 1:
            return
        count = self.count("1.0", "end-1c", "displaylines")
        lines = (count[0] if count else 0) + 1
        if int(super().cget("height")) != lines:
            super().configure(height=lines)

    def _on_destroy(self, event):
        if event.widget is not self:
            return
        if self._resize_job is not None:
            self.after_cancel(self._resize_job)
            self._resize_job = None
        self._set_variable(None)

    def cget(self, key):
        if key == "text":
            return self._text
        if key == "textvariable":
            return self._variable or ""
        if key == "wraplength":
            return self._wraplength
        if key == "style":
            return self._style
        if key == "anchor":
            return self._anchor
        if key == "justify":
            return self._justify
        return super().cget(key)

    __getitem__ = cget

    def configure(self, cnf=None, **kwargs):
        if isinstance(cnf, str):
            if cnf in {"text", "textvariable", "wraplength", "style", "anchor", "justify"}:
                return (cnf, cnf, cnf.title(), "", self.cget(cnf))
            return super().configure(cnf, **kwargs)
        options = dict(cnf or {})
        options.update(kwargs)
        if not options:
            return super().configure()
        previous_text = self._text
        new_text = str(options.pop("text", self._text))
        if "textvariable" in options:
            self._set_variable(options.pop("textvariable"))
            if self._variable is not None:
                new_text = self._text
        if "wraplength" in options:
            self._wraplength = max(0, int(options.pop("wraplength") or 0))
        if "anchor" in options:
            self._anchor = options.pop("anchor")
        if "justify" in options:
            self._justify = options.pop("justify")
            self.tag_configure("alignment", justify=self._justify)
        if "style" in options:
            self._style = options.pop("style")
            self._theme_role = "muted" if "muted" in self._style.lower() else "body"
            style = ttk.Style(self)
            for key in ("font", "foreground"):
                value = style.lookup(self._style or "TLabel", key)
                if value:
                    options.setdefault(key, value)
        if "width" in options:
            self._auto_width = False
        # Updating theme/font must never accidentally make display text editable.
        options.pop("state", None)
        result = super().configure(**options) if options else None
        # Binding a different variable updates the cached value before the
        # display is rewritten. Compare against the text visible at entry.
        if new_text != previous_text:
            self._replace_text(new_text)
        else:
            self._request_width()
            self._schedule_resize()
        return result

    config = configure


def _copy(widget: tk.Misc, text: str):
    if text:
        widget.clipboard_clear()
        widget.clipboard_append(text)


def _selected_text(widget) -> str:
    try:
        if isinstance(widget, tk.Text):
            return widget.get("sel.first", "sel.last")
        if widget.selection_present():
            return widget.get()[int(widget.index("sel.first")):int(widget.index("sel.last"))]
    except tk.TclError:
        pass
    return ""


def _all_text(widget) -> str:
    if isinstance(widget, tk.Text):
        return widget.get("1.0", "end-1c")
    if isinstance(widget, (tk.Entry, ttk.Entry)):
        return widget.get()
    variable = widget.cget("textvariable")
    return str(widget.getvar(str(variable))) if variable else str(widget.cget("text"))


def _readonly(widget) -> bool:
    if getattr(widget, "_selectable_label", False):
        return True
    if isinstance(widget, ttk.Entry):
        return widget.instate(("readonly",)) or widget.instate(("disabled",))
    return str(widget.cget("state")) in {"disabled", "readonly"}


def _select_all(widget):
    widget.focus_set()
    if isinstance(widget, tk.Text):
        widget.tag_add("sel", "1.0", "end-1c")
        widget.mark_set("insert", "1.0")
    else:
        widget.selection_range(0, "end")
        widget.icursor("end")
    return "break"


def _tree_rows(widget: ttk.Treeview, rows) -> str:
    columns = widget.cget("displaycolumns")
    if not columns or columns == ("#all",) or str(columns) == "#all":
        columns = widget.cget("columns")
    values = []
    for row in rows:
        line = []
        if "tree" in widget.cget("show"):
            line.append(str(widget.item(row, "text")))
        line.extend(str(widget.set(row, column)) for column in columns)
        values.append("\t".join(line))
    return "\n".join(values)


def _build_context_menu(widget, event=None) -> tk.Menu:
    """Build at invocation time so menu actions reflect the current selection."""
    old_menu = getattr(widget, "_text_actions_menu", None)
    if old_menu is not None:
        old_menu.destroy()
    palette = palette_for(widget)
    menu = tk.Menu(widget, tearoff=False, background=palette["surface"], foreground=palette["text"],
                   activebackground=palette["selected"], activeforeground=palette["text"],
                   disabledforeground=palette["disabled_text"], relief="flat", borderwidth=0)
    widget._text_actions_menu = menu
    if isinstance(widget, ttk.Treeview):
        row = widget.identify_row(event.y) if event is not None else widget.focus()
        column = widget.identify_column(event.x) if event is not None else "#1"
        cell = ""
        if row and column:
            cell = widget.item(row, "text") if column == "#0" else widget.set(row, widget.column(column, "id"))
        rows = widget.selection() or ((row,) if row else ())
        menu.add_command(label="Copy cell", state="normal" if row and column else "disabled", command=lambda: _copy(widget, str(cell)))
        menu.add_command(label="Copy selected rows", state="normal" if rows else "disabled", command=lambda: _copy(widget, _tree_rows(widget, rows)))
        return menu
    if isinstance(widget, (tk.Text, tk.Entry, ttk.Entry)):
        selected = bool(_selected_text(widget))
        if not _readonly(widget):
            menu.add_command(label="Cut", accelerator="Ctrl+X", state="normal" if selected else "disabled", command=lambda: widget.event_generate("<<Cut>>"))
        menu.add_command(label="Copy", accelerator="Ctrl+C", state="normal" if selected else "disabled", command=lambda: _copy(widget, _selected_text(widget)))
        if not _readonly(widget):
            menu.add_command(label="Paste", accelerator="Ctrl+V", command=lambda: widget.event_generate("<<Paste>>"))
        menu.add_command(label="Copy all", command=lambda: _copy(widget, _all_text(widget)))
        menu.add_separator()
        menu.add_command(label="Select all", accelerator="Ctrl+A", command=lambda: _select_all(widget))
    else:
        menu.add_command(label="Copy text", command=lambda: _copy(widget, _all_text(widget)))
    return menu


def _show_context_menu(widget, event):
    menu = _build_context_menu(widget, event if getattr(event, "num", None) == 3 else None)
    x = event.x_root if getattr(event, "num", None) == 3 else widget.winfo_rootx() + 16
    y = event.y_root if getattr(event, "num", None) == 3 else widget.winfo_rooty() + 16
    try:
        menu.tk_popup(x, y)
    finally:
        menu.grab_release()
    return "break"


def install_text_actions(root: tk.Misc):
    """Add copy menus recursively; call again after constructing new dialogs.

    No clipboard contents are read until the user explicitly requests Paste.
    Right-clicking preserves existing text and row selections.
    """
    for widget in (root, *root.winfo_children()):
        supported = isinstance(widget, (tk.Text, tk.Entry, ttk.Entry, tk.Label, ttk.Label, ttk.Treeview))
        if supported and not getattr(widget, "_text_actions_installed", False):
            widget._text_actions_installed = True
            widget.bind("<Button-3>", lambda event, target=widget: _show_context_menu(target, event), add=True)
            widget.bind("<Shift-F10>", lambda event, target=widget: _show_context_menu(target, event), add=True)
            if isinstance(widget, (tk.Text, tk.Entry, ttk.Entry)):
                widget.bind("<Control-a>", lambda _event, target=widget: _select_all(target), add=True)
                widget.bind("<Control-A>", lambda _event, target=widget: _select_all(target), add=True)
                if isinstance(widget, tk.Text):
                    widget.bind("<Button-1>", lambda _event, target=widget: target.focus_set(), add=True)
            elif isinstance(widget, ttk.Treeview):
                widget.bind("<Control-c>", lambda _event, target=widget: (_copy(target, _tree_rows(target, target.selection())), "break")[1], add=True)
        if widget is not root:
            install_text_actions(widget)
