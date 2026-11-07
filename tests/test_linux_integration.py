"""The desktop app on Linux: launchers, single instance, keyring, updates."""
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from norway_job_agent import linux_integration as linux

ROOT = Path(__file__).resolve().parents[1]

# A stand-in for secret-tool that keeps secrets in files, so tests never touch
# the real login keyring. Attributes come in pairs after the subcommand.
FAKE_SECRET_TOOL = r"""#!/bin/sh
store="$FAKE_KEYRING"
command="$1"; shift
if [ "$command" = store ]; then shift 2; fi
key=$(printf '%s_' "$@" | tr -c 'A-Za-z0-9_-' '_')
case "$command" in
  store) cat > "$store/$key" ;;
  lookup) [ -f "$store/$key" ] && cat "$store/$key" || exit 1 ;;
  clear) rm -f "$store/$key" ;;
esac
"""


class DesktopEntryTests(unittest.TestCase):
    def test_exec_arguments_are_quoted_per_the_specification(self):
        self.assertEqual(linux._exec_quote("/usr/bin/python3"), "/usr/bin/python3")
        self.assertEqual(linux._exec_quote("/home/a b/run.py"), '"/home/a b/run.py"')
        self.assertEqual(linux._exec_quote('/x/$HOME"q`'), '"/x/\\$HOME\\"q\\`"')
        self.assertEqual(linux._exec_quote("/x/100%"), '"/x/100%%"')

    def test_entry_opens_this_checkout_with_the_app_icon_and_window_class(self):
        entry = linux.desktop_entry()
        self.assertTrue(entry.startswith("[Desktop Entry]\n"))
        self.assertIn("Name=Personal Job Agent\n", entry)
        self.assertIn(f"Icon={linux.DESKTOP_ID}\n", entry)
        self.assertIn(f"StartupWMClass={linux.DESKTOP_ID}\n", entry)
        self.assertIn("Terminal=false\n", entry)
        self.assertIn(f"Path={ROOT}\n", entry)
        exec_line = next(line for line in entry.splitlines() if line.startswith("Exec="))
        self.assertTrue(exec_line.endswith(" gui"))
        # Exec= escapes backslashes, so compare the path as the file writes it
        # (this also runs on Windows CI, where paths contain backslashes).
        self.assertIn(linux._exec_quote(str(ROOT / "run.py")), exec_line)

    def test_icon_is_a_png(self):
        self.assertEqual(linux.png_icon().read_bytes()[:8], b"\x89PNG\r\n\x1a\n")


@unittest.skipUnless(sys.platform.startswith("linux"), "Linux desktop integration")
class LauncherTests(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())
        self.env = patch.dict(os.environ, {"XDG_DATA_HOME": str(self.folder / "data")})
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_menu_and_desktop_launchers_are_created_replaced_and_removed(self):
        desktop = self.folder / "Desktop"
        desktop.mkdir()
        with patch.object(linux, "desktop_dir", return_value=desktop), \
                patch.object(linux, "_refresh_menus"), patch.object(linux, "_trust") as trust:
            created = linux.create_shortcuts()
            self.assertEqual(created, [str(self.folder / "data/applications" / linux.DESKTOP_FILE),
                                       str(desktop / linux.DESKTOP_FILE)])
            for path in map(Path, created):
                self.assertTrue(os.access(path, os.X_OK))
                self.assertIn("Exec=", path.read_text(encoding="utf-8"))
            trust.assert_called_once_with(desktop / linux.DESKTOP_FILE)
            self.assertTrue(linux.icon_target().is_file())
            linux.create_shortcuts()  # Running again replaces them.
            linux.remove_shortcuts()
        self.assertFalse(any(Path(path).exists() for path in created))
        self.assertFalse(linux.icon_target().exists())

    def test_no_desktop_folder_means_only_the_menu_entry(self):
        with patch.object(linux, "desktop_dir", return_value=None), patch.object(linux, "_refresh_menus"):
            created = linux.create_shortcuts()
        self.assertEqual([Path(path).parent.name for path in created], ["applications"])

    def test_windows_integration_hands_over_on_linux(self):
        from norway_job_agent import windows_integration as windows
        with patch.object(linux, "create_shortcuts", return_value=["menu"]) as create:
            self.assertEqual(windows.create_shortcuts(folder=self.folder), ["menu"])
        create.assert_called_once_with(start_menu=True, desktop=True, folder=self.folder)
        self.assertEqual(windows.window_icon_path().suffix, ".png")
        self.assertEqual(windows.shortcut_locations(), "applications menu and desktop")


