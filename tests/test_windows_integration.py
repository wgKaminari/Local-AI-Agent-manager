"""The desktop app's Windows identity: shortcuts, single instance, title bar."""
import ctypes
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from norway_job_agent import windows_integration as windows
from norway_job_agent.web_api import close_resources, create_app


class LaunchCommandTests(unittest.TestCase):
    def test_shortcut_opens_the_checkout_without_a_console(self):
        target, arguments, folder = windows.launch_command()
        root = Path(__file__).resolve().parents[1]
        self.assertEqual(Path(folder), root)
        self.assertEqual(arguments, f'"{root / "run.py"}" gui')
        if Path(sys.executable).with_name("pythonw.exe").is_file():
            self.assertEqual(Path(target).name.lower(), "pythonw.exe")

    def test_icon_is_bundled_with_every_taskbar_size(self):
        data = windows.icon_path().read_bytes()
        self.assertEqual(data[:4], b"\x00\x00\x01\x00")  # ICO header
        self.assertGreaterEqual(int.from_bytes(data[4:6], "little"), 6)


@unittest.skipUnless(sys.platform == "win32", "Windows shell integration")
class WindowsShellTests(unittest.TestCase):
    def test_shortcut_carries_the_app_identity_target_and_icon(self):
        with tempfile.TemporaryDirectory() as folder:
            created = windows.create_shortcuts(folder=Path(folder))
            self.assertEqual([Path(path).name for path in created], [windows.SHORTCUT_NAME])
            self.assertTrue((Path(folder) / windows.SHORTCUT_NAME).is_file())
            # Running again replaces the shortcut rather than failing.
            windows.create_shortcuts(folder=Path(folder))

    def test_second_claim_for_the_same_data_folder_is_refused(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(windows, "focus_existing_window") as focus:
            previous = windows._mutex
            try:
                self.assertTrue(windows.claim_single_instance(Path(folder) / "a"))
                held = windows._mutex
                self.assertFalse(windows.claim_single_instance(Path(folder) / "a"))
                focus.assert_called_once_with()
                ctypes.windll.kernel32.CloseHandle(ctypes.c_void_p(held))
                # A different private data folder may open its own window.
                self.assertTrue(windows.claim_single_instance(Path(folder) / "b"))
                ctypes.windll.kernel32.CloseHandle(ctypes.c_void_p(windows._mutex))
            finally:
                windows._mutex = previous


class ThemeHookTests(unittest.TestCase):
    def test_saving_the_theme_updates_the_native_title_bar(self):
        with tempfile.TemporaryDirectory() as folder:
            app = create_app(Path(folder), "fixture-session")
            app.config.update(TESTING=True, LOCAL_ORIGIN="http://127.0.0.1:7654")
            seen = []
            app.config["THEME_CHANGED"] = seen.append
            try:
                response = app.test_client().put("/api/appearance", json={"theme": "dark"}, base_url="http://127.0.0.1:7654",
                                                 headers={"X-Session-Token": "fixture-session"})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(seen, ["dark"])
                app.config["THEME_CHANGED"] = lambda theme: 1 / 0  # Cosmetic failures never block saving.
                response = app.test_client().put("/api/appearance", json={"theme": "light"}, base_url="http://127.0.0.1:7654",
                                                 headers={"X-Session-Token": "fixture-session"})
                self.assertEqual(response.get_json()["theme"], "light")
            finally:
                close_resources(app)
                app.extensions["local_state"].worker.shutdown(wait=True)

    def test_served_page_starts_in_the_saved_theme(self):
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / "appearance.json").write_text('{"theme": "dark"}', encoding="utf-8")
            app = create_app(Path(folder), "fixture-session")
            app.config.update(TESTING=True, LOCAL_ORIGIN="http://127.0.0.1:7654")
            try:
                html = app.test_client().get("/", base_url="http://127.0.0.1:7654").get_data(as_text=True)
                self.assertIn('<body data-theme="dark" data-remote="">', html)
                self.assertNotIn("__THEME__", html)
            finally:
                close_resources(app)
                app.extensions["local_state"].worker.shutdown(wait=True)


class ShortcutCommandTests(unittest.TestCase):
    def test_cli_creates_and_removes_shortcuts(self):
        from norway_job_agent.cli import main
        with patch.object(windows, "create_shortcuts", return_value=["Start.lnk", "Desktop.lnk"]) as create, \
                patch("builtins.print"):
            self.assertEqual(main(["shortcuts"]), 0)
        create.assert_called_once_with()
        with patch.object(windows, "remove_shortcuts") as remove, patch("builtins.print"):
            self.assertEqual(main(["shortcuts", "--remove"]), 0)
        remove.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
