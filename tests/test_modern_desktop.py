"""Native shell regression tests: startup, cleanup and unsaved-work protection."""
import io
import socket
import sys
import tempfile
import threading
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from norway_job_agent import modern_desktop
from norway_job_agent.cli import main


class ClosingEvent:
    def __init__(self):
        self.callback = None

    def __iadd__(self, callback):
        self.callback = callback
        return self


class NativeDesktopTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.data = Path(self.folder.name) / "personal"
        self.webview = types.ModuleType("webview")
        self.webview.settings = {}
        self.webview.screens = []
        self.webview.renderer = "edgechromium"
        self.window = MagicMock()
        self.window.events.closing = ClosingEvent()
        self.webview.create_window = MagicMock(return_value=self.window)
        self.webview.start = MagicMock()
        self.addCleanup(patch.stopall)
        patch.dict(sys.modules, {"webview": self.webview}).start()
        self.workspace_type = patch.object(modern_desktop, "LocalWorkspace").start()
        self.workspace = self.workspace_type.return_value
        self.workspace.url = "http://127.0.0.1:54321"
        self.workspace.app.config = {"LOCAL_TOKEN": "fictional-desktop-session"}

    def test_native_window_uses_loopback_and_explicit_windows_renderer(self):
        with patch.object(modern_desktop.sys, "platform", "win32"):
            modern_desktop.launch(self.data)
        self.workspace_type.assert_called_once_with(self.data)
        self.workspace.start.assert_called_once_with()
        args, options = self.webview.create_window.call_args
        self.assertEqual(args, ("Personal Job Agent", "http://127.0.0.1:54321"))
        self.assertEqual(options["min_size"], (900, 640))
        self.assertTrue(options["zoomable"])
        self.assertFalse(self.webview.settings["ALLOW_FILE_URLS"])
        self.webview.start.assert_called_once_with(gui="edgechromium", debug=False, private_mode=True)
        self.workspace.close.assert_called_once_with()

    def test_preview_is_visibly_labelled(self):
        modern_desktop.launch(self.data, preview=True)
        self.assertIn("Fictional design preview", self.webview.create_window.call_args.args[0])

    def test_small_display_controls_the_actual_window_creation_bounds(self):
        self.webview.screens = [types.SimpleNamespace(x=0, y=0, width=1366, height=768,
            frame=types.SimpleNamespace(X=0, Y=0, Width=1366, Height=728))]
        modern_desktop.launch(self.data)
        options = self.webview.create_window.call_args.kwargs
        self.assertEqual((options["width"], options["height"]), (1318, 680))
        self.assertEqual((options["x"], options["y"]), (24, 24))

    def test_windows_rejects_silent_legacy_renderer_fallback(self):
        self.webview.renderer = "mshtml"
        with patch.object(modern_desktop.sys, "platform", "win32"):
            with self.assertRaisesRegex(RuntimeError, "Edge WebView2 Runtime is required"):
                modern_desktop.launch(self.data)
        self.webview.create_window.assert_not_called()
        self.workspace.close.assert_called_once_with()

    def test_window_creation_failure_releases_workspace(self):
        self.webview.create_window.side_effect = ValueError("renderer unavailable")
        with self.assertRaisesRegex(RuntimeError, "renderer unavailable"):
            modern_desktop.launch(self.data)
        self.workspace.close.assert_called_once_with()
        self.webview.start.assert_not_called()

    def test_renderer_start_failure_releases_workspace(self):
        self.webview.start.side_effect = Exception("runtime missing")
        with patch.object(modern_desktop.sys, "platform", "win32"):
            with self.assertRaisesRegex(RuntimeError, "WebView2 Runtime"):
                modern_desktop.launch(self.data)
        self.workspace.close.assert_called_once_with()

    def test_server_thread_start_failure_releases_workspace(self):
        self.workspace.start.side_effect = RuntimeError("cannot start thread")
        with self.assertRaisesRegex(RuntimeError, "cannot start thread"):
            modern_desktop.launch(self.data)
        self.workspace.close.assert_called_once_with()
        self.webview.create_window.assert_not_called()

    def test_missing_dependency_gives_install_instruction_before_starting_server(self):
        with patch.dict(sys.modules, {"webview": None}):
            with self.assertRaisesRegex(RuntimeError, "pip install -r requirements.txt"):
                modern_desktop.launch(self.data)
        self.workspace_type.assert_not_called()

    def test_unsaved_edits_enable_built_in_native_confirmation(self):
        modern_desktop.launch(self.data)
        bridge = self.webview.create_window.call_args.kwargs["js_api"]
        bridge.update_close_state("fictional-desktop-session", True, 1)
        self.assertTrue(self.window.events.closing.callback())
        self.assertTrue(self.window.confirm_close)
        self.window.evaluate_js.assert_not_called()
        self.window.create_confirmation_dialog.assert_not_called()

    def test_close_before_bridge_sync_still_uses_native_confirmation(self):
        modern_desktop.launch(self.data)
        self.assertTrue(self.window.events.closing.callback())
        self.assertTrue(self.window.confirm_close)
        self.assertTrue(self.webview.create_window.call_args.kwargs["confirm_close"])
        self.assertIn("unsaved", self.webview.create_window.call_args.kwargs["localization"]["global.quitConfirmation"])

    def test_clean_workspace_closes_without_prompt(self):
        modern_desktop.launch(self.data)
        bridge = self.webview.create_window.call_args.kwargs["js_api"]
        bridge.update_close_state("fictional-desktop-session", False, 1)
        self.assertTrue(self.window.events.closing.callback())
        self.assertFalse(self.window.confirm_close)
        self.window.create_confirmation_dialog.assert_not_called()

    def test_broken_renderer_does_not_trap_user_in_window(self):
        modern_desktop.launch(self.data)
        self.window.evaluate_js.side_effect = RuntimeError("renderer crashed")
        self.assertTrue(self.window.events.closing.callback())
        self.window.evaluate_js.assert_not_called()
        self.assertTrue(self.window.confirm_close)