@unittest.skipUnless(sys.platform.startswith("linux"), "Linux single instance")
class SingleInstanceTests(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())
        runtime = self.folder / "run"
        runtime.mkdir(mode=0o700)
        self.env = patch.dict(os.environ, {"XDG_RUNTIME_DIR": str(runtime)})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.addCleanup(linux.release_single_instance)
        self.addCleanup(linux.on_focus_request, None)

    def test_second_start_is_refused_and_brings_the_window_forward(self):
        focused = threading.Event()
        linux.on_focus_request(focused.set)
        self.assertTrue(linux.claim_single_instance(self.folder / "a"))
        # flock() conflicts between open files even in one process, so this
        # is what a second start sees.
        self.assertFalse(linux.claim_single_instance(self.folder / "a"))
        self.assertTrue(focused.wait(5))

    def test_another_data_folder_opens_its_own_window(self):
        self.assertTrue(linux.claim_single_instance(self.folder / "a"))
        lock_a, _ = linux._instance_paths(self.folder / "a")
        lock_b, _ = linux._instance_paths(self.folder / "b")
        self.assertNotEqual(lock_a, lock_b)

    def test_lock_is_free_again_after_the_app_exits(self):
        code = ("import sys; sys.path.insert(0, %r); from pathlib import Path; "
                "from norway_job_agent import linux_integration as l; "
                "sys.exit(0 if l.claim_single_instance(Path(%r)) else 3)") % (str(ROOT / "src"), str(self.folder / "a"))
        self.assertEqual(subprocess.run([sys.executable, "-c", code], timeout=30).returncode, 0)
        self.assertTrue(linux.claim_single_instance(self.folder / "a"))
        self.assertEqual(subprocess.run([sys.executable, "-c", code], timeout=30).returncode, 3)

    def test_focus_socket_is_private(self):
        self.assertTrue(linux.claim_single_instance(self.folder / "a"))
        _, path = linux._instance_paths(self.folder / "a")
        self.assertEqual(path.stat().st_mode & 0o077, 0)
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.connect(str(path))
            client.sendall(b"nonsense")


@unittest.skipUnless(sys.platform.startswith("linux"), "Linux keyring")
class KeyringTests(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())
        self.store = self.folder / "keyring"
        self.store.mkdir()
        bin_dir = self.folder / "bin"
        bin_dir.mkdir()
        tool = bin_dir / "secret-tool"
        tool.write_text(FAKE_SECRET_TOOL, encoding="utf-8")
        tool.chmod(0o755)
        self.data = self.folder / "data"
        self.data.mkdir()
        self.env = patch.dict(os.environ, {"PATH": f"{bin_dir}:{os.environ.get('PATH', '')}", "FAKE_KEYRING": str(self.store),
                                           "XDG_RUNTIME_DIR": str(self.folder), "JOB_AGENT_KEYRING": ""})
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_api_key_lives_in_the_keyring_not_the_data_folder(self):
        from norway_job_agent import cloud_ai
        key = "sk-ant-fixture-secret-1234"
        with patch.object(cloud_ai, "list_models", return_value=[{"id": "claude-fixture"}]):
            cloud_ai.save_key(self.data, "anthropic", key)
        stored = (self.data / cloud_ai.KEY_FILE).read_text(encoding="utf-8")
        self.assertNotIn("fixture-secret", stored)
        self.assertEqual(json.loads(stored)["keys"]["anthropic"]["protection"], "linux-keyring")
        self.assertEqual(cloud_ai._key(self.data, "anthropic"), key)
        self.assertEqual(len(list(self.store.iterdir())), 1)
        cloud_ai.delete_key(self.data, "anthropic")
        self.assertEqual(list(self.store.iterdir()), [])

    def test_missing_keyring_entry_asks_to_add_the_key_again(self):
        from norway_job_agent import cloud_ai
        with self.assertRaisesRegex(cloud_ai.CloudAIError, "login keyring"):
            cloud_ai._unprotect({"protection": "linux-keyring", "item": "ai-key-anthropic"}, self.data)

    def test_gmail_token_roundtrip_and_disconnect_clear_the_keyring(self):
        from norway_job_agent import gmail
        token = {"scope": gmail.SCOPE, "refresh_token": "fixture-refresh", "access_token": "fixture-access"}
        gmail._save_token(self.data, token)
        stored = gmail._token_path(self.data).read_text(encoding="utf-8")
        self.assertNotIn("fixture-refresh", stored)
        self.assertEqual(gmail._load_token(self.data), token)
        gmail.disconnect(self.data)
        self.assertEqual(list(self.store.iterdir()), [])

    def test_without_a_keyring_secrets_stay_in_a_private_file(self):
        from norway_job_agent import cloud_ai
        with patch.dict(os.environ, {"JOB_AGENT_KEYRING": "off"}):
            entry = cloud_ai._protect("sk-fixture", self.data, "anthropic")
        self.assertEqual(entry["protection"], "none")
        self.assertEqual(cloud_ai._unprotect(entry, self.data), "sk-fixture")
        self.assertEqual(list(self.store.iterdir()), [])


