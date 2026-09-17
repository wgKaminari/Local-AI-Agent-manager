"""Shared colours and in-place light/dark appearance for the local workspace.

Changing appearance never recreates an editor: selection, undo history, focus,
and unsaved work belong to the widgets and stay intact.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk


PALETTES = {
    "light": {
        "bg": "#f7f8fa", "surface": "#ffffff", "sidebar": "#eef1f4",
        "hover": "#e7ebef", "selected": "#dceee9", "border": "#dce2e7",
        "text": "#202b33", "muted": "#62717d", "accent": "#187a68",
        "accent_text": "#ffffff", "success": "#187a68", "warning": "#92631f",
        "warning_bg": "#fff3d9", "disabled": "#e9edf0", "disabled_text": "#83909b",
        "selection": "#bddfd5",
    },
    "dark": {
        "bg": "#171b20", "surface": "#20262c", "sidebar": "#12171c",
        "hover": "#2b343c", "selected": "#233f3b", "border": "#36424c",
        "text": "#edf2f5", "muted": "#a2b0bb", "accent": "#74d4b9",
        "accent_text": "#102b25", "success": "#74d4b9", "warning": "#e8bc72",
        "warning_bg": "#403421", "disabled": "#293139", "disabled_text": "#81909c",
        "selection": "#38685b",
    },
}


def palette_for(widget: tk.Misc) -> dict[str, str]:
    """Resolve a widget or dialog's palette through its owning window."""
    current = widget
    while current is not None:
        palette = getattr(current, "_app_palette", None)
        if palette is not None:
            return palette
        current = getattr(current, "master", None)
    return PALETTES["light"]