class NativeCloseStateTests(unittest.TestCase):
    def setUp(self):
        self.bridge = modern_desktop.NativeCloseState("fictional-desktop-session")

    def test_unknown_initial_state_requires_confirmation(self):
        self.assertTrue(self.bridge._requires_confirmation())

    def test_invalid_token_cannot_clear_close_warning(self):
        for token in (None, "", "other-session", "\u00e9"):
            with self.assertRaisesRegex(ValueError, "Invalid desktop session"):
                self.bridge.update_close_state(token, False, 1)
        self.assertTrue(self.bridge._requires_confirmation())

    def test_state_and_revision_are_strictly_validated(self):
        for dirty, revision in (("false", 1), (0, 1), (False, -1), (False, True), (False, "2"), (False, 2.1)):
            with self.assertRaisesRegex(ValueError, "boolean.*revision"):
                self.bridge.update_close_state("fictional-desktop-session", dirty, revision)
        self.assertTrue(self.bridge._requires_confirmation())

    def test_late_clean_update_cannot_overwrite_new_unsaved_changes(self):
        self.bridge.update_close_state("fictional-desktop-session", False, 1)
        self.bridge.update_close_state("fictional-desktop-session", True, 3)
        self.assertEqual(self.bridge.update_close_state("fictional-desktop-session", False, 2), {"dirty": True, "revision": 3})
        self.assertTrue(self.bridge._requires_confirmation())

    def test_successful_save_clears_confirmation_and_duplicate_revision_is_ignored(self):
        self.bridge.update_close_state("fictional-desktop-session", True, 1)
        self.bridge.update_close_state("fictional-desktop-session", False, 2)
        self.bridge.update_close_state("fictional-desktop-session", True, 2)
        self.assertFalse(self.bridge._requires_confirmation())

    def test_bridge_has_only_the_narrow_state_update_public_method(self):
        public = {name for name in dir(self.bridge) if not name.startswith("_") and callable(getattr(self.bridge, name))}
        self.assertEqual(public, {"update_close_state"})