@unittest.skipUnless(sys.platform.startswith("linux"), "Linux system information")
class MemoryTests(unittest.TestCase):
    def test_free_memory_comes_from_proc_meminfo(self):
        from norway_job_agent.runtime_setup import memory_available_gb
        free = memory_available_gb()
        self.assertIsInstance(free, float)
        self.assertGreater(free, 0)


class LinuxUpdateTests(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())
        (self.folder / "app").mkdir()
        (self.folder / "install.json").write_text("{}", encoding="utf-8")
        (self.folder / "app" / "install.sh").write_text("# installer", encoding="utf-8")

    def test_installed_copy_updates_through_a_detached_install_sh(self):
        from norway_job_agent import updates
        with patch("norway_job_agent.updates.checkout", return_value=self.folder / "app"), \
                patch("norway_job_agent.updates.sys.platform", "linux"), \
                patch("norway_job_agent.updates.subprocess.Popen") as popen:
            result = updates.start_update("v0.10.0")
        command = popen.call_args.args[0]
        self.assertEqual(command[0], "/bin/bash")
        self.assertNotEqual(Path(command[1]).parent, self.folder / "app")  # Runs from a temporary copy.
        self.assertEqual(Path(command[1]).read_text(encoding="utf-8"), "# installer")
        self.assertEqual(command[command.index("--install-root") + 1], str(self.folder))
        self.assertEqual(command[command.index("--tag") + 1], "v0.10.0")
        self.assertEqual(command[command.index("--wait-pid") + 1], str(os.getpid()))
        self.assertTrue(popen.call_args.kwargs["start_new_session"])
        self.assertEqual(result["log"], str(self.folder / "update.log"))

    def test_linux_offers_the_curl_install_command(self):
        from norway_job_agent import updates
        with patch("norway_job_agent.updates.sys.platform", "linux"):
            self.assertIn("install.sh | bash", updates.install_command())
        with patch("norway_job_agent.updates.sys.platform", "win32"):
            self.assertIn("install.ps1 | iex", updates.install_command())


@unittest.skipUnless(sys.platform.startswith("linux"), "Linux installer")
class InstallScriptTests(unittest.TestCase):
    def test_installer_parses_and_validates_its_options(self):
        script = ROOT / "install.sh"
        self.assertEqual(subprocess.run(["bash", "-n", str(script)]).returncode, 0)
        bad = subprocess.run(["bash", str(script), "--tag", "latest; rm -rf ~"], capture_output=True, text=True)
        self.assertEqual(bad.returncode, 2)
        self.assertIn("release version", bad.stderr)
        self.assertEqual(subprocess.run(["bash", str(script), "--gui", "tk"], capture_output=True).returncode, 2)


if __name__ == "__main__":
    unittest.main()