def _styles(root: tk.Misc, p: dict[str, str]) -> None:
    style = ttk.Style(root)
    if style.theme_use() != "clam":
        style.theme_use("clam")
    style.configure(".", font=("Segoe UI", 10), background=p["bg"], foreground=p["text"],
                    bordercolor=p["border"], lightcolor=p["border"], darkcolor=p["border"])
    for role, surface in (("", "bg"), ("Surface.", "surface"), ("Sidebar.", "sidebar")):
        style.configure(role + "TFrame", background=p[surface])
        style.configure(role + "TLabel", background=p[surface], foreground=p["text"])
    style.configure("Muted.TLabel", foreground=p["muted"])
    style.configure("Section.TLabel", font=("Segoe UI", 11, "bold"))
    style.configure("Title.TLabel", font=("Segoe UI", 25, "bold"))
    style.configure("Eyebrow.TLabel", font=("Segoe UI", 9, "bold"), foreground=p["muted"])
    style.configure("TButton", padding=(14, 9), borderwidth=1, relief="flat",
                    background=p["surface"], foreground=p["text"], focuscolor=p["accent"])
    style.map("TButton", background=[("disabled", p["disabled"]), ("pressed", p["selected"]),
                                     ("active", p["hover"])],
              foreground=[("disabled", p["disabled_text"])], bordercolor=[("focus", p["accent"])])
    for name in ("TEntry", "TCombobox", "TSpinbox"):
        style.configure(name, padding=(10, 8), arrowsize=12, borderwidth=1,
                        fieldbackground=p["surface"], background=p["surface"], foreground=p["text"],
                        insertcolor=p["text"], arrowcolor=p["muted"], selectbackground=p["selection"],
                        selectforeground=p["text"])
        style.map(name, fieldbackground=[("disabled", p["disabled"]), ("readonly", p["surface"])],
                  foreground=[("disabled", p["disabled_text"])],
                  selectbackground=[("!focus", p["surface"]), ("focus", p["selection"])],
                  selectforeground=[("!focus", p["text"]), ("focus", p["text"])],
                  bordercolor=[("focus", p["accent"])],
                  lightcolor=[("focus", p["accent"])], darkcolor=[("focus", p["accent"])])
    # The combobox popdown is a native Listbox outside the Python widget tree.
    for option, value in (("background", p["surface"]), ("foreground", p["text"]),
                          ("selectBackground", p["selected"]), ("selectForeground", p["text"])):
        root.option_add("*TCombobox*Listbox." + option, value)
    for name in ("TNotebook", "Shell.TNotebook", "FlatDeck.TNotebook"):
        style.configure(name, borderwidth=0, tabmargins=0, background=p["bg"],
                        bordercolor=p["bg"], lightcolor=p["bg"], darkcolor=p["bg"])
    style.layout("Shell.TNotebook.Tab", [])
    style.layout("FlatDeck.TNotebook.Tab", [])
    style.configure("TNotebook.Tab", padding=(16, 10), borderwidth=0,
                    background=p["bg"], foreground=p["muted"])
    style.map("TNotebook.Tab", background=[("selected", p["selected"]), ("active", p["hover"])],
              foreground=[("selected", p["text"])])
    style.configure("Treeview", rowheight=42, borderwidth=0, background=p["surface"],
                    foreground=p["text"], fieldbackground=p["surface"])
    style.configure("Treeview.Heading", font=("Segoe UI", 9, "bold"), padding=(10, 11),
                    relief="flat", borderwidth=0, bordercolor=p["bg"], lightcolor=p["bg"],
                    darkcolor=p["bg"], background=p["bg"], foreground=p["muted"])
    style.map("Treeview", background=[("selected", p["selected"])], foreground=[("selected", p["text"])])
    style.map("Treeview.Heading", background=[("active", p["hover"])])
    for orient in ("Vertical", "Horizontal"):
        style.configure(orient + ".TScrollbar", arrowsize=9, width=10, borderwidth=0,
                        relief="flat", background=p["border"], troughcolor=p["bg"],
                        arrowcolor=p["muted"], lightcolor=p["bg"], darkcolor=p["bg"])
        style.map(orient + ".TScrollbar", background=[("active", p["muted"]), ("pressed", p["muted"])])
    style.configure("Horizontal.TProgressbar", background=p["accent"], troughcolor=p["hover"],
                    borderwidth=0, lightcolor=p["accent"], darkcolor=p["accent"])
    for name in ("TCheckbutton", "TRadiobutton"):
        style.configure(name, background=p["bg"], foreground=p["text"],
                        indicatorbackground=p["surface"], indicatorforeground=p["accent"],
                        indicatormargin=(0, 0, 8, 0), focuscolor=p["accent"])
        style.map(name, background=[("active", p["bg"])], foreground=[("disabled", p["disabled_text"])],
                  indicatorbackground=[("selected", p["accent"]), ("disabled", p["disabled"])])
    style.configure("TLabelframe", bordercolor=p["border"], borderwidth=1)
    style.configure("TLabelframe.Label", background=p["bg"], foreground=p["muted"])
    for name in ("TSeparator", "Horizontal.TSeparator", "Vertical.TSeparator"):
        style.configure(name, background=p["border"], bordercolor=p["border"],
                        lightcolor=p["border"], darkcolor=p["border"])
    style.configure("TPanedwindow", background=p["bg"])


_LEGACY = {
    "#ffffff": "bg", "white": "bg", "#f9f9f9": "sidebar",
    "#202123": "text", "#212121": "text", "#202020": "text", "black": "text",
    "#4e4e4e": "text", "#6b6b6b": "muted", "#888888": "muted", "#777777": "muted",
    "#e6e6e6": "border", "#aaaaaa": "border", "#d9d9d9": "border", "#bdbdbd": "border",
    "#999999": "disabled_text", "#f1f1f1": "disabled", "#929292": "accent",
    "#ececec": "hover", "#f0f0f0": "hover", "#ededed": "hover", "#eaeaea": "hover",
    "#f6f6f6": "hover", "#f7f7f7": "hover", "#eeeeee": "selected", "#e9e9e9": "selected",
    "#efefef": "selected", "#dcece6": "selection", "#d9e7f5": "selection",
    "#f7f3e9": "warning_bg", "#796437": "warning", "#89602a": "warning",
    "#28735e": "success", "#37765c": "success",
}


def _role(value: str) -> str | None:
    value = str(value).lower()
    if value in _LEGACY:
        return _LEGACY[value]
    for palette in PALETTES.values():
        for key, color in palette.items():
            if color == value:
                return key
    return None


def _parent_background(widget: tk.Misc, p: dict[str, str]) -> str:
    parent = getattr(widget, "master", None)
    if parent is not None:
        try:
            if isinstance(parent, ttk.Widget):
                return ttk.Style(widget).lookup(parent.cget("style") or parent.winfo_class(), "background") or p["bg"]
            return str(parent.cget("background"))
        except tk.TclError:
            pass
    return p["bg"]