class WindowBoundsTests(unittest.TestCase):
    def screen(self, width, height, *, x=0, y=0, work_height=None, scale=1):
        return types.SimpleNamespace(x=x, y=y, width=width, height=height, scale=scale,
            frame=types.SimpleNamespace(X=x, Y=y, Width=width, Height=work_height or height))

    def test_large_display_keeps_desired_size_and_centers_it(self):
        bounds = modern_desktop._window_bounds([self.screen(2560, 1440, work_height=1400)])
        self.assertEqual(bounds, {"width": 1440, "height": 960, "min_size": (900, 640), "x": 560, "y": 220})

    def test_small_work_area_caps_both_size_and_minimum(self):
        bounds = modern_desktop._window_bounds([self.screen(800, 600, work_height=560)])
        self.assertEqual((bounds["width"], bounds["height"]), (752, 512))
        self.assertEqual(bounds["min_size"], (752, 512))
        self.assertLess(bounds["x"] + bounds["width"], 800)
        self.assertLess(bounds["y"] + bounds["height"], 560)

    def test_primary_screen_is_selected_even_if_secondary_is_listed_first(self):
        bounds = modern_desktop._window_bounds([self.screen(2560, 1440, x=-2560), self.screen(1366, 768, work_height=728)])
        self.assertEqual((bounds["width"], bounds["height"], bounds["x"], bounds["y"]), (1318, 680, 24, 24))

    def test_logical_bounds_are_not_scaled_again_for_high_dpi(self):
        bounds = modern_desktop._window_bounds([self.screen(1536, 864, work_height=816, scale=1.25)])
        self.assertEqual((bounds["width"], bounds["height"]), (1440, 768))

    def test_missing_platform_work_area_reserves_space_for_desktop_chrome(self):
        bounds = modern_desktop._window_bounds([types.SimpleNamespace(x=0, y=0, width=1280, height=720)])
        self.assertEqual((bounds["width"], bounds["height"]), (1232, 608))
        self.assertEqual(bounds["min_size"], (900, 608))

    def test_unavailable_display_list_uses_compact_fallback(self):
        self.assertEqual(modern_desktop._window_bounds([]), {"width": 1100, "height": 720, "min_size": (900, 640)})


class LocalWorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.addCleanup(patch.stopall)
        self.create_app = patch("norway_job_agent.web_api.create_app").start()
        self.app = self.create_app.return_value
        self.app.config = {}
        self.create_server = patch("waitress.create_server").start()
        self.server = self.create_server.return_value
        self.server.effective_port = 12345
        self.server.task_dispatcher.threads = set()
        self.close_resources = patch("norway_job_agent.web_api.close_resources").start()
        self.thread_type = patch.object(modern_desktop.threading, "Thread").start()

    def test_server_binds_loopback_and_sets_exact_origin(self):
        workspace = modern_desktop.LocalWorkspace(Path("fixture"))
        options = self.create_server.call_args.kwargs
        self.assertEqual(options["host"], "127.0.0.1")
        self.assertEqual(options["port"], 0)
        self.assertEqual(self.app.config["LOCAL_ORIGIN"], "http://127.0.0.1:12345")
        self.assertIs(workspace.start(), workspace)
        self.thread_type.return_value.start.assert_called_once_with()

    def test_failed_listener_creation_cleans_up_application(self):
        self.create_server.side_effect = OSError("port unavailable")
        with self.assertRaisesRegex(OSError, "port unavailable"):
            modern_desktop.LocalWorkspace(Path("fixture"))
        self.close_resources.assert_called_once_with(self.app)
        self.thread_type.assert_not_called()

    def test_failed_initial_cleanup_preserves_listener_error(self):
        self.create_server.side_effect = OSError("port unavailable")
        self.close_resources.side_effect = RuntimeError("cleanup failed")
        with self.assertRaisesRegex(OSError, "port unavailable"):
            modern_desktop.LocalWorkspace(Path("fixture"))

    def test_failed_resource_cleanup_still_closes_listener_once(self):
        workspace = modern_desktop.LocalWorkspace(Path("fixture"))
        self.close_resources.side_effect = RuntimeError("browser already closed")
        with self.assertRaisesRegex(RuntimeError, "browser already closed"):
            workspace.close()
        workspace.close()
        self.server.close.assert_called_once_with()
        self.server.task_dispatcher.shutdown.assert_called_once_with(timeout=2)
        self.close_resources.assert_called_once_with(self.app)

    def test_successful_close_is_idempotent_before_or_after_start(self):
        workspace = modern_desktop.LocalWorkspace(Path("fixture"))
        workspace.close()
        workspace.close()
        self.close_resources.assert_called_once_with(self.app)
        self.server.close.assert_called_once_with()

    def test_listener_close_failure_still_stops_request_workers(self):
        workspace = modern_desktop.LocalWorkspace(Path("fixture"))
        self.server.close.side_effect = OSError("listener failure")
        with self.assertRaisesRegex(OSError, "listener failure"):
            workspace.close()
        self.server.task_dispatcher.shutdown.assert_called_once_with(timeout=2)

    def test_request_workers_stop_before_listener_and_wakeup_socket_close(self):
        workspace = modern_desktop.LocalWorkspace(Path("fixture"))
        calls = []
        self.server.task_dispatcher.shutdown.side_effect = lambda **kwargs: calls.append("workers")
        self.server.close.side_effect = lambda: calls.append("listener")
        workspace.close()
        self.assertFalse(self.server.accepting)
        self.assertEqual(calls, ["workers", "listener"])

    def test_worker_shutdown_failure_still_releases_listener(self):
        workspace = modern_desktop.LocalWorkspace(Path("fixture"))
        self.server.task_dispatcher.shutdown.side_effect = RuntimeError("worker failure")
        with self.assertRaisesRegex(RuntimeError, "worker failure"):
            workspace.close()
        self.server.close.assert_called_once_with()
        self.thread_type.return_value.join.assert_called_once_with(timeout=2)


