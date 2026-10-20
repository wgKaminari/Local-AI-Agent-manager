"""Native WebView2 window around a private, loopback-only Python workspace."""
from __future__ import annotations

import contextlib
import secrets
import sys
import threading
from pathlib import Path


class NativeCloseState:
    """The only JavaScript bridge: cache whether native close needs confirmation."""

    def __init__(self, token: str):
        self._token = token
        self._lock = threading.Lock()
        self._dirty = True
        self._revision = -1

    def update_close_state(self, token, dirty, revision):
        if not isinstance(token, str) or not secrets.compare_digest(
                token.encode("utf-8"), self._token.encode("utf-8")):
            raise ValueError("Invalid desktop session.")
        if not isinstance(dirty, bool) or isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
            raise ValueError("Close state needs a boolean and a nonnegative revision.")
        with self._lock:
            if revision > self._revision:
                self._dirty, self._revision = dirty, revision
            return {"dirty": self._dirty, "revision": self._revision}

    def _requires_confirmation(self):
        with self._lock:
            return self._dirty


class LocalWorkspace:
    def __init__(self, data_dir: Path):
        from waitress import create_server
        from .web_api import close_resources, create_app
        self._closed = False
        self.app = create_app(Path(data_dir))
        try:
            self.server = create_server(self.app, host="127.0.0.1", port=0, threads=6,
                                        clear_untrusted_proxy_headers=True,
                                        max_request_body_size=16 * 1024 * 1024)
        except Exception:
            # Preserve the startup error while releasing the application's workers.
            with contextlib.suppress(Exception):
                close_resources(self.app)
            raise
        self.url = f"http://127.0.0.1:{self.server.effective_port}"
        self.app.config["LOCAL_ORIGIN"] = self.url
        self.thread = threading.Thread(target=self.server.run, name="local-workspace", daemon=True)

    def start(self):
        self.thread.start()
        return self

    def close(self):
        from .web_api import close_resources
        if self._closed:
            return
        self._closed = True
        # Keep Waitress's wake-up socket alive until in-flight requests finish.
        # HTTPChannel.service() signals that socket after producing a response.
        self.server.accepting = False
        try:
            close_resources(self.app)
        finally:
            try:
                self.server.task_dispatcher.shutdown(timeout=2)
            finally:
                # Cleanup failures must still release the local listener.
                if self.server.task_dispatcher.threads:
                    # A slow request may outlive the bounded drain. Release the
                    # listening socket now, but keep its wake-up socket available
                    # until the last request worker exits.
                    from waitress.wasyncore import dispatcher
                    dispatcher.close(self.server)
                    self._cleanup_thread = threading.Thread(
                        target=self._finish_after_requests, name="local-workspace-cleanup", daemon=True)
                    self._cleanup_thread.start()
                else:
                    self._close_server()

    def _finish_after_requests(self):
        dispatcher = self.server.task_dispatcher
        with dispatcher.lock:
            while dispatcher.threads:
                dispatcher.thread_exit_cv.wait()
        self._close_server()

    def _close_server(self):
        try:
            self.server.close()
        finally:
            if self.thread.ident is not None and self.thread is not threading.current_thread():
                self.thread.join(timeout=2)


def report_startup_error(message: str) -> None:
    """Keep double-click/pythonw failures visible, even without a console."""
    message = f"Personal Job Agent could not open.\n\n{message}"
    try:
        if sys.platform == "win32":
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, message, "Personal Job Agent", 0x10)
        else:
            from tkinter import Tk, messagebox
            root = Tk()
            root.withdraw()
            try:
                messagebox.showerror("Personal Job Agent", message, parent=root)
            finally:
                root.destroy()
    except Exception:
        # A terminal remains useful on systems without any working GUI backend.
        if sys.stderr is not None:
            print(message, file=sys.stderr)


def _window_bounds(screens):
    """Fit the initial native window within the primary display's logical area."""
    if not screens:
        return {"width": 1100, "height": 720, "min_size": (900, 640)}
    screen = next((item for item in screens if item.x == 0 and item.y == 0), screens[0])
    x, y, width, height = screen.x, screen.y, screen.width, screen.height
    frame = getattr(screen, "frame", None)
    if frame is not None and all(hasattr(frame, key) for key in ("X", "Y", "Width", "Height")):
        # pywebview's Windows Screen.frame is the taskbar-aware WorkingArea.
        x, y, width, height = (int(getattr(frame, key)) for key in ("X", "Y", "Width", "Height"))
    else:
        # Other renderers may expose only screen bounds; reserve desktop chrome.
        height = max(1, height - 64)
    width, height = max(1, int(width)), max(1, int(height))
    window_width = min(1440, max(1, width - min(48, width // 10)))
    window_height = min(960, max(1, height - min(48, height // 10)))
    return {"width": window_width, "height": window_height,
            "min_size": (min(900, window_width), min(640, window_height)),
            "x": x + (width - window_width) // 2, "y": y + (height - window_height) // 2}


def launch(data_dir: Path, *, preview: bool = False):
    try:
        import webview
        from . import web_api  # Check all dependencies before opening a window.
        import waitress
    except ImportError as exc:
        raise RuntimeError("Modern desktop dependencies are missing. Run python -m pip install -r requirements.txt. The previous interface is available with python run.py gui --classic.") from exc
    from . import windows_integration as windows
    if not preview:
        windows.set_app_id()
        if not windows.claim_single_instance(data_dir):
            return  # The open window was brought to the front instead.
    workspace = LocalWorkspace(data_dir)
    try:
        workspace.start()
        webview.settings["ALLOW_FILE_URLS"] = False
        webview.settings["ALLOW_DOWNLOADS"] = True
        webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = True
        title = "Personal Job Agent" + (" — Fictional design preview" if preview else "")
        from .web_api import _appearance
        # Match the page background so the window does not flash before loading.
        dark = _appearance(Path(data_dir))["theme"] == "dark"
        background = "#0e0f11" if dark else "#fafaf9"
        close_state = NativeCloseState(workspace.app.config["LOCAL_TOKEN"])
        try:
            bounds = _window_bounds(webview.screens)
        except Exception:
            bounds = _window_bounds([])
        if sys.platform == "win32" and webview.renderer != "edgechromium":
            raise RuntimeError("Microsoft Edge WebView2 Runtime is required for this desktop interface.")
        window = webview.create_window(title, workspace.url, **bounds,
                                       text_select=True, zoomable=True,
                                       background_color=background, js_api=close_state,
                                       confirm_close=True, localization={
                                           "global.quitConfirmation": "There may be unsaved edits or tasks still running. Close anyway?"
                                       })

        def closing():
            # pywebview raises closing synchronously on the native UI thread.
            # Evaluating JavaScript here can deadlock WebView2. Its backend reads
            # confirm_close after this handler and owns the native dialog.
            window.confirm_close = close_state._requires_confirmation()
            return True

        window.events.closing += closing
        # pywebview follows the Windows theme; follow the app's theme instead.
        window.events.shown += lambda: windows.set_title_bar_theme(dark)
        workspace.app.config["THEME_CHANGED"] = lambda theme: windows.set_title_bar_theme(theme == "dark")
        # Explicit Chromium renderer: never silently fall back to Internet Explorer.
        webview.start(gui="edgechromium" if sys.platform == "win32" else None,
                      debug=False, private_mode=True, icon=str(windows.icon_path()))
    except Exception as exc:
        hint = " Check that Microsoft Edge WebView2 Runtime is installed." if sys.platform == "win32" else ""
        raise RuntimeError(f"The desktop window could not start.{hint}\n{exc}") from exc
    finally:
        workspace.close()