def _theme_widget(widget: tk.Misc, p: dict[str, str]) -> None:
    if isinstance(widget, (tk.Tk, tk.Toplevel)):
        widget._app_palette = p
    # Explicit widget options override ttk styles. Only replace recognised
    # colours, preserving tag roles and deliberate application state.
    if isinstance(widget, ttk.Widget):
        for option in ("background", "foreground"):
            try:
                role = _role(widget.cget(option))
                if role:
                    widget.configure(**{option: p[role]})
            except tk.TclError:
                pass
    else:
        options = set(widget.keys())
        updates = {}
        for option in ("background", "foreground", "activebackground", "activeforeground",
                       "disabledforeground", "disabledbackground", "readonlybackground",
                       "highlightbackground", "highlightcolor", "insertbackground", "selectbackground",
                       "selectforeground", "troughcolor"):
            if option in options:
                role = _role(widget.cget(option))
                if role:
                    updates[option] = p[role]
        if isinstance(widget, (tk.Text, tk.Entry, tk.Listbox, tk.Spinbox)):
            updates.update(background=p["surface"], foreground=p["text"], selectbackground=p["selection"],
                           selectforeground=p["text"], highlightbackground=p["border"], highlightcolor=p["accent"])
            if "insertbackground" in options:
                updates["insertbackground"] = p["text"]
            if "inactiveselectbackground" in options:
                updates["inactiveselectbackground"] = p["selection"]
            for option in ("disabledbackground", "readonlybackground"):
                if option in options:
                    updates[option] = p["surface"]
        if getattr(widget, "_selectable_label", False):
            surface = getattr(widget, "_theme_surface", None)
            updates.update(background=p.get(surface, _parent_background(widget, p)),
                           foreground=p["muted" if getattr(widget, "_theme_role", "") == "muted" else "text"],
                           highlightthickness=0, borderwidth=0)
        elif getattr(widget, "_theme_surface", None) in p:
            updates["background"] = p[widget._theme_surface]
        if isinstance(widget, tk.Scrollbar):
            updates.update(background=p["border"], activebackground=p["muted"], troughcolor=p["bg"],
                           highlightbackground=p["bg"])
        if isinstance(widget, tk.Menu):
            updates.update(background=p["surface"], foreground=p["text"], activebackground=p["selected"],
                           activeforeground=p["text"], disabledforeground=p["disabled_text"], borderwidth=0)
        if isinstance(widget, (tk.Tk, tk.Toplevel)):
            updates["background"] = p["bg"]
        if updates:
            widget.configure(**updates)
        if isinstance(widget, tk.Text):
            for tag in widget.tag_names():
                if tag == "sel":
                    continue
                for option in ("foreground", "background"):
                    role = _role(widget.tag_cget(tag, option))
                    if role:
                        widget.tag_configure(tag, **{option: p[role]})


def _finish_widget_theme(widget: tk.Misc) -> None:
    # Composite widgets know which child surfaces belong together. Run their
    # semantic styling last, after generic colour migration has visited children.
    callback = getattr(widget, "apply_theme", None)
    if callable(callback):
        callback()


def apply_theme(root: tk.Misc, name: str = "light") -> dict[str, str]:
    """Apply a named palette to a complete widget tree without rebuilding it."""
    if name not in PALETTES:
        raise ValueError("Theme must be 'light' or 'dark'.")
    palette = PALETTES[name]
    root._app_palette = palette
    root._app_theme = name
    _styles(root, palette)

    def visit(widget):
        _theme_widget(widget, palette)
        for child in widget.winfo_children():
            visit(child)
        _finish_widget_theme(widget)

    visit(root)
    if not getattr(root, "_theme_map_bound", False):
        def on_map(event):
            try:
                _theme_widget(event.widget, palette_for(root))
                _finish_widget_theme(event.widget)
            except (tk.TclError, AttributeError):
                # Tk-owned popdowns are not always represented by Python widgets.
                pass
        root.bind_all("<Map>", on_map, add="+")
        root._theme_map_bound = True
    root.event_generate("<<AppThemeChanged>>", when="tail")
    return palette