class RealLocalWorkspaceTests(unittest.TestCase):
    def test_slow_request_keeps_wakeup_socket_after_bounded_shutdown(self):
        with tempfile.TemporaryDirectory() as folder:
            workspace = modern_desktop.LocalWorkspace(Path(folder))
            entered, release = threading.Event(), threading.Event()
            original_app = workspace.app.wsgi_app
            failures = []

            def held_request(environ, start_response):
                entered.set()
                if not release.wait(5):
                    raise TimeoutError("Test request was not released")
                return original_app(environ, start_response)

            workspace.app.wsgi_app = held_request
            workspace.start()

            def request():
                try:
                    with urlopen(Request(workspace.url, headers={"Connection": "close"}), timeout=5) as response:
                        response.read()
                except Exception as exc:
                    failures.append(exc)

            client = threading.Thread(target=request)
            client.start()
            try:
                self.assertTrue(entered.wait(5))
                shutdown = workspace.server.task_dispatcher.shutdown
                with patch.object(workspace.server.task_dispatcher, "shutdown", side_effect=lambda **kwargs: shutdown(timeout=0.01)), \
                        self.assertLogs("waitress", level="WARNING") as logs:
                    workspace.close()
                self.assertTrue(any("still running" in entry for entry in logs.output))
                self.assertFalse(workspace.server.trigger._closed)
                with self.assertRaises(OSError):
                    socket.create_connection(("127.0.0.1", int(workspace.server.effective_port)), timeout=0.2)
                release.set()
                client.join(5)
                workspace._cleanup_thread.join(5)
                self.assertFalse(client.is_alive())
                self.assertFalse(workspace._cleanup_thread.is_alive())
                self.assertFalse(workspace.server.task_dispatcher.threads)
                self.assertTrue(workspace.server.trigger._closed)
                self.assertEqual(failures, [])
            finally:
                release.set()
                client.join(5)
                if hasattr(workspace, "_cleanup_thread"):
                    workspace._cleanup_thread.join(5)
                workspace.close()

    def test_inflight_request_finishes_before_wakeup_socket_is_closed(self):
        with tempfile.TemporaryDirectory() as folder:
            workspace = modern_desktop.LocalWorkspace(Path(folder))
            entered, release, draining = threading.Event(), threading.Event(), threading.Event()
            original_app = workspace.app.wsgi_app
            failures = []

            def held_request(environ, start_response):
                entered.set()
                if not release.wait(5):
                    raise TimeoutError("Test request was not released")
                return original_app(environ, start_response)

            workspace.app.wsgi_app = held_request
            workspace.start()

            def request():
                try:
                    with urlopen(Request(workspace.url, headers={"Connection": "close"}), timeout=5) as response:
                        response.read()
                except Exception as exc:
                    failures.append(exc)

            shutdown = workspace.server.task_dispatcher.shutdown

            def drain(**kwargs):
                draining.set()
                return shutdown(**kwargs)

            client = threading.Thread(target=request)
            closer = threading.Thread(target=workspace.close)
            client.start()
            try:
                self.assertTrue(entered.wait(5))
                with patch.object(workspace.server.task_dispatcher, "shutdown", side_effect=drain):
                    closer.start()
                    self.assertTrue(draining.wait(5))
                    self.assertFalse(workspace.server.trigger._closed)
                    release.set()
                    closer.join(5)
                client.join(5)
                self.assertFalse(closer.is_alive())
                self.assertFalse(client.is_alive())
                self.assertFalse(workspace.server.task_dispatcher.threads)
                self.assertEqual(failures, [])
            finally:
                release.set()
                if closer.ident is not None:
                    closer.join(5)
                client.join(5)
                workspace.close()

    def test_loopback_serves_interface_and_closes_listener_and_workers(self):
        # No browser or external connection: exercise the actual local server lifecycle.
        with tempfile.TemporaryDirectory() as folder:
            workspace = modern_desktop.LocalWorkspace(Path(folder))
            try:
                workspace.start()
                request = Request(workspace.url, headers={"Connection": "close"})
                with urlopen(request, timeout=5) as response:
                    self.assertEqual(response.status, 200)
                    self.assertIn(b"Personal Job Agent", response.read())
            finally:
                workspace.close()
            self.assertFalse(workspace.thread.is_alive())
            self.assertFalse(workspace.server.task_dispatcher.threads)
            self.assertTrue(workspace.app.extensions["local_state"].closing)
            with self.assertRaises(OSError):
                socket.create_connection(("127.0.0.1", int(workspace.server.effective_port)), timeout=0.2)


class DesktopEntryPointTests(unittest.TestCase):
    def test_gui_opens_modern_desktop_by_default(self):
        with patch.object(modern_desktop, "launch") as launch:
            self.assertEqual(main(["gui"]), 0)
        launch.assert_called_once()

    def test_explicit_classic_switch_retains_previous_desktop(self):
        with patch("norway_job_agent.desktop.launch") as classic, patch.object(modern_desktop, "launch") as modern:
            self.assertEqual(main(["gui", "--classic"]), 0)
        classic.assert_called_once()
        modern.assert_not_called()

    def test_gui_failure_is_visible_without_pythonw_stderr(self):
        with patch.object(modern_desktop, "launch", side_effect=RuntimeError("missing runtime")), \
                patch.object(modern_desktop, "report_startup_error") as report, \
                patch.object(sys, "stderr", None):
            self.assertEqual(main(["gui"]), 1)
        report.assert_called_once_with("missing runtime")

    def test_windows_error_dialog_works_without_console(self):
        with patch.object(modern_desktop.sys, "platform", "win32"), \
                patch.object(sys, "stderr", None), patch("ctypes.windll", create=True) as native:
            modern_desktop.report_startup_error("Install the desktop dependencies.")
        args = native.user32.MessageBoxW.call_args.args
        self.assertIn("Install the desktop dependencies.", args[1])
        self.assertEqual(args[2], "Personal Job Agent")

    def test_failed_dialog_falls_back_to_terminal(self):
        output = io.StringIO()
        with patch.object(modern_desktop.sys, "platform", "win32"), \
                patch.object(sys, "stderr", output), patch("ctypes.windll", create=True) as native:
            native.user32.MessageBoxW.side_effect = OSError("no desktop")
            modern_desktop.report_startup_error("Dependency missing")
        self.assertIn("Dependency missing", output.getvalue())


if __name__ == "__main__":
    unittest.main()
